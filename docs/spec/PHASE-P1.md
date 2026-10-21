# PHASE-P1 规格

## 0. 元信息

| 项 | 值 |
| --- | --- |
| 文档 | PHASE-P1 规格（第三部分 P1 批次） |
| 版本 | v1 |
| 日期 | 2026-10-21 |
| 状态 | **待实施** |
| 范围 | 5.1 转写区分说话人（asr 功能扩展）、5.2 批量队列、5.3 补全生成参数 |
| 关联文档 | `docs/spec/PHASE-3-BACKLOG.md`（§1.1 选定路线、§2 批量队列、§6 补全生成参数）、`docs/spec/ASR-ENGINE-1.md`（ASR 引擎化机制）、`docs/ENGINE_RESEARCH.md` |
| 许可证约束 | 仅 MIT / Apache-2.0 / BSD；禁止 gated 模型（不得要求 HF 登录或接受条款） |

---

## 1. 背景与目标

第三部分收尾阶段剩余三项能力，已在 `PHASE-3-BACKLOG.md` 登记并给出技术结论，本规格把它们落成可实施、可验收的细则。

现状缺口：

1. **转写没有说话人信息**。`backend/app/engine/asr.py:107` 的 `LocalASREngine.transcribe()` 虽然已经调用 `model.transcribe(..., vad_filter=True)`，但只取 `seg.text`（第 118 行），**时间戳被直接丢弃**；`backend/app/worker.py:577` 的 `_run_asr()` 只写一个 `<task_id>.txt` 纯文本，任务详情没有分段结构。多人对话转写后要人工重听分辨谁在说。
2. **只能一次提交一条**。`POST /api/tasks` 一次一个任务；`frontend/src/pages/Workbench.tsx:313` 的 `submit()` 单条提交后立刻订阅该任务的音频流。要产出 50 条不同音色/不同语速的音频，只能手点 50 次。
3. **采样参数不全**。`TTSParams`（`backend/app/engine/base.py:12`）只有 `speed / pitch / temperature / top_p / top_k / repetition_penalty / steps`，没有扩散类引擎关心的 `num_step / guidance_scale / denoise`；`local.py:413` 的 `base_kwargs` 也只传 5 个采样键。

目标（三条，各自可独立验收）：

- **G1** 转写可选输出「谁在哪一段说话」，默认关闭，关闭时输出结构与现在完全一致；
- **G2** 一次提交多条文本，参数支持逐项列表（每条不同语速/时长），产出可打包下载；
- **G3** 补齐三个生成参数，任务级可覆盖设置页默认值，不支持的引擎置灰并提示，云端透传。

---

## 2. 需求澄清清单

### 2.1 需求分级

| 编号 | 需求 | 级别 | 说明 |
| --- | --- | --- | --- |
| R1 | 转写结果带 `segment.speaker`，关闭时全为 null | **Must** | 后端返回结构 + 落盘文件 |
| R2 | 说话人区分走全宽松许可证路线（faster-whisper + sherpa-onnx + CAM++ + sklearn） | **Must** | PHASE-3-BACKLOG §1.1 已定 |
| R3 | CAM++ 作为 `kind="asr"` 引擎条目，可在引擎管理页安装/删除 | **Must** | 复用 ASR-ENGINE-1 机制 |
| R4 | 转写结果页按 speaker 分色显示 | **Must** | 界面 |
| R5 | `POST /api/batch` 批量提交，逐项列表参数 | **Must** | 核心 |
| R6 | 批次 = 父任务 + N 子任务，进度可见、可取消 | **Must** | 复用现有任务表与 worker |
| R7 | 批次产出打包 zip 下载 | **Must** | zipfile 标准库 |
| R8 | `num_step / guidance_scale / denoise` 进 `TTSParams` + 设置页默认 + 高级面板 + 云端透传 | **Must** | |
| R9 | 不支持的引擎对这三个参数置灰并给提示 | **Must** | 沿用 `kind_tags` 门禁思路 |
| R10 | 字幕导出可选带说话人前缀 | **Should** | `speaker_prefix=1` |
| R11 | 引擎管理页 CAM++ 卡片显示「约 28MB」与下载进度 | **Should** | 安装体验 |
| R12 | 批量支持从粘贴的多行文本自动拆分 | **Should** | 界面便利 |
| R13 | 批次失败重试（只重试失败项） | **Nice** | |
| R14 | 说话人聚类结果可手工改名（SPK1→张三）并随任务保存 | **Nice** | 不落音色库 |
| — | 说话人区分用于 pyannote / WhisperX | **Won't** | gated 权重，违反许可证约束 |
| — | 实时/流式说话人区分 | **Won't** | 离线方案 |
| — | 重叠说话（两人同时讲）分离 | **Won't** | 离线聚类通病，见 §11 |
| — | 把说话人「归属到已有音色库」模式 | **Won't** | 后台已注明「后续再加」，不在 P1 |
| — | `target_duration_s` 时长控制、导出档位、后期处理链 | **Won't** | 属 P2（3.3/3.4/3.5） |
| — | 批量上传 CSV/Excel 文件解析 | **Won't** | P1 只支持 JSON 与粘贴多行文本 |
| — | 云端 ASR 的说话人区分 | **Won't** | 云端返回无时间轴保证，且需改远端契约 |
| — | 批量并发多 worker | **Won't** | 现 `state.py:108` 为 `workers=1`，见 §11 |

### 2.2 假设

- **A1** 主环境可新增两个可选依赖 `sherpa-onnx`（Apache-2.0，自带 onnxruntime）与 `scikit-learn`（BSD-3），不新建 venv；缺失时只有在打开 `asr.diarize.enabled` 才报错。
- **A2** CAM++ 权重来自 sherpa-onnx 官方发布包（3D-Speaker `speech_campplus_sv_zh-cn_16k-common`，约 28MB，Apache-2.0），**程序自动下载，用户无需手工**；下载链路复用 `engine/adapters/asr_whisper.py` 的 `install(progress, dry_run)` 事件通道。
- **A3** `faster-whisper` 的 segments 时间戳精度足够用作说话人切段（segment 级，不做词级对齐）。
- **A4** 批量子任务串行执行（沿用单 worker），「批量」的收益是一次提交、一次导出，不是并行加速。
- **A5** `num_step` 与现有 `steps` 语义不同：`steps` 是「生成 token 上限」（`base.py:19` 注释），`num_step` 是「迭代解掩码 / 扩散步数」（`docs/COMPETITIVE_VOICESTUDIO.md` 口径）。两者共存，不合并。
- **A6** 老任务、老 `config.yaml`、老 `runtime.json` 一律不需要迁移即可继续工作。

### 2.3 待确认问题（建议默认答案已给出）

