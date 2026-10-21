# 规格：反直觉行为修复（BEHAVIOR-1）

- 日期：2026-10-21
- 来源：用户逐条点名的「行为怪点（不是 bug，但反直觉）」10 项
- 处理原则：**先定性，再决定改不改**。逐条给出「实测现状 → 判定 → 方案 → 验收」，其中 **B1 经复核与描述不符**，按实际行为改写。

---

## 1. 逐条核对

### B1 ASR 引擎选择的优先级 —— 「被忽略」不成立，但**反向怪**

- **描述**：`asr.engine=cloud` + `run.mode=local` → 强制走本地 whisper，显式选择被忽略。
- **实测**：`engine/asr.py::ASRRouter.plan()` 先判 `asr.engine`（cloud/local），只有留空才回落到 `run.asr_prefer`；
  `transcribe()` 里 `run.mode=local` 只过滤「云端回退」，而 `order[0]` 仍是 `plan()` 的结果 → **cloud 选择被尊重**。
- **真实的怪点（方向相反）**：`asr.engine=cloud` + `run.mode=local` 时 `order` 最终变成 `["cloud"]`，
  即**云端转写失败也不会回退本地 whisper**，而本地 whisper 明明可用；用户会看到「只云端 / 只本地」以外的第三种组合语义。
- **判定**：文档语义缺陷（不是 bug）。三方（`asr.engine` / `run.mode` / `run.asr_prefer`）都有话说，没有一个明确的优先级说明。
- **方案**：
  1. 明确优先级并写进 `CONFIG.md`：`asr.engine`（显式）> `run.asr_prefer`（hybrid 时）；`run.mode=local` 只影响**回退候选**，不影响首选；
  2. 首选为 cloud 且 `run.mode=local` 时，**允许回退本地**（本地 whisper 已装即有效），并在任务日志写明「云端失败，已回退本地」；
  3. 状态页 `/api/system/status` 的 `asr_engine` 增加 `asr_effective_candidates`（实际尝试顺序），让界面能显示真相。
- **验收**：`asr.engine=cloud` + `run.mode=local` + 云端不可达 + 本地 whisper 已装 → 任务 `completed`、日志含「回退本地」；`GET /api/system/status` 能看到候选顺序。

### B2 hybrid + 全局开「区分说话人」→ 一律本地优先

- **实测**：`asr.py:307-309` —— `want` 为真时 `order = ["local"] + …`，硬编码本地优先。
- **判定**：**合理的实现**（云端没有声纹链路），但属隐式策略：用户选 hybrid 后仍被强制本地，且没有开关。
- **方案**：保留本地优先，但（a）任务日志首行写明「已请求说话人区分 → 优先本地 CAM++」；（b）新增 `asr.diarize.prefer`（`local`/`cloud-first`，默认 `local`），
  选 `cloud-first` 时先云端出纯文本、再本地补声纹（成本高，默认关）。
- **验收**：`prefer=local` 行为与现在一致且在日志可见；`prefer=cloud-first` 时先云端再本地补 SPK，失败给出可读原因。

### B3 云端转写不支持说话人区分，只在任务日志提示一句

- **实测**：`asr.py:319-321` 只在结果里塞 `diarize_skipped` 字符串；前端只在任务日志显示。
- **判定**：**静默失效风险**（用户勾了开关、拿到了没有 SPK 的结果）。
- **方案**：改为**提交前拦截**——工作台勾了「区分说话人」且当前引擎链只能云端时，提交按钮旁直接提示并提供两个选项：
  「改用本地（约慢 N 倍）」「关闭说话人区分继续」；后端仍保留 `diarize_skipped` 作为兜底。
- **验收**：云端-only 环境下勾开关 → 界面出现前置提示（不等到任务结束）；强制提交时任务详情显示显式警示徽章。

### B4 MCP 只有 stdio；spec 里的「可选 SSE」没做，也没有 resources/prompts

- **实测**：`mcp_server.py` 只有 stdio，`capabilities` 只声明 `{"tools": {}}`，4 个工具（`tts_synthesize`/`asr_transcribe`/`task_status`/`voice_list`）。
- **判定**：**文档承诺 > 实现**（`PHASE-3-BACKLOG.md §7` 写了「stdio + 可选 SSE」）。
- **方案**：本次**不做 SSE**，改为把边界写清：`docs/spec/PHASE-3-BACKLOG.md §7` 与 README 标注「仅 stdio；SSE/resources/prompts 未实现，属后续项」。
- **验收**：全仓不再出现「可选 SSE」这种未兑现表述；`docs/API.md` 有一节 MCP 能力边界表。

