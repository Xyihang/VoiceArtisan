# PHASE-3-BACKLOG · 第三部分功能待办（后续阶段）

- 版本：v1 · 2026-10-20
- 状态：**本阶段不实施**，仅登记需求、技术结论与拆分建议
- 关联：`docs/spec/CLOUD-AGENT-1.md`、`docs/spec/ASR-ENGINE-1.md`

---

## 0. 排期建议

| 阶段 | 内容 | 理由 |
| --- | --- | --- |
| 本次（P0） | 一：云主机一键接入 Agent；二：ASR 纳入引擎管理 | 用户明确先做 |
| 下一阶段（P1） | 3.1 说话人区分 + 3.2 批量队列 + 3.6 补全生成参数 | 依赖前置能力；3.6 改动最小可并行 |
| 再下一阶段（P2） | 3.3 后期处理链 + 3.4 导出档位 + 3.5 时长控制 + 3.7 MCP Server | 涉及音频处理管线与导出契约，需整体设计 |

---

## 1. 转写区分说话人（3.1）

**结论（调研）：现有 `faster-whisper` 单模型做不到。** Whisper 只做「语音→文本」，模型结构里没有说话人嵌入；`faster-whisper` 也不输出 speaker 标签。想要「一个模型同时转写 + 区分说话人」的可行路径只有两条：

| 路径 | 组成 | 优点 | 代价 |
| --- | --- | --- | --- |
| A. WhisperX 流水线 | faster-whisper（转写）+ pyannote-audio 3.x（说话人）+ 词级对齐 | 生态成熟、中文可用 | 新增依赖（pyannote + torch 已在）；pyannote 模型需 HF 授权下载 |
| B. FunASR 一体化 | SenseVoice-Small（转写 + 情绪/事件）+ Cam++（说话人向量，需自行组合） | 中文强、阿里达摩院、国内可直连 ModelScope | 仍需两个模型组合，非「单模型单次推理」 |
| C. sherpa-onnx 两段式 | Paraformer/Whisper + 3D-Speaker 声纹聚类 | 纯 CPU 可用 | 精度与文档一般 |

**建议**：按 `ASR-ENGINE-1` 的引擎化结构，新增「说话人区分」为**可插拔的后处理阶段**（`diarize` provider），默认关闭；默认 provider 选 A（WhisperX 路线）落在一个独立 venv 里，避免污染主环境。转写返回结构扩展为 `segments[{start, end, text, speaker}]`，并保持无 diarize 时 `speaker=null` 的向后兼容。

**待确认**：是否接受额外下载 pyannote 权重（约 30–100 MB）与 HF token。

---

## 1.1 选定路线（全宽松许可证，无 gated 模型）

**结论：转写继续用 faster-whisper（MIT），说话人区分用 sherpa-onnx（Apache-2.0）+ CAM++ 声纹（Apache-2.0）+ sklearn 层次聚类（BSD-3）。**

| 环节 | 组件 | 许可证 | 获取 |
| --- | --- | --- | --- |
| 转写 | faster-whisper（已有） | MIT | pip |
| 转写权重 | Whisper small/medium | MIT | 已在 `models/faster-whisper` |
| 声纹抽取 | sherpa-onnx（`SpeakerEmbeddingExtractor`） | Apache-2.0 | pip |
| 声纹模型 | 3D-Speaker / CAM++（`speech_campplus_sv_zh-cn_16k-common`） | Apache-2.0 | ModelScope 直下，无需 token |
| 聚类 | scikit-learn `AgglomerativeClustering` | BSD-3-Clause | pip |
| VAD（可选） | silero-vad / FSMN-VAD | MIT / Apache-2.0 | pip / ModelScope |

**不采用 whisperX + pyannote 3.1**：WhisperX 代码 BSD-2 没问题，但其说话人模型是 HF **gated 权重**（需登录接受条款），商用条款需单独核对，与「可随便用」的要求冲突。

### 实现步骤（不新增重型依赖，复用现有结构）

1. `backend/app/diarize.py`：
   - `extract_embeddings(pcm, sr)` → 用 sherpa-onnx 的 CAM++ 按 VAD 段抽 192 维 embedding；
   - `cluster(embeddings, max_speakers)` → sklearn 层次聚类（cosine + average linkage），输出每段 speaker 编号；
2. `asr.py` 的 `transcribe()` 返回结构扩展：`segments[{start, end, text, speaker}]`；不做区分时 `speaker=null`（向后兼容）；
3. 配置：`asr.diarize.enabled`（默认 false）、`asr.diarize.max_speakers`（默认 4）、`asr.diarize.threshold`；
4. 引擎化：CAM++ 作为 **ASR 附属模型**放进 `engine/adapters/asr_speaker.py`，沿用 `kind="asr"`，可在引擎管理页安装/删除（复用 ASR-ENGINE-1 的机制）；
5. 前端：ASR 分区加「区分说话人」开关 + 任务详情里按 speaker 分色显示。

### 优缺点

