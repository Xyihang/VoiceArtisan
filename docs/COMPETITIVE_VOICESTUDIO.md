# 功能差距报告：VOICESTUDIO vs. 本项目（QWENTTS）

> 调研时间：2026-10-19 · 调研方式：`web search` + GitHub REST API（`/repos`、`/git/trees`、`/contents`）实读 README 与 docs/代码路径
> 基准项目：`E:\trae_projects\QWENTTS`（本仓库，专有许可）
> 规则：**凡未在本次调研中实际读到的内容，一律标注「未核实」**；不推测、不补全功能。

---

## 1. 摘要

**VOICESTUDIO** 指 GitHub 上的 `debpalash/VoiceStudio`（约 **7.5 万 Star**、AGPL-3.0、Python 后端 + Electron 桌面端）：一个「完全本地的 ElevenLabs 替代品」，主线是 **声音克隆 / 音色设计 / 视频配音 / 听写 / 转写 / 有声书**，内置 **15+ 个可切换 TTS 引擎与 9 个 ASR 引擎**，并对外提供 **OpenAI 兼容 API、MCP Server、本地语音平台端口**。

对比下来，我们的**合成质量与工程闭环（4GB 跑 1.7B、WS 流式、三后端路由、安装引导器）并不落后**，差距集中在「**把语音能力加工成内容产品**」这一层。我们最该补的 3 件事：

1. **文本规范化 + 发音词典**（数字/单位/型号/多音字），这是当前内容错读的直接来源；
2. **字幕与时间戳产出**（SRT/VTT），把现有句级分块的时间信息变成可交付物；
3. **OpenAI 兼容接口 + MCP/CLI**，把产品从「界面」变成「平台」。

---

## 2. 候选仓库清单

检索词：「github VOICESTUDIO voice」「github VoiceStudio TTS」「site:github.com voice-studio text-to-speech」。命中多个同名仓库，其中相当一部分是同一上游的 fork。

| # | 仓库全名 | URL | Star | 最近更新 | 技术栈 | 许可 | 是否最可能命中 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `debpalash/VoiceStudio` | https://github.com/debpalash/VoiceStudio | **75,323**（fork 8,243，open issues 247） | `pushed_at` 2026-10-18 | Python（FastAPI 后端）+ Electron/TypeScript 桌面端 + React 前端 + Rust sidecar | AGPL-3.0 | ✅ **最可能** |
| 2 | `bayshier/voicestudio-zh` | https://github.com/bayshier/voicestudio-zh | 88 | 2026-08-31 | Python（同上游） | AGPL-3.0 | ⚠️ 次候选：上游的中文译版（仓库自述「译自 debpalash/VoiceStudio」） |
| 3 | `mjlzz/voice-studio` | https://github.com/mjlzz/voice-studio | 14 | 2026-03-24 | Python（ASR & TTS 工具箱，含实时流式、CPU 推理、桌面悬浮话筒、Web UI + CLI） | API 未返回 license 字段 | ❌ 独立项目、Star 极少、方向是「工具盒」而非产品 |
| 4 | `sirgawain0x/VoiceStudio`（及 `eaz/`、`SimonTingle/`、`Vikram25/`、`chriswithsunpower87/` 等同名仓库） | https://github.com/sirgawain0x/VoiceStudio | 0 | 2026-09-24 | 同上游 | AGPL-3.0 | ❌ `fork: true`、`parent: debpalash/VoiceStudio`，是镜像不是独立项目 |

**判定依据（逐条可核）：**

1. `debpalash/VoiceStudio` 是唯一**非 fork、Star 量级最高、且方向完全对口**（TTS/克隆/音色设计）的仓库；GitHub API 返回 `fork: false`、`topics` 含 `text-to-speech / voice-cloning / tts / elevenlabs-alternative`。
2. 它**近期活跃**：`pushed_at = 2026-10-18`（相对调研日仅 1 天）；`created_at = 2026-04-09`，半年内冲到该量级，并在 README 挂了 Trendshift 榜单徽章。
3. 它是**生态中心**：表 4 中所有同名仓库的 `parent` 都指向它，网上中文介绍文（qiniu 新闻页）也以它为对象 —— 说明「VOICESTUDIO」这个说法在传播上指向的唯一实体就是它。
4. 关于 Star 数的口径分歧：第三方博客文章标题写「21K Star」，而 GitHub API 当前返回 `stargazers_count = 75323`。**以 API 返回为准**，博客数字视为过期或口径不同（该差异本身也不影响结论）。

> 未核实：Star 数会随时间变动；本报告的数值是本次 API 调用瞬间的快照。

### 2.1 检索与取证过程（可复现）

| 步骤 | 做法 | 结果 |
| --- | --- | --- |
| 1 | `web_search`：`github voceestudio voice` / `github VoiceStudio voice synthesis TTS` / `site:github.com voice-studio text-to-speech` / `github.com VoiceStudio repository speech` / `GitHub 语音合成 项目 VoiceStudio` | 命中 8 个不同 URL，其中 6 个是同一上游及其 fork；另有 1 个独立项目 `mjlzz/voice-studio`、1 篇第三方介绍文 |
| 2 | `GET /repos/{owner}/{repo}` 逐个取元数据 | 得到 Star / fork / 许可 / `pushed_at` / `fork` / `parent`；据此排除全部 fork |
| 3 | `GET /git/trees/main?recursive=1` | 3,835 个文件路径，**未截断**；用于确认后端/文档/测试的真实结构，避免只信 README |
| 4 | `GET /contents/<path>` 读原文 | 实读 README.md 与 11 篇 docs（feature-catalog、languages、mcp、speech-platform、api-auth、audio-quality、generation-parameters、voice-design、evaluation、performance、STRUCTURE） |
| 5 | 未做到的事 | 未 clone 仓库、未逐行读实现、未安装运行、未读 issue 正文 → 这些一律在正文标「未核实」 |

---

## 3. 对方功能全景（按模块，逐条给来源）

来源标记说明：`[README]`=README.md；`[docs/x]`=仓库内文档；`[code: path]`=源码路径存在但**未逐行阅读实现**；`[未核实]`=仅见于宣传语或无法取证。

