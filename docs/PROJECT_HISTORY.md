# 项目开发轨迹与决策记录（人类可读的“项目记忆”）

> 用途：压缩上下文/换会话后，读这一篇即可恢复全部关键背景、决策与踩坑结论。
> 最后更新：2026-10-18 · 版权：© 2026 xiyihang（保留所有权利，见 [LICENSE](../LICENSE)）

---

## 1. 项目是什么

按 `qwen3tts-dev-prompt.md` 实现的**本地 + 云 GPU 语音系统**：单机跑 Qwen3-TTS（1.7B），界面里完成
语音生成 / 声音克隆 / 音色设计 / 音频转文字，支持本地、云端、混合三种后端，全部数据落在项目目录内。

- 前端：React 18 + Vite 5 + TypeScript，5 个页面（工作台 / 音色库 / 任务中心 / 设置 / 状态与日志）
- 后端：FastAPI + uvicorn（HTTPS 自签）+ SQLite + 单工作线程任务队列 + WebSocket 推送（**任务状态不轮询**）
- 引擎：`qwen-tts 0.1.1`（Base / CustomVoice / VoiceDesign）+ 句级分块流式 + DSP（语速/音高）
- 转写：faster-whisper（本地）或云端 ASR
- 云连接：直连 HTTPS / SSH 隧道（paramiko）/ 三种模式切换 + 混合路由 + 就绪探测 + 空闲停机（默认关闭）

## 2. 硬约束（不可违背）

1. **本地必须用 1.7B**（Base/CustomVoice/VoiceDesign 三件套），0.6B 只作显式开启的回退（默认关闭）；
2. **所有产物与数据不落 C 盘**：`models/`、`data/`、`.cache/` 全在 `E:\trae_projects\QWENTTS` 下；
3. 模型下载：用户先前要求“只给大陆可访问链接”，后来改为“由助手下载”（已完成并 `--verify` 通过）；
4. 回答风格：中文、简洁、给结论与可直接执行的命令，不空谈。

## 3. 环境事实（实测）

| 项 | 值 |
| --- | --- |
| OS / Python | Windows 11 / Python 3.10.8 |
| GPU | RTX 3050 Laptop **4GB**（驱动支持 cu121），桌面主显示走核显 |
| 内存 / CPU | 16GB / 20 逻辑核 |
| torch / transformers | 2.4.1+cu121 / 4.57.3 |
| qwen-tts | 0.1.1 |
| Node | 24（仅构建前端用） |
| ffmpeg | 已装（mp3 导出可用） |
| 网络 | ModelScope 可达；HuggingFace 不可达（用 hf-mirror / ghproxy） |
| 模型 | 1.7B 三件套（各约 4.2GB）+ 0.6B 两件套 + faster-whisper tiny/small |

## 4. 关键决策轨迹（按时间）

| # | 问题 / 需求 | 决策 | 结果 |
| --- | --- | --- | --- |
| 1 | 需求文档要“全部功能” | 先落地后端骨架+引擎抽象+任务队列，再做前端 | 22 项冒烟通过 |
| 2 | 1.7B 在 4GB 显卡上被预检拒绝 | 起初认为“放不下”，改 CPU 模式验证 | 功能全通但 RTF≈0.04，太慢 |
| 3 | 用户质问“参考项目 4GB 也能用且不慢” | **改用参考项目策略：fp16 + `device_map="cuda"` + eager + 低保留显存 + 允许显存超额** | **1.7B 成功在 4GB 上跑：RTF 0.08–0.21** |
| 4 | 设置页切 CPU 后切页又变回 GPU | 原来没有自动保存 | 加 700ms 防抖自动保存 + dirty 守卫 |
| 5 | “全部卸载”后显存仍占一半 | 启动预热在进程重启时又加载 | 卸载时同时关 `warmup_on_start`；实测确认非泄漏 |
| 6 | 浏览器下载被自签证书拦截 | `<a download>` 直接跳转被 Chrome 拦 | 改 fetch + Blob 下载 |
| 7 | 任务卡在 95% 七分钟 | 模型不吐 EOS，无限生成 | 按字数估算 `max_new_tokens` 上限 + 心跳进度 |
| 8 | 克隆报 `ref_text is required` | ICL 模式要求参考文本 | 无参考文本时自动 `x_vector_only_mode=True` |
| 9 | 用户反馈“声音和文字对不上、都不全” | **根因：分块按字数硬切，把 `1.7B`/`4GB`/`GPT-4o` 切断** | 改为只在标点/词边界切（`_is_safe_cut` + `split_first_chunk`），ASR 回译验证内容完整 |
| 10 | ASR 任务永远失败 | ① 任务行没保存音频资产 id ② 语言传 `chinese` 而非 `zh` ③ 失败后回落未配置的云端掩盖真错 | 三处全修 |
| 11 | 用户要求“空闲停机默认关、可设置开” | 默认 `false`，设置页可开，Docs 同步 | 已生效 |
| 12 | 用户要求“流式默认关”“质检开关默认关” | `defaults.streaming=false`、`qc.enabled=false` | 已生效 |
| 13 | 前端要“更有设计感” | 加载 `frontend-design` skill，定调「广播台控制台」 | 等宽数字、章节编号 01/02、细网格底纹、错峰入场 |
| 14 | 要署名与版权 | 全局页脚 + `LICENSE`（专有，保留所有权利）+ `THIRD_PARTY_NOTICES.md` | 已完成 |
| 15 | 要“方便别人部署”的文档 | 补 QUICKSTART / CONFIG / TROUBLESHOOTING / CLOUD_SETUP / CLOUD_PROVIDERS + docs 索引 + 2 份配置预设 | 已完成 |
| 17 | 对标 GitHub VoiceStudio 找差距 | 子 Agent 调研 → `docs/COMPETITIVE_VOICESTUDIO.md`（321 行，24 项对比） | 定出 P0×3 / P1×5 |
| 18 | **P0-1 文本规范化** | 新增 `textnorm.py`（279 行，纯标准库，23 条单测）；`tts.normalize` 可热改；分块前生效、幂等；质检用规范化文本比对 | 实测 `4GB→四 G B`、`¥99→九十九元`、`2026-10-18→二零二六年十月十八日` |
| 19 | **P0-2 OpenAI 兼容接口** | 新增 `api/openai.py`（517 行）：`/v1/audio/speech`、`/v1/audio/transcriptions`、`/v1/audio/voices`、`/v1/models`；OpenAI 形状错误体；Bearer/X-API-Key 鉴权 | 任意 OpenAI SDK 可直接接入；`stream=true` 明确 400 |
| 21 | 用户要「多模型 + 安装器/前端可增删 + 设置页引擎卡片」 | 新建适配器体系（`adapters/base.py` 接口 + `registry.py` 注册表 + `manager.py` 安装服务 + `api/engines.py` 5 接口 + `engines_cli.py`）；配置 `engine.active` 热切换 | 前端引擎卡片已上线（WS 进度） |
| 22 | 先做了 piper/kokoro/melotts 轻量引擎 → 用户明确否决（要 CosyVoice 级别） | **下架三个轻量引擎**（删适配器 + 删 `models/piper-zh` + 卸载 piper-tts/kokoro/misaki） | 现仅注册 `qwen3-tts-1.7b` + `voxcpm1.5` |
| 23 | 高质量模型选型 | 调研 `docs/ENGINE_RESEARCH.md`：国内可达 CosyVoice3 / CosyVoice2 / CosyVoice-300M / VoxCPM1.5 / IndexTTS-2；MegaTTS3 无 ModelScope 仓、F5/Spark/fish/MaskGCT 为 CC-BY-NC 非商用 → 排除 | 用户拍板先接 **VoxCPM1.5**（pip 直装、1.82GB、零 GitHub 依赖） |
| 24 | 干跑预览 | 新增 `engines_cli install <id> --dry-run` 与 `GET /api/engines/{id}/plan` | 实测 voxcpm1.5：6 文件 / 1862.7MB，不下载不落盘 |
| 25 | 用户手动 `pip install voxcpm` 把主环境 torch 换成 2.14.1+cpu（CUDA 失效） | 立即恢复 `torch/torchaudio==2.4.1+cu121`、`torchvision==0.19.1+cu121`；随后建立**版本钉死 + 引擎隔离**双保险 | 主环境恢复到 `2.4.1+cu121`、CUDA 可用 |
| 26 | 要求「所有命令不得拉高版本」 | 新增 `requirements.pins.txt`（22 项 ==）、`docs/VERSION_POLICY.md`、6 个 `locks/<engine_id>.txt`（129 条 ==）；安装改 `--no-deps 主包==ver` + `-c constraints -r locks`；装后逐包校验，不一致抛 `VERSION_MISMATCH` | 全仓无裸 pip 命令；`verify qwen3-tts-1.7b` 通过 |
| 27 | 要求「引擎装依赖别破坏主环境」 | 每引擎独立 venv（`models/<id>/.venv`，qwen 走主环境 inprocess）+ 子进程 `runner.py` 推理桥；卸载连 venv 一起删 | venv 内无 torch；主环境 torch 不变；缺依赖时 runner 返回可读错误 |
| 28 | 复查遗留项 | ① 桥接层补 5 条单测（backend/tests/test_runner_bridge.py），**当场发现真 bug**：
unner.write_wav 写 .tmp 后缀导致 soundfile 无法推断格式 → **所有隔离引擎合成必失败**，已改为显式 ormat="WAV" + 失败清理；② 残留 torch dist-info 复核为已清空；③ ASR 安装提示已指向 
equirements.pins.txt；④ launcher 裸 torch 命令钉版本 | 5 项后端测试全绿（5+26+23+8+17） |
| 29 | 按 ui-craft（dense-dashboard, CRAFT7/MOTION3/DENSITY9）改前端交互 | 两批完成：① 工作台+任务中心+全局命令面板（Ctrl+K、j/k 行导航、行内操作、筛选写 URL、批量重试、骨架行、错误码徽章、Ctrl+Enter、记忆、失败重试；**按用户要求不做生成后自动滚动**）② 设置页锚点导航+搜索+云端测试连接+字段校验+ConfirmDialog、状态页自动刷新+日志 chips+复制、音色库一屏克隆+搜索排序。顺带修掉「设置页每次自动保存都整页重建」的真 bug | 前端 tsc 0 错误；Playwright 22/22 + 49/49；构建 JS 328.69 kB/CSS 53.19 kB |
| 20 | **P0-3 字幕导出** | worker 记录时间轴 → `data/outputs/<id>/chunks.json`；`GET /api/tasks/{id}/subtitles?format=srt\|vtt\|ass`；前端任务详情加下载 | 8 条单测；实测 SRT 时间码正确 |
| 16 | 要 Windows 安装引导器 | 委派子 Agent 做 `launcher/`（tkinter + PyInstaller），只提示用户自己装前置 | **已完成**：`launcher/dist/QWENTTS-Setup.exe`（8.62MB），自检 11/11 |

## 5. 关键技术结论（踩过的坑，别再踩）

### 5.1 4GB 显存跑 1.7B（最重要的突破）
- 权重 fp16 ≈ 3.8–3.9GB；4GB 卡靠 **Windows WDDM 共享内存小幅超额**（实测峰值 4049MB）可整模型加载；
- 必需配置：`dtype: float16`、`device_map="cuda"`、`attn_implementation: eager`、`low_cpu_mem_usage=True`、
  `vram_reserve_gb: 0.1`、`allow_vram_overcommit: true`；
- **前提**：独显不能被桌面/浏览器占用（本机主显示走核显，独显空闲约 148MB）；
- 加载判定必须按「总显存 × 1.05 − 小保留」而非「空闲显存 − 1GB」，否则会误判拒绝（`vram.can_attempt`）。

### 5.2 不要用 accelerate offload
`local.low_vram.enabled=true` 能加载，但生成时报 `Tensor.item() cannot be called on meta tensors`
（qwen-tts 0.1.1 的 generate 循环不兼容）→ 默认关闭，错误映射为可读 `VRAM_INSUFFICIENT`。

### 5.3 CPU 模式要 float32
CPU 上 fp16/bf16 都是软件模拟（更慢，bf16 实测 RTF≈0.009，float32≈0.04）→ CPU 模式强制
`float32 + eager + low_cpu_mem_usage`，并移除“内存紧张自动降 bf16”的逻辑。

### 5.4 fp16 数值风险
偶发 `probability tensor contains inf/nan` → 把 `local.dtype` 改 `bfloat16` 即可（同尺寸、略慢）。

### 5.5 分块与流式
- 流式是**句级分块**（qwen-tts 0.1.1 无逐帧流式）；
- 切分红线：只在标点/词边界切，**绝不切断数字/型号/英文词**；
- 拼接：18ms 交叉淡化 + 60ms 句间停顿；播放端 1.2s 预缓冲；
- 默认**非流式**（整段生成，语气最连贯）；需要低首包时在高级参数打开。

### 5.6 模型加载/资源
- 显存或内存紧张时先卸载其它已加载模型再加载（避免互相 OOM）；
- CPU 模式加载前校验内存（约 5–6GB/模型），不够时最多等 20 秒让系统回收。

### 5.7 ASR
- 语言标识要映射：`chinese→zh / english→en …`，未知值走自动识别（不要传非法代码）；
- `tiny` 会听错（4GB→四季），对外默认 `small`；
- 未配置云端时，本地 ASR 失败**不要**回落云端（否则真实错误被 `CLOUD_UNREACHABLE` 覆盖）。

### 5.8 前端工程细节
- 下载必须 fetch+Blob（Chrome 拦自签证书下的导航式下载）；
- 设置页自动保存（800ms 防抖）+ 切页不丢；
- `index.html` 加 `Cache-Control: no-store`，避免重建后白屏；
- Web Audio 需用户手势才能播放（预缓冲后在状态机里等 `play()` 成功）。

### 5.9 云端显卡兼容
Blackwell（5090/5080/5070/PRO 6000/5000/6000D，sm_120）**不支持** `torch 2.4.x+cu121`，需 `torch>=2.7 + cu128`。

## 6. 实测数据（可复现口径）

### 6.1 1.7B 在 RTX 3050 4GB（fp16 + 超额）

| 能力 | 模型 | 首包 | 耗时 | 音频 | RTF | 显存 |
| --- | --- | --- | --- | --- | --- | --- |
| 语音生成 | 1.7B-CustomVoice | 9.1s | 25.3s | 5.21s | 0.206 | 3991MB |
| 声音克隆 | 1.7B-Base | 25.1s | 37.1s | 4.18s | 0.113 | 4049MB |
| 音色设计 | 1.7B-VoiceDesign | 22.0s | 22.0s | 1.68s | 0.076 | 3996MB |
| 长文本流式（7 句） | 1.7B-CustomVoice | 38.7s | — | 50.0s | — | ≈4.0GB |

