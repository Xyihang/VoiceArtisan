# 文档索引

按「你现在想做什么」选一篇，不用从头读。

## 先读这一篇

| 文档 | 用途 |
| --- | --- |
| [PROJECT_HISTORY.md](PROJECT_HISTORY.md) | **项目记忆**：需求与硬约束、决策轨迹、已踩的坑与修法、实测数据、当前默认值、文件地图、待办 |

## 第一次部署

> 想直接照着手把手做：看 **[CLOUD_ACCESS_TUTORIAL.md](CLOUD_ACCESS_TUTORIAL.md)**（选镜像 → 无卡准备 → 起服务 → 隧道 → 验收 → 一键关机 → 故障速查）。

| 文档 | 适合场景 | 读完能做 |
| --- | --- | --- |
| [QUICKSTART.md](QUICKSTART.md) | 想 10 分钟跑出第一段音频 | 启动服务、放模型、生成、验收清单 |
| [CLOUD_AGENT.md](CLOUD_AGENT.md) | 云主机一键接入 Agent：准备、8 步流程、AI 诊断配置、验收步骤 |
| [LAUNCHER.md](LAUNCHER.md) | 想点鼠标完成安装（不想敲命令） | 用图形引导器选目录/体检/下模型/装依赖/起服务，出 `VoiceArtisan-Setup.exe` |
| [MODELS.md](MODELS.md) | 不知道下哪个模型、下载慢 | 拿到直链 / 脚本下载、目录结构、完整性校验 |
| [CONFIG.md](CONFIG.md) | 想改模式、显存、ASR、限额 | 逐项配置说明 + 环境变量速查 + 两份预设 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 报错了 | 按错误码定位原因与修法 |

## 接云端 GPU

| 文档 | 适合场景 |
| --- | --- |
| [CLOUD_SETUP.md](CLOUD_SETUP.md) | 从零租云卡 → 部署云端服务 → 本地连上（含 SSH 隧道、成本控制） |
| [CLOUD_PROVIDERS.md](CLOUD_PROVIDERS.md) | 不知道该租哪家：平台对比、价格区间、选型检查清单、避坑 |
| [../cloud/README.md](../cloud/README.md) | Docker / 裸机部署云端、容器参数、安全清单 |

## 本轮新增 / 更新的文档

| 文档 | 内容 |
| --- | --- |
| [VERSION_POLICY.md](VERSION_POLICY.md) | 版本锁定规则：`==` 唯一、锁文件必须纯 ASCII、pip 缓存位置、冲突立即中止 |
| [ENGINE_ISOLATION.md](ENGINE_ISOLATION.md) | 每引擎独立 venv + 子进程推理桥；venv 位置、体积、手动修法 |
| [ENGINE_RESEARCH.md](ENGINE_RESEARCH.md) | 6 个引擎的国内可获取性、体积、许可证、4GB 可行性核实 |
| [INTERACTION_GUIDELINES.md](INTERACTION_GUIDELINES.md) | 交互/排版/间距/色彩/动效/QA 闸门标准 + 已实现快捷键 |
| [spec/HARDENING-1.md](spec/HARDENING-1.md) | 全量自检规格：一条命令跑 20 项检查（静态/接口/引擎/界面/数据），交付前门禁 |
| [spec/ENGINE-AGNOSTIC-1.md](spec/ENGINE-AGNOSTIC-1.md) | 引擎无关化规格：音色可用性、能力页签门禁、切换按钮卡死修复、文案去 1.7B 硬编码 |
| [spec/UI-POLISH-1.md](spec/UI-POLISH-1.md) | 对齐/留白/弹窗居中/自适应高度的规格与实测数据 |
| [spec/CLOUD-AGENT-1.md](spec/CLOUD-AGENT-1.md) | 云主机一键接入 Agent 规格（含 §5A LLM 决策层） |
| [spec/ASR-ENGINE-1.md](spec/ASR-ENGINE-1.md) | ASR 纳入引擎管理规格 |
| [spec/PHASE-P1.md](spec/PHASE-P1.md) | P1 批次规格：说话人区分 / 批量队列 / 补全生成参数 |
| [spec/PHASE-3-BACKLOG.md](spec/PHASE-3-BACKLOG.md) | 第三部分待办与说话人区分调研结论 |
| [spec/GUIDE-1.md](spec/GUIDE-1.md) | 应用内「教程」页规格：覆盖设置 11 个分区 + 常见错误速查、搜索、跳转验收标准 |
| [LAUNCHER.md](LAUNCHER.md) | 安装器说明（已移除下载模型、新增 venv 选择） |

## 能力索引（说话人区分与导出）

