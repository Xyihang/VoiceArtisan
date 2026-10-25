# MCP 扩展计划（MCP-EXPANSION-1）

> 状态：**计划文档（未实现）**
> 关联：`backend/app/mcp_server.py`（现状 4 个工具、仅 stdio）、`docs/API.md`
> 目标版本：v1.2.x

---

## 0. 现状盘点

| 能力 | 现状 |
| --- | --- |
| 传输 | 仅 stdio（行分隔 JSON-RPC 2.0），无 SSE / HTTP 网关 |
| 协议方法 | `initialize` / `ping` / `tools/list` / `tools/call` / `notifications/*` |
| 工具 | `tts_synthesize`、`asr_transcribe`、`task_status`、`voice_list` |
| 缺 | resources、prompts、sampling；批量、说话人、导出/字幕、引擎、云端引擎、预热、取消/删除 |
| 架构 | MCP 进程**不加载模型**，全部转发到本机 `https://127.0.0.1:8443` 的 HTTP 接口 |

**设计原则（不改变）**：MCP 层只做协议转换 + 参数校验 + 结果整形，业务逻辑一律复用后端 HTTP 接口。理由：4GB 显存下不能在第二个进程再建引擎；复用排队/取消/日志/进度。

---

## 1. 阶段一：协议能力（resources / prompts / sampling）

### 1.1 resources —— 让 Agent 能"读"而不是"调"

**URI 方案**：`qwentts://<类型>/<id>[/子资源]`

| URI | 内容 | 数据来源 |
| --- | --- | --- |
| `qwentts://voices` | 音色清单（id/名称/语言/是否内置/参考音频时长） | `GET /api/voices` |
| `qwentts://voices/{voice_id}` | 单个音色详情 | `GET /api/voices/{id}` |
| `qwentts://tasks` | 最近任务列表（默认 20 条，支持 `?status=&limit=`） | `GET /api/tasks` |
| `qwentts://tasks/{task_id}` | 任务详情（含参数、后端、耗时、错误） | `GET /api/tasks/{id}` |
| `qwentts://tasks/{task_id}/transcript` | 纯文本转录（`text/plain`） | 任务 `transcript` |
| `qwentts://tasks/{task_id}/segments` | 逐句时间轴（`application/json`） | 任务 `segments` |
| `qwentts://tasks/{task_id}/subtitles.srt` | 字幕原文（`text/plain`） | `GET /api/tasks/{id}/subtitles` |
| `qwentts://engines` | 本机引擎状态（含 `active`） | `GET /api/engines` |
| `qwentts://cloud/engines` | 云端引擎状态 | `GET /api/cloud/engines` |
| `qwentts://system/status` | 显存、延迟、模型、隧道、空闲停机 | `GET /api/system/status` |
| `qwentts://logs/recent` | 最近 N 条日志（默认 100，可 `?level=`） | `GET /api/system/logs` |

**需要实现的方法**

- `resources/list`：静态返回上表（含 `mimeType`、`name`、`description`）
- `resources/templates/list`：带 `{task_id}`、`{voice_id}` 的模板（URI Template 形式）
- `resources/read`：按 URI 取内容；未知 URI → `-32602 Invalid params`
- `resources/subscribe` + `notifications/resources/updated`（**可选，M1 末尾再做**）：
  订阅 `qwentts://tasks/{id}`，任务状态变化时推 `task.update`（复用现有事件总线语义）

**实现要点**
- 单条资源体积上限：`segments` 超过 2000 条时截断并在末尾附 `{"truncated": true, "total": N}`
- 二进制资源（音频/zip）**不通过 resources 暴露**，只给 URL（stdio 传二进制会污染管道）
- 所有读操作走 `GET`，失败映射为 `isError: true` 的文本内容（MCP 习惯）

### 1.2 prompts —— 预置"怎么用这套工具"的提示词

| prompt 名 | 参数 | 用途 |
| --- | --- | --- |
| `dub_script` | `topic`、`tone`、`language`、`target_duration_s` | 生成可直接喂给 `tts_synthesize` 的配音稿（含停顿标记、分句） |
| `multi_role_dialog` | `scenario`、`roles`（"旁白,主播,顾客"）、`turns` | 生成多角色对话脚本 + 建议音色映射，配合 `batch_tts_create` 一次出多轨 |
| `voice_pick` | `sample_text`、`requirements` | 从 `qwentts://voices` 里挑音色并解释理由 |
| `subtitle_polish` | `task_id` | 读 `segments` → 出「断句更自然、标点补齐」的新 SRT 草稿（只返回文本，不写库） |
| `speaker_naming` | `task_id` | 根据对话内容给 `SPK1/SPK2` 起更语义化的名字（配合 `task_rename_speakers`） |