### 3.1 语音合成 / 引擎层

| 能力 | 说明 | 来源 |
| --- | --- | --- |
| 默认引擎 VoiceStudio（k2-fsa/OmniVoice） | 自研/自托管模型，646 语言、581k 小时训练数据 | `[README]` `[docs/feature-catalog.md]` `[docs/languages.md]` |
| 多引擎可切换（15+） | omnivoice-subprocess、omnivoice-gguf、CosyVoice 3、KittenTTS、MLX-Audio、VoxCPM2、MOSS-TTS-Nano、MOSS-TTS-v1.5、gpt-sovits、sherpa-onnx、IndexTTS 2.5、supertonic3、dots.tts、Confucius4-TTS、pockettts、audiocpp | `[docs/feature-catalog.md]`；每个引擎有独立指南 `[docs/engines/*.md]`（30 篇）；代码 `[code: backend/engines/*]` |
| 生成参数 | `num_step`（迭代解掩码步数）、`guidance_scale`、`denoise` token、时长/语速、长文本 | `[docs/generation-parameters.md]` |
| 声音克隆 | 参考音频 + 参考文本（缺 transcript 会跑一次 Whisper 补齐） | `[README]` `[docs/performance.md]` `[code: backend/services/speaker_clone.py]` |
| 音色设计 | 纯文本描述 → 说话人属性（性别/年龄/音高/风格/口音），可「保存后固定种子样音」 | `[docs/voice-design.md]` `[docs/mcp.md]` |
| 变声/声音转换 | 路由 `voice_convert`、服务 `rvc.py` | `[code: backend/api/routers/voice_convert.py, backend/services/rvc.py]`；具体界面行为**未核实** |
| 有声书 / 长文 | `audiobook`、`longform_*`（分段、续跑、段缓存）、`stories` | `[code: backend/services/audiobook.py, longform_render.py, longform_resume.py, routers/stories.py]` |
| AI 水印 | 合成音频加水印（分块、采样率覆盖） | `[docs/feature-catalog.md]`；`[code: backend/api/routers/watermark.py]` |

### 3.2 音频后处理 / 质量标准

| 能力 | 说明 | 来源 |
| --- | --- | --- |
| 导出精度可选 | 默认 16-bit WAV，可选更高精度（Standard / For editing / Original 三档用户语言） | `[docs/audio-quality.md]` |
| 母带/响度 | "broadcast mastering"、音量平衡（volume balancing） | `[docs/audio-quality.md]`；`[code: backend/services/loudness.py]` |
| 清理 | 生成前 `denoise` token、cleanup 选项 | `[docs/generation-parameters.md]` `[docs/audio-quality.md]` |
| 信号自检 | 可选信号检查（clipping 等）与可重复 A/B 对比 | `[docs/audio-quality.md]`；`[code: scripts/compare_generation_quality.py]` |
| 评测指标 | WER（可懂度）、SIM-o（说话人相似度）、UTMOS（自然度），多套测试集 | `[docs/evaluation.md]`；`[code: omnivoice/eval/{wer,speaker_similarity,mos}]` |

### 3.3 转写 / 听写 / 说话人

| 能力 | 说明 | 来源 |
| --- | --- | --- |
| ASR 引擎 9 种 | WhisperX（默认）、Faster-Whisper、MLX Whisper、PyTorch Whisper、Parakeet TDT(+MLX)、Moonshine、FunASR、sherpa-onnx、OpenAI 兼容服务 | `[docs/feature-catalog.md]` `[docs/engines/*]` |
| 实时听写（系统级） | 悬浮听写组件（floating widget），sherpa-onnx 流式，部分/最终文本走 WS | `[docs/feature-catalog.md]` `[docs/speech-platform.md]` `[code: backend/services/sherpa_dictation.py, api/routers/capture_ws.py]` |
| 说话人日志 | Speaker Diarization（本地 + 原生两条实现） | `[docs/feature-catalog.md]`；`[code: backend/services/diarization_{local,native,runtime}.py]`；准确率**未核实** |
| 人声分离 | Vocal Isolation | `[docs/feature-catalog.md]`；实现路径未定位 → **未核实** |

### 3.4 视频配音 / 字幕

| 能力 | 说明 | 来源 |
| --- | --- | --- |
| 视频配音流水线 | 抽取→转写→翻译→分句→时长适配→合成→混音 | `[code: backend/services/dub_pipeline.py, dub_batching.py, dub_background.py, dub_qc.py]` `[docs/feature-catalog.md]` |
| 时长适配 | `duration_planner`、`fit_planner`、`fitted_subtitles`、`onset_align`（按镜/按字幕对齐） | `[code: backend/services/duration_planner.py, fit_planner.py, fitted_subtitles.py, onset_align.py]` |
| 字幕格式 | SRT 解析、WebVTT 导出转义、卡拉 OK 风格 ASS | `[code: backend/services/karaoke_ass.py]`；`[code: tests/test_srt_parser.py, test_webvtt_export_escaping.py, test_karaoke_ass.py]` |
| 翻译 | 多翻译引擎 + 术语表（glossary） | `[code: backend/services/sonitranslate.py, api/routers/glossary.py, dub_translate.py]` |

### 3.5 接口 / 集成 / 平台化