| 编号 | 问题 | 建议默认 | 若不采纳的影响 |
| --- | --- | --- | --- |
| **Q1** | 是否接受新增 `sherpa-onnx` + `scikit-learn` 两个依赖（安装体积合计约 +60MB）？ | **接受**。写进 `requirements.pins.txt` 的「可选」分组，未装时只有打开开关才报 `DIARIZE_DEPS_MISSING` | 只能砍掉说话人区分，或改为纯 numpy 实现平均连接聚类（精度更差、代码更多） |
| **Q2** | `num_step` 与现有 `steps` 冲突如何处理？VoxCPM 适配器现在把 `steps` 当扩散步数用（`voxcpm.py:177-182`）。 | **`steps` 保持原语义不动，新增 `num_step` 默认 null=引擎默认；VoxCPM 优先读 `num_step`，为空时回落到 `steps` 的 2–30 旧规则** | 直接复用 `steps` 会让「token 上限 2048」被当成 2048 步扩散，引擎崩 |
| **Q3** | `guidance_scale` 是否首批就落到引擎？目前只有 VoxCPM 有对应概念（`cfg_value` 硬编码 2.0，`voxcpm.py:180`）。 | **首批只映射 VoxCPM 的 `cfg_value`，其余引擎置灰**；接口与存储层先行 | 全部置灰会让该字段「只能存不能用」，用户体验倒退 |
| **Q4** | 批次上限与父任务身份如何定？ | **单批 50 项、单批总字符 50000**；父任务 type 复用首个子任务类型，用 `params.batch_role="parent"` 标记，**不扩 `TASK_TYPES`** | 扩 `TASK_TYPES` 需同步改「任务中心」筛选页签与 db 白名单，回归面变大 |
| **Q5** | 批次 zip 是否包含失败项占位？ | **只含成功项 + `manifest.json` 记录失败项与原因** | 失败项放空文件会污染下游剪辑流程 |

---

## 3. 现状盘点（读码结论）

### 3.1 后端

| 位置 | 现状（已核实） | 可复用 / 需改动 |
| --- | --- | --- |
| `backend/app/api/tasks.py:110` | `CreateTaskRequest` 仅 9 个字段：`type / text / voice_id / asset_id / instruct / design / language / params / output_format / force_backend`。无批次、无父任务、无 diarize 开关 | 需加字段；`create_task()`（137）可直接复用为「子任务落库」函数 |
| `backend/app/api/tasks.py:131` | `_merged_params(cfg, extra)` = `cfg.defaults.model_dump()` + 请求 params，**请求侧 None 被丢弃**（`if v is not None`），但 defaults 里的 None 会原样写进 params | 新增参数默认值必须避免 None 进 defaults，见 §11 风险 R-4 |
| `backend/app/api/tasks.py:406` | 已有 `POST /api/tasks/batch-delete`（批量删除）；`GET /api/tasks`（322）分页 20/页、上限 100，**无 parent/batch 过滤参数** | 批量删除的入参形态可参考；列表需加 batch 过滤 |
| `backend/app/api/tasks.py:426` | `GET /api/tasks/{id}/subtitles` 从 `data/outputs/<task_id>/chunks.json` 渲染 srt/vtt/ass | 字幕复用；加 `speaker_prefix` 参数即可 |
| `backend/app/engine/asr.py:107` | `transcribe()` 返回 6 键：`text / language / language_probability / duration / backend / model / elapsed_ms`，**无 segments**；第 113-118 行已开 `vad_filter=True`，但只收集 `seg.text` | segments 就在手边，补收集即可，见 §5.1 |
| `backend/app/engine/asr.py:144` | `ASRRouter.transcribe()` 本地失败回落云端；云端 `CloudEngine.transcribe()`（`cloud.py:312`）直接 `resp.json()`，无时间轴保证 | diarize 只能在本地路径做，「云端后端 → 跳过」 |
| `backend/app/engine/base.py:12` | `TTSParams` 12 字段，`steps=2048`；`from_dict()` 用 `cls.__dataclass_fields__` 白名单过滤，**未知键静默丢弃** | 新参数必须进 dataclass 才会生效；老版本服务端也会静默丢弃 → 云端透传向前兼容 |
| `backend/app/engine/local.py:413` | `base_kwargs` 只传 `do_sample / temperature / top_p / top_k / repetition_penalty`；`hard_cap = req.params.steps or cfg.local.max_new_tokens`（420），`_token_budget()` 出 `max_new_tokens` | `generate_voice_clone`(440) / `generate_voice_design`(448) / `generate_custom_voice`(456) 都是 `**gen_kwargs`，加参数不破签名 |
| `backend/app/engine/cloud.py:288` | 云端请求体 `payload["params"] = req.params.to_dict()`（297） | 新参数加进 `to_dict()` 即自动透传，无需改云端 |
| `backend/app/api/remote.py:89` | 远端 `SynthPayload.params` → `TTSParams.from_dict(payload.params)` | 服务端老代码静默忽略未知键 → 新旧互通 |
| `backend/app/engine/adapters/voxcpm.py:175` | `_generate_kwargs()`：`inference_timesteps` 取 `params.steps`（仅 2–30 有效，否则回落 10）、`cfg_value` 硬编码 `2.0`、`denoise` 硬编码 `False` | 三个新参数的第一落地点，见 §5.3 |
| `backend/app/engine/router.py:63` | `_check_capability()` 按 `registry.capabilities(engine_id)` 校验 `req.kind ∈ kind_tags`（tts/clone/design） | 只做「能力类型」门禁，**没有参数级门禁** → 需新增参数能力标记 |
| `backend/app/engine/adapters/asr_whisper.py` | ASR 引擎化范式：`kind="asr"`、`kind_tags=["asr"]`、`cache_root/models_root/is_installed/required_files/install(progress, dry_run)/remove/status/health`、`ASR_ADAPTER_CLASSES`、`SIZE_TO_ENGINE_ID` | CAM++ 适配器照抄这套结构（`asr_speaker.py`） |
| `backend/app/worker.py:577` | `_run_asr()` 只写 `<task_id>.txt`，只更新 `transcript/output_file/output_format="txt"`，**不写 chunks.json、无 segments** | 需新增 segments 落盘；字幕导出对 asr 任务目前不可用 |
| `backend/app/worker.py:384` | `_finalize()` 负责音频拼接、`encode_output`、`_write_timeline`、状态流转与 `bus.publish` | 批量子任务复用同一路径；父任务需在 `_execute` 早退 |
| `backend/app/worker.py:65` | `_restore()` 启动时把 `running` 置 failed、`queued` 重新 `submit` | **父任务若留 queued 会被当普通任务执行**，见 §11 风险 R-5 |
| `backend/app/config.py:175` | `AsrLocalCfg{engine_id, model_size, device, compute_type, beam_size}`；`AsrCfg{engine, local, cloud}` | 新增 `asr.diarize` 子节 |
| `backend/app/config.py:21` | `RUNTIME_MUTABLE` 白名单含 `asr.local`、`defaults`、`engine.active`，**不含 `asr.diarize`** | 必须登记，否则 `PUT /api/settings` 直接 400 |
| `backend/app/config.py:199` | `LimitsCfg`：`max_upload_mb=50 / max_ref_audio_seconds=30 / max_asr_audio_seconds=1800 / max_text_chars=5000` | 新增 `max_batch_items`、`max_batch_total_chars` |
| `backend/app/db.py:33` | `TaskRow` 无 `segments / parent / batch` 字段；`to_dict()`（68）固定 25 键 | 见 §7 数据规格 |
| `backend/app/db.py:177` | 初始化只有 `Base.metadata.create_all(engine)`；**全仓 grep 无 `PRAGMA table_info` / `ALTER TABLE`，没有列升级机制** | 加列必须自带自愈式建列，见 §7.1 |
| `backend/app/state.py:108` | `state.tasks.start(workers=1)` | 批量子任务天然串行 |

### 3.2 前端