| 能力 | 说明 | 文档 |
| --- | --- | --- |
| 云端说话人区分 | `POST /v1/engine/asr` 支持 `diarize`/`max_speakers`/`min_speakers`，响应回 `diarize_backend`；云端需装 CAM++，由 `GET /v1/health` 的 `asr.diarize` 协商；偏好 `asr.diarize.prefer`（`local`/`cloud`/`auto`） | [API §7](API.md) · [CONFIG §asr](CONFIG.md) · [TROUBLESHOOTING §四之二](TROUBLESHOOTING.md) |
| 云端引擎远程管理 | 本地后端通过 SSH 管理**云端**引擎：列表（含手工直传的模型）/ 安装 / 删除 / 切换当前；与本机引擎分区完全分开。规格见 `spec/CLOUD-ENGINES-1.md` | [API](API.md) · [spec/CLOUD-ENGINES-1.md](spec/CLOUD-ENGINES-1.md) |
| 按说话人拆分导出 / 重导出 | `POST /api/tasks/{id}/split-by-speaker` + `GET /api/tasks/{id}/speakers/zip`；`POST /api/tasks/{id}/export` 从无损原件 `source.wav` 换档重导出；`POST /api/tasks/{id}/speakers/merge` 合并说话人 | [API §5](API.md) · [CONFIG §export](CONFIG.md) · [TROUBLESHOOTING §四之二](TROUBLESHOOTING.md) |

## 运维与交付

| 文档 | 适合场景 |
| --- | --- |
| [DEPLOY.md](DEPLOY.md) | 环境要求、venv 安装、systemd / Windows 服务、Nginx 反代、备份升级、端口清单 |
| [DELIVERY-AND-CLOUD-VERIFY.md](DELIVERY-AND-CLOUD-VERIFY.md) | **发版与云端实证手册**：exe/源码包/便携包怎么打、打包必排项、云端模型直传、diarize 实证命令与踩坑（av.open / 配置覆盖） |
| 视频配音（规划中） | 见 `spec/MCP-EXPANSION-1.md` 第 8 节：可行性、MVP 范围、工作量与风险 | [spec/MCP-EXPANSION-1.md](spec/MCP-EXPANSION-1.md) |
| 液态玻璃 Dock（规划中） | 去侧栏 + 底部 Dock + 独立搜索圆钮的规格与验收 | [spec/SPEC-LIQUID-DOCK-1.md](spec/SPEC-LIQUID-DOCK-1.md) |
| [PACKAGING-AND-TRIBAL-KNOWLEDGE.md](PACKAGING-AND-TRIBAL-KNOWLEDGE.md) | **维护者手册（经验文档）**：打包/发版全流程、打包脚本硬规则、PyInstaller 占用处理、按模块的踩坑清单（依赖与 torch、安装进度、云端、其它历史事故）、打包后抽查清单、排障速查 |
| [UPGRADE.md](UPGRADE.md) | 独立升级工具 `QWENTTS-Upgrader.exe` 的使用方法与保留/覆盖规则 |
| [BENCHMARK.md](BENCHMARK.md) | 实测数据：1.7B 在 4GB 卡、流式 vs 非流式、云端链路、验收结论 |
| [API.md](API.md) | 接口文档：请求/响应示例、WebSocket 事件、`/v1` 云端接口、错误码 |
| [COMPETITIVE_VOICESTUDIO.md](COMPETITIVE_VOICESTUDIO.md) | 竞品与功能差距：GitHub VOICESTUDIO 调研、24 项功能对比、缺失功能排序与路线建议 |
| [ENGINE_RESEARCH.md](ENGINE_RESEARCH.md) | 引擎调研：CosyVoice 3/2/300M、IndexTTS-2、F5-TTS、VoxCPM、Spark-TTS 等在 ModelScope/hf-mirror 的真实体积、许可证、显存与 4GB 可行性 |

## 二次开发

| 文档 | 适合场景 |
| --- | --- |
| [DESIGN.md](DESIGN.md) | 前端设计令牌表、页面结构、组件清单、响应式与无障碍 |
| [API.md](API.md) | 前后端契约；接自有系统时按此对接 |
| [MCP.md](MCP.md) | 给外部 Agent 接入：stdio 启动、Claude Desktop / Cline 配置、4 个工具参数与返回、环境变量、能力边界（无 SSE/resources/prompts） |
| [../README.md](../README.md) | 目录结构、能力对照表、常用脚本 |

## 常用脚本速查

| 脚本 | 作用 |
| --- | --- |
| `scripts/start.ps1` / `scripts/start.sh` | 一键启动（检查依赖、构建前端、自检、起服务）；`--selfcheck` 只自检，`--cloud` 以云端角色启动 |
| `scripts/download_models.py` | 下载模型：`--size 1.7B`、`--verify`、`--links` |
| `scripts/run_infer.py` | 单跑一次推理（定位问题）：`--dry-run`、`--offload`、`--device cpu` |
| `scripts/smoke_test.py` | 接口冒烟（22 项） |
| `scripts/full_test.py` | 全功能验收（32 项，默认要求 1.7B） |
| `scripts/benchmark.py` | 首包/RTF/显存实测，可写入 BENCHMARK.md |
| `scripts/ui_check.py` / `ui_check_failure.py` | 前端截图与失败路径检查 |
| `download-models.bat` / `.sh` | 双击/一键下载 1.7B 三件套 |