| 能力 | 说明 | 来源 |
| --- | --- | --- |
| 本地 speech platform | HTTP `:3900` 收音频文件、WS 收 PCM/WebM 出部分/最终文本 —— 桌面 App 只是其中一个前端 | `[docs/speech-platform.md]` |
| MCP Server | 挂在后端 `/mcp`，工具：`generate_speech`/`clone_voice`/`describe_voice`/`design_voice`/`transcribe`/`list_voices`/`list_personalities`/`list_languages`/`check_health`；支持按 agent 绑定音色 | `[docs/mcp.md]` `[code: backend/mcp_server.py, backend/services/mcp_bindings.py]` |
| OpenAI 兼容 | 专门路由 `openai_compat`；文档提到 OpenAI SDK 以 Bearer 传 key 即可用 | `[code: backend/api/routers/openai_compat.py]` `[docs/api-auth.md]` `[code: tests/test_openai_sdk_contract.py, test_openai_speech_binary_guard_1172.py]` |
| 鉴权 | 默认 loopback 免鉴权；两道可选闸：Share PIN（局域网分享）、`OMNIVOICE_API_KEY`；`OMNIVOICE_TRUSTED_NETWORKS` 白名单；管理员会话 | `[docs/api-auth.md]` `[code: backend/services/admin_sessions.py]` |
| 其他协议面 | n8n 工作流、Twilio 电话、`calls`、bot 配置、marketplace/gallery/community | `[code: backend/api/routers/{telephony_twilio,calls,community,marketplace,gallery}.py]` `[code: tests/test_n8n_workflow.py, test_telephony_twilio.py]`；具体成熟度**未核实** |
| 插件/扩展 | 插件目录 + Plugin SDK | `[code: backend/plugins, backend/services/plugin_sdk.py]` `[docs/feature-catalog.md]`「Extensible」；插件 API 是否稳定**未核实** |

### 3.6 部署形态 / 工程

| 能力 | 说明 | 来源 |
| --- | --- | --- |
| 桌面端 | **Electron 是唯一桌面与 Web UI**（0.5.3 是最后一个 Tauri 版本，旧壳已移除） | `[README]` `[docs/electron-migration.md]` |
| 发布渠道 | macOS `.dmg` / Windows `.exe`(MSI) / Linux `.AppImage`·`.deb` / Snap / Microsoft Store 流水线 | `[README]` `[docs/store-publishing.md]` `[code: scripts/verify-windows-msi.ps1, create_snap_store_assets.mjs]` |
| 一键安装 | `curl -fsSL https://voicestudio.sh/install \| sh`（Windows 用 `irm ... \| iex`），带 `--version/--main/--uninstall` | `[README]` `[docs/install/script.md]` |
| Docker | compose + Dockerfile + GPU 队列/远端 GPU | `[code: deploy/docker-compose.yml, Dockerfile]` `[docs/remote-gpu.md]` `[code: backend/services/gpu_gateway.py]` |
| 移动端 | iOS / Android（README 标为 "preview"） | `[README]` `[docs/mobile.md]` `[code: mobile/]`；是否正式可用**未核实** |
| 硬件覆盖 | NVIDIA CUDA / Apple Silicon MPS / 纯 CPU / Windows-on-ARM（实验） | `[README]` |
| 质量门禁 | ~385 个 `tests/test_*.py`（命名即契约，如 `test_ssml_lite.py`、`test_loudness.py`、`test_diarization_native_contract.py`）；`scripts/check-docs-drift.py` 防文档漂移；覆盖率/许可证脚本 | `[code: tests/, scripts/]` |
| 训练/微调 | `omnivoice/training`、`examples/run_finetune.sh`、Colab notebook | `[code: omnivoice/training/, examples/]` `[notebooks/OmniVoice_Studio_Colab.ipynb]` |
| 许可治理 | 逐模型许可清单与证据链 | `[README]` `[docs/licensing/model-inventory.md]` `[code: scripts/check_model_licenses.py]` |

### 3.7 桌面工作区与交互（Electron）

| 工作区 | 作用（据文档标题/README 截图说明） | 来源 |
| --- | --- | --- |
| Launchpad | 入口聚合，引导到各工作区 | `[docs/electron-launchpad.md]` `[README 截图]` |
| Voice Cloning | 选音色或加参考录音→输入文本→生成 | `[README]` |
| Voice Design | 文本描述设计音色，保存时渲染固定种子样音 | `[docs/electron-voice-design.md]` `[docs/voice-design.md]` |
| Dubbing | 视频配音工作区（含字幕、翻译、时长适配） | `[README]` `[docs/electron-dubbing.md]` `[docs/dubbing/]` |
| Dictation | 系统级听写组件 + 快捷键 + 原生插入 | `[docs/speech-platform.md]` |
| Studio / Long-form | 长文与有声书工程 | `[docs/electron-longform.md]` `[docs/electron-workflows.md]` |
| Gallery / Library / Projects | 产物库、项目与素材管理 | `[docs/electron-gallery.md]` `[docs/electron-projects.md]` |
| Engines / Models | 安装与管理本地语音模型、磁盘占用、许可提示 | `[README 截图]` `[docs/electron-runtime.md]` `[docs/engines/disk-usage.md]` |
| Logs / Repair / Updates / Settings | 日志、修复、更新通道、设置 | `[docs/electron-logs.md]` `[docs/electron-repair.md]` `[docs/update-channels.md]` |
| Connections / LLM providers | 远端 GPU、LLM 供应商配置 | `[docs/electron-connections.md]` `[docs/electron-llm-providers.md]` |
| Privacy | 分析需同意、本地优先 | `[README]` `[docs/electron-privacy.md]` |

> 界面细节（布局、动效、可用性）只从截图与文档标题推断，**逐项未核实**。

---

## 4. 功能对比表

图例：**缺失 / 弱于 / 持平 / 强于**（「我们」= QWENTTS，依据本仓库 README / docs/PROJECT_HISTORY.md / docs/CONFIG.md / docs/API.md）