| 位置 | 现状 | 可复用 / 需改动 |
| --- | --- | --- |
| `frontend/src/components/ParamsPanel.tsx:17` | `SLIDERS` 数组 7 项（speed/pitch/temperature/top_p/top_k/repetition_penalty/steps），渲染成 range；下接「流式合成」「分块字数」「输出格式」「语言」 | 新增 3 个控件；需要「按引擎能力置灰」的入参 |
| `frontend/src/pages/Workbench.tsx:53` | 参数从 localStorage `STORE_PARAMS` 恢复；`:313 submit()` 组 body → `api.createTask(body)` → `stream.start(id)` | 批量入口不订阅音频流，改为跳转批次视图 |
| `frontend/src/lib/api.ts:71` | `createTask(body)` 单条；`:90 batchDelete(ids)` 已有批量语义 | 新增 `createBatch / getBatch / batchZip` |
| `frontend/src/pages/TasksPage.tsx:98` | 已有 `selected: string[]` + 全选 + 批量删除工具条（`:474-498`） | 批次视图直接复用多选与工具条样式 |
| `frontend/src/types.ts:6` | `TaskParams` 12 字段；`Task` 无 segments/parent 字段 | 同步扩展 |

### 3.3 依赖现状

- `requirements.pins.txt` 已锁 `numpy==2.2.6`、`faster-whisper==1.2.1`；
- **无 `sherpa-onnx`、无 `scikit-learn`、无 `onnxruntime`**（全仓 grep 无命中）→ 说话人区分是本批次唯一新增依赖点。

---

## 4. 用户故事

**说话人区分**

- US-1：我是播客剪辑，把 40 分钟双人对谈丢进转写，希望结果按时间轴标出「SPK1 / SPK2」，直接分辨哪句是谁说的，不用反复听。
- US-2：我是客服质检，需要把转写导出成字幕，希望在字幕前带说话人前缀，方便后续按人统计话术。

**批量队列**

- US-3：我是电商运营，有 50 条商品口播文案，想一次粘贴提交、统一用同一个音色，第 3、7、11 条语速 0.9，其余 1.0，跑完打一个 zip 拿走。
- US-4：我是课程制作，一次提交 30 节课的标题音，想在任务中心看到「12/30」进度，发现第 5 条失败后取消剩下的，改文案再提交。

**补全生成参数**

- US-5：我是音色调优，想固定 `num_step=16`、`guidance_scale=2.0` 做 A/B，找到最稳的组合后保存为默认值。
- US-6：我是普通用户，看不懂这些参数，希望它们默认留空、不影响我现在的生成结果。

---

## 5. 功能规格

### 5.1 说话人区分（asr 功能扩展）

#### 5.1.1 输入

| 项 | 规格 |
| --- | --- |
| 触发开关 | 任务级 `params.diarize`（`true/false/null`），null = 跟随配置 `asr.diarize.enabled`（默认 false） |
| 入口 | 工作台「音频转文字」页签新增开关「区分说话人」；`POST /api/transcribe` 新增表单字段 `diarize`；`POST /api/tasks` 新增请求字段 `diarize` |
| 音频 | 沿用现有资产链路（`asset_id`）；上限沿用 `limits.max_asr_audio_seconds=1800s`；时长超限行为不变（`AUDIO_TOO_LONG`） |
| 语言 | 沿用 `normalize_language()`；不因 diarize 改变语言处理 |

#### 5.1.2 处理（新模块 `backend/app/diarize.py`）

1. **取段**：`faster-whisper` 的 `model.transcribe(audio, language=..., beam_size=cfg.asr.local.beam_size, vad_filter=True)` 产出的 segments，收集 `{index, start, end, text}`。当前代码只取 `seg.text`（`asr.py:118`），改为同时保留时间轴。
2. **切音**：按 `[start, end]` 从原始音频切 16kHz 单声道 PCM；段长 < `asr.diarize.min_segment_s`（默认 0.4s）时与相邻段合并，间隔 ≤ `merge_gap_s`（默认 0.25s）的相邻段先合并再抽取。
3. **抽声纹**：`sherpa-onnx` 的 `SpeakerEmbeddingExtractor` + CAM++（192 维），逐段出 embedding，L2 归一化。
4. **聚类**：`sklearn.cluster.AgglomerativeClustering(n_clusters=None, distance_threshold=asr.diarize.threshold, metric="cosine", linkage="average")`；若聚类数 > `max_speakers`（默认 4），用同样参数强制 `n_clusters=max_speakers` 重跑一次。
5. **回填**：speaker 编号按「首次出现时间」排序，从 0 开始，对外标签 `SPK1/SPK2/…`（与数组下标差 1，避免用户看到 SPK0）。
6. **落盘**：写 `data/outputs/<task_id>/segments.json`（结构见 §7.2）；`<task_id>.txt` 内容与现在**完全一致**（纯文本，不改换行规则）。

默认参数（可在设置页改）：

| 参数 | 默认 | 范围 | 说明 |
| --- | --- | --- | --- |
| `asr.diarize.enabled` | `false` | bool | 总开关；false 时全链路不加载任何声纹模型 |
| `asr.diarize.max_speakers` | `4` | 1–10 | 超过则强制 `n_clusters=max_speakers` |
| `asr.diarize.threshold` | `0.55` | 0.30–0.90 | 余弦距离阈值；越小越容易拆人（人数偏多），越大越容易合并 |
| `asr.diarize.min_segment_s` | `0.4` | 0.2–2.0 | 短于此时长的段不单独抽声纹 |
| `asr.diarize.merge_gap_s` | `0.25` | 0–1.0 | 相邻段间隔小于此值时先合并 |
| `asr.diarize.engine_id` | `"asr-speaker-campplus"` | 枚举 | 声纹引擎条目，空表示用默认 |

#### 5.1.3 引擎条目（`backend/app/engine/adapters/asr_speaker.py`）

| 项 | 值 |
| --- | --- |
| engine_id | `asr-speaker-campplus` |
| 显示名 | `CAM++ 声纹（说话人区分）` |
| `kind` / `kind_tags` | `"asr"` / `["asr"]`（与 `asr_whisper.py` 同组，引擎管理页「语音识别」页签可见） |
| 模型目录 | `models/diarize/campplus/` |
| 必需文件 | `campplus.onnx` |
| 体积 | `expected_mb = 28.0` |
| 下载 | 官方发布包直链（sherpa-onnx speaker-recognition models，3D-Speaker `speech_campplus_sv_zh-cn_16k-common`），**程序自动下载、用户无需手工**；带断点重试，失败给 `DIARIZE_INSTALL_FAILED` + 网络建议 |
| 依赖包 | `sherpa-onnx`、`scikit-learn`（缺失时 `status()` 返回 `package_missing`） |
| 实现方法 | `is_installed / required_files / model_size_mb / status / install(progress, dry_run) / remove / health`，与 `asr_whisper.py` 同名同义（`ASR_ADAPTER_CLASSES` 追加即可被 `/api/engines?kind=asr` 列出） |
| 删除保护 | 被 `asr.diarize.engine_id` 选中且开关为 true 时，删除返回 `409 IN_USE` |

#### 5.1.4 输出

`transcribe()` 返回结构扩展（向后兼容：只新增键，不改已有键的语义）：

