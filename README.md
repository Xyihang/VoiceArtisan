# 声匠 · VoiceSmith —— 多引擎语音工作台（本地 + 云 GPU）

一套可交付的语音生成产品：**本地程序 + 云端 GPU 推理后端**，本地与云端均运行 Qwen3-TTS 1.7B（12Hz）系列模型，
覆盖**语音生成、声音克隆、音色设计、音频转文字**四类核心能力，支持「仅本地 / 仅云端 / 混合」三种运行模式，
前端为明暗双主题的产品级界面。

```
┌─────────────────────────── 本地程序（Windows / Linux） ───────────────────────────┐
│  React 前端（5 页面）  ──WS 实时推送──  FastAPI（HTTPS + 自签证书）                │
│                                          │                                        │
│                            任务队列 / SQLite 持久化 / 执行器                        │
│                                          │                                        │
│              ┌───────────────────────────┴────────────────────────────┐           │
│              │ 路由（仅本地/仅云端/混合，按任务与显存自动选址）        │           │
│              └───────┬────────────────────────────────────┬───────────┘           │
│                      │ 本地 Qwen3-TTS 1.7B (CUDA)          │ HTTP(S) + X-API-Key    │
│                      │ · Base（克隆）                      │ ↑ 可选 SSH 端口转发    │
│                      │ · CustomVoice（内置音色+指令）      │                       │
│                      │ · VoiceDesign（音色设计）           │                       │
│                      └─────────────────────────────────────┴───────────┐           │
└──────────────────────────────────────────────────────────────────────┼───────────┘
                                                                       ▼
                                          云端 GPU 推理服务（同一份代码 /v1 接口，Docker）
```

## 部署方式总览（先选一条路）

| 你的情况 | 推荐路线 | 看这篇 |
| --- | --- | --- |
| 有 NVIDIA 显卡（≥8GB），想全本地 | `run.mode: local` + 1.7B 三件套 | [QUICKSTART](docs/QUICKSTART.md) |
| 只有 4GB 显存 | 本地 `float16` 直接跑 1.7B（已实测可行），或切云端 | [MODELS §4](docs/MODELS.md) |
| 没有独显 / 不想占本机资源 | 云端 GPU + 本地只当界面（`run.mode: cloud`） | [CLOUD_SETUP](docs/CLOUD_SETUP.md) |
| 想两边都用、自动择优 | `run.mode: hybrid`（本地跑得动就本地，跑不动转云端） | [CONFIG](docs/CONFIG.md) |
| 只是先看看界面 | `mock.enabled: true`（占位引擎，不出真实语音） | [QUICKSTART §2](docs/QUICKSTART.md) |
| 想用鼠标点完安装（选目录→体检→下模型→装依赖→起服务） | 图形安装引导器 `launcher/qwentts_launcher.py` / `QWENTTS-Setup.exe` | [LAUNCHER](docs/LAUNCHER.md) |
| 要部署成长期服务 | systemd / Windows 服务 / 反代 + 备份升级 | [DEPLOY](docs/DEPLOY.md) |
| 不知道怎么用某个设置项 | 界面左下角「教程」：13 个分区逐项讲解 + 常见错误速查 | [spec/GUIDE-1](docs/spec/GUIDE-1.md) |

配置预设可直接用：`config/config.local-gpu.example.yaml`、`config/config.cloud-only.example.yaml`。

## 快速开始

```powershell
# Windows（PowerShell，首次会安装依赖并构建前端）
.\scripts\start.ps1
```

```bash
# Linux / macOS
./scripts/start.sh
```

启动后自动打开 `https://localhost:8443`。首次访问会有自签证书提示（点击「高级 → 继续前往」即可）。

**没有模型也能先跑通界面**：把 `config/config.yaml` 的 `mock.enabled` 设为 `true` 使用占位引擎联调（界面会标注「模拟引擎」）。

### 模型下载（项目不自动下载，请手动获取）

本地默认运行 **1.7B 三件套**（语音生成 / 声音克隆 / 音色设计），国内直连链接与目录要求见 **[docs/MODELS.md](docs/MODELS.md)**：

```bash
# 打印国内可访问链接与目标目录
python scripts/download_models.py --links

# 也可以让脚本从 ModelScope 直连下载（约 12.6GB）
python scripts/download_models.py
```

下载后放在 `<项目>/models/Qwen3-TTS-12Hz-1.7B-{Base,CustomVoice,VoiceDesign}/`，运行自检校验：

```bash
cd backend && python -m app.main --selfcheck
```

