# 声匠 · VoiceSmith 运行教程（从安装到接入云端）

> 适用版本：v1.1.59 ｜ 阅读顺序建议：第 1 章 → 第 3 章 → 第 6/7 章按需
> 全文出现的路径都是**压缩包解压后的相对路径**，例：`start.bat` 就是解压目录下的那个文件。

---

## 0. 五分钟跑起来（先看这一节）

```
1) 解压到非系统盘，路径别带中文和空格        例：D:\VoiceSmith
2) 装 Python 3.10（64 位），勾选 Add python to PATH
3) 双击 start.bat                          → 自动建虚拟环境、装依赖、开浏览器
4) 打开 https://localhost:8443              → 浏览器提示证书不安全时点「高级 → 继续前往」
5) 下载模型放进 models\                      → 见第 4 章
6) 「设置 → 引擎与模型」点「安装」→「切换」   → 回工作台就能出音
```

---

## 1. 压缩包里都有什么（文件位置速查）

| 你想做的事 | 文件位置（解压后相对路径） |
| --- | --- |
| **一键启动（推荐）** | `start.bat` （Windows 双击）／`scripts/start.sh`（Linux/macOS） |
| 图形化启动器（选目录→体检→装依赖→起服务） | `launcher/dist/QWENTTS-Setup.exe` |
| 启动器源码（想自己改） | `launcher/qwentts_launcher.py`、`launcher/launcher.json` |
| 手动启动命令 | `scripts/start.ps1`（PowerShell） |
| 直接跑后端（排查用） | `backend/app/main.py` → `cd backend && python -m app.main` |
| **下载模型** | `download-models.bat` / `download-models.sh` / `scripts/download_models.py` |
| 模型存放目录 | `models/`（结构见第 4 章） |
| 前端构建产物（免装 Node） | `frontend/dist/` |
| 后端源码 | `backend/app/` |
| 配置文件（首次启动自动生成） | `config/config.yaml`（模板：`config/config.example.yaml`） |
| 配置预设 | `config/config.local-gpu.example.yaml`、`config.config.cloud-only.example.yaml` |
| 运行数据（库/产物/日志/证书） | `data/`（首次启动生成） |
| 全局文档 | `docs/` |
| 授权协议 | `LICENSE-EULA.txt` |
| 云端部署用 Docker | `cloud/Dockerfile`、`cloud/docker-compose.yml` |
| 升级工具 | `tools/upgrader/dist/QWENTTS-Upgrader.exe`、`tools/upgrader/qwentts_upgrader.py` |
| 界面演示页（纯前端，给别人看功能用） | `demo/` （若包内含） |

> 打不开 `start.bat` 时：右键 →「以 PowerShell 运行」，能看到真实报错行。

---

## 2. 安装准备（三件事）

### 2.1 Python

- 版本：**3.10（64 位）**，其它版本不保证能用
- 安装时**必须勾选** `Add python.exe to PATH`
- 验证：`python -V` 输出 `Python 3.10.x`

### 2.2 显卡（本地跑必需，纯云端可跳过）

| 显存 | 建议 |
| --- | --- |
| ≥ 8GB | Qwen3-TTS 1.7B 三件套，流畅 |
| 4GB | 用 0.6B，或 `float16` 直跑 1.7B |
| 无独显 | 用「仅云端」模式（第 7 章），本地只当界面 |

驱动：NVIDIA 官方驱动 + CUDA 12.1 运行时（装 torch 时脚本会自动拉 cu121 版）。

### 2.3 磁盘与网络

- 磁盘：模型约 **13GB**（1.7B 三件套），建议留 30GB 以上
- 网络：下载走国内镜像（ModelScope / 清华 pypi / 阿里 npm），首次装依赖约 10–20 分钟

---

## 3. 首次启动

### 方式 A：双击 `start.bat`（最简单）

它会依次做：检查 Python → 建 `.venv` → 装 `requirements*.txt` → 生成 `config/config.yaml` → 自检 → 启动服务 → 打开浏览器。

### 方式 B：图形启动器 `launcher/dist/QWENTTS-Setup.exe`

适合第一次装、想看得见进度的人：选目录 → 体检（Python/显卡/磁盘/端口）→ 一键装依赖 → 起服务。
启动器自己的配置存在 `launcher/launcher.json`（记录上次选的目录）。

### 方式 C：手动（排查问题时用）

```powershell
cd <解压目录>
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install -r requirements-torch.txt --index-url https://download.pytorch.org/whl/cu121
cd backend
python -m app.main            # 加 --no-browser 不自动开浏览器
python -m app.main --selfcheck # 只做体检，不起服务
```

### 启动成功后

- 地址：<https://localhost:8443>（HTTPS，自签证书）
- 首次访问：浏览器 →「高级」→「继续前往 localhost」
- 端口被占用：改 `config/config.yaml` 里 `server.port`