| 功能 | VOICESTUDIO | 我们 | 差距类型 | 备注 |
| --- | --- | --- | --- | --- |
| **模型与推理** | 15+ 引擎可热切换（含 GGUF/MLX/subprocess 形态），每引擎独立安装与显存记账 | 单一模型族 Qwen3-TTS 1.7B 三件套（Base/CustomVoice/VoiceDesign），引擎抽象在 `backend/app/engine/base.py` | 弱于 | 我们换来的是「4GB 真能跑」的确定性；多引擎是维护成本，不是必然收益 |
| 模型选择/切换 UI | 模型管理器：安装、磁盘占用、许可证提示、版本告警 | 设置页预热/卸载 + `docs/MODELS.md` 手动下载脚本 | 弱于 | 各自闭环，但我们无「安装进度/磁盘占用」视图（PROJECT_HISTORY 待办里也承认） |
| 4GB/低显存可用性 | 官方口径：无独显走 CPU（约 5GB 磁盘），未提 4GB 跑大模型 | `float16 + vram_reserve_gb 0.1 + allow_vram_overcommit` 实测 1.7B 峰值 4049MB，RTF 0.08–0.21 | **强于** | 这是我们最硬的差异点（docs/BENCHMARK.md §2） |
| **音色管理** | voice profile：克隆/设计/内置统一为 profile，含头像、使用统计、persona 包 | 内置 9 音色 + 自定义音色（新建/重命名/删除）+ `voices` 表 | 弱于 | 缺「使用统计 / 头像 / persona 打包」 |
| 音色复用与共享 | gallery / community / marketplace 路由；persona bundle 可导出 | 无导出/导入音色包 | 缺失 | 我们音色只在本机 SQLite |
| **参考音频处理** | 参考音频校验、时长绑定、静音阶梯处理、重录/替换、参考 transcript 自动补齐 | 上传 + 3–10s 克隆 + 选区裁剪（`POST /api/assets/{id}/trim`）+ `max_ref_audio_seconds=30` | 持平 | 我们有裁剪 API；对方有「缺 transcript 自动跑 Whisper」（我们也有 ICL→x_vector 回落，路子不同） |
| **文本处理** | SSML-lite、文本规范化、数字→词（ML）、发音词典、术语表、多语言校验（不支持即报错而非乱读） | 仅句级安全切分（`backend/app/textsplit.py`，只在标点/词边界切，不切断数字/型号）；无规范化、无 SSML、无词典 | **弱于** | 这是影响内容正确率的头号短板（证据：`tests/test_ssml_lite.py`、`test_text_normalization.py`、`test_number_words_ml.py`、`backend/services/pronunciation.py`、`routers/glossary.py`） |
| 多语言 | 646 语言（OmniVoice 口径），逐语言训练时长表，选错语言直接拒绝 | Qwen3-TTS 支持面（中英日韩 + 方言，README F5），`language` 字段透传 | 弱于 | 数字口径差异大，不宜单看指标 |
| **生成控制** | 步数/guidance/denoise、时长与语速、长文本分章、batch、takes 多版本 | `instruct` 自然语言指令 + temperature/top_p/top_k/repetition_penalty/steps + 语速/音高（合成后 DSP） | 弱于 | 我们有**指令控制**（对方以 speaker 属性 instruct 表达），对方有**时长控制与 takes**，我们没有 |
| 时长/韵律精控 | `duration_planner`/`fit_planner`/`onset_align`，为配音按镜头卡时长 | 只有全局语速倍率（DSP），无目标时长规划 | **弱于** | 我们的语速是「拉伸」，对方是「按目标时长重排」 |
| 种子/可复现 | 音色设计保存时固定种子渲染样音 | 无显式 seed 字段（params 里没有） | 缺失 | docs/API.md §4 的 params 无 seed |
| 批量 | Batch Queue + batch 路由 + Electron 批量文档 | 任务中心支持批量**删除**，无批量**提交**（前端逐条创建） | 弱于 | 差的是「批量输入 → 批量产出」 |
| **多说话人/对话** | 有声书 cast、多说话人配音、stories、persona | 单任务单音色 | 缺失 | 我们只能靠多次生成再手工拼 |
| **实时性** | 听写 WS 流式（部分/最终文本）；TTS 走 `tts_stream` 路由（细节未核实） | 句级分块流式 + WS 推送 + 1.2s 预缓冲播放；默认非流式（语气更连贯） | 持平偏弱 | 我们流式是「合成分块」；对方强在**麦克风实时输入**这一侧 |
| 麦克风实时输入 | 系统级听写组件 + 悬浮窗 + 原生插入 | 无麦克风采集（只有文件上传） | 缺失 | 需要浏览器/桌面端采音与 VAD，工作量不小 |
| **后处理** | 降噪 token、响度/母带、音量平衡、导出精度三档、信号自检 | 仅语速/音高 DSP（`engine/local.py`），无降噪、无响度归一、无精度选项 | **弱于** | 我们的 wav 采样率由模型决定（24000），无 16/24bit 选择 |
| **导出格式与采样率** | WAV（多精度）/Opus/OGG（经 ffmpeg），单声道母带 | wav + mp3（需 ffmpeg），`allowed_output_formats` 白名单 | 弱于 | 缺 opus/flac 与位深控制 |
| **字幕/时间戳对齐** | SRT/VTT/ASS 导出、fitted subtitles、ONSET 对齐 | 无任何字幕产出；分块时长只在任务日志/流事件里 | **缺失** | 我们已有句级边界与每块时长 → 补 SRT 的成本很低（见 §5-3） |
| **API 与集成** | OpenAI 兼容 + MCP + 本地 speech platform + n8n/Twilio + SDK 契约测试 | REST `/api/*` + `/v1/engine/*`（需 `X-API-Key`）+ WS；无 OpenAI 兼容层、无 MCP、无 CLI | **弱于** | 我们的 `/v1/engine/synth` 已是私有协议，改成 OpenAI 形态是增量工作 |
| **并发与队列** | job queue + GPU 池/队列记账 + 分片预算 | 单工作线程串行（`backend/app/worker.py`），前端不轮询 | 弱于 | 单机 4GB 场景串行其实是**正确**选择，不必盲从 |
| 缓存 | 段缓存（longform resume/cache）、prompt 磁盘缓存、clone prompt cache | 无合成结果缓存（每次重算）；有模型常驻/预热 | 弱于 | 直播/长文场景差距明显 |
| **评测与质检** | WER/SIM-o/UTMOS + 测试集 + 对比脚本 | ASR 回译质检（`qc.enabled`，默认关，相似度阈值 0.75） | 弱于 | 我们有「内容是否漏字」这一层，缺「音质/相似度」指标 |
| **监控与日志** | 日志页、错误日志、诊断包、性能画像、存储报告 | 状态与日志页 + 环形缓冲（800 条）+ 自检 + 任务日志 | 持平 | 我们缺「诊断包一键导出」与性能画像 |
| **部署形态** | Electron 三平台 + Docker + Store + 移动端 preview + 一键安装脚本 | Windows 单机（FastAPI 自签 HTTPS + tkinter 引导器 + `QWENTTS-Setup.exe`） + Docker 云端 | 弱于 | 我们**故意**窄：单机 Windows 交付 |
| **安全鉴权** | 默认 loopback 免鉴权；PIN/API key/可信网络；管理员会话；CORS/CSRF 规则 | HTTPS 自签 + `cloud.api_key`（`X-API-Key`）+ 路径穿越白名单 + 上传白名单 | 持平 | 默认值取向不同：对方偏「易用」，我们偏「默认收紧」 |
| **可扩展性** | 引擎插件目录 + Plugin SDK + 内置 skills（`npx skills add`） | 引擎抽象基类（新增引擎需改 `engine/__init__.py` 与 router）；无插件机制 | 弱于 | 我们的抽象够用，但扩展点未开成公开契约 |
| 安装与升级 | 一键脚本 + 自带更新通道（`docs/update-channels.md`），安装器保留设置/项目/模型 | `QWENTTS-Setup.exe` 引导器（选目录→体检→下模型→装依赖→起服务）；无在线升级 | 弱于 | 我们的引导器是「一次性安装」，缺版本检查与升级路径 |
| 本地化 i18n | 英/中/日 README + 界面 locale 一致性测试 | 界面中文为主，英文 README 仍在待办 | 弱于 | PROJECT_HISTORY §10 已列「英文版 README」 |
| 无障碍 | 未见专门说明（`[未核实]`） | 前端三档响应式 + 键盘可达（README §4 一行） | 持平 | 双方都未把无障碍作为卖点 |
| 存储与隐私 | 本地优先，分析需同意（可关）；存储报告与清理 | 数据全部落项目目录、不写系统盘；无遥测 | 持平 | 取向一致 |
| 错误诊断与恢复 | 失败分类、错误日志、诊断包、修复页、崩溃类别报告 | 错误码表（12 类）+ `hint` + 自检 + 日志页；无诊断包导出 | 弱于 | 我们的错误码设计其实更整齐，差「一键收集现场」 |
| CI / 测试规模 | ~385 个后端测试 + 前端测试 + CI workflow + 文档漂移检查 | 冒烟 22 项 + 全功能 32 项 + UI 截图检查 | 弱于 | 我们重端到端，缺边界单测 |
| 文档体系 | README×3 + docs/ 下 100+ 篇（含 ADR、install、engines、store） | docs/ 13 篇，覆盖部署/接口/设计/实测，索引清晰 | 持平 | 我们是「少而全」，对方是「多而细」 |
| 许可 | AGPL-3.0（商用须遵循 AGPL 条款） | 专有，保留所有权利；第三方与模型单列 | — | **不可直接复制对方代码**，只能借鉴设计 |