> **显存与 1.7B（本机 RTX 3050 4GB 实测，桌面走核显）**：
> - 1.7B **可以在 4GB 显卡上跑**：`dtype: float16` + `vram_reserve_gb: 0.1` + `allow_vram_overcommit: true`，
>   靠 WDDM 共享内存小幅超额（实测显存峰值 4049MB）；三种能力实测 RTF 0.08–0.21、首包 9–25s；
> - 前提是桌面/浏览器不占用独显；独显被占导致 OOM 时返回 `VRAM_INSUFFICIENT`，界面可「一键切换到云端」；
> - 无 GPU 或独显被占用时可把 `local.device` 改为 `cpu`（float32 + eager，功能完整、约慢 3-5 倍）；
> - `local.low_vram`（CPU/磁盘 offload）默认关闭：qwen-tts 0.1.1 的生成循环不兼容 accelerate offload；
> - 默认**不会**降级到 0.6B（需显式 `local.allow_small_fallback: true`）。

## 能力对照

| 编号 | 能力 | 实现 |
| --- | --- | --- |
| F1 | 文本转语音 | `POST /api/tts`，内置音色 + 自定义参考音色 |
| F2 | 声音克隆 | `POST /api/clone`，3 秒参考音频零样本克隆，支持跨语言 |
| F3 | 音色设计 | `POST /api/design`，自然语言描述生成音色 |
| F4 | 音频转文字 | `POST /api/transcribe`（本地 faster-whisper / 云端接口） |
| F5 | 内置音色 | 9 个精选音色（男女/年龄/中英日韩/四川话/北京话）+ 动态说话人列表 |
| F6 | 指令控制 | `instruct` 字段，自然语言控制语气/情感/语速/风格 |
| F7 | 流式合成 | 句级分块 + WebSocket 推送 + Web Audio 队列，首包即播 |
| F8 | 参数控制 | 语速、音高、温度、Top P/K、重复惩罚、采样步数、wav/mp3 |
| F9 | OpenAI 兼容接口（/v1/audio/speech、/v1/audio/transcriptions） | 现成 OpenAI SDK/客户端可直接调用；另有 `/v1/models`、`/v1/audio/voices`（差异见 [API §8](docs/API.md)，不支持 `stream`） |
| F10 | 文本规范化 | 数字/单位/型号/日期 → 中文读法（`tts.normalize`，可开关、幂等；任务级 `params.normalize` 覆盖，见 [CONFIG §tts](docs/CONFIG.md)） |
| T1-T8 | 任务中心 | 列表/详情/状态流转/下载/删除/批量删除/重试 + SQLite 持久化 + 每次生成后回收显存 |
| T9 | 字幕导出 | `GET /api/tasks/{id}/subtitles?format=srt\|vtt\|ass`，按分块时间轴生成（毫秒精度、`chunks.json` 落盘），任务详情内可下载 |
| F11 | 多引擎/模型管理（安装器或设置页增删） | `GET/POST/DELETE /api/engines*`（列表/后台安装+事件推送/删除/切换/健康）+ `python -m app.engines_cli`；注册表共 **13 个引擎**：**7 个 TTS**（`qwen3-tts-1.7b` 默认 / `qwen3-tts-0.6b` / `cosyvoice3-0.5b` / `cosyvoice2-0.5b` / `cosyvoice-300m-sft` / `voxcpm1.5` / `indextts2`）+ **5 个 ASR**（faster-whisper `tiny`~`large-v3`）+ **1 个声纹**（CAM++，供说话人区分），见 [API §10](docs/API.md)、[CONFIG §engine](docs/CONFIG.md) |
| F12 | 引擎依赖隔离（每引擎独立 venv） | 除默认引擎走主环境就地推理（`isolation=inprocess`），其余引擎依赖与 torch 全部装在 `<models_dir>/<engine_id>/.venv`，合成走 `runner.py` 子进程；**主环境 torch 2.4.1+cu121 不会被任何安装动作替换**，见 [ENGINE_ISOLATION](docs/ENGINE_ISOLATION.md) |
| N1-N7 | 双后端 | 直连配置、SSH 隧道、三模式切换、混合路由、空闲停机、就绪探测+重试、状态可见 |
| D1-D6 | 部署 | 自签 HTTPS、一键启动、加载提示与复用、单独推理脚本、镜像下载、启动自检 |
| §4 | 前端 | 设计令牌、明暗主题、5 页面、流式播放器、实时推送、三档响应式、键盘可达 |

## 目录结构

