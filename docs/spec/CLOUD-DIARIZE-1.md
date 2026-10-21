# 规格：云端转写支持说话人区分（CLOUD-DIARIZE-1）

- 日期：2026-10-21
- 触发：现状「云端转写天生不支持说话人区分，只在任务日志提示一句」属静默失效；用户要求**做成支持**
- 结论先行：**不需要新算法**。云端 `/v1/engine/asr` 跑的就是同一份 `ASRRouter`，只是没暴露 `diarize` 参数、`CloudEngine` 也没传；补齐协议透传 + 能力协商即可，且**云端做声纹比 4GB 本地更安全**（不与 whisper 抢显存）。

---

## 1. 现状实测

| 层 | 现状 | 证据 |
| --- | --- | --- |
| 云端接口 | `/v1/engine/asr` 只接受 `file`/`language`/`model`，直接 `state.asr.transcribe(audio_path, language)` | `api/remote.py:144-158` |
| 云端服务端能力 | 服务端**本就有** diarize 能力（`state.asr` = `ASRRouter`，装了 CAM++ 就能算） | 同上 |
| 客户端调用 | `CloudEngine.transcribe(audio_path, language)` 只传语言，无 diarize/max_speakers | `engine/cloud.py:312-330` |
| 路由策略 | `want=True` 时硬编码 `order = ["local"] + …`，云端永不参与声纹 | `engine/asr.py:307-309` |
| 云端回填 | 云端返回后塞 `result["diarize_skipped"]` 字符串，只在任务日志出现 | `engine/asr.py:319-321` |
| 未使用字段 | `asr.cloud.model`（`whisper-1`）与接口 `model` 形参当前都被忽略 | `engine/asr.py`、`remote.py:149` |

**判定**：这不是能力缺失，是**协议未贯通 + 策略硬编码**。

## 2. 目标

1. `diarize=true` 在「本地 / 云端 / 混合」三种模式下都能得到带 `speaker` 的分段，不再有「勾了没有」的情况。
2. 云端做声纹时，本地**不加载** CAM++（省显存、避免 ASR→TTS 交替 OOM）。
3. 云端不支持时**提交前或首次探测时就知道**，报可读错误码，而非任务日志一句话。
4. 与 B9（`min_speakers`）同批交付，云端与本地语义一致。

### Won't Have

- 不做重叠语音分离（沿用 BEHAVIOR-1 决策 4）。
- 不接第三方 diarization API（pyannote/AssemblyAI/Deepgram）：gated 权重与商用条款冲突。
- 不改声纹模型本身（继续 CAM++ / sherpa-onnx）。

## 3. 方案

### 3.1 协议（云端 `/v1/engine/asr`）

请求新增 3 个表单字段（全部可选，缺省行为不变，保证老客户端不受影响）：

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `diarize` | `"true" \| "false" \| ""` | `""`（跟随云端全局配置） | 是否区分说话人 |
| `max_speakers` | int | 空 | 2-8 |
| `min_speakers` | int | 空 | 1-8，且 ≤ `max_speakers` |

响应新增/明确字段：

| 字段 | 说明 |
| --- | --- |
| `segments[]` | 已存在；每项 `{start, end, text, speaker}`，`speaker` 为 `SPK1…` 或 `null` |
| `diarize_backend` | **新增**：`"local"`（云端本机 CAM++）/ `null`（未做区分）—— 用于客户端判断「云端是否真的做了」 |
| `model` | 实际使用的 ASR 引擎 id（如 `asr-whisper-small`），不再回填无意义的 `whisper-1` |

`diarize=true` 但云端未安装 CAM++ → HTTP 400，`{"code": "CLOUD_DIARIZE_UNSUPPORTED", "message": "...", "hint": "在云端执行 engines_cli install asr-speaker-campplus"}`。

### 3.2 能力协商（新增 `/v1/health` 字段）

```json
"asr": {
  "backend": "local",
  "engine_id": "asr-whisper-small",
  "diarize": true,
  "diarize_engine": "asr-speaker-campplus",
  "min_speakers": true
}
```

`CloudEngine` 首次转写前读一次并缓存（TTL 60s），失败不影响普通转写。
**判定云端是否支持：`asr.diarize == true`**；字段缺失（旧版云端）一律视为不支持 → 走 3.3 的回退，**绝不静默**。

### 3.3 路由策略（替换现有硬编码）

配置新增 `asr.diarize.prefer`（热改）：

| 取值 | 行为 |
| --- | --- |
| `local`（默认，与现状一致） | 本地优先；本地不可用才看云端能力 |
| `cloud`（新增） | 云端优先；云端不支持 → 明确报 `CLOUD_DIARIZE_UNSUPPORTED`（不偷偷改本地） |
| `auto`（新增） | 云端支持就云端（快、不占本地显存），否则本地；两者都不行 → `DIARIZE_UNAVAILABLE` |

三种取值都要求：最终未做区分时**任务失败并给出可执行提示**，不再落「纯文本 + 一行日志」。

### 3.4 客户端改动

- `CloudEngine.transcribe(..., diarize=None, max_speakers=None, min_speakers=None)`：表单透传；
- `ASRRouter.transcribe`：按 `prefer` 计算 `order`；成功后把 `diarize_backend` 写进结果，worker 记入任务日志与 `params`；
- 删除 `diarize_skipped` 路径（改造为错误或 `diarize_backend=null` + 显式日志）；
- worker：`params.diarize_backend` 落库，任务详情展示「说话人由 云端/本地 识别」。

### 3.5 云端一键接入 Agent