### 6.2 CPU 对照（1.7B，float32）

| 能力 | 音频 | RTF |
| --- | --- | --- |
| 语音生成 | 1.36s | 0.044 |
| 音色设计 | 1.36s | 0.041 |
| 声音克隆 | 1.36s | 0.040 |

### 6.3 流式 vs 非流式（4 句 /≈67 字）

| 方式 | 首包 | 总耗时 | 音频 |
| --- | --- | --- | --- |
| 流式（28 字/块） | 17.4s | 126.9s | 19.03s |
| 非流式 | 101.9s | 102.0s | 16.64s |

### 6.4 验收
- `scripts/smoke_test.py`：22 项通过；
- `scripts/full_test.py`：**32 项全通过（1.7B，含 ASR 回译内容校验）**。

## 7. 当前默认配置（改动后）

```yaml
run.mode: hybrid
local.device: cuda:0
local.dtype: float16
local.attn_implementation: eager
local.vram_reserve_gb: 0.1
local.allow_vram_overcommit: true
local.warmup_on_start: true
local.warmup_kinds: [custom_voice]
local.allow_small_fallback: false
local.low_vram.enabled: false
asr.local.model_size: small
tts.normalize: true                # 文本规范化（数字/单位/型号/日期→中文读法）
tts.normalize_lang: zh
defaults.streaming: false          # 非流式（整段）
defaults.chunk_chars: 28
qc.enabled: false                  # ASR 回译质检默认关
cloud.idle_shutdown_enabled: false # 空闲停机默认关
```

## 8. 文件地图（要改哪里）

| 路径 | 职责 / 注意点 |
| --- | --- |
| `backend/app/main.py` | 组装 FastAPI、静态托管、HTTPS、asyncio 噪声过滤 |
| `backend/app/config.py` | pydantic 配置 + 三层覆盖 + `RUNTIME_MUTABLE` 热改白名单（改配置项要同步这里） |
| `backend/app/engine/local.py` | 1.7B 加载/推理/分块/预算/DSP；**显存策略都在这里** |
| `backend/app/engine/vram.py` | `can_attempt`（超额）/`can_fit`/`can_run_on_cpu`/估算 |
| `backend/app/engine/router.py` | 本地/云端/hybrid 决策与可切换错误码 |
| `backend/app/engine/cloud.py` | 云端调用、重试退避、就绪探测、占位地址快速失败 |
| `backend/app/engine/asr.py` | 语言映射、本地/云端 ASR、失败不误回落 |
| `backend/app/worker.py` | 任务队列、心跳、拼接、质检（`_run_quality_check`） |
| `backend/app/textnorm.py` | 文本规范化（幂等；**型号 token 要整块替换**，别让 `1.7B` 被拆碎） |
| `backend/app/api/openai.py` | OpenAI 兼容 4 端点（复用 state.router/voices/asr，不落库为任务） |
| `backend/app/api/tasks.py` | 含 `GET /api/tasks/{id}/subtitles`（SRT/VTT/ASS，task_id 白名单） |
| `backend/app/textsplit.py` | 分块（**不要在数字/英文词中间切**） |
| `backend/app/api/*.py` | tasks / voices / system / remote / ws |
| `frontend/src/styles/tokens.css` | 设计令牌（颜色/字体/间距/动效/氛围） |
| `frontend/src/pages/SettingsPage.tsx` | 自动保存 + 所有设置项（含质检、预热、显存策略） |
| `frontend/src/lib/download.ts` | fetch+Blob 下载（勿改回 `<a download>`） |
| `scripts/download_models.py` | 模型下载（并行/断点/校验/`--links`） |
| `scripts/full_test.py` | 32 项全功能验收（要求 1.7B） |
| `launcher/qwentts_launcher.py` | Windows 安装引导器（1411 行，tkinter；`--self-test` 可无 GUI 自检）；**不自动装 Python/Node/驱动，只提示** |

## 9. 已知限制（当前版本）

1. 4GB 卡上 1.7B 首包 9–25s，本机 RTF 0.08–0.21；要 ≤1.5s 首包必须云端 GPU；
2. `flash-attn` 在 Windows 难装，用 eager/SDPA；
3. 流式为句级分块，非逐帧；
4. 语速/音高是合成后 DSP，极端值有音质损失；
5. `local.low_vram` 不可用（meta tensor）；
6. 前端 `dist` 未纳入源码备份（需 Node 构建）；
7. 模型（12.6GB）不随源码分发，需按 `docs/MODELS.md` 下载；
8. 云平台 Blackwell 卡需换 torch cu128。

## 10. 待办 / 可选增强

- [x] `launcher/`（已完成）：tkinter 引导器（1411 行）+ PyInstaller 打包（产物 `launcher/dist/QWENTTS-Setup.exe` 8.62MB）+ `docs/LAUNCHER.md`
- [ ] 便携版发布包（含 `frontend/dist`，用户无需 Node）
- [x] 文本规范化 / OpenAI 兼容接口 / 字幕导出（P0 已完成，2026-10-18）
- [x] 多引擎框架 + VoxCPM1.5 注册（2026-10-19）
- [x] CosyVoice3 / CosyVoice2 / CosyVoice-300M / IndexTTS-2 适配器**已在 `registry.py` 注册**（2026-10-19 起；真机未跑通见下一项）
- [ ] 引擎卡片的「安装预览」按钮（后端已有 `/api/engines/{id}/plan`，前端待接）
- [x] 版本钉死 + 引擎 venv 隔离（2026-10-19，详见 docs/VERSION_POLICY.md 与 docs/ENGINE_ISOLATION.md）
- [x] 引擎卡片「安装预览」按钮（2026-10-19 完成：内联面板展示文件/体积/代码仓库/pip 依赖，只 HEAD 探测不下载）
- [ ] 模型下载 UI 化
- [ ] 真机跑通 CosyVoice 系（需装 5GB 权重；成功路径未实测）
- [ ] 清理残留 torch-*.dist-info（历史事故遗留，当前无影响）
- [ ] 模型下载 UI 化（后端下载接口 + 前端进度，白名单 URL + 断点续传）
- [ ] 按厂商细分的云部署附录（恒源云/阿里云安全组截图级）
- [ ] 英文版 README（对外交付）
- [ ] 源文件头部版权注释批量注入

## 11. 如何快速恢复上下文

1. 读本文件（决策与坑）→ 2. 读 `VERSION.json`（环境与入口）→ 3. 读 `docs/CONFIG.md`（当前默认值）
→ 4. 读 `docs/BENCHMARK.md`（性能口径）→ 5. 需要改动时看 `docs/DESIGN.md`（前端令牌）与 `docs/API.md`（契约）。

若服务状态异常：`cd backend && python -m app.main --selfcheck`，并看 `data/logs/app.log`。

## 12. 交付物清单

| 类别 | 内容 |
| --- | --- |
| 源码 | `backend/`（30 个 py）、`frontend/`（24 个文件）、`scripts/`（9 个）、`cloud/`（Dockerfile/compose） |
| 文档 | README + `docs/`（QUICKSTART、CONFIG、MODELS、CLOUD_SETUP、CLOUD_PROVIDERS、DEPLOY、TROUBLESHOOTING、API、DESIGN、BENCHMARK、索引、本文件） |
| 许可 | `LICENSE`（专有）、`THIRD_PARTY_NOTICES.md` |
| 配置 | `config.example.yaml` + 本地 GPU / 仅云端 两份预设 |
| 安装器 | `launcher/dist/QWENTTS-Setup.exe`（8.62MB，选目录→体检→下模型→装依赖→起服务） |
| 安装 | `start.bat`（双击）、`scripts/start.ps1`、`scripts/start.sh`、`requirements.lock.txt` |
| 备份 | `E:\trae_projects\QWENTTS-backups\QWENTTS-src-<时间戳>.zip`（只保留最新一份） |

| 30 | 用户反馈控件不齐 / 留白 / 弹窗随页面滚 | 写 `docs/spec/UI-POLISH-1.md` 后实施：`.field-row` 改顶部对齐、输入框固定 38px、音色卡固定行高、工作台右栏补「最近任务+快捷键」、`Modal` 改 portal + 锁滚动、窄屏补 `min-width:0` | 同行 y 一致、卡片等高 176px、两栏差 171px、弹窗滚动 0/2400 时 top 恒 311、窄屏溢出 0 |
| 31 | 设置页导航要悬浮、任务页要贴底 | 导航 `position: sticky` + 视口高 + 列表内滚；任务页 `page--fill` 显式高度 `100vh - 顶栏 - 8px`（注意 `.page` 的父级是 `<main>` 而非 `.main`） | 导航滚动时恒可见可点；分页底 891/900，页面滚动 0 |
| 32 | 品牌改名 | `Qwen3-TTS` → **声匠 · VoiceSmith**（界面/imdex.html/安装器/文档），引擎名与 `qwen3-tts-1.7b` 保留 | 左上角「声匠 / VOICESMITH」，左下角新增版权三行 |
| 33 | 引擎安装失败（三镜像全挂） | 定位：①模型早已下好，失败在依赖装 venv；②锁文件中文注释 → pip GBK 解码崩；③清华无 voxcpm；④voxcpm 要 torch>=2.5。修复：锁文件纯 ASCII、pip 强制 UTF8、`_ascii_copy` 净化、voxcpm 锁钉 venv 专用 `torch==2.5.1+cu121`、错误分类（冲突立即中止）、pip 缓存挪 `.cache/pip` | 新增 `backend/tests/test_installer_guard.py`（8 条）守护这些规则 |
| 34 | 交付与备份 | 安装器移除「下载模型」；备份包含打包好的 `QWENTTS-Setup.exe`；外部设计参考包（`.cache/`）**不随包分发** | 备份 8.9MB / 155 文件（含 exe） |
| 35 | 云端 GPU 实机接入（AutoDL 无卡准备 → 克隆有卡机） | 无卡模式先备环境：POSIX 重打包源码上传 + 清华源装依赖 + ModelScope 下 1.7B 三件套（17MB/s，13GB）；有卡后修 5 个真实坑：①镜像旧 `_rust*.so` 顶掉新 cryptography → paramiko 崩（必须删目录重装）②镜像 PATH 无 python → 启动脚本显式 export PATH ③Windows zip 反斜杠 → 用 Python zipfile 写 POSIX 路径 ④zip 丢执行位 → chmod +x ⑤克隆机端口/密码变 → 隧道配置要重写并重启本地后端才生效 | 云端 RTX 3090/23.56GB，CustomVoice 占 3981MB；端到端任务 `backend=cloud`，12s 出音 wav 241,964 字节；补装 ffmpeg 4.4.2 后自检无 WARN |
| 36 | 外部机器（Win10 + GTX 950 2GB）实测反馈 9 项缺陷 | P0-1 锁文件损坏（rollup 可选平台包缺 version）→ 重新生成锁文件 + 检测到 Invalid Version 自动 `--no-package-lock` 重试；P0-2 npm install 失败仍继续 build → 分阶段阻断与分别报错；P0-3 启动未校验依赖 → 启动前探测 torch/qwen_tts 并阻断，修正 `退出码 None`；P0-4 体检缺后端依赖 → 新增关键项（torch/qwen-tts/CUDA）；P1-1 显存可行性 → 不足 6GB 降级 WARN 并引导云端；P2-1 子进程 GBK 乱码 → 二进制读取 + UTF-8/GBK 双解码；P2-2 pydantic 保护命名空间警告 → ConfigDict(protected_namespaces=())；P2-3 启动体检自竞态 → 忙时延后重试；建议 4 发行预构建 dist → 构建步骤遇 dist 直接跳过 | 自检实测 11 通过 / 2 提醒 / 0 失败；npm ci 从零装 69 包 12s、构建 3.89s |
| 37 | 用户反馈「不装 Qwen 时很多功能用不了」+「使用按钮一直转圈」 | 根因①：`invalidate_info()` 清空缓存 → 切换后下一次 `/api/engines` 同步重算 6~7 个引擎（voxcpm 单引擎 46s）→ 请求超时 → 按钮不复位；改为标记过期 + 后台刷新，实测复位 0.26s、接口 0.016s。根因②：音色/能力/文案全按 Qwen 1.7B 写死；`/api/voices` 增加 engine 信息与不可用原因、`engine_voices`；工作台页签按引擎 kind_tags 门禁（0.6B 禁用音色设计）并自动落位；设置页标注「仅 Qwen3-TTS 生效」；路由显存文案用引擎名 | 规格见 `docs/spec/ENGINE-AGNOSTIC-1.md`，回归测试 `test_engine_agnostic.py` 5 条 |
| 38 | 切到 VoxCPM 后所有任务失败（PACKAGE_MISSING / reference_wav_path 报错） | 根因①`runner.py` 以脚本运行时 `sys.path[0]`=adapters 目录，`voxcpm.py` 适配器遮蔽了 venv 里的 `voxcpm` 包 → `ImportError: attempted relative import with no known parent package`；修复：runner 启动即把自己的目录移出 sys.path。根因②VoxCPM **1.5** 只支持 `prompt_wav_path+prompt_text`（`reference_wav_path` 是 V2 专属）；修复：`requires_ref_text=True` + worker 用 ASR 自动补参考文本 + 适配器兜底可读错误。同时新增「纯文本合成（不使用音色）」：API 接受 `voice_id=none`，界面在引擎无可用内置音色时提供该选项 | 实测 voxcpm 纯文本合成 completed（4.63s，RTF 0.071），界面卡片可选中并解锁提交 |
| 39 | 用户反馈：自定义音色布局乱、点了报错、声音特别小、关了流式仍显示流式 | ①固定 176px 行高导致卡片空白 → 改内容驱动 + 伪音色独立 .voice-plain；②内置音色喂给非 Qwen 引擎产出 -45.8dBFS 静音 → 新增 supports_builtin_speakers 并在 worker 直接报可读错误，前端自动改「纯文本合成」；③克隆参考文本为空 → ASR 转写失败时给明确指引（真因是 2s 参考音频无人声）；④关闭流式仍显示首包/生成中 → showStreamUi 分支 + 完成即隐藏 | 实测：纯文本合成峰值 -8.0dBFS，完成态无「生成中」，卡片高度 [153,177]，横向溢出 0 |
| 40 | 主界面一直抽搐 | 两个 effect 互相改写 voiceId 形成死循环：「音色已删除→回落内置音色」与「引擎不支持内置音色→改纯文本合成」每帧互相覆盖 → React 无限重渲染。修复：两个 effect 遇到 NO_VOICE_ID 直接返回、回落目标改为第一个可用音色、提示用 useRef 去重 | 实测 5 秒 DOM 变更 voxcpm 1 次 / qwen 0 次（修复前为数百次）|
| 41 | 音色库里堆了 6 个「冒烟克隆/能力测试」 | 是 smoke_test.py / full_test.py 自动造的音色，参考音频是脚本用 make_wav() 生成的 2 秒方波（实测只有 2 个不同采样值），不是人声 → ASR 转写为空、克隆失败/怪声。清理：删掉 6 个测试音色 + 9 个孤儿参考音频；并改脚本自清理（smoke_test 记录 /api/clone 创建的音色并在结尾删除，full_test 在 main 返回前兜底删除）| 清理后 内置 9 / 自定义 0 |
| 42 | 切到 0.6B 后任务报 UnboundLocalError / 加载错目录 / fp16 NaN | ①`router.local_feasible` 上次改文案时引用了未定义的 `adapter` → `UnboundLocalError`；修复：函数开头统一 `adapter = registry.get(engine_id)`。②切到 0.6B 仍加载 1.7B 目录：`/api/engines/{id}/select` 只改配置未 `apply_runtime_config`，且 router 用 `cfg.local.models` 判定；修复：select 后热应用配置 + router 按 `local.size` 选目录。③0.6B 在 float16 下采样 NaN → 该规格默认 bfloat16（1.7B 仍按用户配置） | 实测：0.6B 加载 `-0.6B-CustomVoice`、33s 出 4.32s 音频（peak -3.7 dBFS）；1.7B 回归 30s 出 2.24s 音频（peak -4.2 dBFS），均 completed |
| 43 | 用户要求「仔细检查一遍所有东西」（连续多次改 A 坏 B） | 写 `docs/spec/HARDENING-1.md` 并落地 `scripts/audit_all.py`（20 项：静态/接口/引擎矩阵/真实合成+音量/界面冒烟·抖动·溢出/数据清洁度）；首次全量审计抓出 1 个真 bug（选中已是当前的引擎时 `local.size` 不同步 → manager.select 提前 return），修复并补启动自愈；再体检 20/20 全绿 | 0.6B completed 3.12s peak 0.482、1.7B completed 1.12s peak 0.723；界面 0 JS 异常、3s DOM 变更 0 次、三档宽度溢出 0；深度探测 13 项全过 |
| 44 | 2GB 卡跑 0.6B 报「内部错误」（实为 OOM） | OOM 文本形态很多（CUDA out of memory / CUBLAS_STATUS_ALLOC_FAILED / cuDNN alloc / bad_alloc / not enough memory / 中文「显存不足」），原来只匹配 "out of memory"/"cuda oom"，其余落到 INTERNAL 只显示「内部错误」。修复：新增 errors.is_oom_text() 覆盖 13 种形态，wrap_internal() 与 local._map_error() 统一改用它；VRAM_INSUFFICIENT 文案去掉写死的 1.7B，改为「换 0.6B / 切云端 / CPU / 缩短单句」四条路；生成阶段消息带模型目录名 | 新增 backend/tests/test_oom_mapping.py（5 条）全过 |