```
QWENTTS/
├─ backend/app/                 # FastAPI 后端（推理、路由、任务、接口）
│  ├─ main.py                   # 应用入口（HTTPS、静态前端、自检）
│  ├─ config.py                 # 配置加载（yaml + 环境变量 + 运行期覆盖）
│  ├─ engine/                   # 本地/云端/模拟引擎、显存管理、ASR、路由
│  │  ├─ registry.py            # 引擎注册表（多引擎/模型）
│  │  ├─ manager.py             # 安装/删除/切换编排（后台线程 + 事件推送）
│  │  └─ adapters/              # 适配器：qwen（默认）/ cosyvoice3 / cosyvoice2 / cosyvoice-300m-sft / voxcpm1.5 / indextts2
│  ├─ engines_cli.py            # 引擎管理命令行（安装器用，不依赖 HTTP）
│  ├─ api/                      # REST + WebSocket + 云端 /v1 接口
│  ├─ worker.py                 # 任务队列与执行器（流式、取消、重试、显存回收）
│  ├─ tunnel.py                 # SSH 端口转发（paramiko direct-tcpip）
│  ├─ db.py                     # SQLite 持久化（任务/音色/资产）
│  └─ selfcheck.py              # 启动自检与修复指引
├─ frontend/src/                # React + TS 前端
│  ├─ styles/                   # 设计令牌 + 布局 + 组件样式
│  ├─ components/               # 播放器/波形/音色选择/参考音频/参数面板…
│  ├─ pages/                    # 工作台、音色库、任务中心、设置、状态与日志
│  └─ lib/                      # API 客户端、WS 客户端、Web Audio 引擎
├─ cloud/                       # 云端容器化部署（Dockerfile + compose + 说明）
├─ scripts/                     # 一键启动、模型下载、单次推理、冒烟测试、UI 检查
├─ config/config.example.yaml   # 配置模板
├─ docs/                        # 接口文档、部署运维、前端设计说明、实测报告
├─ models/                      # 模型目录（下载后生成）
└─ data/                        # 运行期数据：SQLite、上传、输出、日志、证书
```

## 常用命令

```bash
python scripts/smoke_test.py            # 接口冒烟（22 项）
python scripts/ui_check.py              # Playwright UI 检查 + 截图
python scripts/run_infer.py --kind custom_voice --text "测试"   # 单独跑推理（定位问题）
python -m app.main --selfcheck          # 只跑启动自检（在 backend/ 下）
python -m app.engines_cli list --json   # 查看引擎/安装状态（在 backend/ 下）
python -m app.engines_cli deps voxcpm1.5 --json   # 看某个引擎的依赖/venv/安全安装命令
python -m app.engines_cli verify voxcpm1.5        # 只按锁文件校验版本（不下载、不安装）

> ⚠️ 不要在**主环境**直接执行 `pip install <模型包>`：`voxcpm` 这类包声明 `torch>=2.5`，
> 会把主环境 `torch 2.4.1+cu121` 换成 CPU 版（真实事故）。引擎依赖一律装进它自己的 venv，
> 命令用卡片上的「复制安装命令」或 `engines_cli deps <id>` 输出的安全形式，详见
> [docs/ENGINE_ISOLATION.md](docs/ENGINE_ISOLATION.md)。
> 引擎依赖的版本**一律**来自 `backend/app/engine/adapters/locks/<engine_id>.txt`（逐行 `包名==精确版本`），
> 安全命令形如：
> ```powershell
> # 1) 引擎主包（--no-deps，精确版本）
> "<models>\voxcpm1.5\.venv\Scripts\python.exe" -m pip install --no-cache-dir --no-deps -c "<models>\voxcpm1.5\.venv\constraints.txt" "voxcpm==2.0.3"
> # 2) 引擎依赖（按锁文件精确安装）
> "<models>\voxcpm1.5\.venv\Scripts\python.exe" -m pip install --no-cache-dir -c "<models>\voxcpm1.5\.venv\constraints.txt" -r "backend\app\engine\adapters\locks\voxcpm1.5.txt"
> ```
> 装完会按锁文件逐包校验，不一致直接报 `VERSION_MISMATCH`，详见 [docs/VERSION_POLICY.md](docs/VERSION_POLICY.md)。
```

## 文档

- [模型获取（国内链接）](docs/MODELS.md)
- [云 GPU 接入教程](docs/CLOUD_SETUP.md)
- [云平台推荐](docs/CLOUD_PROVIDERS.md)
- [项目开发轨迹（记忆）](docs/PROJECT_HISTORY.md)
- [健康度治理规格（HEALTH-1）](docs/spec/HEALTH-1.md)
- [接口文档](docs/API.md)
- [部署与运维](docs/DEPLOY.md)
- [前端设计说明](docs/DESIGN.md)
- [实测报告](docs/BENCHMARK.md)
- [云端部署](cloud/README.md)

## 许可证

本项目为**专有软件**，**保留所有权利（All Rights Reserved）**：未经版权所有人书面授权，
任何人不得使用、复制、修改、分发、商用或创作衍生物。详见 [LICENSE](LICENSE)。

第三方依赖与模型（含 Qwen3-TTS 权重，Apache-2.0，需自行下载）受各自许可约束，
见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

版权所有人：xiyihang · 如需商业授权 / 二次开发许可，请联系 xiyihang360@163.com。
## 约束与说明