```json
{
  "text": "……全文不变……",
  "language": "zh",
  "language_probability": 0.98,
  "duration": 61.2,
  "backend": "local",
  "model": "faster-whisper-small",
  "elapsed_ms": 14200,
  "segments": [
    {"index": 0, "start": 0.0, "end": 3.4, "text": "你好，今天聊点别的。", "speaker": "SPK1"},
    {"index": 1, "start": 3.6, "end": 7.1, "text": "行，你说。", "speaker": "SPK2"}
  ],
  "speakers": [{"id": "SPK1", "seconds": 22.4}, {"id": "SPK2", "seconds": 18.9}],
  "diarize": {"enabled": true, "engine": "asr-speaker-campplus", "max_speakers": 4, "threshold": 0.55, "elapsed_ms": 3200, "degraded": false}
}
```

- **关闭时**：`segments[].speaker = null`，`speakers = []`，`diarize = {"enabled": false}`；`text`、`language`、`duration` 等全部与现在一致（老前端、老脚本不受影响）。
- **降级时**：声纹模型缺失 → 任务直接失败（`ASR_DIARIZE_MODEL_MISSING`，文案给「去 设置 → 引擎与模型 → 语音识别 安装 CAM++」）；聚类样本 < 2 段或聚类异常 → 任务**成功**，`speaker` 全 null，`diarize.degraded=true`，日志写明原因。
- **云端后端**：`ASRRouter` 走到 cloud 且开关为 true → 跳过 diarize，`diarize = {"enabled": false, "skipped": "cloud"}"`，任务成功，日志一条提示。

#### 5.1.5 异常码

| code | 场景 | 文案（短句、动宾） |
| --- | --- | --- |
| `ASR_DIARIZE_MODEL_MISSING` | 开关开但 CAM++ 未安装 | 说话人模型未安装，请到设置 → 引擎与模型 → 语音识别 点「安装」 |
| `DIARIZE_DEPS_MISSING` | 缺 `sherpa-onnx` 或 `scikit-learn` | 缺少说话人区分依赖，请执行 pip install -r requirements.pins.txt |
| `DIARIZE_INSTALL_FAILED` | 下载失败 | 声纹模型下载失败，请检查网络后重试 |
| `SEGMENTS_NOT_FOUND` | 老任务无 segments.json | 该任务没有分段信息，请重新转写 |

---

### 5.2 批量队列

#### 5.2.1 提交结构

`POST /api/batch`，请求：

```json
{
  "type": "tts",
  "defaults": {
    "voice_id": "builtin_vivian",
    "language": "zh",
    "instruct": "",
    "output_format": "wav",
    "params": {"temperature": 0.9, "streaming": false}
  },
  "items": [
    {"text": "第一条口播文案。", "params": {"speed": 0.9}},
    {"text": "第二条口播文案。"},
    {"text": "第三条口播文案。", "params": {"speed": 0.9, "num_step": 16}}
  ],
  "speed": [0.9, 1.0, 0.9],
  "stop_on_error": false
}
```

规则：
- `items[]` 每项可覆盖：`text / voice_id / asset_id / instruct / design / language / params / output_format / diarize`；
- 未覆盖的字段一律**继承 `defaults`**，再往下继承 `cfg.defaults`（与 `_merged_params` 同口径）；
- `items[]` 为空或非数组 → 400 `BATCH_ITEMS_EMPTY`；`items[].text` 为空 → 400 `TEXT_EMPTY`（错误信息带第几项，从 1 计）；
- 单项字符数 ≤ `limits.max_text_chars`（5000），批次总字符 ≤ `limits.max_batch_total_chars`（50000），否则 400 `BATCH_TOO_LARGE`；
- 项数 ≤ `limits.max_batch_items`（50），超出 → 400 `BATCH_ITEMS_TOO_MANY`（文案：单批最多 50 项，请拆成多批）。

#### 5.2.2 「逐项列表」参数语义

顶层出现这些键时，值可以是标量（广播到全部项）或数组（逐项）：

| 键 | 标量示例 | 数组示例 | 说明 |
| --- | --- | --- | --- |
| `speed` | `1.0` | `[0.9, 1.0, 1.1]` | 语速逐一不同 |
| `num_step` | `16` | `[8, 16, 32]` | 新增参数，见 §5.3 |
| `guidance_scale` | `2.0` | `[1.5, 2.0, 2.5]` | |
| `denoise` | `false` | `[true, false, true]` | |
| `temperature / top_p / top_k / repetition_penalty / steps / pitch / chunk_chars` | 标量 | 数组 | 同规则 |

判定规则（顺序执行）：
1. 数组长度 == `items.length` → 逐项取值；
2. 数组长度 == 1 → 视为标量，广播到全部项；
3. 其它长度 → 400 `BATCH_PARAM_LENGTH_MISMATCH`（文案：参数 `speed` 有 2 个值，但批次有 3 项）；
4. 标量与数组混用（同一键既在顶层给数组、又在 `items[].params` 里给值）→ **`items[].params` 优先**（就近覆盖原则）。

明确不做：逐项列表**不**用于 `output_format / language / voice_id`（这三项请用 `items[]` 覆盖，避免「第 3 项是 mp3」这种混批增加打包复杂度）。

#### 5.2.3 父任务与子任务

- **父任务**：1 行 `tasks` 记录，`type` = `items[0].type`（与子任务同），`params.batch_role="parent"`、`params.batch_id="<batch_id>"`；**不产出音频**，不调用 `state.tasks.submit()`。
- **子任务**：N 行，`params.batch_id` 相同、`params.batch_index` 从 0 起，**各自转成现有 `CreateTaskRequest` 走 `create_task()`**，因此语音校验、音色校验、格式校验、日志、重试、下载全部沿用现有逻辑。
- **父子关联**：父任务 id 即批次 id，子任务通过 `params.batch_id` 关联（不新增 parent 外键，见 §7.1）。
- **不允许**：对父任务调用 `retry`（400 `BATCH_NOT_RETRYABLE`，提示「请重试失败项」）。

#### 5.2.4 进度

- 父任务 `progress = (completed + failed + canceled) / total`，`status` 聚合：有未结束子任务 → `running`；全结束且无失败 → `completed`；有失败 → `failed`（`error_message` 写「3/50 项失败」）。
- 每次子任务状态变化触发一次父任务聚合刷新 + `bus.publish("tasks", "task.updated", {...})`（沿用现有 topic，前端无需新通道）。
- `GET /api/batch/{batch_id}` 返回 `counts {total, queued, running, completed, failed, canceled}`，供进度视图轮询/订阅。
- 前端进度刷新沿用「任务中心」既有 SSE/轮询方式（`tasks` topic），不新增 WebSocket。

#### 5.2.5 产出打包

- `GET /api/batch/{batch_id}/zip` → 现场生成并缓存到 `data/outputs/<batch_id>/batch-<batch_id>.zip`；
- 内部命名：`<两位序号>-<voice_name或类型>-<子任务id>.<ext>`，如 `01-builtin_vivian-a1b2c3.wav`；`voice_name` 清空时用 `tts`；文件名中非法字符替换为 `_`；
- 附带 `manifest.json`（UTF-8）：每项 `{index, task_id, text, status, output_file, error_code, error_message, duration_sec}`；
- 只打成功项 + `manifest.json`（Q5 默认）；失败项不建空文件；
- 子任务未全部结束时请求 zip → 返回当前已完成部分的 zip，并在响应头带 `X-Batch-Pending: <n>`；
- zip 用标准库 `zipfile`，不新增依赖。

#### 5.2.6 失败与取消