- 方法：`prompts/list` / `prompts/get`（`arguments` 填参数，返回 `messages[]`，`role: user`）
- 每个 prompt 返回的 `messages` 里**显式列出可用的工具名和参数约束**，降低 Agent 误用

### 1.3 sampling —— 服务端反向请求客户端模型

MCP 的 `sampling/createMessage` 是 **server → client** 的反向调用，用途：

| 场景 | 触发点 | 降级 |
| --- | --- | --- |
| 自动选音色 | `tts_synthesize` 未传 `voice_id` 且请求体带 `"auto_voice": true` | 不支持 sampling → 用默认音色 |
| 说话人命名 | `task_rename_speakers` 传 `auto: true` | 不支持 → 返回 `SPK1..N` 不猜 |
| 字幕断句/标点修复 | `task_subtitles` 传 `polish: true` | 不支持 → 直接返回原始字幕 |
| 长文分段策略 | 文本超阈值（默认 2000 字）时决定章节切分 | 不支持 → 按标点硬切 |

**实现要点**
- stdio 是双向管道：需要**读循环 + 待响应表**（`request_id → Future`），当前实现是纯"一问一答"，要改成并发收发
- 必须带超时（默认 20s）+ 能力探测：`initialize` 时看客户端 `capabilities.sampling` 是否存在
- **任何 sampling 失败都不阻塞主流程**（降级即成功，记 WARNING）
- 隐私：采样时只发送必要文本，并在日志里标注"已外发 N 字到客户端模型"

---

## 2. 阶段二：任务域工具

| 工具 | 输入 | 后端接口 |
| --- | --- | --- |
| `task_cancel` | `task_id` | `POST /api/tasks/{id}/cancel` |
| `task_delete` | `task_id` 或 `task_ids[]` | `DELETE /api/tasks/{id}` 或 `POST /api/tasks/batch-delete` |
| `task_retry` | `task_id`、`force_backend?`、`params?` | `POST /api/tasks/{id}/retry` |
| `task_media_info` | `task_id` | `GET /api/tasks/{id}/media-info` |
| **`batch_tts_create`** | `items[]`（每项 `text` + 可选 `voice_id`/`params`）、`name?` | `POST /api/tasks/batch` |
| **`batch_status`** | `batch_id` | `GET /api/tasks/batch/{batch_id}` |
| `batch_cancel` | `batch_id` | `POST /api/tasks/batch/{batch_id}/cancel` |
| `batch_retry_failed` | `batch_id` | `POST /api/tasks/batch/{batch_id}/retry-failed` |
| `batch_zip_url` | `batch_id` | 返回 `GET /api/tasks/batch/{id}/zip` 链接（不返二进制） |
| **`task_rename_speakers`** | `task_id`、`mapping: {SPK1: "张三"}` 或 `auto: true` | `PATCH /api/tasks/{id}/speakers` |
| `task_merge_speakers` | `task_id`、`from`、`to` | `POST /api/tasks/{id}/speakers/merge` |
| **`task_export`** | `task_id`、`tier: original\|standard\|editing`、`format?` | `POST /api/tasks/{id}/export` |
| **`task_subtitles`** | `task_id`、`fmt: srt\|vtt\|ass`、`by_speaker?`、`polish?` | `GET /api/tasks/{id}/subtitles` |
| `task_split_speakers` | `task_id` | `POST /api/tasks/{id}/split-by-speaker` |
| `voice_create` | `name`、`audio_path`、`text?` | `POST /api/voices`（先 `POST /api/assets` 上传） |

**批量相关约定**
- `batch_tts_create` 立即返回 `batch_id`（后台跑），**不阻塞等待**；要结果就轮询 `batch_status`
- `batch_status` 默认只返回聚合（`counts`）+ 前 20 条明细，`detail: true` 才全量
- 单批上限沿用后端限制（默认 200 条），超出直接报错而不再拆批（避免 Agent 以为成功）

## 3. 阶段三：引擎域工具