- 路径、密钥、地址全部来自配置（`config/config.yaml` + 环境变量 + 界面运行期覆盖），代码中无硬编码。
- 本地与云端默认均使用 1.7B；默认**不**降级到 0.6B（仅 `local.allow_small_fallback: true` 时作为显式标注的回退）。
- 显存不足时优先用 CPU/磁盘 offload 保住 1.7B 能力（`local.low_vram`），关闭则给出明确提示并可一键切云端。
- 所有运行期数据（数据库、上传、输出、日志、证书、缓存）默认落在项目目录，不写系统盘。
- 商用前请确认 Qwen3-TTS 模型许可证与所用音色的授权。

## 近期变更（2026-10-21 · v1.1.11）

| 类别 | 变化 |
| --- | --- |
| 说话人区分 | 工作台「音频转文字」新增「区分说话人 / 最多 N 人」开关（任务级，覆盖全局设置）；「最多 N 人」现在**真的参与聚类**（超过上限合并最相似的簇）；转写进度按声纹阶段推进、可取消；任务中心可把 SPK1 改成真实姓名 |
| 转写可靠性 | 修复 ASR 引擎相对导入越界（开开关就 ImportError）、whisper 模型目录未收敛到 snapshots（model.bin 找不到）、本地模式下仍回退云端导致「任务卡住无日志」；云端不支持说话人区分时明确提示而不是静默出纯文本 |
| 批量队列 | 批次进度实时轮询 + 一键取消批次 + 只重试失败项 + 清空当前批次（前端入口在任务中心工具栏） |
| 后期处理链 | 新增 `config.post`（低切 → 高架 → 压缩 → 峰值归一化），全开关、纯 numpy 实现；实测峰值写回任务参数 |
| 导出档位 | 新增 `export.tier`：Original（模型原生 WAV）/ Standard（44.1k 单声道 MP3 192k）/ For editing（48k 24bit WAV）；可在设置里设默认、工作台按任务覆盖；`GET /api/tasks/{id}/media-info` 读真实文件头返回采样率/声道/位深/体积 |
| 时长控制 | 新增 `target_duration_s`（工作台高级参数），优先级高于语速，保音高伸缩 + 截断/补静音兜底 |
| MCP Server | 新增 `backend/app/mcp_server.py`（stdio，行分隔 JSON-RPC）：`tts_synthesize` / `asr_transcribe` / `task_status` / `voice_list`，启动 `cd backend && python -m app.mcp_server` |
| 字幕 | 字幕导出支持「带/不带说话人前缀」开关（`/api/tasks/{id}/subtitles?speakers=false`） |

## 近期变更（2026-10-19）

| 类别 | 变化 |
| --- | --- |
| 品牌 | 产品名改为 **声匠 · VoiceSmith**（左上角、浏览器标题、安装器标题）；`Qwen3-TTS` 仍作为默认**引擎**名保留 |
| 多引擎 | 7 个引擎可注册/安装/切换：`qwen3-tts-1.7b`（默认，主环境）/ `qwen3-tts-0.6b`（小显存，2GB 可跑，约 3GB 下载，界面里自选）+ `cosyvoice3-0.5b` / `cosyvoice2-0.5b` / `cosyvoice-300m-sft` / `voxcpm1.5` / `indextts2` |
| 依赖隔离 | 非默认引擎各自一个 venv（`models/<id>/.venv`）+ 子进程 `runner.py`，**主环境 torch 永远 2.4.1+cu121** |
| 版本钉死 | 6 个 `locks/<engine_id>.txt`（纯 ASCII、逐行 `==`）+ `requirements.pins.txt`；装完逐包校验，不一致报 `VERSION_MISMATCH` |
| 安装加固 | pip 强制 UTF8、`-r/-c` 自动 ASCII 净化、错误分类（镜像缺包换源 / 版本冲突立即中止）、pip 缓存改到 `.cache/pip` |
| 前端交互 | `Ctrl/⌘+K` 命令面板、任务中心 `j/k` 行内操作与 URL 筛选、工作台 `Ctrl+Enter` 与记忆、设置页锚点导航+搜索+测试连接、状态页自动刷新、音色库一屏克隆 |
| 布局自适应 | 设置页导航悬浮视口高；任务中心一屏占满（表格内滚、分页贴底）；333/760px 零横向滚动；弹窗 `Portal` 固定居中并锁背景滚动 |
| 安装器 | 移除「下载模型」步骤（改到界面里装）；新增「系统 Python / 项目内 venv」选择；`--make-venv` CLI |

> 云端接入手把手教程：[docs/CLOUD_ACCESS_TUTORIAL.md](docs/CLOUD_ACCESS_TUTORIAL.md)

文档索引见 [docs/README.md](docs/README.md)；决策与踩坑记录见 [docs/PROJECT_HISTORY.md](docs/PROJECT_HISTORY.md)。