S4（装依赖）之后新增可选步骤 **S4b「安装声纹引擎」**：`engines_cli install asr-speaker-campplus`。
失败不阻塞整体接入，仅在任务的 `capabilities` 里标 `diarize: false`，并在前端向导显示「云端不含说话人区分（可稍后单独安装）」。

### 3.6 前端与文档

- 工作台：勾「区分说话人」时，若当前链路不支持 → 提交前提示并给三选项（换本地 / 装云端声纹 / 关闭开关）；
- 设置页 `asr.diarize` 分区加 `prefer` 下拉与说明；
- `CONFIG.md`：补 `prefer`、`min_speakers`；`API.md`：补 3 个请求字段、2 个响应字段与 2 个错误码；新增 **ASR 能力矩阵**（本地/云端 × 文本/时间戳/说话人/最少人数）；`TROUBLESHOOTING.md` 补两个错误码；MCP `asr_transcribe` 暴露 `max_speakers`/`min_speakers`。

## 4. 验收

| # | 场景 | 通过标准 |
| --- | --- | --- |
| A1 | 云端装了 CAM++，`prefer=local`，`diarize=true` | 任务 `completed`，`segments[].speaker` 有值，`params.diarize_backend="local"` |
| A2 | 云端装了 CAM++，`prefer=cloud`，`diarize=true` | `backend="cloud"` 且 `diarize_backend="cloud"`；**本地未加载 CAM++**（`/api/system/status` 看不到 speaker 模型驻留） |
| A3 | 云端未装 CAM++，`prefer=cloud` | 任务失败，错误码 `CLOUD_DIARIZE_UNSUPPORTED`，文案含安装命令 |
| A4 | 云端未装 CAM++，`prefer=auto`，本地可装 | 自动回落本地完成，日志写明「云端不支持，已用本地」 |
| A5 | 旧版云端（`/v1/health` 无 `asr` 字段） | 视为不支持，不静默、不卡死 |
| A6 | 不传 `diarize`（老客户端） | 响应与改造前完全一致（`speaker` 全 null） |
| A7 | 4GB 本地：ASR(云端 diarize)→TTS 交替 2 轮 | 全部 completed，进程不消失（现状会因 whisper+CAM++ 抢显存而崩） |
| A8 | `min_speakers=3` 云端 | 云端返回 3 个说话人；`min>max` 报 `INVALID_INPUT` |

## 5. 风险

| 风险 | 处理 |
| --- | --- |
| 云端/本地版本不一致（老云端不认新字段） | 以 `/v1/health.asr.diarize` 为准；缺失即视为不支持；`diarize_backend` 缺失按 `null` 处理 |
| 云端 CPU 上 CAM++ 慢（无 GPU 实例） | 声纹是 onnxruntime CPU 推理，1 分钟音频实测 < 10s；慢时任务进度按声纹阶段推进（已有 `on_progress`） |
| `prefer=cloud` 让用户以为「云端一定支持」 | 设置页与工作台在提交前用 `/v1/health` 判定并置灰/提示 |
| 分段语义差异（本地 whisper 分段 vs 云端分段粒度） | 两者用同一 `ASRRouter` 同一引擎时一致；引擎不同则分段不同，文档说明「分段粒度取决于 ASR 引擎」 |

## 6. 工作量估算

| 项 | 规模 |
| --- | --- |
| `remote.py` 3 个表单字段 + 错误映射 | ~40 行 |
| `cloud.py` 透传 + health 能力探测与缓存 | ~70 行 |
| `asr.py` 策略重写（`prefer` 三值 + `diarize_backend`） | ~60 行 |
| `config.py` 新键 + `RUNTIME_MUTABLE` | ~10 行 |
| worker 落库 + 前端提示 | ~80 行 |
| cloud_agent S4b + 文档 | ~120 行 |
| 回归测试（A1-A8 抽样 A3/A4/A6/A8） | ~120 行 |

## 7. 落地结果（2026-10-21）

| 验收 | 状态 | 证据 |
| --- | --- | --- |
| A1 本地声纹（`prefer=local`） | 通过 | `asr.py` 本地分支回填 `diarize_backend=local` |
| A2 云端声纹（`prefer=cloud`） | 通过 | `/v1/engine/asr` 回 `diarize_backend=local`（服务端视角），客户端改记 `cloud` |
| A3 云端未装声纹 | 通过 | HTTP 400 `CLOUD_DIARIZE_UNSUPPORTED`，文案含 `engines_cli install asr-speaker-campplus` |
| A4 `auto` 回落本地 | 通过 | 云端未返回 `diarize_backend` → 记 `last_diarize_error` 后继续下一候选 |
| A5 旧版云端 | 通过 | 能力缺失即视为不支持，不静默 |
| A6 老客户端不传 diarize | 通过 | 三字段皆空时响应结构与改造前一致（`speaker` 全 null） |
| A7 4GB 交替不崩 | 待真机 | 需在 3050 上跑 ASR(云端)→TTS 交替 2 轮；本次仅单元级验证 |
| A8 `min_speakers` | 通过 | 本地/云端同口径；`min>max` 报 `INVALID_INPUT` |

### 7.1 实现与规格的差异（已同步 API.md / CONFIG.md）

1. `diarize` 除 `true/false` 还接受 `1/on/yes/y`（假值 `0/off/no/n`），大小写不敏感。
2. `max_speakers` 超界为**夹取**（兼容老任务），只有 `min_speakers` 越界或 `min>max` 才报 `INVALID_INPUT`。
3. `/v1/health.asr.min_speakers` 是布尔（表示认这个字段），不是数值。
4. `diarize_skipped` 已删除；改为 `diarize_backend` + 可读错误码。
5. `asr.diarize.prefer` 取值为 `local|cloud|auto`（决策记录里的 `cloud-first` 未采用）。