---

## 4. 模型放哪里

模型**不随包分发**，需要自己下载后放进 `models/`：

```
models\
├─ Qwen3-TTS-12Hz-1.7B-Base\            # 声音克隆
├─ Qwen3-TTS-12Hz-1.7B-CustomVoice\     # 语音生成（内置音色）
├─ Qwen3-TTS-12Hz-1.7B-VoiceDesign\     # 音色设计
├─ Qwen3-TTS-12Hz-0.6B-Base\            # 低显存备选
├─ Qwen3-TTS-12Hz-0.6B-CustomVoice\
├─ faster-whisper\                      # 语音识别（可按档位建子目录）
│   └─ small\
├─ cosyvoice3-0.5b\  voxcpm1.5\ ...     # 其它引擎（装了哪个才需要）
└─ speaker\                             # CAM++ 声纹（区分说话人）
```

**下载方式**：

```powershell
# 图形化：双击 download-models.bat（会问你要下哪几档）
# 命令行：
python scripts\download_models.py                 # 默认 1.7B 三件套
python scripts\download_models.py --size 0.6b     # 低显存
python scripts\download_models.py --out models    # 指定目录
```

放好模型后到「设置 → 引擎与模型」点对应引擎的 **安装**（校验 + 建 venv）→ **切换**（设为当前引擎）。

> 各模型许可不同（部分仅限研究/非商业），商用前请自行确认，见 `docs/MODELS.md`。

---

## 5. 界面导览（底部 Dock 六个入口）

| 入口 | 干什么 |
| --- | --- |
| **工作台** | 写文本、选音色、点开始生成；右侧出播放器与运行状态（首包/实时率/显存）。四个页签：语音生成 / 声音克隆 / 音色设计 / 音频转文字 |
| **音色库** | 9 个内置音色 + 自定义克隆；试听、命名、复用 |
| **任务中心** | 全部任务，可筛选、批量提交、展开看详情（**展开后有播放器**）、重试（本地/云端）、取消、导出 |
| **设置** | 运行模式、云端连接、SSH 隧道、引擎与模型、云端引擎管理、默认参数、授权 |
| **状态与日志** | 健康检查、显存、模型加载、隧道状态、原始日志（排错先看这页） |
| **教程** | 21 个分区逐项讲解 + 常见错误速查 |

---

## 6. 本地引擎安装与切换

1. 设置 → 引擎与模型，选「语音生成 / 语音识别 / 声纹」分组
2. 点 **安装**：会校验模型文件、创建独立 venv、装该引擎依赖
   - **关掉页面不影响安装**：进度在后端继续跑，重新打开会自动续接
3. 安装完成点 **切换**，右上角「当前引擎」会变
4. 显存不够时：切 0.6B，或设置里把 `local.device` 改 `cpu`（慢约 25 倍）

---

## 7. 接入云端（以 AutoDL 为例）

> 适用场景：本机没显卡 / 显卡太小 / 想跑得更快。云端只跑推理，界面还是本机这个。

### 7.1 先在云端准备好机器

1. 租一台 GPU 实例（AutoDL / 其它云均可），确保**开机**
2. 在实例详情页找到 **登录命令**，形如：
   ```
   ssh -p 41425 root@connect.nmb1.seetacloud.com
   ```
   以及旁边的**密码**（实例重装后密码会变，务必用最新的）

### 7.2 方案一：一键接入（推荐，全自动）

设置 → 云端连接 → **一键接入云主机**：

| 模式 | 什么时候用 | 它会做什么 |
| --- | --- | --- |
| **完整安装**（默认） | 云端是干净的机器 | 上传源码 → 装依赖 → 下模型 → 起服务 → 建隧道 → 写本地配置 |
| **已装好，只连通并启动** | 云端已经有声匠（依赖与模型都装好） | 只做 SSH 连通 → 复用 `云端项目目录` → 写配置 → 起服务 → 建隧道 |

填写要点：
- 主机/端口/用户名：把 7.1 的登录命令**整行粘进「粘贴登录命令」**，点「填入」自动识别
- 密码：填控制台里的最新密码（只存在内存，不落盘到日志）
- 云端服务端口：默认 8443；本地映射端口：默认 18443
- 云端项目目录：默认 `~/qwentts`（「已装好」模式必须指向真实存在的目录，含 `backend/app/main.py`）

跑完后：本地配置自动切到「仅云端」并通过隧道访问。

### 7.3 方案二：手动配 SSH 隧道

设置 → SSH 隧道：填主机/端口/用户名/密码 → 「测试连接」→ 「建立隧道」。
之后到 云端连接 填 `https://127.0.0.1:<本地映射端口>` 与 API Key。