- **失败策略**：默认 `stop_on_error=false`——某项失败不影响后续项继续执行；`stop_on_error=true` 时，第一项失败后把剩余 `queued` 子任务置 `canceled`（`error_code="BATCH_ABORTED"`）。
- **取消**：`POST /api/tasks/{batch_id}/cancel` 对父任务生效 → 级联取消所有 `queued/running` 子任务（复用 `WorkerPool.cancel`，逐个调用），已完成子任务保留；父任务 `status=canceled`。
- **单子任务取消**：仍走 `POST /api/tasks/{child_id}/cancel`，父任务进度自动重算。
- **删除**：删除父任务不删子任务（避免误删），界面提示「批次已删除，N 个子任务保留在任务中心」；`POST /api/tasks/batch-delete` 可用于一起清理。

#### 5.2.7 上限

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `limits.max_batch_items` | `50` | 单批最多项数 |
| `limits.max_batch_total_chars` | `50000` | 单批总字符（≈ 50 × 1000 字） |
| `limits.max_batch_seconds_estimate` | `0`（关闭） | 预留：按 (字符数 × 估算 RTF) 预判，>0 时超时直接拒绝。P1 默认关闭 |

---

### 5.3 补全生成参数

#### 5.3.1 取值与默认

| 字段 | 类型 | 默认 | 范围 | 语义 |
| --- | --- | --- | --- | --- |
| `num_step` | `int \| null` | `null`（= 引擎默认） | 4–64 | 迭代解掩码 / 扩散步数；**不是** token 上限（那是 `steps`） |
| `guidance_scale` | `float \| null` | `null`（= 引擎默认） | 0.5–5.0 | 引导强度，越大越贴参考音色/越稳，过大失真 |
| `denoise` | `bool \| null` | `null`（= 引擎默认） | true/false | 生成前对参考音频降噪；开启会把部分引擎输出限到 16kHz（`voxcpm.py:7` 注释） |

**留空 = 用引擎默认值**，这是「不破坏现有行为」的核心：三个字段默认 null，且**不参与** `cfg.defaults` 的序列化默认值（避免 None 经 `_merged_params` 写进 params，见 §11 R-4）。

#### 5.3.2 作用域优先级

1. 任务级 `params.num_step`（工作台高级面板 / API 直传）；
2. 设置页「默认参数」分区（`cfg.defaults.num_step`，仅当用户显式填过）；
3. 引擎默认（不传该键）。

逐层用「键存在且非 null」判定，不用 0 值判定（`denoise=false` 是有效覆盖）。

#### 5.3.3 引擎支持矩阵（首批）

| 引擎 | num_step | guidance_scale | denoise | 落点 |
| --- | --- | --- | --- | --- |
| `qwen3-tts-1.7b`（默认） | ✗ | ✗ | ✗ | 自回归采样，无对应概念；面板置灰 |
| `voxcpm1.5` | ✓ | ✓ | ✓ | `inference_timesteps` ← `num_step`；`cfg_value` ← `guidance_scale`（现硬编码 2.0）；`denoise` ← 新参数（现硬编码 False） |
| 其它轻量引擎 | ✗（P1） | ✗（P1） | ✗（P1） | 后续按需在适配器 `_generate_kwargs` 里接 |

#### 5.3.4 门控方式

- 引擎适配器新增只读属性 `param_tags: list[str]`（默认 `[]`），取值 `"num_step" / "guidance_scale" / "denoise"`；`voxcpm.py` 声明全部三个。
- `registry.capabilities()` 不变（只表示 tts/clone/design），新增 `registry.param_capabilities(engine_id)` 返回 `param_tags`。
- `/api/meta` 响应新增 `engine.active`、`engine.param_tags`，前端据此置灰（不硬编码引擎 id）。
- 后端兜底：目标引擎的 `param_tags` 不含该参数时，**忽略并在任务日志写一条**「引擎 X 不支持 num_step，已忽略」，不报错（避免老客户端带参直接 400）。
- 新增错误码仅在「显式强制」场景使用：请求带 `strict_params: true` 时不支持则 400 `ENGINE_PARAM_UNSUPPORTED`。

#### 5.3.5 云端是否透传

**透传，无需改云端契约。**
- `TTSParams.to_dict()` 增加三键 → `cloud.py:297` 的 `payload["params"]` 自动带上；
- 远端 `api/remote.py:89` `TTSParams.from_dict()` 白名单过滤：新字段在老服务端被静默丢弃（结果仍成功，只是不生效）；老字段在新服务端被新引擎使用；
- 服务端 `/v1/engine/synth` 响应头不新增字段，采样率/模型名等契约不变；
- 若云端返回 `X-Param-Ignored: num_step`（P1 不要求实现，仅登记扩展位），前端可在结果卡片提示「云端已忽略 num_step」。

---

## 6. 接口规格

### 6.1 变更/新增一览