### B5 批次只能整批共用参数，前端不能逐条指定不同音色

- **实测**：后端 `batches.new_batch()` **已支持逐项** `voice_id/asset_id/instruct/design/language/params`（批次重试也保留）；
  前端 `BatchPanel.tsx` 构建 item 时只有 `{ text }` → **限制在前端，不在后端**。
- **判定**：UI 缺口（后端能力被埋没）。
- **方案**：批量面板改为「逐行可展开」：默认整批共用音色/参数，每行可点开覆盖音色、语速、语言；提交时按行生成 item。
- **验收**：同一批次内两行分别用 `builtin-vivian` 与 `builtin-…` 不同音色 → 两个任务 `params.voice_id` 不同且都 `completed`。

### B6 导出档位不能在生成后重新导出，只能在生成时选一次

- **实测**：`worker._finalize` 按 `export_tier` 编码一次；无「重新导出」接口（路由清单里没有 `/export`）。
- **判定**：功能缺口。
- **方案**：新增 `POST /api/tasks/{id}/export`（body：`tier`），从原始生成音频（保留 `source.wav` 不删）按档位重新编码到 `outputs/<id>/`，
  更新 `params._export` 与 `media-info`；任务详情加「重新导出」下拉。
- **验收**：同一任务依次导出 original/standard/editing → `media-info` 分别报 24k WAV / 44.1k MP3 / 48k 24bit WAV，且原音频未被覆盖。

### B7 说话人改名只改 chunks.json（分段+字幕），transcript 正文不变；不能合并说话人

- **实测**：`PATCH /tasks/{id}/speakers` 只写 `chunks.json` 的 `speaker_aliases` 与 `chunk.speaker`；`transcript` 字段不动；无合并能力。
- **判定**：**设计取舍**（原名可还原），但「正文不变」会让用户以为没生效；无合并导致同一人被切成 3 个 SPK 时无法修正。
- **方案**：（a）`transcript` 侧新增只读派生视图（不改库）：按别名渲染「[张三] …」；（b）改名接口支持合并语义 `mapping: {"SPK2": "SPK1"}`（值为另一个 SPK 时按合并处理，归一化到目标 id）。
- **验收**：改名后任务详情正文出现新名字；`{"SPK2":"SPK1"}` 后分段只剩 SPK1 且时间轴连续；清空映射可完全还原。

### B8 没有「按说话人拆分导出音频」

- **实测**：无此路由与前端入口。
- **判定**：功能缺口（会议场景刚需）。
- **方案**：新增 `POST /api/tasks/{id}/split-by-speaker` → 按 `segments` 时间轴切分原音频，产出 `outputs/<id>/speakers/<别名>.wav`，返回清单；
  任务详情提供「按说话人拆分」按钮 + 打包下载。
- **验收**：两人对话任务 → 产出 2 个 wav，时长之和 ≈ 原音频（允许 <100ms 的接缝误差），文件名用别名。

### B9 声纹只有「最多说话人」，没有「最少说话人」

- **实测**：`diarize.py`/`config.py` 中 `min_speakers` 不存在（0 处引用），只有 `max_speakers`（2-8）。
- **判定**：功能缺口（单人被拆成多人时只能靠调小 threshold 试）。
- **方案**：新增 `asr.diarize.min_speakers`（默认 1，范围 1-8，且 ≤ `max_speakers`）；聚类后用「合并最相似簇」循环补足到 `min_speakers` 的下界语义（当前 `_merge_to_limit` 只管上限，扩展为双向夹取）；任务级 `params.min_speakers` 覆盖。
- **验收**：同一素材 `min=3` 时输出 3 个说话人；`min>max` 时返回 `INVALID_INPUT`；不传时行为与现在完全一致。

### B10 文档里没有 MCP 客户端配置示例

- **实测**：`docs/` 只有参数说明，无任何客户端配置片段（`mcpServers` JSON）。
- **判定**：文档缺口。
- **方案**：新增 `docs/MCP.md`：`mcpServers` 配置示例（Claude Desktop / Cline 两种）、4 个工具的参数与返回、`QWENTTS_MCP_*` 环境变量、错误码约定、常见问题（工作目录、`cd backend`、UTF-8）。
- **验收**：照文档复制 JSON 即可连上，`tools/list` 返回 4 个工具。

## 2. 优先级建议