### 7.4 云端引擎管理（装/删云端模型）

设置 → **云端引擎管理**：
- 顶部显示当前云端主机（`root@host:port · 密码已加密保存`）
- 「从接入配置同步」：把一键接入/隧道里的主机信息一键带过来（只补空字段）
- 「测试连接」：验证 SSH 与项目目录
- 「刷新列表」：读云端已装引擎与体积
- 每个引擎右侧可 **安装 / 删除**；安装进度同样支持关页面后续接

> 如果显示「云端 SSH 登录失败（密码或密钥可能已变更）」→ 到控制台复制最新登录命令，点「修改」重新填密码再保存。
> 如果显示「云端项目目录里没有声匠源码」→ 换目录，或改用「完整安装」模式。

### 7.5 验证云端可用

1. 设置 → 运行模式 → **仅云端**
2. 工作台生成一句短文本
3. 任务中心能看到这条任务，`后端` 显示 **云端**，首包延迟正常
4. 状态与日志里隧道显示「已连接」

---

## 8. 配置文件

- 位置：`config/config.yaml`（首次启动由 `config/config.example.yaml` 生成）
- 界面里改的设置会写入**运行期覆盖文件**，优先级高于 `config.yaml`
- 常用项：

```yaml
run:
  mode: hybrid          # local / cloud / hybrid
server:
  host: 0.0.0.0
  port: 8443
storage:
  models_dir: models
  data_dir: data
cloud:
  base_url: https://127.0.0.1:18443
  api_key: ""
  verify_tls: false
  tunnel:
    enabled: true
    local_port: 18443
    ssh_host: connect.nmb1.seetacloud.com
    ssh_port: 41425
    username: root
```

改完**重启服务**生效（界面里的部分开关是即时的）。

---

## 9. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 双击没反应 | 右键「以 PowerShell 运行」看报错；多为 Python 未进 PATH |
| 浏览器提示证书不安全 | 自签证书正常现象 →「高级 → 继续前往」 |
| 端口被占用 | 改 `config/config.yaml` 的 `server.port`，或关掉占用进程 |
| 提示模型缺失 | 按第 4 章放模型，再点引擎「安装」校验 |
| 显存不足（VRAM_INSUFFICIENT） | 换 0.6B / 改 CPU / 或一键切云端 |
| 云端引擎「请求失败」 | 看提示：密码变了→重填；连不上→检查实例是否开机、端口是否放行 |
| 云端隧道连不上 | 设置 → SSH 隧道 →「测试连接」看具体错误；AutoDL 需实例运行中 |
| 生成没声音 | v1.1.59 起**不再自动播放**，点播放器的播放键 |
| 任务列表看不到行 | 窗口太矮时点筛选卡右侧「筛选」展开/收起，或拉大窗口 |
| 想接别的 AI 调用 | 教程页「用其它 AI 调用」有一段可直接发给 AI 的说明；文档见 `docs/MCP.md` |

更多错误码见 `docs/TROUBLESHOOTING.md` 与界面「状态与日志」。

---

## 10. 升级 / 备份 / 卸载

- **升级**：保留 `data/`、`models/`、`config/config.yaml`，用新包覆盖其余文件即可
  （或运行 `tools/upgrader/dist/QWENTTS-Upgrader.exe`）
- **备份**：整个 `data/`（含数据库、产物、音色）+ `config/`
- **卸载**：删除解压目录即可；`.venv` 与 `models` 都在目录内，不会留系统残留

---

## 11. 目录速查（出问题时要找的东西）

| 内容 | 位置 |
| --- | --- |
| 服务日志 | `data/logs/` |
| 任务产物（音频） | `data/outputs/<任务ID>/<任务ID>.wav` |
| 上传的参考音频 | `data/assets/`、`data/uploads/` |
| 音色库文件 | `data/voices/` |
| 数据库 | `data/db/app.db` |
| 自签证书 | `data/certs/` |
| 引擎安装状态 | `data/engines_state.json` |
| 云端主机档案（密码加密） | `data/cloud-host.json` |
| 云端安装任务 | `data/cloud-engines/` |

---

## 12. 还想看更细的

| 主题 | 文档 |
| --- | --- |
| 快速上手 | `docs/QUICKSTART.md` |
| 模型下载与规格 | `docs/MODELS.md` |
| 全部配置项 | `docs/CONFIG.md` |
| 云端接入细节 | `docs/CLOUD_SETUP.md`、`docs/CLOUD_AGENT.md`、`docs/CLOUD_PROVIDERS.md` |
| MCP（给其它 AI 用） | `docs/MCP.md` |
| HTTP / OpenAI 兼容接口 | `docs/API.md` |
| 排错 | `docs/TROUBLESHOOTING.md` |

---

版权所有 © 2026 xiyihang　保留所有权利。