| 工具 | 输入 | 后端接口 |
| --- | --- | --- |
| `engine_list` | `kind?`（tts/asr/speaker） | `GET /api/engines` |
| `engine_install` | `engine_id` | `POST /api/engines/{id}/install` → 返回 `job_id` |
| `engine_install_status` | `engine_id` | `GET /api/engines/{id}/install/status` |
| `engine_remove` | `engine_id` | `DELETE /api/engines/{id}` |
| `engine_select` | `engine_id` | `POST /api/engines/{id}/select` |
| `engine_health` | `engine_id` | `GET /api/engines/{id}/health` |
| **`model_preload`** | `kinds[]`（`custom_voice`/`base`/`voice_design`/`asr`） | `POST /api/system/preload` |
| `model_unload` | `kinds[]?`（空 = 全部） | `POST /api/system/unload` |
| `cloud_engine_list` | — | `GET /api/cloud/engines` |
| `cloud_engine_install` | `engine_id` | `POST /api/cloud/engines/{id}/install` → `job_id` |
| `cloud_engine_job` | `job_id` | `GET /api/cloud/engines/jobs/{job_id}` |
| `cloud_engine_remove` | `engine_id` | `DELETE /api/cloud/engines/{id}` |
| `cloud_engine_select` | `engine_id` | `POST /api/cloud/engines/{id}/select` |
| `cloud_host_get` / `cloud_host_set` / `cloud_host_test` | 主机档案字段 | `/api/cloud/engines/host`、`/host/test` |
| `cloud_probe` | — | `POST /api/cloud/probe` |

**约定**
- 安装类工具**永远异步**：返回 `job_id` + `poll_with` 提示（用哪个工具查）
- `model_preload` 可能吃满显存 → 返回每个模型的结果与 `vram_allocated_mb`，失败不抛整体错误
- 云端类工具在未配置主机时返回结构化错误：`{"ok": false, "reason": "host_not_configured", "next": "cloud_host_set"}`

## 4. 阶段四：健壮性与横切

| 项 | 方案 |
| --- | --- |
| 长任务超时 | 统一 `QWENTTS_MCP_TIMEOUT`（默认 600s）；超时返回 `status:"timeout"` + `task_id`，让 Agent 自行轮询而不是重复提交 |
| 返回体积 | 文本工具（transcript/segments/日志）默认截断 + `truncated` 标记；`full: true` 才全量 |
| 错误映射 | 后端 `AppError` → MCP `isError: true` + 文本内含 `code/hint/detail`（保持现有风格） |
| 幂等 | `batch_tts_create` 支持 `idempotency_key`，重复提交返回同一 `batch_id` |
| 鉴权 | 沿用 `QWENTTS_MCP_API_KEY`；stdio 场景默认空（本机可信） |
| 并发收发 | stdio 读循环改成"读线程 + 写锁"，为 sampling 做准备 |
| 可观测 | 每次 `tools/call` 记一行：工具名、耗时、结果大小、是否降级 |

## 5. 里程碑与验收

| 阶段 | 内容 | 验收标准 |
| --- | --- | --- |
| **M1** | resources（list/templates/read）+ prompts（list/get） | 用 MCP Inspector 能看到 11 个资源模板、5 个 prompt；`resources/read` 能取到真实任务转录 |
| **M2** | sampling 双向通道 + 4 个 sampling 场景（全部带降级） | 客户端不支持 sampling 时功能不受影响；支持时"自动选音色/自动命名/字幕润色"可用 |
| **M3** | 任务域 15 个工具（含批量/说话人/导出/字幕） | 端到端：批量 3 条 → 查状态 → 改说话人名 → 导出字幕 srt → 取 zip 链接 |
| **M4** | 引擎域 15 个工具（含云端/预热） | 端到端：列引擎 → 装一个小引擎（whisper-tiny）→ 预热 → 卸载 |
| **M5** | 文档 + 示例配置 + 回归测试 | `docs/API.md` 增 MCP 章节；`tests/test_mcp_*.py` 覆盖工具清单与参数校验；`pytest` 全绿 |

**测试分层**
1. 单测：`_call_tool` 参数校验、错误映射、截断逻辑、URI 解析（mock HTTP 层）
2. 协议测：喂入 `initialize/tools/list/tools/call/resources/read/prompts/get` 的原始 JSON 行，断言响应结构
3. 真机 e2e：脚本启动 MCP 子进程 + 本机后端，跑一条完整业务链路