| 45 | 用户要求「侧栏最下面加一个教程页，涵盖设置里所有功能」 | 写 `docs/spec/GUIDE-1.md` 后实现：新增路由 `guide`（`frontend/src/pages/GuidePage.tsx`），导航栏底部独立分组 `.rail__nav-bottom`（`margin-top:auto` + 分隔线，窄屏 `display:contents` 摊回底部条）；13 个分区＝「五分钟跑通」+ 设置 11 个分区 + 「常见错误速查」，字段名与设置页 `Field label` 逐字一致，含关键词搜索 / 命中高亮 / 滚动联动 / `#/guide?s=xxx` 深链；每张卡片「跳到设置」直落 `#/settings?s=<id>`；命令面板新增「教程」。顺带修好设置页深链的一次性滚动偏移（80ms + 700ms 二次校正：异步引擎卡片会推移布局） | 实测：分区 13、抽样 6 个设置字段全部可搜到、跳转后目标分区 top=76px、横向溢出 0、命令面板命中；`tsc --noEmit` 0 错、`vite build` 4.8s；`audit_all.py --skip-tts` 19/20（唯一失败 D3 因当时尚未打包） |

| 46 | 用户要求「前后端都加密、保留原包另出加密包」，随后改为「只用源码混淆（可跨 Python 版本）+ 不要文档」 | 两轮落地：①PyArmor 9.2.7（trial）把 `backend/app` 56 个模块编成「加载器 + 加密字节码」并自带 `pyarmor_runtime.pyd`，实测可用，但运行时链接 `python310.dll`、绑定解释器版本与平台，trial 版也不宜商用 → 按用户要求删除产物；②改为纯源码变换：删 docstring/注释 + 4363 个字符串常量做 zlib→逐文件随机密钥异或→base64，运行时由注入的 `_s()` 还原，生成后逐文件 `compile()` 校验，**不依赖任何运行时、跨版本跨平台**；前端只发 `dist` 再过 javascript-obfuscator（383KB→1.0MB）。踩坑：①异或与压缩顺序写反 → 启动 `zlib.error: incorrect header check`；②`api.deps.py` 的模块 docstring 在 future import 之前，导致 future import 不在首行 → SyntaxError；③顺带修掉 `engine/registry._save_cache()` 直接遍历 `_INFO_CACHE.items()` 的并发竞态（混淆让循环变慢后必现 `RuntimeError: dictionary changed size during iteration`），线上源码同步修复 | 产物 `QWENTTS-obf-v1.1.3-<ts>.zip` 8.94MB / 87 文件（无 docs、无 frontend/src）；干净目录解压启动：health ok、`/api/engines` 200、Traceback 0、教程页 19 项 PASS、6 个页面 0 JS 异常 |
| 47 | 用户要求实现「云主机一键接入 Agent」+「ASR 纳入引擎管理」（第三部分后续） | 第一部分：新增 `app/cloud_agent.py`（S0-S8 状态机：参数校验/SSH+指纹/环境体检/上传源码/建 venv 装依赖/启动服务/就绪探测/写本地配置/本地验收）与 `api/cloud_agent.py`（connect/jobs/job/resume/cancel/delete/models），进度走 `cloud-agent` 事件 topic；LLM 决策层走 OpenAI 兼容接口、默认 `deepseek-chat`，只用 `/models` 校验 + 失败诊断，不可用自动降级；密码只在内存、状态文件与日志全脱敏。第二部分：新增 `engine/adapters/asr_whisper.py` 5 个 ASR 引擎（tiny/base/small/medium/large-v3），`BaseAdapter.info()` 增加 `kind`，`/api/engines?kind=asr`、`manager._select_asr/_remove_asr`、`asr.local.engine_id`；转写不再隐式下载，未装报 `ASR_MODEL_MISSING`。前端：引擎管理加「语音合成/语音识别」页签，设置页云端分区加「一键接入云主机」内联向导，ASR 分区模型规模改只读并给跳转。文档：`docs/spec/CLOUD-AGENT-1.md`（含 §5A）、`docs/spec/ASR-ENGINE-1.md`、`docs/spec/PHASE-3-BACKLOG.md`、`docs/CLOUD_AGENT.md` | 实测：`/api/engines?kind=asr` 返回 5 条（tiny 149MB/small 927MB 判定 ready）、默认 12 条；agent 任务 S0 ok → S1 failed(SSH_UNREACHABLE) 并给出 3 条建议；落盘 JSON 无密码；`tsc`+`vite build` 通过（361KB/gzip 121KB）；设置页页签与接入按钮实测存在、切到语音识别可见 Whisper 卡片、0 JS 异常 |
| 48 | 用户要求「先只把云主机接入 Agent 和 ASR 纳入引擎管理做完整」 | 把 Agent 从「一次性线性执行」改成**可续做状态机**：`_steps` 改为 9 个单步方法 + 统一循环，续做时已 ok 的步骤标 `skipped`；S3 增加源码 sha256 比对、S4 增加 venv 依赖自检、S5 改为 SFTP 上传云端 config.yaml（避免嵌套引号）并复用存活 pid；S6 就绪判定升级为 `/v1/health` 的 `ready=true` **且** 一次真实 `POST /v1/tts` 成功，否则报 `CLOUD_NOT_READY` 并给出原因；S8 本地探活同样要求 `ready`。前端向导新增 skipped 状态与「续做（跳过已完成步骤）」（重新输入密码后从失败步继续）。ASR：`/api/engines?kind=asr` 分组、select/remove 生命周期与 `ENGINE_BUSY` 保护全部实测通过 | 实测：首次任务 `S0 ok / S1 failed(SSH_UNREACHABLE)` → 续做后 `S0 skipped / S1 failed`（未从头开始）；`select asr-whisper-small` → `active_asr=asr-whisper-small` 且 TTS active 不变；删除在用引擎返回 `ENGINE_BUSY`；`remove asr-whisper-base`（未安装）正常返回 freed_mb=0；`tsc+vite build` 通过（362KB/gzip 121KB） |
| 49 | 用户要求「AI 配置要能前端直接填」 | 新增 `GET/POST /api/cloud/agent/llm` 与前端向导内「AI 设置」面板（接口地址/模型/密钥 + 拉取模型列表）；密钥用 `secretstore.py` 的 Windows DPAPI（`CryptProtectData`）加密为 `api_key_enc` 落盘，`ConfigManager.reload` 时解密回内存；非 Windows 无系统级加密 → 不落盘，只注入进程环境变量并在界面标注。另：LLM 支持 `QWENTTS_AGENT_BASE_URL/MODEL/KEY` 环境变量覆盖 | 实测：POST 保存后 `runtime.json` 中无 `sk-test-123456` 明文（`api_key_enc` 长 328），界面掩码 `sk-t***56`，`encrypted_at_rest=true`；重建后 `GET /models` 能解出密钥并成功列出 `deepseek-chat` |
| 50 | 用户要求「云主机一键接入 Agent 全流程真正跑通」 | 真机逐轮排查修掉 12 个缺陷：①S5 在仓库根启动（No module named app）②复用旧 pid 导致跑旧代码 ③config.resolve_paths 忽略 storage.models_dir ④S6 引用未定义 llm ⑤就绪探测打 /v1/tts（实为 /v1/engine/synth，405）⑥(nohup&) 随通道关闭被杀 → setsid + </dev/null + pgrep ⑦S5 只验 3 秒存活 → 12s+8s 双检 ⑧端口检查依赖 ss（云端无）→ netstat 兜底 ⑨隧道每 5 秒刷日志 → 失败退避/15s 冷却/30s 限流 ⑩requirements.txt 未钉 torch → 装到 cu130/CPU 版 → 钉 2.4.1+cu121 + S4 断言 CUDA ⑪隧道配置改了不生效（对象未重建）→ apply_runtime_config 重建 + 启动热应用 + S7 回调 ⑫旧明文密码覆盖新密文 → 密文优先并清洗 另：S6 超时 900/600/300s → 60/120/45×2（最坏 3 分钟）；S6 实时进度写入步骤 detail；前端「换机器不沿用旧进度」（续做仅同目标可见）；新增 scripts/remote_config_patch.py + AI 工具 write_remote_config/restart_service；soxfree.py 把 torchaudio 后端切 soundfile，引擎 venv 自动补装 soundfile（多镜像回退） | 真机验证：region-42 2080Ti 冷启动（清空源码+venv，保留模型）S0–S8 全绿，S4 日志 `cuda True 2.4.1+cu121`，S8 探活 109ms；真实任务 completed / backend=cloud / 4.48s / rtf 0.463 |
| 51 | 用户要求「P1 三项全做，顺带修缺陷」 | ①5.3 生成参数：`TTSParams` 新增 `num_step/guidance_scale/denoise`（默认 None），`local.py` 按 `inspect.signature` 过滤后透传（不支持的引擎自动忽略，不报 TypeError），`DefaultsCfg` 同步新增且 `_merged_params` 改 `exclude_none=True`（否则 None 会污染任务 params 并在 int() 处崩），工作台参数面板加三个控件；②5.1 说话人区分：新增 `app/diarize.py`（sherpa-onnx 抽 CAM++ embedding + sklearn 余弦层次聚类），`asr.transcribe()` 返回 `segments` 并在开关打开时回填 `speaker`，新增 `AsrDiarizeCfg`（默认关闭）+ `asr.diarize` 进 RUNTIME_MUTABLE，新增引擎 `asr-speaker-campplus`（Apache-2.0，ModelScope 下载，自动挑选 .onnx 改名），设置页加「区分说话人/最多说话人」；③5.2 批量队列：新增 `app/batches.py` + `POST /api/tasks/batch`（单批 ≤50 条 / ≤5 万字，逐项 params 覆盖批次默认值）、`GET /api/tasks/batch/{id}`（按任务聚合进度）、`GET .../zip`（成功项 + manifest.json 记录失败）；**不引入父任务行**（避免 worker._restore 把父任务当普通任务跑） | 实测：`/api/engines?kind=asr` 6 条含 CAM++；`/api/settings` 出现 `asr.diarize`；批次创建返回 2 个 task_id，单项 params 正确带上 `num_step=32/guidance_scale=2.5/denoise=true/batch_id/batch_index`；批次进度聚合 `{failed:1, running:1}`；zip 200 且 manifest 正确记录失败项 |
| 52 | 用户要求「工作台加区分说话人的开启/关闭按钮」 | ①任务级开关：POST /api/tasks 的 params.diarize 与 params.max_speakers 原样落库，worker._run_asr 解析后传给 ASRRouter.transcribe(diarize, max_speakers)，LocalASREngine.transcribe 里「任务级覆盖全局 asr.diarize.enabled」（显式 false 也生效），_diarize 的 max_speakers 夹在 2-8；②工作台「音频转文字」页签加复选框「区分说话人」+「最多 N 人」下拉（localStorage 记忆 qwentts.workbench.asrDiarize/asrSpeakers），提交时写进 params，结果区在带 speaker 时按 SPK 徽标+起始时间逐行渲染（lib/format.ts 新增 speakerColor），任务中心详情同步加「说话人分段」；③任务日志新增「开始转写：区分说话人（最多 N 人）/不分说话人」，转写结束的 WS task.final 直接带 segments | 实测（3050 真机）：diarize=true 任务 5.2s 完成 -> segments=[SPK1 0.05-5.05, SPK2 6.05-11.05]；diarize=false 1.6s 完成 -> speaker=null；浏览器 E2E 截图确认勾选框、人数下拉、SPK1/SPK2 彩色分段、任务中心「说话人分段」均可见；tsc --noEmit 与 vite build 通过 |
| 53 | 用户要求「全都做全都修」（3 个实质缺陷 + 6 个次要 + 5 个未做功能） | ①max_speakers 真接入：diarize.py 新增 _merge_to_limit，阈值聚类后按「最多 N 人」反复合并质心最相似的两簇（不做强制 K 聚类，避免单人被拆）；②重试可覆盖：RetryRequest 新增 params（白名单 diarize/max_speakers/force_backend），工作台重试带上当前开关；③云端不再静默丢说话人：显式 diarize 时优先本地，云端返回带 diarize_skipped 并写任务日志；DIARIZE_*/CANCELED 不再被备用后端吞掉；④日志判据改为「None 跟随全局配置」，不再把默认关写成已开启；⑤设置页最多说话人改 2-8 与后端一致；⑥声纹阶段接 on_progress/取消（进度 0.55~0.95 + 每 5 段一条日志）；⑦_model_dir_with_bin 改为只看 root/refs/main/snapshots 并返回 None 触发明确报错；⑧批量：新增取消批次与只重试失败项接口，前端 BatchPanel 重写（进度轮询/取消/重试失败/清空，批次 id 存 localStorage）；⑨说话人改名 PATCH /tasks/{id}/speakers + SpeakerSegments 组件（chunks.json 记 speaker_id/speaker_aliases，可还原）；⑩字幕 speakers 查询开关；⑪后期处理链 backend/app/postprocess.py（频谱低切/高架 + 向量化软压缩 + 峰值归一化，config.post 全开关）+ SettingsPage 新分区；⑫target_duration_s（TTSParams/Defaults/ParamsPanel，保音高伸缩夹 0.5-2.0，实测写回 params._post）；⑬MCP Server backend/app/mcp_server.py（stdio JSON-RPC，4 个工具）；⑭文档：README v1.1.8 变更表、COMPETITIVE 说话人日志/时长控制结论更新、VERSION 1.1.8 + entrypoints.mcp_server | 验证：compileall/tsc/vite 全绿；实测 max_speakers=2 与 8 在同一素材上给出 2 个 vs 4 个说话人（threshold 调低放大人数），重试覆盖后新任务 params 带上新值，改名后 segments 与字幕同步变化，subtitle speakers=false 去掉前缀，批次取消后 status=canceled、retry-failed 生成新批次，target_duration_s=2.0 的任务 duration_sec=2.0 且 params._post 记录 stretch_factor，MCP initialize/tools/list/tools/call(voice_list) 正常返回 |
| 54 | 用户要求「修」只读复查提出的 3 个必修项 + 小项 | ①重试失败项丢参数：`batches.new_batch` 现在持久化逐项 `voice_id/asset_id/instruct/design/language/params`；`retry-failed` 完整复制这些字段（并先落盘再建任务，循环中途报错不再产生孤儿任务）；②ASR 取消语义：`asr.py` 在 faster-whisper 逐段产出时检查 `should_cancel`（不开说话人区分也能取消），`worker._fail` 对 `CANCELED` 落 `status=canceled` + `canceled=True`（原来错记成 failed）；③契约/文档：`types.ts` 的 `Task` 补 `speaker_aliases`；MCP 非 JSON 行回 `-32700 Parse error`、stdin/stdout 强制 UTF-8、请求超时改为 `QWENTTS_MCP_REQUEST_TIMEOUT`（默认 300s）、版本号取自 `app.__version__`；`API.md` 补 retry 覆盖/PATCH speakers/batch 四端点/字幕 speakers；`CONFIG.md` 补 asr.diarize 与 post 段、defaults.target_duration_s；`config.example.yaml` 补 `asr.diarize` 与 `post`。小项：`worker` 日志用夹取后的 limit；后处理未归一化时硬限幅 ±1.0 防削波；retry 覆盖白名单扩到常用合成参数；`app.__version__` 1.0.0→当前版本；设置页「默认参数」加目标时长输入。附带修掉 `local.py` 两处 `from ...errors` 越界导入（OOM/异常映射一触发就 ImportError，掩盖真实错误），并把「Unsupported languages」映射成 `INVALID_INPUT` 而非 INTERNAL | 实测：批次清单 JSON 确认逐项字段落盘；`retry-failed` 新批次保留 `builtin-vivian/保持温柔/zh/{speed:1.15}`；ASR 排队取消与运行中取消都落 `canceled=True`；MCP 收到非法行返回 1 条 `-32700`；刷新后失败任务显示真实 `ValueError: Unsupported languages` 而不是 ImportError。compileall/tsc/vite build 全绿 |
| 55 | 复查后回归测试暴露「进程直接消失」（无 traceback） | 现象：ASR（whisper 走 GPU）跑完后紧接着跑 TTS，模型加载到 2053MB 后进程被驱动级强制结束（客户端看到 `WinError 10054`，app.log 没有任何异常）。原因：4GB 卡上 whisper 与 TTS 同时驻留显存。修法：`worker._execute` 的 finally 里无条件 `asr_router.unload()`（释放 whisper 并 `vram.hard_release`），下次转写出需重新加载约 3s；另外把引擎侧的「Unsupported languages」映射为 `INVALID_INPUT`（原来报 INTERNAL「内部错误」，用户看不出是语言填错） | 验证：ASR→TTS→ASR→TTS 交替 2 轮全部 completed（TTS 2.0s / 2.24s），进程存活且 `/api/health` 仍为 `local`；无效语言任务现在返回 `INVALID_INPUT` 并附带支持语言列表 |
| 56 | 用户问「新加功能的教程 / 教程页改没改」 | 教程页（`frontend/src/pages/GuidePage.tsx`）此前**没跟着改**，本次补齐 5 个分区：`diarize`（区分说话人，含 CAM++ 安装、任务级开关、最多 N 人语义、改名入口、云端不支持提示、取消语义）、`post`（后期处理链每个字段的取值含义与实测写回）、`duration`（目标时长、夹取与 `stretch_clamped` 说明）、`batch`（批量提交/进度/取消/重试失败项/打包）、`mcp`（启动方式、4 个工具、4 个环境变量、错误码约定）；`SectionId` 与图标同步新增，分区总数 13 → 18。字段名与设置页 `Field label` 逐字对齐，保证「教程里搜」和「设置里搜」命中同一分区 | 实测：`tsc` / `vite build` 通过（392.6KB / gzip 133.6KB）；浏览器打开教程页确认 5 个新分区标题均可见，页面显示「共 18 个分区」 |
| 57 | 用户质问「导出档位为啥没做！」 | 原因：上一轮我按「3.3 后处理链 / 3.5 时长控制 / 3.7 MCP Server」三项收口，那是从我自己先前的待办清单复述来的，没回 `docs/spec/PHASE-3-BACKLOG.md` 逐条对，§4「导出档位」就整条漏掉——是范围遗漏，不是技术卡点。本轮补做：新增 `backend/app/export.py`（`TIERS=original/standard/editing`，`encode_with_tier()` 按档位重采样+编码，`probe()` 读真实文件头，soundfile 失败退 ffprobe）；`ExportCfg{tier}` + `RUNTIME_MUTABLE` 加 `export`；`TTSParams.export_tier` 支持任务级覆盖；`worker._finalize` 改用档位编码并把实测规格写进 `params._export`；新增 `GET /api/tasks/{id}/media-info`；前端工作台高级参数与设置页默认参数各加「导出档位」下拉，任务中心详情新增「实际规格」一行；教程页新增「导出档位」分区（18→19）；CONFIG/API/README/example 配置同步 | 实测三档：original → 24000Hz/单声道/16bit/WAV/107KB；standard → 44100Hz/单声道/MP3/57KB；editing → 48000Hz/单声道/24bit/WAV/357KB；`media-info` 与 `params._export` 数值一致（读文件头），`tsc` 与 `vite build` 通过 |
| 58 | 用户要求「交付物重建 + 云端实证」 | ①交付物：`launcher/build.ps1` 重建 `QWENTTS-Setup.exe`（9,049,210 B / 8.63MB），`--self-test` 实测 **13 项通过 / 2 项提醒 / 0 失败、退出码 0**；源码包与便携包重建，顺带修掉打包脚本两个问题——`launcher/build/**`（PyInstaller 中间产物）会被打进包，以及 **`config/config.yaml` 会被打进包**（含本机云端地址/密钥，属泄露风险），现已同时排除。②云端实证：先用 SFTP 把本地 whisper small（464MB）与 CAM++ ONNX（27MB）直传，省掉云端 HF 下载（此前 `Errno 99` 卡死）；过程中发现并修掉 3 个真问题——(a) 解压源码包会覆盖云端 `config/config.yaml` 导致 models_dir 指回项目内、`diarize` 变 false（本次改为单独上传云端配置，并让打包排除该文件）；(b) PyAV 19.x 的 `av.open` 不接受 `metadata_errors`，faster-whisper 1.2.1 解码直接 TypeError → **ASR 改为自己用 soundfile 解码成 16k float32 再喂 numpy 给 faster-whisper**，彻底摆脱 PyAV 版本约束（本地 82 tests 通过、本地转写 8.0s 正常）；(c) `/v1/health` 的 `asr.min_speakers` 是能力布尔值而非数值，容易误读，已在文档说明 | 云端实测（无卡模式 CPU）：`/v1/health` → `ready:true, asr:{backend:local, engine_id:asr-whisper-small, diarize:true, diarize_engine:asr-speaker-campplus}`；`POST /v1/engine/asr`（diarize=true, max_speakers=2）返回 `diarize_backend:local`、2 段 `SPK1 0.05-5.05 / SPK2 6.05-11.05`，文本正确。本地同样跑通（8.0s，SPK1/SPK2） |
| 59 | 用户要求「把这个经验写到一个文档里然后备份 ZIP（保留原来所有 ZIP）」 | 新增 `docs/DELIVERY-AND-CLOUD-VERIFY.md`（三章：①打包交付——产物矩阵/标准流程/`make_backup.py` 关键点/5 个坑/交付前清单；②云端实证——模型本地 SFTP 直传、云端专用 config、启动与 `/v1/health`·`/v1/engine/asr` 验收命令、5 个坑；③复用命令速查），并入 `docs/README.md` 的「运维与交付」索引。踩坑内容全部来自本轮实证：PyInstaller 中间产物入包（3.72MB→13.23MB）、`config/config.yaml` 入包（泄露本机云端地址/api_key/SSH）、源码包覆盖云端配置、PyAV 19.x `av.open` 不接受 `metadata_errors` 导致转写 TypeError、`/v1/health` 的 `asr.min_speakers` 是能力布尔值、`exec_command` 里 `&` 起的进程随 channel 关闭被杀（要 `setsid nohup`） | 备份按要求**保留历史全部 zip**（v1.1.2 / obf-v1.1.3 / src-v1.1.4~v1.1.12 / portable-v1.1.11-12，共 16 个未删除），新增 `QWENTTS-src-v1.1.12-<ts>.zip` 与 `QWENTTS-portable-v1.1.12-<ts>.zip`（内含本文档）；`docs/README.md` 索引可检索 |
| 60 | 用户要求「所有云端引擎也能在本地管理：一键部署后本地后端能连上云端，设置页新增云端引擎管理，与本地分开，可控制云端下载/删除模型」 | 新增 `docs/spec/CLOUD-ENGINES-1.md`（目标/非目标——明确**不做**本地→云端直传兜底/多主机管理；SSH 通道理由；档案字段；8 个接口；界面 5 条；6 条验收）。后端三件套：`app/cloud_hosts.py`（`data/cloud-host.json` 存 host/port/user/project_dir/python，密码走 DPAPI `password_enc`，`has_password` 脱敏回前端）、`app/cloud_engines.py`（SSH 调云端 `engines_cli`：`list --json` 解析、`install` 流式解析 `[阶段] N% msg` 写 job、`remove`、`select`；job 落盘 `data/cloud-engines/jobs/*.json`，`CLOUD_ENGINES_BUSY` 防并发）、`app/api/cloud_engines.py`（`/api/cloud/engines` 8 个端点，模块级单例，错误码登记 `CLOUD_HOST_MISSING`/`CLOUD_ENGINES_FAILED`/`CLOUD_ENGINES_BUSY`）。一键接入 S8 成功与 prepare_only 分支都会 `_remember_host()` 自动存档案。前端：`components/CloudEnginesPanel.tsx`（云端档案表单 + 测试连接 + 按 tts/asr/speaker 分组的卡片行 + 安装进度条与日志 + 删除/设为当前）、`SettingsPage` 新增「云端引擎管理」分区（13→14 分区）、`GuidePage` 同步新增该分区、`types.ts`/`lib/api.ts` 补类型与 8 个方法。顺带修 bug：`engines_cli list` 原来走 stale-while-revalidate 缓存，一次性进程永远读到旧值（云端 `asr-whisper-small` 463MB 在盘上却报 not_installed）→ 新增 `registry.all_items_fresh()` + `manager.items_fresh()`，CLI 强制刷新并回写缓存；`app.__version__` 1.1.11→1.1.13 修好版本一致性测试 | 实测（打 region-42）：空态返回 400 `CLOUD_HOST_MISSING` 而非 500；档案保存后 `has_password:true`、密码不回显；`/host/test` 返回指纹 `ssh-ed25519 f207…`、`Python 3.12.3`、项目目录存在；列表 13 个引擎且**直传上去的** `asr-whisper-small 463.7MB` / `campplus 27MB` 均 ready；`install asr-whisper-tiny` 流式进度 15%→完成，完成后 ready 74.6MB；`remove` 返回 `freed_mb 74.6`；浏览器实拍设置页「云端引擎管理」：主机行、13 个引擎分三组、已安装/未安装徽标、当前徽章、18 个安装按钮/5 个删除按钮，0 JS 异常；UI 点「安装」到完成全流程通过（进度区块 + 已完成）。`pytest` 82 passed、`tsc` 0 错。云端环境另修：`huggingface_hub 2.2.0` 的 httpx 客户端损坏导致 `snapshot_download` 必挂，降到 0.36.2 后正常（属环境问题，已记入规格踩坑） |
| 61 | 用户提出 4 条云端引擎管理优化：①语音识别/声纹「设为当前」无反馈 ②安装进度旁多余提示 ③项目目录/解释器/用户名应给默认值 ④与云端连接模块参数联动 | ①根因是列表只回 `active`（TTS 口径），ASR/声纹的当前引擎在 `asr.local.engine_id`/`asr.diarize.engine_id`，选了也不会变 → `manager.items()/items_fresh()` 统一补 `active_asr`/`active_speaker`，前端按三路口径判断，并在选择后先做乐观更新再拉权威值（绿色「当前」徽章立刻出现）；②去掉安装开始时的「进度看下方日志」toast（进度块已在同页）；③④新增 `cloud_hosts.derive_defaults()`：主机/端口/用户名来自 `cloud.tunnel` 或最近一次**成功的一键接入任务**，项目目录/解释器给默认值（`/root/autodl-tmp/QWENTTS`、`/root/miniconda3/bin/python`）；`GET /api/cloud/engines/host` 现在总是返回 `prefill` + `sources`（saved/agent/tunnel/default），前端据此预填并提示「已自动填入」；一键接入的 `_remember_host` 也改用 `looks_like_python_path()` 守卫——`env.python` 是「Python 3.12.3」这种版本输出，不能当解释器路径存 | 实测：`/host` 在无档案时返回 `host=region-42…/port=26322/user=root`（source=agent）+ 默认项目目录与解释器；`select` 声纹引擎返回 `kind=speaker`，列表 `active_asr`/`active_speaker` 分别跟随且互不影响（tiny→small 可来回切）；浏览器实测三行同时显示绿色「当前」（Qwen3-TTS / Whisper Small / CAM++），点「设为当前」1.2s 内出现徽章、刷新后保持，0 JS 异常；bundle 中已无「进度看下方」。`pytest` 82 passed、`tsc` 0 错 |
| 62 | 用户要求「额外下载地址只在装 torch 时用；无卡也要装显卡版；先删文件再重跑验证」+ 三条确认 | ①拆分依赖：`requirements.txt` 去掉全局 `--extra-index-url` 与 torch/torchaudio 两行；新增 `requirements-torch.txt`（torch/torchaudio/torchvision 三条精确 pin，索引由命令行传）；`requirements.pins.txt` 移除 torch 三行（否则 `-r pins -i 国内镜像` 找不到 `+cu121` 轮子必失败）。②安装顺序改为**先 torch 再普通依赖**：`qwen-tts` 声明了 torch 依赖，先装普通依赖会让 pip 从清华源白拉一份 CPU 版 torch（约 900MB）再被覆盖。③索引优先级改为**上交镜像 → pytorch 官方**（实测上交索引 0.28s、官方 2.19s）。④无卡模式不再跳过 torch：照装 cu121 版（开卡即用），CUDA 断言仅在非无卡时作为硬门禁。⑤`start.ps1`/`start.sh`/`launcher`（含 TORCH_INDEX）同步两段式。⑥`cloud_engines` 调云端 `engines_cli` 时优先用部署自带的 `.venv/bin/python`（一键接入把依赖装 venv，系统 python 缺 pydantic-settings 会 import 失败）。⑦`S4B` 即使跳过下载也记录 `models_dir`，让 S5 写到数据盘、复用已有模型。⑧guard 测试改为守护「pins 不含 torch + torch 清单含 cu121 pin + 三个文件都没有生效的全局 --extra-index-url」 | 云端实测（清理系统盘旧部署、保留数据盘 14G 模型）：完整流程 S0–S5 全绿，`S4` 日志显示「① 安装 GPU 版 torch（mirror.sjtu.edu.cn）→ ② 安装其余依赖（清华）→ CUDA 自检 cuda False 2.4.1+cu121 → 无卡模式：torch 已是 cu121 版，开卡后直接可用」，S4B「已跳过下载；数据盘现有 3 个模型目录」；S6 失败＝无 GPU（云端返回“未检测到可用的 CUDA 设备”，属预期）。改跑 `prepare_only` 后 **S0–S4B 全绿、succeeded**，并自动写入云端档案 `project_dir=~/qwentts`。`/api/cloud/engines` 用新部署验证通过（13 个引擎）。`pytest` 82 passed |
| 63 | 用户截图反馈：顶部「云端不可达」、测试连接红字「隧道模式下浏览器无法自己走 SSH」、隧道显示 远端 8443 但服务在 18444 | 三层原因：①`测试连接`是**浏览器**直接 fetch `cloud.base_url`，隧道/自签证书/协议不匹配时必然失败，且报错文案误导；②本地隧道 `remote_port` 还是上次的 8443（S7 只在完整流程才写，无卡 prepare_only 不写），而这次云端服务跑在 18444 → 隧道转发不到服务；③没有端口/协议不一致的诊断。修法：①新增 `POST/GET /api/cloud/probe`，由**后端**探测：自动枚举「隧道(https/http) → 直连 → 反协议直连」，忽略自签证书，返回 via/latency/ready/model，全失败时给出「隧道远端端口与服务端口是否一致」等定向提示；②前端「测试连接」改调该接口，显示「经隧道(https) … 连通 · ready · 模型」或后端给的 hint；③`cloud_agent` 新增 `_sync_tunnel_port()`，**无卡 prepare_only 成功时也把 `cloud.tunnel.remote_port` 同步成 options.remote_port**，避免隧道指着旧端口 | 实测：修正前 probe 失败并准确报「隧道转发不到服务」；把 remote_port 改 18444 后 `probe → {ok:true, via:隧道(https), 80.9ms, ready:true, model:Qwen3-TTS…}`，`/api/health` 的 cloud 变 `{reachable:true, ready:true, latency:125ms}`、tunnel `running:true, remote:127.0.0.1:18444, fail_streak:0`；浏览器实测顶部变「云端 134ms」绿标、点「测试连接」显示绿色「连接成功（延迟 75.6 ms）」、隧道提示自动变成 18443 → 18444，0 JS 异常 |
| 64 | 用户要求「隧道开启时服务地址只读自动跟随 + 03 每个框加简单介绍，小白容易混」 | ①`服务地址`：隧道开启时输入框改为 `disabled`，值自动渲染成 `{scheme}://127.0.0.1:{local_port}`，旁边给一个「https/http」下拉只切协议（切协议即写回 base_url，保证 `effective_base_url()` 取到正确 scheme）；隧道关闭时恢复可编辑，填真实云端地址。字段 hint 随状态切换（隧道开＝「地址自动跟随本地映射端口，这里只需选协议」；隧道关＝「后端访问云端的真实地址」）。②03「SSH 隧道」9 个输入框全部补一句人话 hint：启用隧道（请求改走本地端口）、SSH 主机（region-xx.seetacloud.com）、SSH 端口（通常不是 22）、用户名（多为 root）、私钥路径（有 pem 就填，否则用密码）、密码（DPAPI 密文）、本地映射端口（避开 8443）、远端地址/端口（必须等于云端服务实际端口，否则「隧道在跑但连不上」）。③把「测试连接」的旧文案（说自己会用浏览器直连 /v1/health）改成「由后端探活（走隧道 + http/https 自动选择）」，并同步 02 的隧道提示口径 | 实测：服务地址显示 `https://127.0.0.1:18443` 且 `readonly/disabled=true`、协议下拉存在；03 九条说明全部可见（截图确认）；「浏览器直连」旧文案消失、「由后端探活」出现；点测试连接返回绿色「连接成功（延迟 …ms）」；0 JS 异常；`tsc` 0 错、`vite build` 通过 |
| 65 | 用户质疑「缺依赖不应该是程序解决吗，为什么让用户解决」 | 现状确实把锅甩给了用户：所有引擎适配器都只做 `package_installed()` 检查，缺包直接抛 `PACKAGE_MISSING` 并让用户自己 `pip install`（历史上是为了防止 pip 把主环境 cu121 的 torch 换成 CPU 版）。改法：新增 `deps.install_main_packages()` —— 只对 **isolation=inprocess（主环境）引擎**、只装它自己声明的包；**自动生成 torch 约束文件**（从 `requirements-torch.txt` 读出 torch/torchaudio/torchvision 的 cu121 pin，作为 `-c` 传给 pip），这样即使依赖链碰到 torch 也不会把它换掉；清华源失败自动回落官方源；装完 `deps.invalidate()` 清探测缓存。`manager._install_worker` 在调用 `adapter.install()` 之前先跑这一步，进度里显示「自动安装依赖：xxx → 依赖已装好：xxx」；适配器里原来的报错文案改成「自动安装未成功」（只作为兜底）。venv 隔离引擎不动（它们本来就有自己的锁文件安装流程） | 云端实测（region-42 的 `~/qwentts/.venv`）：`POST /api/cloud/engines/asr-speaker-campplus/install` → 日志 `[依赖] 2% 自动安装依赖：sherpa-onnx` → `[依赖] 15% 依赖已装好：sherpa-onnx` → 下载模型 → `completed`，列表变 `asr-speaker-campplus ready 27.0MB`；事后校验 venv：`torch 2.4.1+cu121 cuda=12.1`（未被替换）、`sherpa_onnx 1.13.8`、`sklearn 1.9.1`。`pytest` 82 passed |
| 66 | 用户反馈：点「安装」后一直显示「排队中」，几分钟后突然「安装完成」，全程看不到任何百分比 | 三个真实原因：①前端初始文案是写死的「任务 xxx 已排队」，之后完全依赖 WebSocket `engine.install` 事件，代码里明确不轮询——事件一丢就停在占位文案；②主环境引擎的自动装依赖用 `subprocess.run` 一把跑完，装 onnxruntime 这类包要几分钟且期间**零事件**；③`asr_speaker._download` 用 `urlopen(timeout=120)` 逐块读，GitHub 卡住时静默最长 120 秒，`install/status` 实测 40 秒停在 `p=0.2 下载 github.com` 不动。改法：①`deps.install_main_packages` 换成 `Popen` + 读取线程 + **每 2 秒心跳**（"正在安装依赖… 已等待 Ns（最近一行 pip 输出）"）；②`manager._install_worker` 新增心跳线程：任何阶段超过 5 秒无新进度就补发一条「仍在进行：<原始文案>（已等待 Ns）」，且心跳只读 `base` 文案不覆写，避免「仍在进行：」套娃；③`install()` 立刻推一条 `queued` 事件，初始 message 改「正在启动安装任务…」；④`_download` 改**断点续传**（`Range: bytes=<已有>-`，`.part` 保留）+ **30 秒停滞超时**（超时抛错自动换下一个源）；⑤前端 EngineCard 加**兜底轮询**：只要有未完成的安装就每 3 秒查 `install/status`，覆盖 WS 丢事件的情况；`EngineInstallStatus` 补 `running` 字段 | 本机实测（CAM++ 27MB，临时移走模型目录触发真实下载）：`install/status` 序列 `下载 github.com (0.2)` → 每 5 秒 `仍在进行：下载 github.com（已等待 5s）` → `下载 hf-mirror.com` → `12672KB/27618KB(45.88%)` → `95%` → `done 安装完成 27MB`，共 6 个不同状态；pip 心跳实测 `--dry-run six` 8.1 秒内 4 条心跳；WS 实测收到 `queued → pip 1% → done` 三条事件。`pytest` 82 passed、`tsc` 0 错、`vite build` 通过 |
| 67 | 用户贴出安装器日志：体检全部 OK，但界面报 `TclError: Item model_base already exists`（_render_checks 失败两次） | 根因是 key 撞车：`detect_model` 用 `"model_" + model_name.split("-")[-1].lower()`，而 `Qwen3-TTS-12Hz-1.7B-Base` 与 `Qwen3-TTS-12Hz-0.6B-Base` 末段都叫 `Base` → 两项都得到 `model_base`，Treeview `insert(iid=...)` 第二次就抛 TclError，整块体检结果渲染失败（日志里"处理消息 checks 失败"）。改法：①key 带上规格段 `model_{17b|06b}_{base|customvoice|voicedesign}`；②`_render_checks` 增加兜底去重（同 key 追加 `#n`），以后任何新模型撞名也不会再炸界面 | 实测 `detect_model` 五个模型 key 分别为 `model_17b_base / model_17b_customvoice / model_17b_voicedesign / model_06b_base / model_06b_customvoice`（唯一）；`collect_checks()` 全量 17 项无重复 key；新 exe `--self-test` 退出码 0。注意：旧 exe 正被用户运行占用（WinError 5），已生成 `launcher/dist/QWENTTS-Setup.exe.new`，关掉安装器后 `move /Y` 覆盖即生效 |
| 68 | 用户要求「把所有打包和只有你知道的经验写成经验文档」 | 新增 `docs/PACKAGING-AND-TRIBAL-KNOWLEDGE.md`（维护者手册），八个章节：①三个产物（源码包/便携包/安装器/升级工具）的生成命令与**构建顺序**（前端 → exe → zip，反了会打进旧 dist）；②发版九步流程（compileall+pytest 全绿 → tsc/build → VERSION.json 与 `app.__version__` 同步 → PROJECT_HISTORY 追加 → 重打 exe → 打两个 zip → 提交 → 抽查）；③打包脚本硬规则（只增不删、禁用 Compress-Archive、`launcher/build` 与 `config/config.yaml` 必须排除、`.new` 跳过、src 跳过所有 dist）；④PyInstaller 经验（exe 运行中被占用 → 先 build 到别的 distpath 再 `move /Y`、`--self-test` 退出码、`--windowed` 下 print 不可见、安装器日志在 exe 旁的 `data/logs/launcher.log`）；⑤按模块的踩坑清单（依赖与 torch 五条、引擎进度三条、云端六条、其它历史事故十一条）；⑥打包后抽查（体积/文件数基准：src 3.77MB/242、portable 21.02MB/248）；⑦排障速查表；⑧不要做的事。同时在 `docs/README.md` 索引里补齐该文档与 `UPGRADE.md` | 文档已落地并在索引中可查；`pytest` 82 passed；重新打包 src/portable 均含该文档 |
| 69 | 用户截图：ASR 连续失败，日志 `加载本地 ASR 模型 size=tiny device=cuda compute=int8_float16` → `Requested int8_float16 compute type, but the target device or backend do not support efficient int8_float16 computation.`（换成 float16 / int8 也一样失败），配置仍是 cuda | 旧代码只在 `device == "cpu"` 时把 float16/int8_float16 降成 int8，**device=cuda 加载失败就直接抛 ASR_FAILED**，没有任何回退——所以整条 ASR 不可用，而日志和界面都还显示 cuda，容易误判成“模型坏了”。改法：新增 `_load_candidates(device, compute)` 生成逐级降级序列 `cuda+配置精度 → cuda+int8_float16/float16/int8/float32 → cpu+int8 → cpu+float32`（CPU 侧仍禁止半精度），`_ensure()` 逐个尝试并 WARNING 记录每次失败，成功后把**实际生效值**存进 `_effective_device/_effective_compute`、写日志「ASR 已降级运行：配置 … → 实际 …」并通过 `health()` 暴露。失败时的报错同时带「首个尝试」和「最后几次」原因（首个通常就是根因）。另外澄清：`adapters/asr_whisper.py` 里 `device="cpu"/compute_type="int8"` 只是安装预取，不是推理 | 新增 `backend/tests/test_asr_load_fallback.py` 6 条守护测试（候选顺序、CPU 不用半精度、cuda 内降级、全 cuda 失败退回 cpu、全失败报错含首个组合、CPU 正常只试一次）→ 全量 **88 passed**；真实库验证（本机 RTX 3050 + torch 2.4.1+cu121）：tiny/small 在 `cuda/int8_float16` 下第一档即成功并记录实际值，`cpu/float16` 自动变 `cpu/int8`。文档：维护者手册新增 5.2.1「ASR / 声纹 跑在哪」、`CONFIG.md` 补设备与精度说明（含 Whisper 多档与 CAM++ 固定 CPU） |
| 70 | 用户要求「先写计划文档」：MCP 扩展（resources / prompts / sampling、批量任务、说话人改名、导出/字幕、引擎管理、云端引擎、模型预热），并问「视频配音好做吗」 | 新增 `docs/spec/MCP-EXPANSION-1.md`：①现状盘点（4 工具、仅 stdio、MCP 进程不持有模型）；②**resources**：11 个 URI（voices/tasks/transcript/segments/subtitles/engines/cloud engines/system status/logs）与 `list / templates/list / read / subscribe` 方法与截断规则；③**prompts**：5 个模板（dub_script、multi_role_dialog、voice_pick、subtitle_polish、speaker_naming）；④**sampling**：4 个反向调用场景（自动选音色、说话人命名、字幕润色、长文切分），全部带降级，并指出需要把 stdio 改成「读线程 + 待响应表」的双向管道；⑤任务域 15 个工具（batch_tts_create/batch_status/batch_cancel/batch_retry_failed、task_rename_speakers/merge、task_export、task_subtitles、split_speakers、voice_create…）逐个映射到已有后端接口；⑥引擎域 15 个工具（engine_* / model_preload / model_unload / cloud_engine_* / cloud_host_* / cloud_probe）；⑦横切（超时、截断、幂等键、鉴权、可观测）；⑧M1–M5 里程碑与三层测试；⑨风险与非目标。另加第 8 节**视频配音可行性**：现有 ffmpeg/asr+diarize/逐句 TTS/fit_duration/字幕导出已覆盖约 70%，新增只需任务类型 `dub` + 逐句时间轴驱动 + 说话人→音色映射 + 混音封装，V1 估 3–5 天、V2 多角色 +2–3 天、翻译 +3 天、口型同步建议不做 | 文档已落地并在 `docs/README.md` 索引中可查；`pytest` 88 passed；src/portable 已重打含该文档 |
| 70 | 用户要求「先写计划文档」：MCP 扩展（resources / prompts / sampling、批量任务、说话人改名、导出/字幕、引擎管理、云端引擎、模型预热），并问「视频配音好做吗」 | 新增 `docs/spec/MCP-EXPANSION-1.md`：①现状盘点（4 工具、仅 stdio、MCP 进程不持有模型）；②**resources**：11 个 URI（voices/tasks/transcript/segments/subtitles/engines/cloud engines/system status/logs）与 `list / templates/list / read / subscribe` 方法与截断规则；③**prompts**：5 个模板（dub_script、multi_role_dialog、voice_pick、subtitle_polish、speaker_naming）；④**sampling**：4 个反向调用场景（自动选音色、说话人命名、字幕润色、长文切分），全部带降级，并指出需要把 stdio 改成「读线程 + 待响应表」的双向管道；⑤任务域 15 个工具（batch_tts_create/batch_status/batch_cancel/batch_retry_failed、task_rename_speakers/merge、task_export、task_subtitles、split_speakers、voice_create…）逐个映射到已有后端接口；⑥引擎域 15 个工具（engine_* / model_preload / model_unload / cloud_engine_* / cloud_host_* / cloud_probe）；⑦横切（超时、截断、幂等键、鉴权、可观测）；⑧M1–M5 里程碑与三层测试；⑨风险与非目标。另加第 8 节**视频配音可行性**：现有 ffmpeg/asr+diarize/逐句 TTS/fit_duration/字幕导出已覆盖约 70%，新增只需任务类型 `dub` + 逐句时间轴驱动 + 说话人→音色映射 + 混音封装，V1 估 3–5 天、V2 多角色 +2–3 天、翻译 +3 天、口型同步建议不做 | 文档已落地并在 `docs/README.md` 索引中可查；`pytest` 88 passed；src/portable 已重打含该文档 |
| 71 | 用户报告：云端有 1.7B 全模型、本地只有 0.6B、运行模式＝纯云端时，点「音色设计」无法设计；并要求排查同类问题（先出 spec 再修） | 根因在**前端**：`Workbench.tsx` 的能力门禁用本机 `/api/engines` 的 active 引擎 `kind_tags`，而 `qwen06.py` 是 `["tts","clone"]`（无 design）→ 纯云端也被本地 0.6B 挡死；后端其实是对的（`router.plan()` 在 cloud 模式直接返回 primary=cloud，`_check_capability` 只在 primary=local 时生效）。修法：①新增 `backend/app/capabilities.py`（`effective_capabilities` + 纯函数 `effective_capabilities_with`），按 mode 计算：cloud 只看云端模型名、local 只看本机引擎、hybrid 取并集，并给出 `missing/missing_hint/cloud_models`；②`/api/meta` 新增 `capabilities` 字段（只增不改）；③`engine/cloud.py` 校验云端模型名非空，空则抛新增的 `CLOUD_MODEL_UNSET`（附中文提示）；④前端 `Workbench` 门禁/页签 toast/chip/instruct 全部改用 `meta.capabilities`，`engineIsQwen` 改为按能力判定；⑤`BatchPanel` 批量类型按同一能力过滤并自动回落；⑥`AppContext` 新增 `reloadMeta`，`reloadSettings` 顺带刷新 meta（切模式后门禁立刻更新）；⑦文案：GuidePage 与 CONFIG.md 说明"云端 1.7B 同样支持设计" | 新增 `backend/tests/test_capabilities.py` 6 条（cloud 忽略本地 0.6B、cloud 缺 VoiceDesign、local 0.6B 无 design、local 1.7B 全能力、hybrid 并集、base_url 未配置）→ 全量 **94 passed**；`tsc` 0 错、`vite build` 通过；真机实测三态：`cloud` 设计页签可用（chip「云端 · Base / CustomVoice / VoiceDesign」）、`local` 禁用且提示「本地引擎「qwen3-tts-0.6b」不支持音色设计；把运行模式切成「仅云端」或装 Qwen3-TTS 1.7B 后可用」、`hybrid` 可用（chip「qwen3-tts-0.6b + 云端」），0 JS 异常；`/api/meta` 三态输出 source=cloud/local/both 均正确 |
| 72 | 用户追问「那云端没有 1.7B 模型的情况下这种你考虑到了吗」 | 上一版只按**配置的模型名**判能力：设置里填了 VoiceDesign 就算有，云端其实没装时会「界面显示可用、提交才失败」。补法：新增 `backend/app/cloud_models.py`，探云端 `GET /v1/models`（同一个后端，`ready` 即云端模型目录是否就绪）→ 得到云端**实际**具备的能力；**60 秒缓存 + 后台线程刷新**，`/api/meta` 永不阻塞；探测失败（实例没开/隧道断）时返回旧快照或 None。`capabilities` 优先用实测结果，探测不可用时退回"按配置名"，并新增 `cloud_verified / cloud_ready_models / cloud_probe_error` 三个字段让界面区分"已实测"与"按配置推断"；前端 chip 的 title 会写明是哪种 | 新增 `backend/tests/test_capabilities_cloud_models.py` 5 条（云端未装 VoiceDesign → design 不可用、云端已装 → 可用、探测失败退回配置名并标记 unverified、混合模式也用云端实测、缓存命中不再重复探测）→ 全量 **99 passed**；`tsc` 0 错、`vite build` 通过 |
| 73 | 用户要求「把云端 ASR / 声纹也加上实测」（上一版 asr 写死为 true） | `cloud_models._probe` 除了 `/v1/models` 再探 `/v1/health` 的 `asr` 块：`engine_id` 有值 → 云端 ASR 可用（并用它覆盖 models 里的通用 id），`asr.diarize` 为真 → 云端声纹可用；结果多返回 `asr_ok / diarize_ok`。`capabilities` 在"已实测"分支不再无脑 `+{"asr"}`，改为以实测为准，`asr` 缺失会进 `missing`；新增 `diarize_available`（true/false，**未实测时为 null，绝不猜成 true**）。前端 `CapabilitySet` 补字段，工作台在「区分说话人」打开且 `diarize_available===false` 时提前提示"云端未装声纹引擎，本次只出整段文本" | 新增 `backend/tests/test_capabilities_asr_diarize.py` 4 条（云端无 ASR → asr 不可用；diarize 只有实测为真才 true；未实测必须 null；本地起 HTTPServer 验证 probe 解析 models+health 两个响应）→ 全量 **103 passed**；`tsc` 0 错、`vite build` 通过 |
| 74 | 用户确认方案 C 后要求「开始改生产前端」（/spec） | 按 `docs/spec/SPEC-LIQUID-DOCK-1.md` 落地：①`tokens.css` 新增玻璃 token（浅/深）；②`layout.css` 去掉 `.main` 侧栏偏移、加 Dock 底部留白与 `scroll-padding-bottom`，新增 `.content-brand` 与 `.docknav*` 全套样式（胶囊、1.5px 色散玻璃边、按住放大、独立搜索圆钮、窄屏只留图标、`prefers-reduced-motion` 降级）；③新增 `components/DockNav.tsx`：6 项 tablist、Canvas + `feDisplacementMap` 生成边缘折射贴图（常态 3.2px / 按住 9px）、CSS 过渡 520ms 滑动、只认胶囊的拖拽（命中测试 + 4px 阈值）、方向键切换；④`App.tsx` 删除 `rail`/`RAIL_BOTTOM_ROUTES`，品牌块迁入内容区，顶栏移除 `CommandButton`（搜索统一到 Dock 圆钮并复用现有 `CommandPalette`）。用户拍板四条：教程进 Dock、顶栏保留、Dock 常驻悬浮、接受 Safari/Firefox 无折射 | 实测（1440×900）：无侧栏；Dock 572×76；6 tab；`backdrop-filter=url("#qwentts-dock-liquid")…`；点最右 tab 胶囊 x `12→34→74→137→212→306→352→400→423→447→462`（连续滑动）；按住胶囊 `118×105`、上下各凸出 15px、图标仍 `22×22`（无缩放无重影）；深浅主题与搜索面板正常、0 error；`tsc` 0 错、`vite build` 通过、`pytest` 103 passed。另记录环境坑：SoX 14.4.2 的 `sox -h` 缺 `AUDIO FILE FORMATS:` 致 python-sox 导入崩溃 → 用 `.cache/soxshim/sox.bat` 走 `NO_SOX` 分支绕过 |
| 75 | 用户两条反馈：①左上角 LOGO 改到顶栏居中并调小；②按住胶囊拖动时应「松手后等动画播完再切页」，不能边拖边切 | ①`App.tsx` 把品牌块从内容区移入 `.topbar`（标题左 / 品牌绝对居中 / 状态右），`layout.css` 新增 `.topbar__brand` 系列（`position:absolute; left:50%; translateX(-50%)`，mark 26px、标题 13px、副标 9px，≤900px 隐藏副标），并删除不再使用的 `.content-brand` 样式；②`DockNav.tsx` 拖拽中只更新胶囊位置与 `pendingIndexRef`（不再调用 `onNavigate`），`endDrag` 时先吸附再 `navigateAfterSettle()`：监听胶囊 `transitionend(transform)` 后切页，并加 560ms 兜底定时器（reduced-motion/后台标签不触发 transitionend 时也能切），卸载时清理定时器 | 实测：顶栏品牌 `width=98, height=26, centerOffset=0`、内容区无 `.content-brand`；拖拽中标题保持「工作台」，松手 900ms 后变「设置」；点击 tab 回归验证 `120ms` 内即切页（教程/任务中心/工作台三次均正确）；`tsc` 0 错、`vite build` 通过、控制台 0 error |
| 76 | 用户两条调整：①顶栏图标与文字放到最大（不超出顶栏）；②拖拽切页时机改到「识别完成、胶囊缩小前一刻」 | ①`.topbar__brand` 图标 26→**32px**、标题 13→**15px**、副标 9→**11px**，并加 `max-height: calc(var(--shell-topbar) - var(--space-2)*2)` 兜底不溢出；②`DockNav` 用 `whenSettled()` 替换 `navigateAfterSettle()`：松手后**保持放大**先把胶囊滑到目标格，监听 `transform` 的 `transitionend`（480ms 兜底）那一刻先 `onNavigate()`、紧接着 `setLifted(false)` 缩小，形成「滑到位 → 切页 → 缩小」的严格顺序 | 实测：顶栏品牌 `brand=118×32, mark=32×32, topbarH=60, centerOffset=0, overflow=false`；拖拽 330px 松手后采样（每 25ms 记录标题与胶囊宽度）：`切页时刻 1148ms`、`宽度回落到常态(88)时刻 1527ms` → **切页早于缩小**，最终标题由「工作台」→「状态与日志」；`tsc` 0 错、`vite build` 通过、控制台 0 error |
| 77 | 用户三条：①点击切页太早，要在胶囊到位前一刻切；②顶栏改成单图标居中、声匠与 VOICESMITH 分列左右；③所有页面底部都限定在 Dock 上方 | ①`DockNav` 引入 `pillIndex`（胶囊视觉位置与路由解耦）：点击时先让胶囊滑过去，`setTimeout(SLIDE_MS - NAV_LEAD_MS)` 后才 `onNavigate`；实测新页面首帧渲染约 200ms，故 `NAV_LEAD_MS` 定为 **330ms**，视觉上约在到位前 40ms 完成切换；键盘方向键走同一 `goTo`；拖拽路径仍用 `whenSettled`（到位那一刻切页后缩小）。②`App.tsx`/`layout.css` 顶栏品牌改为「声匠 + 图标(绝对居中) + VOICESMITH」，图标 32px、`markCenterOffset=0`、不溢出顶栏。③新增 `--dock-safe-bottom = dock 高 + 24 + 24`；`.shell .page/.page--fill` 用高优先级补 `padding-bottom/scroll-padding-bottom`（components.css 的 `.page{padding}` 会覆盖单条 padding-bottom）；components.css 里 8 处 `100vh` 容器减去该安全区，内部滚动容器（settings-nav__list / card__body / table-wrap / batch-rows / log-list）补底部内边距 | 实测：`click→标题变化 469ms`、`click→胶囊最后移动 508ms`（切页早于到位）；顶栏 `mark 32×32、居中偏移 0`；五个页面（工作台/音色库/任务中心/设置/状态）最低容器 bottom ≤ 754，Dock top = 800 → 全部在 Dock 上方；`tsc` 0 错、`vite build` 通过、`pytest` 103 passed、控制台 0 error。**另修一处自己的失误**：上一轮替换品牌样式时误删了 `layout.css` 里的 `.topbar__title/.topbar__meta/.page/page-enter/card-enter` 规则（导致页面内边距变 0、入场动画消失），本轮已原样恢复 |
| 78 | 用户：「能不能去掉页面头，把页面头的功能全部融入顶栏，做成和图标相互轮播的形式」 | ①新增 `components/TopbarCarousel.tsx`：两页轮播（品牌「声匠 ⟡ VOICESMITH」／页面信息「标题 + 副标题 + 状态胶囊」），切页立刻显示页面信息，6s 后翻品牌、3.2s 再翻回；悬停/聚焦暂停，点击手动切换，`prefers-reduced-motion` 只做淡入淡出；②`App.tsx` 删除顶栏左侧标题，接入轮播；新增 `PAGE_SUBTITLES`（6 个页面的一句话说明）与 `topbarChips`（模式 / 显存 / 云端模型，顺带修掉原来「云端 云端 ·」重复文案）；`Shell` 补 `settings` 依赖；③6 个页面删除 `<h1>+page__subtitle` 标题块（设置页/任务页/音色库/状态页/教程页保留各自工具栏按钮，工作台整块 `page__head` 移除，状态胶囊上移顶栏）；④`layout.css` 新增 `.topbar-carousel/.topbar-slide/.topbar-chip` 样式，`.topbar__meta` 补 `margin-left:auto`（标题移除后状态需自己顶右），chip 改中性配色（玻璃白边在白色顶栏上不可见）；≤1280px 隐藏副标题、≤900px 隐藏状态胶囊 | 实测：`pageHeads=0 / h1=0`；轮播初始页面信息页 `title=工作台`、`chips=[模式 仅云端, 显存 未占用, 云端 Base / CustomVoice / VoiceDesign]`；点击可切到品牌页（`声匠⟡VOICESMITH` 居中）；移开鼠标 6s 后自动翻页；切到任务中心后轮播立刻显示「任务中心」；`tsc` 0 错、`vite build` 通过、`pytest` 103 passed、控制台 0 error |
| 79 | 用户：页面名（工作台/任务中心）放顶栏最左、加粗加大；「副标题 + 状态胶囊」那组在顶栏居中、字号加大一点，且不能越过左侧标题 | ①`TopbarCarousel` 去掉 `title`（标题不参与轮播），`App.tsx` 顶栏最左新增 `.topbar__page-title`（20px / 权重 700 / 不换行）；②轮播容器从 `inset:0` 改为 `left:50% + translateX(-50%)`，`width: min(60vw,760px)`、`max-width: calc(100vw - 标题宽 - 右侧状态宽)`，slide 加 `max-width:100%; overflow:hidden`，副标题 `min(38vw,420px)` 省略号；③字号：副标题 12→**13px**、状态胶囊 11→**12px**（高度 22→24px）；④淡入淡出错开（旧内容先退 240ms、新内容延迟 110ms 进场），修掉切换瞬间两页文字叠在一起 | 实测（1440×900，/tasks）：标题 `left=24, right=103, 20px/700`；轮播 `centerOffset=0`（严格居中）、`slide 375..1065`、右状态 `1212..1416`；`overlap=false`；`descFont=13px, chipFont=12px`；截图 `topbar_page_info.png`（页面信息页）与 `topbar_brand.png`（品牌页）；`tsc` 0 错、`vite build` 通过、控制台 0 error |
| 80 | 用户截图指出页面底部页脚"是不是落下了"，要求放进顶栏和图标一起：`Made by xiyihang360@163.com  ⟡图标⟡  保留所有权利 · © 2026 xiyihang` | ①`App.tsx` 删除 `.app-footer` 整块（页脚不再出现在页面底部）；②`TopbarCarousel` 品牌页改为三段式：左「Made by xiyihang360@163.com」+ 中间图标 + 右「保留所有权利 · © 2026 xiyihang」；③字号统一 12px、取消大写与字距（原来 `__sub` 是 11px uppercase + letter-spacing），署名用次级文字色、版权用弱化色；④清理 components.css 里残留的 `.app-footer` 样式 | 实测：`document.querySelectorAll('.app-footer').length = 0`（首屏与滚动到底都是 0）；品牌页 `parts = [topbar-slide__name: Made by xiyihang360@163.com, topbar-slide__mark(图标), topbar-slide__sub: 保留所有权利 · © 2026 xiyihang]`，整体宽 381px、`centerOffset=0` 居中、不与左侧标题重叠；截图 `topbar_brand_credit.png`；`tsc` 0 错、`vite build` 通过、控制台 0 error |
| 81 | 用户要求品牌轮播页改成五段式并调大字号：`Made by xiyihang360@163.com  声匠 图标 VOICESMITH  保留所有权利 · © 2026 xiyihang` | `TopbarCarousel` 品牌页由三段扩为五段：`.topbar-slide__credit`（署名）＋ `.topbar-slide__name`（声匠）＋ `.topbar-slide__mark`（图标）＋ `.topbar-slide__sub`（VOICESMITH）＋ `.topbar-slide__credit`（版权）；`layout.css` 新增 `.topbar-slide__credit`（13px 弱化色），`__name` 12→**15px/600**、`__sub` 恢复大写 + 字距（12px 次级色） | 实测：`parts = [credit 13px, name 15px, mark, sub 12px, credit 13px]`；整体 `box 450..990（宽 540）`、`centerOffset=0`；`overlapLeft=false`（不压左侧「任务中心」）、`overlapRight=false`（不压右侧状态）；截图 `topbar_brand_5parts.png`；`tsc` 0 错、`vite build` 通过 |
| 82 | 用户要求把 `.cache/preview/glass-dock-demo-liquid.html` 里的卡片与其它表面样式（除 Dock、顶栏）也搬到生产前端 | 在 `components.css` 末尾追加「液态玻璃表面层」，用 `.shell` 前缀提高优先级、只覆写表面不改布局：①`.card/.health-card/.engine-card` → `--glass-bg` + `blur(16px) saturate(1.9)` + `1px var(--glass-border)` + `var(--glass-shadow)` + 内外双层 inset 高光，圆角改 `--radius-xl`(24px)；②`.btn` 胶囊化 + 玻璃底 + 悬停上浮，`.btn--primary` 渐变 + 光晕，`.btn--danger` 渐变红，`.btn--ghost/--icon` 透明；③`.chip/.filter-chip/.kbd/.badge` → 玻璃小药丸；④`.input/.textarea/.select` → 玻璃内凹 + focus 光环；⑤`.segmented` 玻璃槽 + 选中项白玻璃块；⑥`.table-wrap/th/td/tr:hover` 发丝线 + 玻璃底；⑦`.modal/.cmdk` 更厚玻璃 + 大阴影；⑧`.settings-nav`（设置/教程左侧目录）玻璃薄片；⑨`tokens.css` 补 `--radius-lg: 18px` / `--radius-xl: 24px` | 实测（浅色）：卡片 `radius=24px`、`backdrop-filter=blur(16px) saturate(1.9)`、`bg=rgba(255,255,255,0.62)`、`border=rgba(255,255,255,0.7)`；（深色）卡片 `bg=rgba(20,26,34,0.55)`、同样模糊；输入 `rgba(255,255,255,0.82)`、分段与胶囊同为玻璃底；截图 `glass_light.png` / `glass_dark.png` / `glass_settings.png` / `glass_workbench.png`；`tsc` 0 错、`vite build` 通过、控制台 0 error |
| 83 | 用户反馈「很多卡片都重叠了」「页面太卡」，要求先给方案 | 先量化诊断再改：`document.querySelectorAll` 命中 **177 个 backdrop-filter 元素**（btn 51 / input 33 / chip 30 / select 25 / badge 11 / note 8 / card 13 / 顶栏与 Dock 4），滚动帧间隔 avg **95.8ms**、max 183ms（≈10fps）；重叠扫描：排除父子嵌套后 6 个页面真实重叠均为 **0**（判断为 backdrop-filter 让卡片成为包含块导致内部悬浮/下拉错位，或窗口尺寸差异，等用户截图确认）。给出四方案：A 分层玻璃（控件去模糊）、B 伪元素玻璃（修包含块错位）、C 自适应分级、D 模拟玻璃；用户选「按建议」= A+B | 落地：①A——`.btn/.input/.textarea/.select/.chip/.filter-chip/.kbd/.badge/.inline-note/.segmented` 全部移除 `backdrop-filter`，保留玻璃底色/描边/内高光；②B——玻璃层移到 `.card::before`（`z-index:-1`、`border-radius:inherit`），卡片本身不再有 backdrop-filter，因此不再是包含块，内部下拉/悬浮层恢复正常定位；③A/B 实测后发现卡片伪元素模糊仍是瓶颈：A/B 对比 blur 开=68.2ms、关=16.9ms，blur 半径 16→6px 无差别（成本在面积不在半径）→ 改走 D：卡片用渐变「模拟玻璃」（`--glass-card-fill` 双层渐变 + `--glass-card-edge` 内高光），零合成层；④视觉收敛：`.page` 卡片间距 `--space-5`(24) → `--space-6`(32)，卡片阴影由 3 层收到 2 层 | 改后实测：backdrop-filter 元素 **177 → 4**（顶栏 / Dock / 胶囊 / 圆钮，均为小面积）；滚动帧间隔 **95.8ms → 18.2ms**（≈55fps，静态 16.7ms）；`card.blur=none`、`::before` 背景为双层渐变、圆角 24px；6 个页面真实重叠 0；`tsc` 0 错、`vite build` 通过、`pytest` 103 passed。截图 `glass2_light.png` / `glass2_dark.png` |
| 84 | 用户：「很多输入框都和背景融合看不到框了！需要大面积排查」 | 写对比度审计脚本 `.cache/audit_inputs.py`：对 `input/textarea/select/.btn` 计算边框色与页面底色的 WCAG 对比度（<1.12 或 alpha<0.25 判为不可见），跨 5 个页面扫描。**根因**：我在玻璃表面层里把 `--glass-border`（`rgba(255,255,255,.7)`，本是"玻璃浮在内容上"的半透明白边）误用成了输入框/按钮/卡片/胶囊的边框 → 白底白边，等于没有框。修复：①`.input/.textarea/.select` 改 `background: var(--color-surface)` + `1px solid var(--color-border-strong)`，hover 变 `--color-text-muted`，focus 保留蓝色光环；②`.btn` 实底白 + `--color-border-strong` 边框，hover 加深；`.btn--ghost/--icon` 由透明改实底 + 同款边框（原来透明底+发丝边几乎看不见）；③`.chip/.filter-chip/.kbd/.badge` 改 `--color-surface` + `--color-border`；④`.card/.settings-nav` 边框改 `--color-border`；⑤`.segmented` 改 `--color-surface-nested` + `--color-border`；⑥`.modal/.cmdk` 边框改 `--color-border-strong`。仅保留搜索圆钮（FAB）与 Dock 使用 `--glass-border`（这两个本来就浮在页面上，白边是刻意的） | 审计结果（低对比控件数）：`/settings 167→9`、`/workbench 18→1`、`/voices 40→2`、`/tasks 11→0`、`/status 5→0`，剩余全部是 `.btn--primary/--danger`（本身就是彩色底、无边框，属误报）；实测浅色输入框 `border=rgb(207,214,223)`、`bg=rgb(255,255,255)`，深色 `border=rgb(53,65,79)`、`bg=rgb(20,26,34)`；截图 `borders_light.png` / `borders_dark.png` / `audit_inputs.png`；`tsc` 0 错、`vite build` 通过 |
| 85 | 用户贴了 6 张截图（设置各分区、音色库克隆卡；不含教程页）：①这些位置的输入框/下拉/按钮要对齐美化；②工作台卡片间距过近，对比其它页面 | ①`.field-row` 从 `flex + align-items:flex-end` 改成 `grid-template-columns: repeat(auto-fit, minmax(200px,1fr))`，列宽完全相等、顶部对齐（实测设置页 2/3/4 列分别是 551/551、359×3、264×4，gap 24px）；②`.field` 统一 `gap:6px`、`.label` 行高 1.3、`.field-note` 上移 2px；③控件高度统一 **38px**（`.input/.select`），`.select` 补 `padding-right:32px` 给箭头，`.textarea` 保持 `height:auto; min-height:96px`；④`.segmented` 加 `width:fit-content`（原来撑满整行像空条，实测 194px）；⑤`.field-row > .btn/.toolbar { justify-self:start; align-self:end }` 防止网格把按钮拉宽；⑥`.btn:disabled` 由 `opacity:.5` 改为中性灰底+灰字（原来蓝底发白像"半透明按钮"）；⑦`.clone-step--save` 虚线改实线 + 浅底；⑧**工作台**：`layout.css` 里原本没有 `.workbench` 栅格定义（所以卡片是紧贴的），补 `grid-template-columns: minmax(0,1.12fr) minmax(0,1fr)` + `gap:32px`，`.workbench__column` 同为 32px 栅格，<1180px 退单列 | 实测：工作台 `columns=2、cardGaps=[32,32,32,32,32]、columnGap=32、display=grid`；设置页各字段行等宽；控件高度 `[38,38,38,38,38]`；`tsc` 0 错、`vite build` 通过；截图 `polish_workbench.png` / `polish_settings2.png` / `polish_voices.png` |
| 86 | 用户："我截图里的按钮呢！还没有对齐！主要就是同行按钮直接上下错位了" | 上一轮只做了等宽网格，但同一行里各字段「标签换行 / 多一行说明」高度不同，仍会把控件推上推下（例如「校验 TLS 证书 / 空闲自动停机 / 空闲时长」三个控件不在同一水平线）。改为 **CSS subgrid**：`.field-row` 定义 4 行轨道，`.field-row > .field` 用 `grid-template-rows: subgrid; grid-row: span 4`，于是所有字段的「标签行 / 控件行 / 说明行」严格共用轨道；直接放在行里的 `.btn/.toolbar/.segmented/.field-error` 用 `grid-row: 2; align-self: end` 落到控件行。另：`@supports` 包裹保证老浏览器回退到 `align-items: start`。**踩坑记录**：同一套 subgrid 套到音色库 `.clone-flow`（3 步）时会叠字——因为步骤里元素数不定（命名步骤有 label+input+说明+textarea 4 个），已回滚，克隆卡保持原样 | 验收脚本按"视觉行分组"检查（容差 8px 内算同一行，避免自动换行误报）：`settings badCount=0`、`voices badCount=0`、`workbench badCount=0`；截图 `align_cloud_card.png`（校验 TLS 证书 / 空闲自动停机 / 空闲时长 三个控件同一水平线）、`align_asr_card.png`（ASR 行 5 个控件对齐）、`align_clone_card.png`（克隆卡未被误伤）；`tsc` 0 错、`vite build` 通过、`pytest` 103 passed |
| 87 | 用户：「/spec 最后再仔细检查一遍没有造成功能损坏就打包 zip」 | 先落规格 `docs/spec/SPEC-RELEASE-1.1.41.md`（澄清 → 验收标准 → 风险 → 打包规格），再写自动化回归脚本 `.cache/regression_check.py`（24 项断言）跑一遍，全通过后打包 | 回归 **24/24 PASS**：六路由渲染与标题、无侧栏、Dock 6 tab + 圆钮、顶栏轮播与左标题（left=24）、卡片无 backdrop-filter、输入框边框 rgb(207,214,223)、背景网格在、overflowX=0、Dock bottom 876/900、胶囊仍有 `url(#qwentts-dock-liquid)` 折射、页脚已移除、点 Dock 切页、搜索面板可开关、设置 13 分区可点、输入框可编辑、任务 12 行、音色 9 张、深色主题生效、控制台仅 3 条「云端不可达」400（环境因素，已判为预期）。同时 `pytest` 103 passed、`tsc` 0 错、`vite build` 通过。打包：`python .cache/make_backup.py` 与 `... portable`，产物落 `E:\trae_projects\QWENTTS-backups\`，排除 data/models/.cache/.git/node_modules/config.yaml |
| 88 | 用户追问：「和后端的通信验证了吗」 | 承认上一轮只验了"页面能渲染数据"（隐式经过接口），没有逐接口核对契约。补做 `.cache/verify_backend_api2.py`：按前端 `api.ts` 里的**真实路径**逐个请求并校验关键字段，另用浏览器 `new WebSocket` 验 `/ws/events`，写接口只打校验分支（不产生数据） | **19/19 PASS**：health/meta/system.status（mode,model,local,cloud,asr_candidates）/selfcheck/logs/settings/engines/voices/tasks 列表/tasks 详情/media-info/cloud.engines.host/cloud.engines.jobs/cloud.agent.llm 全 200 且字段齐全；`POST /api/tasks` 空文本 400、`POST /api/settings/mode` 非法值 422；`wss://…/ws/events` 握手成功、收到 2 条事件；云端 `/api/cloud/engines`、`/api/cloud/probe` 返回 400 属"实例未开机"环境因素。过程中修正的是**脚本自身**的两处错误假设：①`/api/capabilities`、`/api/cloud_agent/*`、`/api/cloud_engines/*` 路径猜错（真实前缀 `/api/cloud/agent`、`/api/cloud/engines`，能力在 `/api/meta`），前端从未调用这些错误路径；②`/api/system/status` 字段名是 `model` 不是 `models`。规格文档 `docs/spec/SPEC-RELEASE-1.1.41.md` 追加第 5 章记录 |
| 89 | 用户：「进生产吧，记得还有果冻」+ 两个问题：①滑动时四角会突然闪成直角 ②长按松手切换 demo 里没做 | ①新增 `components/PageSlide.tsx` 落地「方案 2 · 轨道连续滑动」：旧页快照 + 中间页骨架 + 目标页真实渲染；快照由 `App.tsx` 的 `go()` 在**切页之前**抓取（`[data-page-live]` 的 innerHTML），因为 React 卸载后 DOM 就没了；滑动用命令式控制（先 `transition:none` 瞬移到起点、强制回流、下一帧再带过渡走向终点），避免用 state 起点时浏览器从 0 过渡导致方向反了；收尾只清多余槽位、transform 归零，不重建 DOM；②四角直角问题：`.page-stage` 加 `border-radius: 24px` + **`overflow: clip`**（用 clip 而不是 hidden，hidden 会变成滚动容器、破坏页内 `position: sticky` 的设置目录/表头）；③胶囊果冻：`DockNav` 新增 `jelly` 状态（`dy = clamp(relY,±70)*0.12`、`sy = 1 - relY/700`），合成到 transform 的 `translate + scale(2-sy, sy)`，松手归零；④拖拽松手切页走同一个 `go()`，因此也有滑动动画 | 实测：向右翻（工作台→状态）translateX `0 → -553 → -2002`；向左翻（状态→工作台）`-5833 → -5022 → -3158`（反向正确）；跨 4 档时 `slots=5、ghosts=3`（中间「音色库/任务中心/设置」是骨架）；`.page-stage` `radius=24px, overflow=clip`；果冻：鼠标上方 `matrix(0.914,0,0,1.086)`、下方 `matrix(1.086,0,0,0.914)`；拖拽松手落到「设置」；回归 24/24 PASS、`pytest` 103 passed、控制台无非预期错误；截图 `prod_slide_mid.png` |
| 90 | 用户两个问题：①每个页面都不水平居中了 ②每次滑到目标页都会卡一下 | ①原因：`PageSlide` 空闲时仍把页面包在 `display:flex` 的 `.page-track > .page-slot` 里，槽位 `flex:0 0 auto` 会按内容收缩，页面 `margin:0 auto` 失去参照 → 不居中。改为：**空闲态直接渲染 `.page-live` 容器**（普通块级、`width:100%`），只有滑动期间才套 track/slot；②原因：落地那帧同时做「关过渡 + 卸载 5 个槽位（含 50KB 旧页快照 DOM）」→ 一次性重排/GC。改为：落地帧只关过渡 + transform 归零，把 `setSlots(null)` 放到下一帧并用 `startTransition` 低优先级执行 | 实测（1440×900）：居中 `pageLeftGap=0 / pageRightGap=0 / centered=true`（切页前后都是）；滑动落地段（600–900ms）连续 3 次跑都是 `16.6–16.8ms/帧`，无长帧；全程 avg 16.8–18.0ms，仅剩滑动中/收尾后各一次 33ms（合成与延迟卸载），落地瞬间不再卡 |
| 91 | 用户：「算了你恢复一下备份然后只保留果冻效果吧」 | 回滚到 v1.1.42 的前端状态：`git checkout 91e500f -- frontend/src/App.tsx frontend/src/styles/layout.css frontend/src/components/DockNav.tsx`，删除 `components/PageSlide.tsx`（页面切换动画整套下线）；然后在 `DockNav` 里**只**重新加果冻：`jelly {dy, sy}` 状态 + pointermove 计算 `dy = clamp(relY,±70)*0.12`、`sy = 1 - relY/700`，合成 `translate(... + dy) scale(2-sy, sy)`，松手归零；`.docknav__pill` 加 `transform-origin: 50% 50%` | 实测：`hasTrack=false / hasStage=false`（切页无滑动动画，点 tab 直接换页）；页面重新居中 `pageLeftGap=0 / pageRightGap=0`；果冻：鼠标上方 `matrix(0.914,0,0,1.086)`、下方 `matrix(1.086,0,0,0.914)`、松手回 `1,1`；回归 24/24 PASS、`pytest` 103 passed、控制台无非预期错误。按要求**未打包** |
| 92 | 用户暴怒报 4 个阻断缺陷（/spec）：①任务取消是摆设 ②重试只能云端、没有本地 ③工作台生成中只剩"停止"无法追加 ④任务列表在本机啥也看不见 | 先落规格 `docs/spec/SPEC-TASKS-FIX-1.md`（澄清→根因→规格→验收），根因实测如下：①`worker.cancel()` 对运行中任务只 `event.set()` 不写库不推事件，`_execute` 也不复查状态 → 排队中被取消仍会执行；②`api/tasks.py::retry_task` 里 `backend="cloud" if force=="cloud" else "local"` **硬编码 local**，原云端任务重试会掉回本地，且 UI 只有一个重试入口；③`Workbench` 主按钮是三元互斥，streaming 时整块被"取消生成"替换；④`.page--fill` 高度公式**已扣** `--dock-safe-bottom`，`.shell .page` 又统一加 124px padding-bottom → 双重扣减，900px 窗口表格可视区仅 184px（≈3 行）。修复：①取消——排队直接落库 canceled+推事件，运行中置事件同时写日志「已请求取消」+推 `task.cancel_requested`，`_execute` 执行前复查 status；②重试——默认 `backend=(force or row.backend or "local")`，UI 行内拆成「本地重试(HardDrive)」「云端重试(Cloud)」两个入口，toast 按后端说明，快捷键 R 仍等于原后端；③工作台——`.primary-action__row` 内「追加生成」始终存在且生成中可点（`canSubmit` 去掉 streaming 限制），旁边独立「停止」；④CSS——`.page--fill` 只留 `--space-4` 的 padding，`.card--fill .table-wrap` 加 `min-height: clamp(220px,40vh,320px)` | 实测 9/9 PASS：列表 `wrapH=473、可见 8 行`（1920×1080）；取消 `status=canceled / code=CANCELED`（1.5s 内）；重试 `cloud→cloud`、`cloud→local`、`local→cloud` 三种组合全部正确；工作台空闲只有「开始生成」，生成中为 `[追加生成(可点), 停止2.4s]`，停止后回到「开始生成」；全局回归 24/24 PASS、`pytest` 103 passed、控制台 0 error。截图 `tasks_fixed.png` |
| 94 | 用户："设置页面的左栏应该滑动时位置不变" | 根因：v1.1.36 加「液态玻璃表面层」时写了 `.shell .settings-nav { position: relative }`（为了给 `::before` 玻璃层做定位基准），它的优先级 (0,2,0) 高于基础规则 `.settings-nav { position: sticky }`，sticky 被覆盖成 relative → 目录跟着页面滚走。修复：`.shell .settings-nav` 改为 `position: sticky; top: calc(var(--shell-topbar)+var(--space-3)); align-self: start;`，玻璃底层仍由 `::before` 负责 | 实测（1920×900）：修复前 `position=relative`、滚动 600px 后左栏 top 从 220 → -380（跟走）；修复后 `position=sticky`、滚动 600px 后 top 稳定在 **72px**（正好顶栏下方）；`tsc`/`vite build` 通过 |
| 95 | 用户：教程页「用其它 AI 调用」里再加一段可直接发给 AI 的文本，AI 看完就知道怎么接入（不要包含后端启动教程） | 在 `mcpConnect` 章节的 snippets 首位插入「把这段直接发给你的 AI（它会自己配好接入）」：自包含说明包含——①它是什么（stdio / JSON-RPC 2.0 / 协议 2024-11-05、MCP 进程不加载模型）；②连接参数（command=python.exe 绝对路径、args=["-m","app.mcp_server"]、cwd=backend、env 三个可选变量）；③4 个工具签名与返回；④使用建议（排队等待/超时、diarize 用法、download_url）；⑤给 AI 的自检清单（配好 → 列工具 → voice_list → tts_synthesize 验证 → 回报 task_id/耗时/地址） | 实测教程页 `codeBlocks=6`（新增片段排第一）、无控制台错误；`tsc` 0 错、`vite build` 通过 |
| 96 | 用户转来外部 AI 实测反馈（P0–P4 共 9 条）：① `_request()` 没给 JSON body 设 Content-Type → 所有写工具 422；② `language` 无校验，`zh` 先建任务再在引擎 400；③ 任务失败仍 `isError:false`；④ 长任务把 `tools/call` 挂住 600s；⑤ `download_url` 对 Agent 不可用；⑥ 克隆/设计缺席；⑦ `voice_list` 字段太少；⑧ `docs/MCP.md` 漂移（写 v1.1.11、`start/end` vs `start_s/end_s`）；⑨ 强依赖 `cwd=backend` | 全部处理：①`_request()` 只要带 body 就显式 `headers["Content-Type"]=content_type`（json 与 multipart 都走这条）；②新增 `LANG_ALIAS`（zh/cn/中文→chinese…）+ `_check_language()` 在建任务前对照 `/api/voices` 的 languages 校验；③`tools/call` 用 `isError = payload.ok is False`，失败/超时/取消都带 `error_code`；④新增 `tts_submit` / `asr_submit` 立即返回 task_id，其余工具支持 `wait:false`；⑤返回 `output_path`（真实目录是 `data/outputs/<task_id>/<file>`，已实测修正）；⑥新增 `voice_clone`（multipart 传 `/api/voices`）、`voice_design`、`task_cancel`，工具数 4→9；⑦`voice_list` 补 language/gender/age/accent/available 并支持语种过滤；⑧`docs/MCP.md` 按 v1.1.52 重写（9 工具、`start_s/end_s`、错误语义、变更记录表）；⑨新增 `backend/pyproject.toml` 的 `qwentts-mcp = app.mcp_server:main` console script。另加 `python -m app.mcp_server --selftest`（initialize→tools/list→health→voice_list→真实合成→转写回环）与 9 条回归测试 `backend/tests/test_mcp_server.py` | 实测 `--selftest` 6 步全 PASS（`tts` completed、`output_path=...\data\outputs\49e8882a2ccd\49e8882a2ccd.wav`、转写回环拿到 transcript）；`pytest` **112 passed**（原 103 + 新增 9）；`tsc` 0 错、`vite build` 通过 |