---

## 5. 我们缺失的功能（按「用户价值 × 实现难度」排序）

> 排序口径：先做「价值高 + 难度低」，再把「价值高但要动模型/架构」的放 P1。

### 5.1 P0

**① 文本规范化（数字/单位/型号/英文词/多音字边界）**
- 对方怎么做：独立文本规范化 + 数字转词（ML）+ 多语言校验，落点是「不支持就报错，不许乱读」（`docs/languages.md`；`tests/test_text_normalization.py`、`test_number_words_ml.py`、`backend/services/number_words_ml.py`）。
- 我们现状：`backend/app/textsplit.py` 只保证**不切断**数字/型号（PROJECT_HISTORY §5.5 踩坑 #9），但不做**读法**转换；`1.7B`、`4GB`、`2024-10-18`、`¥1,280` 全靠模型猜。
- 改动点：新增 `backend/app/textnorm.py`（数字/单位/日期/货币/缩写 → 中文读法，保留 ASCII 边界）→ 在 `worker.py` 合成前调用；`frontend/src/pages/Workbench.tsx` 加「原文 / 规范化后」切换预览。
- 工作量：**1-3 天** · 优先级 **P0**

**② OpenAI 兼容接口（`/v1/audio/speech`、`/v1/audio/transcriptions`）**
- 对方怎么做：独立 `openai_compat` 路由 + OpenAI SDK 契约测试 + Bearer key（`backend/api/routers/openai_compat.py`、`tests/test_openai_sdk_contract.py`、`docs/api-auth.md`）。
- 我们现状：`/v1/engine/synth` 是私有协议（docs/API.md §7），任何现成客户端都得改代码。
- 改动点：`backend/app/api/remote.py` 增 `/v1/audio/speech`（`input/voice/model/response_format/speed` → 转发到现有 synth）、`/v1/audio/transcriptions`（multipart → 现有 ASR）；复用 `X-API-Key` 鉴权；文档补 `docs/API.md §9`。
- 工作量：**1-3 天** · 优先级 **P0**

**③ 字幕与时间戳导出（SRT / VTT）**
- 对方怎么做：`karaoke_ass.py`、`fitted_subtitles.py`、`onset_align.py`，配音流水线直接产出字幕（`tests/test_srt_parser.py`、`test_webvtt_export_escaping.py`）。
- 我们现状：句级分块已有**每块文本 + 时长 + 序号**（`task.chunk` 事件，docs/API.md §6），但这些信息只进日志，不产物化。
- 改动点：`backend/app/worker.py` 收集 chunk 元数据 → 新增 `backend/app/subtitles.py` 输出 SRT/VTT → `POST /api/tasks/{id}/export?format=srt`；`frontend/src/pages/TasksPage.tsx` 下载菜单加两项。
- 工作量：**1-3 天**（非流式任务需按时长比例分配，属已知精度损失）· 优先级 **P0**

### 5.2 P1

**④ 响度归一化 + 静音裁剪 + 导出精度**
- 对方：`services/loudness.py`、「broadcast mastering」、导出精度三档（`docs/audio-quality.md`）。
- 我们：只有语速/音高 DSP（`backend/app/engine/local.py`），wav 固定模型采样率，mp3 靠 ffmpeg 默认参数。
- 改动点：`engine/local.py` 后处理链加 `ffmpeg loudnorm -16 LUFS` 与首尾静音裁剪；`config.py` 增 `outputs.loudness_lufs`、`outputs.trim_silence`、`outputs.bit_depth`；`frontend/src/components/ParamsPanel.tsx` 加开关。
- 工作量：**1-3 天** · **P1**