| 优先级 | 条目 | 理由 |
| --- | --- | --- |
| P0 | B3、B5、B10 | 静默失效 / 后端能力被前端埋没 / 十分钟能补齐的文档缺口 |
| P1 | B1、B6、B7 | 语义澄清 + 两个高频工作流（重导出、合并同一人） |
| P2 | B2、B8、B9、B4 | 需要新链路或新算法；B4 本次只做文档边界 |

## 3. 决策记录（2026-10-21 已拍板）

| # | 事项 | 决策 |
| --- | --- | --- |
| 1 | B2 `diarize.prefer` | **做，只留开关**：`local`（默认）/ `cloud-first`；`cloud-first` 会让同一段音频跑两遍，界面需标注耗时约翻倍 |
| 2 | B6 重导出原件 | **保留**：生成后额外落 `source.wav`（无损、不覆盖），供事后按任意档位重导出；磁盘约 +1 倍，接受 |
| 3 | B7 说话人合并 | **新开接口** `POST /api/tasks/{id}/speakers/merge`（body：`{"from": ["SPK2"], "to": "SPK1"}`），不改名接口语义 |
| 4 | B8 重叠语音 | **不管**：不做重叠检测/归属策略，按片段自身时间轴直接切分；重叠部分自然被两个文件各含一次，文档写明该限制 |
| 5 | 重叠归属替代方案 | 不做 |
| 6 | 交付节奏 | 按 P0 → P1 → P2 分批，每批以 `pytest` + 对应验收项收口 |

## 4. 落地结果（2026-10-21，P0+P1+P2 全部完成）

| 条目 | 实现位置 | 验收要点 |
| --- | --- | --- |
| B1 | `engine/asr.py`（候选顺序 `_plan_order`/`candidates_for`）、`api/system.py`（`asr_candidates`） | `asr.engine=cloud` + `run.mode=local` 时云端失败回退本地并写 warning 日志 |
| B2 | `asr.diarize.prefer` 三值 + 日志首行打印候选顺序 | 与 CLOUD-DIARIZE-1 §3.3 统一实现（决策里的 `cloud-first` 并入 `cloud`/`auto`） |
| B3 | 工作台勾选旁前置提示（读 `asr_diarize` 权威块）+ 后端 `CLOUD_DIARIZE_UNSUPPORTED`/`DIARIZE_UNAVAILABLE` | 不再等任务结束才提示 |
| B4 | `docs/MCP.md` §6 能力边界表；`PHASE-3-BACKLOG.md §7` 改为「仅 stdio」 | 未兑现的「可选 SSE」表述已清除 |
| B5 | `components/BatchPanel.tsx` 逐行展开覆盖（音色/语速/语言/instruct） | 后端本就支持逐项字段，前端已暴露 |
| B6 | `POST /api/tasks/{id}/export` + `export.save_source()` | 生成时落 `source.wav`，旧任务报 `EXPORT_SOURCE_MISSING` |
| B7 | `POST /api/tasks/{id}/speakers/merge` + `transcript_by_speaker` | 合并只改 chunks.json 时间轴归属；正文按别名派生渲染，不改库 |
| B8 | `POST /api/tasks/{id}/split-by-speaker` + `GET .../speakers/{name}` + `.../speakers/zip` | 文件名安全化（Windows 设备名/重名/非法字符）；重叠按片段自身时间轴各含一次 |
| B9 | `diarize._enforce_minimum` + `resolve_speaker_bounds` | `min=1` 时零行为变化；`min>max` 报 `INVALID_INPUT` |
| B10 | `docs/MCP.md`（8 节，含 Claude Desktop / Cline 配置） | 照抄即可连上 |

### 4.1 与原方案的差异（以实现为准，已同步文档）

1. `POST /export` 响应体是 `{ok, task_id, tier, tier_label, file, media, task}`（不是规格里写的 `export:{…}`）。
2. 单轨下载最终采用**后端文件路由** `GET /api/tasks/{id}/speakers/{name}`，不再让浏览器解压 zip。
3. `speakers/merge` 的 `from` 同时接受别名 `sources`；未知说话人报 `INVALID_INPUT`，无 `chunks.json` 报 `SUBTITLE_NOT_FOUND`。
4. 任务 params 写回的是 `min_speakers_effective`（另有 `diarize_backend`），不是 `min_speakers`。
5. MCP 实际实现是**经本机 HTTPS 调自己的 REST**（`QWENTTS_MCP_BASE_URL`），不是规格里说的「进程内复用 service 层」——原 spec 表述已改。