| 方法 | 路径 | 类型 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/tasks` | 变更 | 新增可选字段 `diarize`；`params` 可含 `num_step / guidance_scale / denoise` |
| `POST` | `/api/transcribe` | 变更 | 新增表单字段 `diarize: bool = false` |
| `GET` | `/api/tasks/{id}/segments` | 新增 | 取分段（含 speaker） |
| `GET` | `/api/tasks/{id}/subtitles` | 变更 | 新增 `speaker_prefix=0|1`（默认 0，行为不变） |
| `POST` | `/api/batch` | 新增 | 批量提交 |
| `GET` | `/api/batch/{batch_id}` | 新增 | 批次详情 + 计数 + 子任务列表 |
| `GET` | `/api/batch/{batch_id}/zip` | 新增 | 打包下载 |
| `POST` | `/api/tasks/{batch_id}/cancel` | 变更 | 父任务取消 → 级联 |
| `GET` | `/api/tasks` | 变更 | 新增可选 `batch_id` 过滤 |
| `GET` | `/api/meta` | 变更 | 新增 `engine.param_tags` |
| `GET/PUT` | `/api/settings` | 变更 | 新增 `asr.diarize`、`defaults.num_step` 等（需登记 `RUNTIME_MUTABLE`） |
| `GET` | `/api/engines?kind=asr` | 变更 | 多一个 `asr-speaker-campplus` 条目 |

### 6.2 示例

**创建带说话人区分的转写任务**

```json
POST /api/tasks
{
  "type": "asr",
  "asset_id": "ast_9f2c1e",
  "language": "chinese",
  "diarize": true
}
```

响应（与现有任务对象同构，只多 `params.diarize`）：

```json
{
  "id": "t_8c31ab", "type": "asr", "status": "queued",
  "params": {"diarize": true, "language": "chinese"},
  "progress": 0.0, "created_at": 1792000000.12
}
```

**取分段**

```json
GET /api/tasks/t_8c31ab/segments
{
  "task_id": "t_8c31ab",
  "diarized": true,
  "speakers": [{"id": "SPK1", "seconds": 22.4}, {"id": "SPK2", "seconds": 18.9}],
  "segments": [
    {"index": 0, "start": 0.0, "end": 3.4, "text": "你好，今天聊点别的。", "speaker": "SPK1"},
    {"index": 1, "start": 3.6, "end": 7.1, "text": "行，你说。", "speaker": "SPK2"}
  ]
}
```

老任务或无分段 → 404 `{"detail": {"code": "SEGMENTS_NOT_FOUND", "message": "该任务没有分段信息，请重新转写"}}`。

**批量提交**

```json
POST /api/batch
{
  "type": "tts",
  "defaults": {"voice_id": "builtin_vivian", "language": "chinese", "output_format": "wav"},
  "speed": [0.9, 1.0, 0.9],
  "items": [
    {"text": "第一条口播文案。"},
    {"text": "第二条口播文案。"},
    {"text": "第三条口播文案。"}
  ],
  "stop_on_error": false
}
```

响应 201：

```json
{
  "batch_id": "b_5f7d21",
  "parent_task_id": "b_5f7d21",
  "total": 3,
  "children": ["t_c1a001", "t_c1a002", "t_c1a003"],
  "counts": {"total": 3, "queued": 3, "running": 0, "completed": 0, "failed": 0, "canceled": 0}
}
```

**批次详情**

```json
GET /api/batch/b_5f7d21
{
  "batch_id": "b_5f7d21", "status": "running", "progress": 0.67,
  "counts": {"total": 3, "queued": 0, "running": 1, "completed": 2, "failed": 0, "canceled": 0},
  "zip_ready": false,
  "items": [
    {"index": 0, "task_id": "t_c1a001", "status": "completed", "output_file": "t_c1a001.wav", "duration_sec": 4.12},
    {"index": 1, "task_id": "t_c1a002", "status": "completed", "output_file": "t_c1a002.wav", "duration_sec": 3.88},
    {"index": 2, "task_id": "t_c1a003", "status": "running", "progress": 0.4}
  ]
}
```

**设置页默认参数（新增键）**

```json
PUT /api/settings
{
  "defaults": {"num_step": 16, "guidance_scale": 2.0, "denoise": false},
  "asr": {"diarize": {"enabled": true, "max_speakers": 2, "threshold": 0.5}}
}
```

---

## 7. 数据规格

### 7.1 任务字段与关联

- **不新增数据库列**（P1 目标：避免触发无迁移机制的 ALTER）。
  - 批次身份放在 `tasks.params` JSON 内：`batch_id`、`batch_role`（`parent`/`child`）、`batch_index`、`batch_total`、`stop_on_error`；
  - 查询子任务用 `params` 文本匹配（SQLite `LIKE '%"batch_id":"<id>"%'`）——因为 `create_task` 写库用 `json.dumps(..., ensure_ascii=False)` **不带空格**，可精确匹配；封装在 `db.list_batch_children(batch_id)` 里，避免散落。
  - 若后续决定加列，必须在 `db.py` 初始化处补「自愈建列」（`PRAGMA table_info(tasks)` 缺列则 `ALTER TABLE tasks ADD COLUMN …`），因为 `db.py:177` 只有 `create_all`，**不会给已存在的表加列**。
- `tasks.transcript` 继续存纯文本；**分段不落 DB**，落盘文件（下节），任务表结构零变更。

### 7.2 落盘位置

| 文件 | 路径 | 内容 |
| --- | --- | --- |
| 转写文本 | `data/outputs/<task_id>/<task_id>.txt` | 与现在完全一致（不因 diarize 改变） |
| 分段 | `data/outputs/<task_id>/segments.json` | 见下 |
| 字幕时间轴 | `data/outputs/<task_id>/chunks.json` | 现状不变（TTS 用）；asr 任务 P1 不写 |
| 批次包 | `data/outputs/<batch_id>/batch-<batch_id>.zip` | 见 §5.2.5 |
| 声纹模型 | `models/diarize/campplus/campplus.onnx` | 程序自动下载，用户无需手工 |
| 安装状态 | `data/engine-install-state.json` | 沿用 ASR-ENGINE-1 机制 |

### 7.3 segments.json 结构

```json
{
  "task_id": "t_8c31ab",
  "diarized": true,
  "engine": "asr-speaker-campplus",
  "threshold": 0.55,
  "max_speakers": 4,
  "degraded": false,
  "created_at": 1792000123.4,
  "segments": [
    {"index": 0, "start": 0.0, "end": 3.4, "text": "你好，今天聊点别的。", "speaker": "SPK1", "speaker_index": 0}
  ]
}
```

- `start/end` 单位秒，保留 3 位小数；
- `speaker_index` 为聚类原始编号（0 起），`speaker` 为对外标签；两者都存，便于「改名」扩展时保持稳定；
- 未开启 diarize 时该文件**不生成**（老行为零变化）。

### 7.4 zip 命名

`batch-<batch_id>.zip`，内部条目 `<NN>-<voice_name>-<task_id>.<ext>` + `manifest.json`；同一批次内序号补足两位（`01`…`50`），保证解压后自然排序。

---

## 8. 非功能需求

| 项 | 要求 | 验证方式 |
| --- | --- | --- |
| 转写性能 | 纯 CPU（int8 small）1 分钟音频：转写 ≤ 20s，声纹抽取 + 聚类 ≤ 6s，**端到端 < 30s** | `elapsed_ms` + `diarize.elapsed_ms` 实测 |
| 声纹内存 | 60 分钟音频的 embedding 总量 < 60MB（192 维 × ≤1200 段 × 4B） | 采样实测 |
| 批量吞吐 | 50 项、每项 ≤ 200 字：批次总时长 ≈ 单项均值 × 50（串行）；不引入额外空转（`submit` 连续入队 < 200ms） | 观察 worker 日志时间戳 |
| 批量并发 | 沿用 `workers=1`（`state.py:108`）；不新增并发 worker，避免 4GB 卡显存争抢 | 代码评审 |
| 上限 | 单批 50 项 / 50000 字符；单任务 5000 字符；转写 1800s | 接口 400 用例 |
| 兼容性 1 | `asr.diarize.enabled=false` 时 `segments[].speaker=null`，接口多返回空键，前端旧代码忽略即无感 | AC8 |
| 兼容性 2 | 老 `config.yaml` / `runtime.json` 缺新键 → 走 pydantic 默认值，无迁移 | AC13 |
| 兼容性 3 | 老任务无 segments.json → `GET /segments` 404 `SEGMENTS_NOT_FOUND`；`subtitles` 行为不变 | AC9 |
| 兼容性 4 | 三个新参数默认 null → 现有生成结果（波形、时长）**逐字节不变**（同 seed 同模型） | AC16 |
| 空转成本 | 关闭 diarize 时不 import `sherpa_onnx` / `sklearn`（延迟导入在 `diarize.py` 内） | 启动日志无相关 import |
| 下载体积 | CAM++ 约 28MB，程序自动下载、用户无需手工；进度事件沿用 `engine` topic | AC6 |
| 可观测 | 日志至少 3 条：是否启用 diarize、聚类出几个 speaker、耗时 | 任务日志面板 |

---

## 9. 界面规格

### 9.1 工作台「高级参数」（`ParamsPanel.tsx`）

在现有 7 个滑杆之后、「流式合成」之前，新增「生成参数」分组（折叠内二级分组，默认收起）：

| 控件 | 形态 | 取值 | 置灰规则 |
| --- | --- | --- | --- |
| `num_step` 迭代步数 | range + 「留空」复选框 | 4–64，步长 1 | 当前引擎 `param_tags` 不含 `num_step` → 置灰 + 提示「当前引擎不支持，使用引擎默认」 |
| `guidance_scale` 引导强度 | range | 0.5–5.0，步长 0.1 | 同上 |
| `denoise` 参考音频降噪 | 三态（默认/开/关） | null/true/false | 同上；开启时补一行小字「开启后部分引擎输出限为 16kHz」 |

- 三态控件必须支持「默认（不传）」，不能用「关 = false」代替，否则破坏 A5 的「留空=引擎默认」；
- 置灰信息来自 `/api/meta` 的 `engine.param_tags`，不硬编码引擎 id；
- 与现有控件同款样式类：`field / label / label__hint / range-row / num`，不引入新组件库。

### 9.2 工作台「音频转文字」

- 上传区下方新增开关「区分说话人」（默认关，与 `asr.diarize.enabled` 同步初值）；
- 开关打开但 CAM++ 未安装 → 开关旁黄色提示 + 「去引擎管理」按钮；
- 转写结果区：开启时按 speaker 分色渲染，每段左侧色条 + `SPK1` 徽标；关闭时保持现在的纯文本 `<p style="whiteSpace: pre-wrap">`（`Workbench.tsx:665`）；
- 分色方案沿用现有主题变量（不新增色板）：SPK1 `--accent`、SPK2 `--success`、SPK3 `--warning`、SPK4+ `--muted`；同一任务内颜色稳定（按 `speaker_index` 取模）。

### 9.3 批量提交入口

- 工作台文本区右下角新增「批量提交」次按钮（与「开始生成」同行、次级样式）；
- 点击打开右侧抽屉（宽 520px）：
  - 文本区：粘贴多行，一行一条（空行忽略），右上角显示「已识别 12 条」；
  - 参数区：音色、语言、输出格式（批次级）；语速等「可为逐项」的参数给「统一」/「逐项」切换——选「逐项」时渲染可编辑表格（行 = 文本，列 = speed/num_step/guidance_scale/denoise）；
  - 底部：项数上限提示「单批最多 50 项」+ 「提交批次」；
- 提交成功后 toast「批次已创建 #b_5f7d21」，并跳转「任务中心 → 批次」视图。

### 9.4 任务中心「批次」视图

- 顶部筛选页签追加「批次」（不影响现有状态筛选）；
- 批次卡片：标题「批量 · 50 项」、进度条 + 「32/50」、计数徽标（成功/失败/取消）、「下载打包」「取消剩余」按钮；
- 展开后为子任务列表（复用 `TasksPage.tsx` 的行与选中逻辑，`:98 selected` 直接复用），支持只对失败项重试；
- 批量工具条（`:474-498`）保持不变，只多「选中项打包」一项。

### 9.5 设置页

- `asr` 分区新增「说话人区分」小节：开关、最大人数（1–10）、聚类阈值（0.30–0.90，带「越小越容易拆人」提示）、「CAM++ 状态：已装/未装 + 去引擎管理」；
- `defaults` 分区按现有渲染方式（`SettingsPage.tsx:1441` 的 key 循环）新增三行，且这些行**允许留空**（空 = 不写默认值）。

### 9.6 窄屏（≤ 768px）

- 参数面板单列，滑杆占满宽度，数值右对齐；
- 批量抽屉改为全屏；逐项表改卡片流（每条一张卡，字段竖排），避免横向滚动；
- 说话人分段列表每段独立成块，色条在左、编号与时间在上行，文本换行不截断；
- 批次卡片按钮收成图标 + 文字两行，进度条保持可见。

---

## 10. 验收标准

### 说话人区分

- **AC1**｜步骤：设置 → 引擎与模型 → 语音识别，找到「CAM++ 声纹（说话人区分）」，点「安装」→ 观察进度 → 完成后看 `models/diarize/campplus/`。预期：卡片显示「约 28MB」，进度条跑到 100%，目录内出现 `campplus.onnx`（27–30MB）。**全程无需手工下载**。
- **AC2**｜步骤：上传一段 2 人对话（60s，16kHz 以上），打开「区分说话人」，提交转写。预期：任务完成，结果区按 SPK1/SPK2 分色；`GET /api/tasks/{id}/segments` 返回 `diarized=true`、`speakers` 恰好 2 个。
- **AC3**｜步骤：对同一段音频，人工标 10 个说话人切换点，与返回的 `segments[].speaker` 切换点比对。预期：10 个切换点的绝对误差均 **< 1s**，且 speaker 编号前后一致（不出现同一人在两段之间被拆成 3 个标签）。
- **AC4**｜步骤：纯 CPU（`asr.local.device=cpu`、`compute_type=int8`、small 模型）跑 60s 音频。预期：任务 `elapsed_ms` **< 30000**，其中 `diarize.elapsed_ms` < 6000。
- **AC5**｜步骤：4 人会议录音（120s），`max_speakers=4` 跑一次；再改 `max_speakers=2` 跑一次。预期：前者 `speakers` 长度 ≤ 4，后者恰好 = 2（强制合并生效）。
- **AC6**｜步骤：打开开关但先删除 CAM++ 模型，再提交转写。预期：任务 `failed`，`error_code="ASR_DIARIZE_MODEL_MISSING"`，文案含「设置 → 引擎与模型 → 语音识别」。
- **AC7**｜步骤：把 `sherpa-onnx` 卸载后打开开关转写。预期：`DIARIZE_DEPS_MISSING`，其余功能不受影响；关闭开关后转写正常。
- **AC8**｜步骤：关闭`区分说话人`，转写同一段音频。预期：`text` 与 AC2 的纯文本部分**逐字符一致**，`segments[].speaker` 全为 `null`，`speakers=[]`，`diarize.enabled=false`。
- **AC9**｜步骤：对 2026-10-21 之前创建的老 asr 任务调用 `GET /api/tasks/{id}/segments` 与 `/subtitles?format=srt`。预期：前者 404 `SEGMENTS_NOT_FOUND`；后者行为与此前一致（有 chunks.json 则成功，无则 `SUBTITLE_NOT_FOUND`）。
- **AC10**｜步骤：导出一个已 diarize 任务的字幕，带 `speaker_prefix=1`。预期：每行字幕文本前带 `[SPK1] ` 前缀；不带该参数时输出与现在逐字节一致。

### 批量队列

- **AC11**｜步骤：提交 3 项批次，`speed=[0.9, 1.0, 0.9]`。预期：201 返回 `batch_id` + 3 个子任务 id；任务中心出现批次卡「0/3」；3 个子任务完成后 `GET /api/batch/{id}` 的 `counts.completed=3`，`progress=1.0`。
- **AC12**｜步骤：提交 `speed=[0.9, 1.0]` 但 3 项。预期：400 `BATCH_PARAM_LENGTH_MISMATCH`，文案点名 `speed` 与两个数字（2 / 3）。
- **AC13**｜步骤：提交 51 项。预期：400 `BATCH_ITEMS_TOO_MANY`，文案「单批最多 50 项，请拆成多批」；50 项可正常提交。
- **AC14**｜步骤：批量中让第 2 项失败（填超长文本>5000 字，或使用不存在音色）。预期：默认 `stop_on_error=false` 下第 3 项继续完成；父任务最后 `status=failed`，`error_message` 含「1/3 项失败」；zip 内只有 2 个音频 + `manifest.json`（manifest 里第 2 项 `status=failed` 且带错误码）。
- **AC15**｜步骤：批次跑到「2/5」时点「取消剩余」。预期：父任务 `canceled`，未开始/进行的 3 个子任务 `canceled`，已完成 2 个保留且文件仍可下载；`counts {completed:2, canceled:3}`。
- **AC16**｜步骤：从批次卡点「下载打包」。预期：得到 `batch-<id>.zip`，内部文件名形如 `01-builtin_vivian-<task_id>.wav`，序号两位补零、按序号自然排序，含 `manifest.json`（UTF-8，中文不乱码）。
- **AC17**｜步骤：对父任务调用 `POST /api/tasks/{batch_id}/retry`。预期：400 `BATCH_NOT_RETRYABLE`，提示「请重试失败项」；对子任务调用 `retry` 行为与现在一致。

### 补全生成参数

- **AC18**｜步骤：默认引擎（qwen3-tts）打开高级参数。预期：三项均置灰，提示「当前引擎不支持，使用引擎默认」；提交任务后 `params` 中不含这三键（或为 null），日志无报错。
- **AC19**｜步骤：切到 VoxCPM1.5，设 `num_step=16 / guidance_scale=2.5 / denoise=true` 提交。预期：任务成功；`params` 中三键留存；日志/健康信息显示实际传入值（`inference_timesteps=16`、`cfg_value=2.5`、`denoise=true`）。
- **AC20**｜步骤：设置页「默认参数」把 `num_step` 填 8；工作台不填提交；再单任务填 16 提交。预期：前者任务 `params.num_step=8`，后者 16（任务级覆盖默认值）。
- **AC21**｜步骤：不设任何新参数，用同一音色/同一文本生成两次（`temperature=0` 不可用，故对比时长与首包区间）。预期：时长差 ≤ 2%、无异常；与 PHASE-P1 实施前的产物对比，时长偏差 ≤ 2%（证明确实走引擎默认）。
- **AC22**｜步骤：直连`POST /v1/engine/synth` 带 `params.num_step=16`（VoxCPM 引擎）。预期：200，返回 wav；响应头 `X-Sample-Rate` 存在；不报未知参数错误。
- **AC23**｜步骤：用旧版远端服务（不识别新键）跑云端合成，请求带三键。预期：合成成功（新键被 `from_dict` 白名单丢弃），任务不失败，日志无 traceback。

### 回归

- **AC24**｜步骤：跑既有自检 `scripts/audit_all.py`（20 项）。预期：全绿；引擎矩阵、页面冒烟不受影响。
- **AC25**｜步骤：清空 localStorage、用默认配置启动，走一遍 tts/clone/design/asr 四条老链路。预期：与 PHASE-P1 实施前行为一致（除新增的可选开关/控件外无差异）。

---

## 11. 风险与依赖

| 编号 | 风险 | 影响 | 应对 |
| --- | --- | --- | --- |
| R-1 | 许可证 | 高（产品红线） | 全链路 MIT/Apache-2.0/BSD-3：faster-whisper(MIT) + sherpa-onnx(Apache-2.0) + CAM++(Apache-2.0) + sklearn(BSD-3)。**明确不用** pyannote/WhisperX（gated 权重）。实施时在 `docs/spec` 附录登记每个组件许可证与来源 URL |
| R-2 | 新增依赖体积 | 中 | `sherpa-onnx` wheel 约 20–30MB（自带 onnxruntime），`scikit-learn` + `scipy` 约 40MB；写进 pins 的「说话人区分（可选）」分组，未装不影响启动 |
| R-3 | 模型体积与下载 | 中 | CAM++ 约 28MB；沿用 `engine` topic 进度事件；下载失败给可读错误 + 重试；**程序自动下载，用户无需手工** |
| R-4 | `defaults` 序列化污染 | 高（会直接 500） | `_merged_params` 用 `cfg.defaults.model_dump()` 全量合并，若把 `num_step: null` 写进 `DefaultsCfg` 默认值，params 里就会出现该键；而 `TTSParams.from_dict` 会把它原样塞进 dataclass，后续 `int(None)` 崩。**约束：三字段在 `DefaultsCfg` 里用「不写入 model_dump」的方式表达（例如单独 `advanced: dict[str, Any] = {}` 容器，或 `model_dump(exclude_none=True)`）**，实施时必须单测覆盖 |
| R-5 | 父任务被 worker 误执行 | 高 | 父任务不 `submit`，但 `worker._restore()` 会把 `queued` 重新入队。**必须**在 `_execute` 与 `_restore` 里按 `params.batch_role=="parent"` 早退，否则父任务会当成普通 TTS 跑（文本为空 → `TEXT_EMPTY` 失败，污染批次状态） |
| R-6 | 聚类调参 | 中 | `threshold` 0.55 在 2–4 人中文对话上表现尚可，但 5 人以上易过度合并；已在界面暴露阈值 + `max_speakers` 双旋钮，并在文档写清「越小越容易拆人」 |
| R-7 | 批量串行对 GPU 的压力 | 中 | 50 项串行 = 单项耗时 × 50；期间 `keep_loaded=true` 不反复卸载；`allow_parallel=false` 时不会显存翻倍。若用户抱怨慢，P2 再评估「批量并行度」 |
| R-8 | 云端透传兼容 | 低 | 新字段在老服务端被白名单丢弃（AC23）；反向场景（老客户端连新服务端）字段缺失走引擎默认。契约不变，无需版本协商 |
| R-9 | 无数据库迁移机制 | 中 | 本规格**刻意不加列**，批次信息放 `params` JSON；若后续确需加列，必须先补 `PRAGMA table_info` + `ALTER TABLE` 的自愈建列逻辑（现全仓无此能力） |
| R-10 | `asr.diarize` 未登记 `RUNTIME_MUTABLE` | 中 | 设置页保存会 400。实施第一步就把它加进 `config.py:21` 白名单，并加一条「新配置节必须登记」的自检 |
| R-11 | `steps` 与 `num_step` 混淆 | 中 | VoxCPM 目前把 `steps` 当扩散步数（`voxcpm.py:177`），新逻辑必须「`num_step` 优先、为空才回落 `steps` 的 2–30 规则」，并保留旧行为可用（AC19 + 回归用例） |

依赖顺序：D1 `TTSParams` 扩展（无外部依赖）→ D2 `asr_speaker` 适配器 + 引擎管理可见（依赖 ASR-ENGINE-1，已实施）→ D3 批次落库/聚合（依赖 D1 的逐项参数）→ D4 前端参数门控（依赖 `/api/meta` 扩展）。

---

## 12. 排期建议

| 顺序 | 功能 | 估时 | 理由 |
| --- | --- | --- | --- |
| ① | **5.3 补全生成参数** | 2 天 | 改动最小、无 DB 变更、云端自动透传；是 5.2「逐项列表」参数的载体（没有这三个字段，批量就少一半价值）；也是唯一能纯本地闭环验收的部分 |
| ② | **5.2 批量队列** | 4 天 | 复用现有任务表 + worker + `create_task`，风险集中在「父任务语义」与 zip 打包；依赖 ① 的字段定义，先做 ① 可避免二次改 `TTSParams` |
| ③ | **5.1 说话人区分** | 5 天 | 唯一引入新依赖与新模型下载的功能，风险最高（许可证文档、下载链路、聚类调参）；且与其它两项无耦合，可最后做、可独立回退 |

并行建议：③ 的「引擎适配器 + 模型下载」可先与 ①② 并行开发（互不触碰同一文件）；「聚类调参」必须拿到真实多人音频后再做，安排在 ③ 后半段。

前置动作（各 0.5 天）：把 `asr.diarize` 与 `defaults` 新键登记进 `RUNTIME_MUTABLE`；把 ①②③ 的新错误码补进 `errors.py` 的码表。