**⑤ 发音词典（自定义读法表）**
- 对方：`services/pronunciation.py` + `/api/pronunciation` 路由 + Unicode 匹配测试（含未命中即失效的回归测试）。
- 我们：无。改法：SQLite 新表 `pronunciations(term, reading, lang)`，`textnorm.py` 里做最长匹配替换；`VoicesPage.tsx` 加「读法」标签页。
- 工作量：**1-3 天** · **P1**

**⑥ 批量提交与项目（Batch Queue）**
- 对方：`routers/batch.py`、Batch Queue、Electron 批量文档（`docs/electron-batch.md`）。
- 我们：任务中心能批量**删**，不能批量**建**；一次只能一个文本。
- 改动点：`backend/app/api/tasks.py` 增 `POST /api/tasks/batch`（文本行→多任务）；`frontend/src/pages/Workbench.tsx` 支持多行/CSV 导入并显示各自进度。
- 工作量：**1-3 天** · **P1**

**⑦ MCP / CLI 集成**
- 对方：`/mcp` 挂载 + 8 个工具 + 按 agent 绑定音色（`docs/mcp.md`、`backend/mcp_server.py`）。
- 我们：无。改法：新增 `backend/app/mcp_server.py`，把 tts/clone/design/transcribe 包装成 MCP 工具挂到现有 FastAPI；再加 `scripts/qwentts_cli.py`。
- 工作量：**1-3 天** · **P1**

**⑧ 多说话人对白 / 有声书 cast**
- 对方：`audiobook.py`（章节、多角色 cast、封面、取消）、`stories`、persona。
- 我们：单音色单任务。需在任务模型上引入「段落 → 音色」映射与拼接策略。
- 改动点：`backend/app/db.py`（新表）+ `worker.py`（多段串行合成 + 交叉淡化拼接）+ 新页面 `frontend/src/pages/StudioPage.tsx`。
- 工作量：**1 周+** · **P1**

### 5.3 P2

**⑨ 可复现种子 / takes 多版本**：对方「设计保存即固定种子出样音」+ generation takes。我们 params 无 seed。改法：`engine/local.py` 传 `torch.manual_seed`，`worker.py` 记录 seed。**1-3 天**，P2。
**⑩ 音质评测（SIM-o/UTMOS）**：对方 `omnivoice/eval/*` + `docs/evaluation.md`。我们只有 ASR 回译。改法：在 `qc` 基础上加 speaker embedding 相似度。**1 周+**，P2。
**⑪ 说话人日志（Diarization）**：**已落地（v1.1.7）**。本地 faster-whisper + sherpa-onnx CAM++ 声纹 + sklearn 余弦层次聚类，工作台「音频转文字」有「区分说话人 / 最多 N 人」开关（任务级覆盖全局设置），任务中心可把 SPK1 改成真实姓名，字幕可带/不带 `[SPKn]` 前缀。多人会议仍受 4GB 显存与短片段限制，准确率以实测为准（单/双人素材已验证：单人不拆分，双人切换点 5.93s 对齐）。
**⑫ 目标时长规划（配音用）**：**部分落地（v1.1.8）**。新增 `target_duration_s`：生成后用保音高时间伸缩对齐目标时长（dsp.time_stretch），超出 0.5~2.0 倍范围再截断/补静音并标记 `stretch_clamped`；不改变引擎条件，属于「事后对齐」而非模型级时长条件。
**⑬ 缓存（段级/提示级）**：对方 `longform_resume`/段缓存/prompt 磁盘缓存。**1-3 天**，P2。
**⑭ 麦克风实时听写**：对方 sherpa-onnx 流式 + 悬浮窗 + 原生插入。我们需要浏览器采音 + VAD + 低延迟 ASR（faster-whisper 不是流式）→ **1 周+，P2**。

### 5.4 登记但不排期（价值/成本不划算或方向不符）

| 功能 | 对方实现来源 | 不排期理由 |
| --- | --- | --- |
| 15+ 引擎热插拔 | `backend/engines/*` + `docs/engines/*`（30 篇） | 维护成本 ≫ 收益，且破坏 4GB 确定性 |
| 视频配音全链路 | `services/dub_pipeline.py` 等 8 个模块 | 依赖 ffmpeg/翻译/对齐三套外部系统，法律风险高 |
| 说话人日志 | `services/diarization_*.py` | **已改为做**：CAM++ 只有 ~27MB，抽完即释放，4GB 卡可跑（见 §5.3 ⑪） |
| 人声分离、变声（RVC） | `services/rvc.py` 等 | 与「语音生成」主线关联弱 |
| 训练/微调 | `omnivoice/training/`、`examples/run_finetune.sh` | 4GB 显存不可能 |
| 移动端、Snap/Store 发布 | `mobile/`、`store/`、`docs/store-publishing/` | 单机 Windows 交付是我们的边界 |
| AI 水印 | `api/routers/watermark.py` | 有合规价值，但需先确认目标客户是否要求（**待产品决策**） |
| 电话/呼叫（Twilio/calls） | `routers/telephony_twilio.py`、`calls.py` | 需实时低延迟链路，与本地 9–25s 首包矛盾 |

---

## 6. 可借鉴的设计