| 优点 | 缺点 |
| --- | --- |
| 全部 MIT/Apache/BSD，可商用可闭源，无 gated、无需 HF token | 说话人区分精度不如 pyannote 3.1（中文场景差距较小） |
| 中文场景 CAM++ 优于通用 pyannote | 需要自己实现「VAD 切段 → 聚类 → 回填时间轴」的胶水代码 |
| ONNX 推理，CPU 可跑，不占显存，与现有 venv 隔离机制兼容 | 重叠说话（两个人同时讲）场景识别弱，属离线分离方案通病 |
| 模型国内 ModelScope 直下，可断点续传 | 首次要下 ~30MB 声纹模型；聚类参数需按场景调 |
| 与现有 faster-whisper 时间戳天然对齐（词级时间可直接切段） | 4 人以上会议需要调 `max_speakers`，否则容易过度合并 |

### 迁移与回退

- 默认关闭（`asr.diarize.enabled=false`），不影响现有转写行为；
- 需要时在引擎管理页安装 CAM++（约 30MB）后打开开关；
- 若聚类效果不满意，可只用「声纹归属到已知音色」模式（不做聚类，直接和音色库比对），该模式后续再加；
- 完全回退 = 关开关 + 删除 CAM++ 条目，无残留配置。

### 一句话结论

**精度略降、代码要自己写，换来的是"许可证无风险 + 中文更强 + CPU 可跑"。**

### 验收

1. 一段两人对话：输出 `segments` 带 `speaker`，说话人切换点与人工标注误差 < 1s；
2. `asr.diarize.enabled=false` 时输出结构与现在完全一致（`speaker` 全为 null）；
3. 引擎管理页能看到 CAM++ 条目，可安装/删除；
4. 纯 CPU 下 1 分钟音频处理时间 < 30s。

## 2. 批量队列（3.2）

- 新增 `POST /api/batch`：`items[]` 支持「逐项列表」参数（每项可覆盖 `text/voice/params/duration/...`），未覆盖的继承批次默认值。
- 复用现有任务表与 worker：批次 = 父任务 + N 个子任务，进度走既有 `tasks` topic。
- 导出：批次产出打包 zip（沿用 3.4 导出档位）。

## 3. 后期处理链（3.3）

- 处理阶段：`EQ（低切 + 高架可选）` → `压缩（阈值/比率/启动释放）` → `归一化（峰值 -1 dBFS）` → `音量平衡（按批次响度中位数对齐）`。
- 实现：`backend/app/dsp.py` 已有基础 DSP，扩展为 `PostChain`；全部可开关、参数存 `config.post`。
- 验收：频谱对比 + 峰值/响度实测（LUFS 前后差 ≤ 0.5）。

## 4. 导出档位（3.4）

| 档位 | 规格 |
| --- | --- |
| Standard | 44.1 kHz / 16 bit / 单声道 MP3 192k |
| For editing | 48 kHz / 24 bit / 单声道 WAV |
| Original | 模型原生采样率、不重采样、WAV |

成品卡片展示：实际采样率、声道、格式、文件体积（读真实文件头，不写死）。

## 5. 时长控制（3.5）

- 新增 `target_duration_s`，优先级高于 `speed`；实现方式：生成后按目标时长做「截断 / 静音补齐 / 时间伸缩（atempo 链）」。
- 长文沿用 `audio_chunk_duration` 分块（已存在），拼接口径与现有一致。

## 6. 补全生成参数（3.6）

- `num_step`、`guidance_scale`、`denoise` 三个字段：加入 `TTSParams`、设置页默认参数分区、工作台高级面板、`/v1` 远端请求体与云端适配器透传。
- 需按引擎能力门控（不支持的引擎置灰并提示），沿用 `kind_tags` 门禁机制。

## 7. MCP Server（3.7）

**已实现范围（v1.1.11）：仅 stdio；SSE / resources / prompts 未实现，登记为后续项。**

| 项 | 现状 |
| --- | --- |
| 传输 | **仅 stdio**（行分隔 JSON-RPC 2.0，协议版本 `2024-11-05`），启动 `cd backend && python -m app.mcp_server` |
| 工具 | `tts_synthesize` / `asr_transcribe` / `task_status` / `voice_list`（4 个） |
| 能力声明 | `initialize` 的 `capabilities` 只有 `{"tools": {}}` |
| 调用路径 | 复用**本机 HTTP 接口**（默认 `QWENTTS_MCP_BASE_URL=https://127.0.0.1:8443`），不新建引擎、不加载模型；原设想的「进程内直调 service 层」未采用（4GB 卡会重复占显存） |
| 认证 | 本地 stdio 无需认证；需要时设 `QWENTTS_MCP_API_KEY`（带 `X-API-Key` 头） |
| 未实现（后续项） | **SSE / Streamable HTTP 传输**、`resources`、`prompts`、`sampling`、`logging`/进度通知、`completions`；客户端侧表现为无资源/提示词面板，硬调对应方法回 `-32601` |

使用文档（客户端配置、4 个工具参数与返回、环境变量、错误码、能力边界表）：见 [docs/MCP.md](../MCP.md)。