## 6. 风险与取舍

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| sampling 需要双向管道改造 | 现有问答式循环要重写 | 先做 M1/M3（无 sampling 也完整）；sampling 单独 M2，可回滚 |
| 工具数量暴涨到 ~35 个 | Agent 选择困难、schema 变长 | 工具名统一 `<域>_<动作>`；`description` 里写"何时用/何时不用"；resources 承担"读"、tools 承担"写" |
| 批量/安装类长任务 | Agent 反复提交造成重复任务 | 一律返回 id + `poll_with`；支持 `idempotency_key` |
| 云端工具依赖 SSH | 未配置主机时全是错误 | 结构化 `host_not_configured` + 指路 `cloud_host_set` |
| 资源体积 | 大任务 segments 可能几 MB | 默认截断 + `full` 开关 |

## 7. 非目标

- ❌ HTTP/SSE 传输（远程 Agent 直连）——留到 v1.3 单独评估
- ❌ 通过 MCP 传二进制（音频/zip）——只给 URL
- ❌ MCP 进程内直接持有模型/引擎实例
- ❌ OAuth / 多租户

---

## 8. 附：视频配音可行性评估

**结论：好做，且现有底座已覆盖约 70%。** 建议作为独立任务类型 `type="dub"` 接入，不改表结构（参数进 `params` JSON）。

### 8.1 已有可直接复用的能力

| 环节 | 现有实现 |
| --- | --- |
| 音轨提取 / 混音 / 封装 | 已集成 **ffmpeg**（导出、探测、post 处理都在用） |
| 转写 + 时间轴 + 说话人 | faster-whisper + CAM++（`segments` 已含起止时间与 SPK） |
| 逐句合成 | TTS 已支持分段合成、`fit_duration`（0.5–2.0 倍拉伸）对齐时长 |
| 音色映射 | 自定义音色/克隆已有；`speakers` 改名接口已有 |
| 字幕 | srt/vtt/ass 导出已有 |
| 成品规格探测 | `media-info`（读文件头）已有 |

### 8.2 需要新增的（MVP 范围）

1. 任务类型 `dub`：输入视频 → 提取音轨（`ffmpeg -vn -ac 1 -ar 16000`）
2. **逐句驱动合成**：按 `segments` 时间轴为每句单独合成，时间戳偏移拼接（而不是整段合成后再切）
3. 说话人 → 音色映射表（`params.voice_map: {"SPK1": "voice_id"}`），未映射的用默认音色
4. 时长对齐策略：句子超时 → `fit_duration` 压缩（上限 1.6×）；不足 → 补静音到时间点
5. 混音输出：`原背景音（可选保留，默认 -18dB 压低）+ 新配音轨` → `ffmpeg -c:v copy` 封装回 mp4
6. 字幕轨可选内嵌（`-c:s mov_text`）

### 8.3 工作量估算

| 版本 | 范围 | 估时 |
| --- | --- | --- |
| **V1（能用）** | 单视频、单音色、按句对齐、保留背景音、导出 mp4 + srt | **3–5 天**（含真机联调） |
| **V2（多角色）** | 说话人 → 多音色映射、逐句音量归一化、分轨导出 | +2–3 天 |
| **V3（翻译/口型）** | 翻译配音需要接 LLM（已有 AI 配置）+ 二次校对；口型同步需要 talking-head 模型（如 Wav2Lip） | 翻译 +3 天；**口型建议不做**（模型大、显存不够、效果不稳） |

### 8.4 主要风险

| 风险 | 说明 |
| --- | --- |
| 时长冲突 | 译文/多音色语速不一致 → 句子超时只能压缩到 1.6×，再超就得砍字；需要在 UI 里给出"超时句子"清单 |
| 长视频 | 30 分钟视频会产生几百个子任务，排队时间远超合成时间 → 要分批 + 断点续做 |
| 音画不同步感 | 硬对齐到时间点会有"赶/拖"感；V2 再做句内微调（前后各留 80ms 缓冲） |
| 磁盘 | 中间产物（原音轨/逐句 wav/混音）按 10 分钟视频约 300MB，需放 `data/tmp` 并在完成后清理 |

### 8.5 与 MCP 的关系

视频配音一旦落地，MCP 侧只需加两个工具即可对外暴露：
- `dub_video(video_path, voice_map, keep_bgm, subtitle)` → `task_id`
- `dub_status(task_id)`（复用 `task_status` + `media-info`）

**因此建议顺序**：先做 MCP 扩展（M1–M5），再做视频配音 V1；两者互不阻塞，但 MCP 的 `batch_tts_create` 会成为配音逐句合成的现成底座。