1. **「引擎 = 目录 + 指南 + 契约测试」三件套**。对方每个引擎都有 `backend/engines/<name>/` + `docs/engines/<name>.md` + 专属测试。我们新增能力时只改代码不改文档，导致 `docs/` 与实现易漂移。→ 建议把 `backend/app/engine/base.py` 的抽象升级为「引擎清单 + 显式能力位（supports: clone/design/instruct/languages/sample_rate）」，前端据此禁用不可用选项（现在靠 if 判断）。
2. **技术参数翻译成用户语言**。对方把位深/步数/母带包装成 Standard / For editing / Original，默认值不打脸用户。我们的 `frontend/src/components/ParamsPanel.tsx` 直接暴露 `temperature/top_p/top_k/repetition_penalty`，对创作者不友好。→ 加「简单/高级」两档，简单档只留情绪、语速、导出。
3. **文档防漂移做成脚本**。对方 `scripts/check-docs-drift.py`、`docs/features.yaml`（能力单一事实源）。我们 `README.md` 的 F1–F8 对照表是手写的，改功能容易忘同步。→ 用一份 `docs/capabilities.yaml` 生成 README 表格与设置页开关。
4. **「测试名即规格」的可读测试**。对方 385 个 `tests/test_<行为>.py`，光看文件名就知道边界（`test_clone_ref_silence_ladder.py`、`test_no_unspeakable_chunks_1330.py`）。我们 `scripts/smoke_test.py`（22 项）+ `full_test.py`（32 项）偏端到端，缺单点边界断言。→ 把踩过的坑（PROJECT_HISTORY §5）逐条固化成 `backend/tests/test_*.py`。
5. **把产品开成平台，而不是把界面做全**。对方 MCP + OpenAI 兼容 + `:3900` 语音平台 + 按 agent 绑定音色 —— 桌面 App 只是入口之一。我们所有能力都锁在 React 界面后，脚本用户只能读 API 文档手搓。→ 优先做 §5.1-② 与 §5.2-⑦。
6. **许可证证据链**。对方 `docs/licensing/model-inventory.md` + `scripts/check_model_licenses.py`，逐模型记录来源、证据链接、未解决项。我们只有 `THIRD_PARTY_NOTICES.md`，商用前无法自证。→ 建议做一张「模型/音色/依赖 → 许可 → 证据链接」的表。

---

## 7. 不建议照搬的

| 对方做法 | 为什么不照搬（结合我们的已知限制） |
| --- | --- |
| 15+ TTS 引擎可热插拔 | 我们的交付承诺是「4GB 单卡跑通 1.7B」（docs/BENCHMARK.md）。每多一个引擎就多一套显存预算、许可证、安装脚本与回归面；单机 Windows 更是依赖地狱（flash-attn 都装不上，PROJECT_HISTORY §9-2）。 |
| Electron 三平台 + Snap/Store/移动端 | 我们的形态是「Windows 单机 + `QWENTTS-Setup.exe` 引导器」。对方的 6 条发布流水线与其团队规模匹配，我们照搬=把维护成本乘 6。 |
| 视频配音全链路（转写→翻译→时长适配→混音） | 依赖 ffmpeg/yt-dlp/翻译服务，且版权与平台条款风险高。我们已有「音频转文字 + 克隆」，可先做「字幕导出」这一小块（§5.1-③）。 |
| 646 语言承诺 | 我们的模型是 Qwen3-TTS，实际支持面有限（README F5：中英日韩 + 方言）。照抄数字会造成**虚假宣传**，且用户一旦发现不支持就是信任崩塌。 |
| 默认 loopback 免鉴权 | 对方偏易用（本机无鉴权）。我们默认 HTTPS + 云端强制 `X-API-Key`，更符合「专有软件 + 商用交付」的定位，不应为了对齐而放松。 |
| 训练/微调管线（`omnivoice/training`、finetune 脚本） | 4GB 显存连推理都要靠显存超额（峰值 4049MB），训练无从谈起。 |
| 直接复制其代码 | 对方 **AGPL-3.0**，我们是**专有许可**（LICENSE）。AGPL 的传染性会污染整个产品。**只借鉴设计与接口形态，不复制实现**。 |
| 并发执行/GPU 池队列 | 单卡 4GB 串行是正确解（`worker.py` 单工作线程）。并发只会让每任务都 OOM。 |

---

## 8. 结论与建议路线

### P0（两周内，三件事）

| # | 事项 | 理由 | 落地文件 |
| --- | --- | --- | --- |
| 1 | 文本规范化 + 发音词典（合并做） | 直接决定「读得对不对」，是内容质量天花板 | 新增 `backend/app/textnorm.py`、`pronunciations` 表；改 `backend/app/worker.py`、`frontend/src/pages/Workbench.tsx` |
| 2 | OpenAI 兼容 `/v1/audio/speech`、`/v1/audio/transcriptions` | 把已有能力零成本接入现成生态（OpenAI SDK、LangChain、n8n） | `backend/app/api/remote.py` + `docs/API.md` |
| 3 | SRT/VTT 字幕导出 | 现有 chunk 元数据已够，投入产出比最高 | 新增 `backend/app/subtitles.py`；改 `worker.py`、`TasksPage.tsx` |

### P1（一个月内）

- 响度归一化（-16 LUFS）+ 首尾静音裁剪 + 位深选择（`engine/local.py`、`config.py`、`ParamsPanel.tsx`）
- 批量提交（多行文本/CSV → 任务组）（`api/tasks.py`、`Workbench.tsx`）
- MCP Server + `scripts/qwentts_cli.py`（`backend/app/mcp_server.py`）
- 多说话人对白（有声书最小可用版）（`db.py`、`worker.py`、新页面）
- 可复现 seed + takes 多版本（`engine/local.py`）

### P2

- 音质评测（SIM-o 说话人相似度，接现有 `qc`）
- 说话人日志（Diarization）
- 段级缓存与断点续跑
- 麦克风实时听写（需先评估 faster-whisper 非流式的延迟下限）
- 模型下载 UI 化（PROJECT_HISTORY 待办已列）

### 明确不建议做

1. **多引擎热插拔**（15+ 引擎）——违背「4GB 单卡确定性交付」的核心卖点；
2. **视频配音全链路**——依赖重、法律风险高，先做字幕导出即可覆盖 80% 需求；
3. **训练/微调**——4GB 显存不可能；
4. **移动端与多平台发布**——单机 Windows 交付是我们的边界；
5. **盲目对齐「646 语言」数字口径**——只承诺实测支持的语言，宁少勿虚；
6. **照抄 AGPL 代码**——专有许可下不可行。

---

## 9. 本次调研用到的链接

- https://github.com/debpalash/VoiceStudio （主对象）
- https://api.github.com/repos/debpalash/VoiceStudio （Star/fork/许可/时间戳）
- https://api.github.com/repos/debpalash/VoiceStudio/git/trees/main?recursive=1 （目录树，3,835 项）
- https://raw.githubusercontent.com/debpalash/VoiceStudio/HEAD/README.md （经 `contents` API 读取）
- https://github.com/debpalash/VoiceStudio/blob/main/docs/feature-catalog.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/languages.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/mcp.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/speech-platform.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/api-auth.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/audio-quality.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/generation-parameters.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/voice-design.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/evaluation.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/performance.md
- https://github.com/debpalash/VoiceStudio/blob/main/docs/STRUCTURE.md
- https://github.com/debpalash/VoiceStudio/tree/main/docs/engines
- https://github.com/debpalash/VoiceStudio/tree/main/backend/services
- https://github.com/debpalash/VoiceStudio/tree/main/backend/api/routers
- https://github.com/bayshier/voicestudio-zh
- https://github.com/mjlzz/voice-studio
- https://github.com/sirgawain0x/VoiceStudio （fork 示例）
- 本仓库：`README.md`、`docs/PROJECT_HISTORY.md`、`docs/CONFIG.md`、`docs/API.md`、`docs/BENCHMARK.md`、`docs/README.md`

---

## 10. 不确定项（未核实清单）

| 项 | 状态 |
| --- | --- |
| VoiceStudio 的 Star 数口径 | GitHub API 返回 75,323；第三方博客称 21K。以 API 快照为准，但**该数值会持续变动** |
| 各引擎的实际可用性与质量 | 仅从 `docs/feature-catalog.md` 与目录结构确认**存在**，未逐一安装验证 |
| TTS 流式实现细节（`tts_stream.py`） | 文件存在；是否逐帧流式、首包延迟**未核实** |
| 「Vocal Isolation」人声分离 | README/feature-catalog 有列出，未定位到实现文件 → **未核实** |
| 说话人日志准确率、配音音画对齐精度 | 只有测试文件名与文档标题，无实测数据 → **未核实** |
| 插件 API 稳定性、marketplace/community 是否可用 | 目录存在；是否已上线/可用**未核实** |
| 移动端 iOS/Android | README 标注 "preview"，实际可用性**未核实** |
| SSML 支持程度 | 只有 `tests/test_ssml_lite.py` 表明是「lite」子集，覆盖范围**未核实** |
| OpenAI 兼容接口的具体端点和参数映射 | 只见路由文件名与 SDK 契约测试，未读实现 → 细节**未核实** |
| 与我们能力的对应关系 | 「我们」一侧全部依据本仓库文档；若代码与文档不一致，以代码为准，本报告未逐行校验我们自己的全部实现 |
| 对方 GitHub issue 摘要 | 仅取到 `open_issues_count = 247`，未逐条阅读 issue 内容 |

---

## 11. 附录：我们当前能力基线（自查表）

> 用于核对「对比表」里「我们」一列没有夸大也没有漏项。全部依据本仓库文档与文件存在性。

| 能力 | 实现落点 | 状态 |
| --- | --- | --- |
| 文本转语音（内置音色） | `POST /api/tts` · `backend/app/engine/local.py` | 有 · 实测 RTF 0.206（1.7B/4GB） |
| 自然语言指令控制 | `instruct` 字段 → CustomVoice | 有 |
| 零样本声音克隆（3–10s 参考） | `POST /api/clone` · Base 模型 · `ref_text` 缺失走 x_vector 模式 | 有 · 实测首包 25.1s |
| 音色设计 | `POST /api/design` · VoiceDesign 模型 | 有 |
| 音频转文字 | `POST /api/transcribe` · `engine/asr.py`（faster-whisper 本地 / 云端） | 有 · tiny 会听错，默认 small |
| 句级分块流式 + 预缓冲播放 | `worker.py` 分块 · `/ws/tasks/{id}` · `frontend/src/lib` Web Audio | 有 · 默认关闭 |
| 语速 / 音高 | 合成后 DSP（`engine/local.py`） | 有 · 极端值有音质损失 |
| 采样参数 | temperature / top_p / top_k / repetition_penalty / steps | 有 |
| 内置音色库 | 9 个精选 + 动态说话人列表（`api/voices.py`） | 有 |
| 自定义音色管理 | 新建 / 重命名 / 删除（`POST/PATCH/DELETE /api/voices`） | 有 |
| 任务中心 | 列表 / 详情 / 日志 / 取消 / 重试 / 批量删除 / wav+mp3 下载（`TasksPage.tsx`） | 有 |
| 实时推送不轮询 | `/ws/events`、`/ws/tasks/{id}` | 有 |
| 三后端 + 混合路由 + 强制后端 | `engine/router.py`、`force_backend`、`hybrid_long_text_chars` | 有 |
| SSH 隧道 | `backend/app/tunnel.py`（paramiko） | 有 |
| 云端就绪探测与重试 | `engine/cloud.py`（`readiness_timeout_s`、`retries`、退避） | 有 |
| 空闲停机 | `cloud.idle_shutdown_enabled`（默认关）+ `/api/system/shutdown` | 有 |
| 模型预热 / 卸载 | `/api/system/preload`、`/unload` + 设置页 | 有 |
| 显存自适应（4GB 跑 1.7B） | `engine/vram.py`（`allow_vram_overcommit`）+ `local.py` | 有 · 峰值 4049MB |
| ASR 回译质检 | `worker.py::_run_quality_check`（默认关，阈值 0.75） | 有 |
| 设置热改 + 自动保存 | `config.py` `RUNTIME_MUTABLE` + `SettingsPage.tsx` 防抖 | 有 |
| 启动自检 | `backend/app/selfcheck.py` · `--selfcheck` | 有 |
| Windows 安装引导器 | `launcher/qwentts_launcher.py` → `QWENTTS-Setup.exe` | 有 · 自检 11/11 |
| 文档与许可 | docs/ 13 篇 + `LICENSE`（专有）+ `THIRD_PARTY_NOTICES.md` | 有 |

**基线结论**：在「语音生成四件套 + 单机可交付 + 4GB 可用」这条主线上我们并不薄弱；缺口集中在**文本层（规范化/词典/SSML）、产出层（字幕/响度/位深）、平台层（OpenAI 兼容/MCP/CLI）、内容层（多角色/批量）**四块。
