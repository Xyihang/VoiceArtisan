# 安装引导器（Launcher）· 使用说明

给「拿到源码压缩包」的人用的图形化安装引导：**一个窗口把选目录 → 体检 → 下模型 → 装依赖 → 构建前端 →
生成配置 → 启动服务** 全部串起来，长任务的输出实时滚动在窗口里。

- 程序：`launcher/qwentts_launcher.py`（**只用 Python 标准库 + tkinter**，不额外装包）
- 打包：`launcher/build.ps1` → `launcher/dist/QWENTTS-Setup.exe`（单文件，双击即用）
- 日志：`data/logs/launcher.log`（UTF-8 追加，带时间戳）
- 记忆：`launcher/launcher.json` 记住上次选的项目根目录 **和运行环境选择**（系统 Python / 项目内 venv；不可写时回落到临时目录）

> 引导器**不会**替你安装系统级软件（Python / Node.js / 显卡驱动）。缺这些东西时它只给出准确命令与链接，
> 你自己装完点「刷新检测」即可。项目自身的 pip 依赖、前端构建、模型下载由它执行。

---

## 一、前置准备清单（先自己装好，装完再开引导器）

| 前置 | 是否必需 | 版本要求 | 安装方式 | 说明 |
| --- | --- | --- | --- | --- |
| Python | **必需** | ≥ 3.10 | `winget install Python.Python.3.12` 或 <https://www.python.org/downloads/> | 安装时**务必勾选 Add Python to PATH** |
| pip | **必需** | 随 Python | 同上（`python -m ensurepip --upgrade`） | 引导器只调用，不代装 |
| 磁盘空间 | **必需** | 项目所在盘 ≥ 15 GB | — | 1.7B 三件套模型约 12.6 GB |
| NVIDIA 驱动 | 有条件 | cu121 需驱动 ≥ 530 | <https://www.nvidia.cn/geforce/drivers/> | 无独显可用云端 / CPU 模式，不影响安装 |
| Node.js / npm | 可选 | LTS | `winget install OpenJS.NodeJS.LTS` | 只有需要**重新构建前端**时才要；已有 `frontend/dist` 可跳过 |
| ffmpeg | 可选 | 任意 | `winget install Gyan.FFmpeg` | 只有**导出 mp3** 需要，wav 不受影响 |
| 管理员权限 | **不需要** | — | — | 引导器不写注册表、不改系统 PATH，**不要**用管理员运行 |

---

## 二、界面长什么样

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Qwen3-TTS 一键安装器          选择解压目录 → 环境体检 → 下模型 → …           │
├──────────────────────────────────────────────────────────────────────────────┤
│ 项目根目录：[ E:\QWENTTS                                  ] [浏览…] [刷新检测]│
├──────────────────────────────────────────────────────────────────────────────┤
│ 运行环境（依赖装进哪里 / 用哪个解释器跑后端）                                 │
│  (•) 系统 Python（默认）  ( ) 项目内 venv（推荐）  [创建 venv]  venv 已就绪…  │
├──────────────────────────────────────────────────────────────────────────────┤
│ 环境体检                                                                     │
│  状态 │ 检查项                │ 说明                                         │
│   ✓   │ Python                │ Python 3.10.8（C:\…\Python310\python.exe）   │
│   ✓   │ pip                   │ pip 26.2.1 …                                  │
│   ✓   │ 项目内 venv（可选）   │ 已创建，18.6 MB（当前使用）                   │
│   ✓   │ 磁盘空间              │ E:\ 剩余 42.9 GB / 需要 ≥ 15 GB              │
│   ✓   │ GPU 驱动              │ NVIDIA GeForce RTX 3050 …，驱动 596.49       │
│   ✓   │ Node / npm（可选）    │ node v24.16.0，npm 11.13.0                    │
│   ✓   │ ffmpeg（可选）        │ ffmpeg version …                             │
│   ✓   │ 模型 1.7B-Base        │ 已就绪，4.23 GB                              │
│   ✓   │ 模型 1.7B-CustomVoice │ 已就绪，4.21 GB                              │
│   ✓   │ 模型 1.7B-VoiceDesign │ 已就绪，4.21 GB                              │
│   ✓   │ 配置文件 config.yaml  │ 已存在（5049 字节）                          │
│   ✓   │ 前端构建产物 dist     │ 已存在 index.html                            │
├──────────────────────────────────────────────────────────────────────────────┤
│ 操作                                                                         │
│ [安装依赖] [仅校验] [生成配置] [构建前端] [启动服务] [停止服务]                │
│ 模型下载：启动服务后到「设置 → 引擎与模型」点「安装」（约 12.6GB）             │
│ [启动服务] [停止服务] [打开界面] [打开日志目录] [复制修复命令]                │
│ 等价手动命令：[ python -m pip install -r requirements.lock.txt … ] [复制命令] │
├──────────────────────────────────────────────────────────────────────────────┤
│ 日志（实时滚动）                                                             │
│ [19:33:52] [OK  ] Python 3.10.8 …                                            │
│ [19:33:52] 关键项全部通过，可以继续后续步骤。                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│ 当前步骤：体检完成（12 项，失败 0，提醒 0）  运行环境：项目内 venv 提示：…   │
└──────────────────────────────────────────────────────────────────────────────┘
```

状态图标：`✓` 通过 / `!` 提醒（可选或可后补）/ `✕` 失败（关键项，必须先解决）。
选中任意一行后按底部「复制修复命令」（或双击该行），可把它对应的安装命令复制到剪贴板。

---

## 三、为什么建议用项目内 venv

窗口顶部「运行环境」是二选一的，两种方式**都可用**，区别只在于依赖装到哪个 Python 里：

| | 系统 Python（默认） | 项目内 venv（推荐） |
| --- | --- | --- |
| 依赖装到哪 | 当前系统 `python` 的 site-packages | `<项目根>\.venv\Scripts\python.exe` |
| 是否影响全局环境 | 会影响（其它项目可能撞版本） | **完全隔离**，删目录即消失 |
| 额外磁盘 | 0 | 约 1.5–2 GB（含依赖；空 venv 约 20 MB） |
| 与 `scripts/start.ps1` 行为 | 一致 | 需自己用 venv 的解释器启动后端 |

建议用 venv 的三个理由：

1. **不污染全局 Python**：torch/cu121 这类大包一旦装进系统 Python，其它项目很容易版本打架；
   装进 `.venv` 后，整个项目的依赖都困在项目目录里，卸载就是删目录。
2. **可随时删掉重建**：出问题不用「重装 Python」。删掉 `.venv` 目录、再点一次「创建 venv」就是干净重来；
   引导器里的「创建 venv」按钮遇到已存在的 venv 会弹框问是否**重建**（重建会先停服务，再用 `shutil.rmtree` 删目录重装）。
3. **与系统环境隔离**：Windows 上系统 Python 常被其它软件影响（多个 Python 版本、MS Store 版、各类工具链）；
   venv 只继承解释器本体，pip 升级 / 装包都不会互相牵连。

操作方式：选「项目内 venv（推荐）」→ 点「创建 venv」（首次约 10–60 秒，日志出现 `退出码：0` 即成功）
→ 之后「安装依赖 / 启动服务」自动全部改用 `.venv\Scripts\python.exe`。
**构建前端仍走系统 node/npm**（前端与 Python 环境无关）。

> 若选了「项目内 venv」但还没创建：体检里 `venv` 行变黄色提醒，「安装依赖 / 仅校验 / 启动服务」
> 四个按钮置灰，状态栏提示"请先创建 venv"。命令行等价物：
> `python launcher\qwentts_launcher.py --make-venv <项目根>`。

---

## 四、按顺序操作（首次安装约 30–60 分钟，大头是模型下载）

1. **选目录**：点「浏览…」，选**解压出来的 QWENTTS 根目录**（能看到 `backend`、`scripts`、`config`）。
   选错时标题下方会红字提示"选错了，请选解压出来的 QWENTTS 目录"，并说明缺哪个特征文件：
   `backend/app/main.py`、`scripts/download_models.py`、`config/config.example.yaml`。
2. **选运行环境**（推荐）：在窗口顶部「运行环境」里点「项目内 venv（推荐）」，再点「创建 venv」。
   想跟 `scripts/start.ps1` 保持一致就保持默认的「系统 Python」（依赖装进当前 Python）。
3. **环境体检**：选对目录后自动开始（也可随时点「刷新检测」）。红色项按提示自己装好软件，再点一次「刷新检测」。
4. **下载模型**（已从安装器移除）：安装器只负责把环境跑起来。启动服务后到 「设置 → 引擎与模型 → Qwen3-TTS 1.7B」点「安装」，约 12.6 GB、支持断点续传；命令行等价命令：`python -m app.engines_cli install qwen3-tts-1.7b`。
   下完可点「仅校验」核对完整性。
5. **安装依赖**：先检测 `import torch`，失败则按 cu121 源装 `torch` + `torchaudio`，再装
   `requirements.lock.txt`（国内镜像，失败自动回落官方源）。选了 venv 时这些 pip 命令全部用
   `.venv\Scripts\python.exe -m pip` 执行。
6. **构建前端**：`npm install --registry https://registry.npmmirror.com` + `npm run build`。
   已有 `frontend/dist` 可跳过。**这一项始终用系统 node/npm**，与 venv 无关。
7. **生成配置**：`config/config.yaml` 不存在时从 `config/config.example.yaml` 复制；**已存在不会覆盖**。
8. **启动服务**：后台起 `python -m app.main`（`PYTHONPATH=<根>\backend`，cwd=`backend`），
   服务自己会开浏览器。引导器随后最多等 60 秒轮询健康接口，就绪时日志打印
   `服务已就绪 https://localhost:8443`。
9. **停止服务**：`taskkill /PID <pid> /T /F`，**只**杀本程序启动的那棵进程树。
   直接关窗口若有任务在跑 / 服务在运行，会先弹确认框。

对应的等价手动命令（引导器执行的就是这些，可自己复制到终端）：

```powershell
# 1) 依赖（torch 若已可导入可跳过第一行）
python -m pip install "torch==2.4.1+cu121" "torchaudio==2.4.1+cu121" --index-url https://download.pytorch.org/whl/cu121
python -m pip install -r requirements.pins.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2) 模型
python scripts/download_models.py --size 1.7B --out "<根>\models"
python scripts/download_models.py --verify --size 1.7B --out "<根>\models"

# 3) 前端
cd frontend; npm install --registry https://registry.npmmirror.com --no-audit --no-fund; npm run build

# 4) 配置 + 启动
Copy-Item config\config.example.yaml config\config.yaml      # 仅当 config.yaml 不存在
cd backend; $env:PYTHONPATH = "<根>\backend"; python -m app.main
```

---

## 五、命令行用法（含无 GUI 自检 / 创建 venv）

```powershell
# 直接打开 GUI，并预填项目根
python launcher\qwentts_launcher.py --root E:\QWENTTS

# 不开窗口，只体检并打印 JSON（自动化/验收用）
python launcher\qwentts_launcher.py --self-test E:\QWENTTS --json

# 人读版报告
python launcher\qwentts_launcher.py --self-test E:\QWENTTS

# 无 GUI 创建项目内 venv（已存在则复用；加 --force 先删后重建）
python launcher\qwentts_launcher.py --make-venv E:\QWENTTS
python launcher\qwentts_launcher.py --make-venv E:\QWENTTS --force

# 指定运行环境做自检（默认沿用 launcher.json 里记住的选择）
python launcher\qwentts_launcher.py --self-test E:\QWENTTS --json --env venv
```

`--make-venv` 的真实输出（首次创建）：

```text
创建 venv：E:\QWENTTS\.venv（约 1-3 分钟）
$ C:\Users\you\AppData\Local\Programs\Python\Python310\python.exe -m venv E:\QWENTTS\.venv
  退出码：0
venv 创建完成：E:\QWENTTS\.venv\Scripts\python.exe（3.10.8，18.1 MB）
venv：E:\QWENTTS\.venv
解释器：E:\QWENTTS\.venv\Scripts\python.exe
版本：3.10.8
大小：18.1 MB
结果：成功（新建）
```

创建成功后会把 `launcher.json` 的 `env` 记成 `venv`，之后 GUI 与 `--self-test` 默认都用 venv。

自检 JSON 每项字段：`key / name / status(ok|warn|fail) / ok / detail / fix_hint / command / critical / extra`，
顶层还有 `project_markers_ok`、`critical_failed`、`models_missing`、`summary`、`exit_code`，
以及 venv 相关三项（新增，**向后兼容**，老字段不变）：

```json
"env_choice": "venv",
"effective_python": "E:\QWENTTS\.venv\Scripts\python.exe",
"venv": {
  "exists": true,
  "selected": true,
  "python": "E:\QWENTTS\.venv\Scripts\python.exe",
  "size_mb": 18.1
}
```

- `effective_python`：**最终用来装依赖 / 跑后端的解释器**。选 venv 且已创建时指向
  `<项目根>\.venv\Scripts\python.exe`，否则指向系统 Python；venv 未创建时为 `null`。
- `venv.exists / selected / size_mb`：venv 是否存在、当前是否被选中、占多少 MB。
- venv 项**非致命**：不存在只是提醒，不会让 `exit_code` 变成 1。

退出码：**0** = 关键项（`python`、`pip`、`disk`）全通过且目录特征文件齐全；**1** = 有致命项。
**模型缺失不致命**，只在 `models_missing` 里标出（可后补下载）。

---

## 六、打包成 exe

```powershell
# 在项目根执行（需要已装 Python + 网络）
powershell -NoProfile -File .\launcher\build.ps1
```

- 脚本会检查 `pyinstaller`，缺失则 `python -m pip install "pyinstaller==6.21.0"`（默认走清华镜像，失败回落官方源）
- 打包命令：`pyinstaller --noconfirm --clean --onefile --windowed --name QWENTTS-Setup launcher/qwentts_launcher.py`
- 产物：`launcher/dist/QWENTTS-Setup.exe`（约 8–10 MB，单文件、无控制台）
- 结束后打印产物路径与大小；失败时打印退出码 + PyInstaller 日志尾部（`%TEMP%\qwentts-pyinstaller.log`）
- 可选参数：`-SkipInstallMirror`（不用国内镜像装 pyinstaller）、`-KeepBuild`（保留 `launcher/build` 中间目录）

分发方式：把 `QWENTTS-Setup.exe` 和源码压缩包一起发给用户，双击 exe 即可。
exe 只包含引导器本体（标准库 + tkinter），**不含**模型、后端代码，也不含 Python 运行时之外的依赖。

---

## 七、常见问题（FAQ）

**Q1：双击 exe 被杀软/Windows Defender 拦了？**
PyInstaller onefile 打包的程序常被误报（自解压行为）。选「更多信息 → 仍要运行」，或把 exe 加入白名单；
介意的话直接用 `python launcher\qwentts_launcher.py` 跑源码版，功能完全一致。

**Q2：提示"未找到 Python 3.10+"，但命令行里 `python -V` 正常？**
引导器会按 `python` / `python3` / `py -0p` / `%LOCALAPPDATA%\Programs\Python\Python3*` 的顺序找。
若你是在 MS Store 版或虚拟环境里装的，请装官方 3.10+ 安装包并勾选 Add to PATH，然后点「刷新检测」。
**不要**用管理员身份运行引导器，也不需要。

**Q3：`pip` 报 `--version` 失败 / `ImportError`？**
说明当前 Python 的 pip 坏了：`python -m ensurepip --upgrade`（选中 pip 行按「复制修复命令」可直接复制），
仍不行就重装 Python（勾选 pip）。

**Q4：磁盘空间不足（< 15 GB）？**
模型 12.6 GB + 依赖约 3 GB。腾出空间，或把整个项目解压到空间更大的盘再重选目录。

**Q5：GPU 显示"无 NVIDIA 显卡"或"驱动过旧"？**
不影响安装。无独显可把 `run.mode` 改 `cloud`，或把 `local.device` 改 `cpu`（功能完整、慢 3–5 倍）。
驱动过旧（主版本 < 530）时 cu121 的 torch 用不了，请更新驱动后重装依赖。

**Q6：没有 Node，`frontend/dist` 也缺？**
服务仍能起，但只能访问 `https://localhost:8443/docs` 接口文档。装 Node LTS 后点「构建前端」，
或直接找一份 `frontend/dist` 放进项目。

**Q7：模型下载中断 / 提示不完整？**
脚本支持断点续传：界面里再点一次「安装」即可续；下完点安装器的「仅校验」核查。公司网络屏蔽 ModelScope 时，
按 `docs/MODELS.md` 给的手动链接下载后放到 `models/Qwen3-TTS-12Hz-1.7B-*`。

**Q8：启动服务报"端口 8443 已被占用"？**
先关掉占用程序（或上次残留的服务进程），或改 `config/config.yaml` 的 `server.port`（例如 9443），
改完再点「启动服务」。

**Q9：日志窗口提示"60 秒内未就绪"？**
首启若要加载模型会慢，可稍后点「打开界面」看是否已起来；若进程已退出，日志里会有真实报错
（依赖缺失 / 模型路径错 / 端口占用）。详见 `docs/TROUBLESHOOTING.md`。

**Q10：浏览器提示证书不安全？**
服务用 `data/certs` 里的自签证书。点「高级 → 继续前往 localhost」即可，属于预期行为。

**Q11：想彻底卸载 / 回滚？**
引导器没有写注册表、没有改系统 PATH、没有装系统级软件。删掉项目目录即完成回滚；
只有 pip 依赖装在系统 Python 里，需要的话 `python -m pip uninstall -r requirements.lock.txt -y`。

**Q12：能在没装 tkinter 的 Python 上跑吗？**
不能（官方 Windows 安装包默认带 tkinter）。若报 `ModuleNotFoundError: tkinter`，请重装官方 Python；
无 GUI 环境下可用 `--self-test` 做体检。

**Q13：创建 venv 失败怎么办？**
按日志里的真实报错对症处理（引导器只调用 `python -m venv`，不会自动装 Python）：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ensurepip is not available` / `No module named ensurepip` | 系统 Python 缺 ensurepip（精简安装包 / 部分发行版） | 先跑 `python -m ensurepip --upgrade`，或重装官方 Python 安装包（勾选 pip）；也可先 `python -m pip --version` 确认 pip 可用 |
| 日志里创建到一半就失败，目录被自动删掉 | 杀软 / Windows Defender 拦截大量文件写入 | 把项目目录加入白名单（或临时关闭实时防护）后重试；企业管控机器需找 IT |
| `No space left on device` / 体积卡住 | 磁盘不足（venv + 依赖约 1.5–2 GB） | 清理磁盘，或把项目放到空间更大的盘再重选目录 |
| `PermissionError` / `Access is denied` | 目录无写权限，或被其它进程占用（服务、编辑器、资源管理器预览） | 换一个可写目录；先点「停止服务」并关掉占用 `.venv` 的程序，再点「创建 venv」 |
| `删除旧 .venv 失败` | 服务仍在跑 / 文件被占用 | 点「停止服务」后重试；实在删不掉就手动删除 `.venv` 目录 |

命令行等价操作：`python launcher\qwentts_launcher.py --make-venv <项目根> --force`
（`--force` 会先删再建，输出里能看到 `-m venv` 的完整日志与退出码）。

**Q14：切换 venv 后要不要重装依赖？**
**要。** 依赖是「装进某个解释器」的：系统 Python 里装好的 torch 不会跑进 `.venv`。
所以从「系统 Python」切到「项目内 venv」（或反过来）后，请重新点一次「安装依赖」。
判断方法：选中「项目内 venv」后在终端执行
`<项目根>\.venv\Scripts\python.exe -c "import torch; print(torch.__version__)"`（路径按实际项目根）；能打印版本就说明这个环境已装好。
重建 venv（`.venv` 被删掉）等于环境清零，依赖必须重装一次。
另外模型文件在 `models` 目录、与解释器无关，**不需要**重新下载；前端 `frontend/dist` 同理不用重建。

---

## 八、已知限制

- 打包后的 exe 可能被杀软误报（见 Q1）；窗口模式下 stdout 不可见，`--self-test` 结果会写到
  `%TEMP%\qwentts-selftest.txt`，源码模式下正常打印到控制台。
- 引导器**不**安装 Python / Node / 驱动。venv 由它用 `python -m venv` 创建，但**只**创建在
  `<项目根>\.venv`，不改系统 PATH、不写注册表、不碰其它目录。
- 默认仍是「系统 Python」（与 `scripts/start.ps1` 行为一致，依赖装进当前 `python`）；
  选了「项目内 venv」后，`start.bat` / `scripts/start.ps1` 不会自动用 venv，需自己指定
  `.venv\Scripts\python.exe`。
- 健康检查优先请求 `/api/health`（后端主接口），404 时回落到 `/api/healthz`（备用别名）；二者任一可响应即视为就绪；
  只要端口有 HTTP 响应即视为就绪。
- 关闭引导器时会弹确认框；确认退出会一并 `taskkill` 掉本程序启动的服务进程（不确认则什么都不做）。
  若希望"关窗口后服务继续跑"，请改用 `scripts/start.ps1` 或 `start.bat`。

## 缺陷修复记录（2026-10-20，外部测试反馈）

外部机器（Win10 + GTX 950 2GB）实测报出的 9 个问题，均已修复：

| 编号 | 问题 | 修复 |
| --- | --- | --- |
| P0-1 | `npm install` 报 `Invalid Version:`（`package-lock.json` 中 `@rollup/rollup-openbsd-x64` 缺 `version`） | ① 重新生成干净锁文件（`lockfileVersion 3`、117 条目、0 个空版本），已验证 `npm ci` 从零可装；② 构建步骤检测到 `Invalid Version` 时**自动备份锁文件并用 `--no-package-lock` 重试一次** |
| P0-2 | `npm install` 失败后仍继续 `npm run build`，报误导性的 `'tsc' 不是内部或外部命令` | 分阶段阻断：install 非零 → 立即报「依赖安装失败（npm install 退出码 N）」并给排查方向；只有 install 成功才进入编译，编译失败单独报「编译失败（npm run build 退出码 N）」 |
| P0-3 | 「启动服务」不校验后端依赖，服务自检失败退出 | 启动前先探测 `torch` / `qwen_tts`，缺失则直接阻断并提示「先点『安装依赖』（约 2.5GB，建议项目内 venv）」；`退出码 None` 改为真实 `returncode` |
| P0-4 | 体检不检查 Python 后端依赖，torch/qwen-tts 全缺仍显示"关键项全部通过" | 体检新增**关键项**「后端依赖（torch / qwen-tts）」：一次子进程同时探测 `torch.__version__` / `qwen_tts` / `cuda.is_available()`；缺失为 FAIL，CUDA 不可用为 WARN |
| P1-1 | GPU 只看驱动版本，2GB 显存也标 `[OK]` | 解析显存并与 `MIN_VRAM_MB`（6144）比较：不足时降为 WARN，写明「1.7B 建议 ≥6 GB，本地会显存不足，必须用云端」，并给云端模式引导 |
| P2-1 | 子进程中文日志乱码（`'tsc' �������`） | 子进程输出改二进制读取 + 「先 UTF-8、失败退 GBK」解码（`decode_process_line()`） |
| P2-2 | pydantic 警告 `Field "model_size" has conflict with protected namespace "model_"` | `AsrLocalCfg` 设 `model_config = ConfigDict(protected_namespaces=())` |
| P2-3 | 启动 2 秒内提示"已有任务在执行"（自触发竞态） | 启动自动体检改为 `_auto_checks_once()`：busy 时 1.2s 后重试（最多 3 次），不再与自身任务锁冲突 |
| 建议 4 | 用户机器不该依赖 Node/npm | 发行包**附带预构建 `frontend/dist`**；构建步骤检测到 dist 已存在时直接跳过（打印"无需 Node/npm"），体检也显示「前端构建产物：已存在（无需 Node 也能启动）」 |

修复后的自检输出示例（本机实测）：

```
[ OK ] 后端依赖（torch / qwen-tts）: torch 2.4.1+cu121 / qwen-tts installed（CUDA 可用）
[WARN] GPU 驱动: NVIDIA GeForce RTX 3050 Laptop GPU，驱动 596.49，显存 4.0 GB（1.7B 建议 ≥6 GB）：本地模式会显存不足，必须用云端
[ OK ] 前端构建产物 frontend/dist: 已存在 index.html（无需 Node 也能启动）
汇总：11 项通过，2 项提醒，0 项失败 → 退出码 0
```

### 小显存怎么办（0.6B）

体检里 GPU 行现在会写清「1.7B 建议 ≥6 GB；0.6B 约 2 GB 可跑」，并按显存给出两条路：

1. 显存 ≥2GB → 在「设置 → 引擎与模型」里选 **Qwen3-TTS 0.6B（小显存）** 卡片安装（约 3GB）；
2. 显存不足或想跑 1.7B → 用云端 GPU（见 [CLOUD_ACCESS_TUTORIAL.md](CLOUD_ACCESS_TUTORIAL.md)）。

0.6B 与 1.7B **可以同时存在**，卡片上点「使用」即可切换，切换时 `local.size` 会自动同步。

### 复核清单（请测试同学按此验收）

按顺序做，每步都有明确的「应该看到什么」：

| # | 操作 | 期望结果 |
| --- | --- | --- |
| 1 | 删掉 `frontend/node_modules`，直接点「启动服务」 | **完全不调用 npm**，日志会说"已存在预构建产物…无需 Node/npm"，服务正常起来 |
| 2 | 在没装 torch 的机器上点「启动服务」 | 立刻拦下：「后端依赖未安装（缺 torch、qwen-tts）：请先点『安装依赖』…」，**不再**出现"服务进程提前退出" |
| 3 | 点「环境体检」 | 看到新增关键项「后端依赖（torch / qwen-tts）」；显存 2GB 的机器 GPU 行显示 `[WARN] … 显存 2.0 GB（1.7B 建议 ≥6 GB）：本地模式会显存不足，必须用云端` |
| 4 | 故意把 `frontend/package-lock.json` 改坏（例如删掉某条 `version`）后点「构建前端」 | 自动备份为 `package-lock.json.bak` 并用 `--no-package-lock` 重试成功，日志有"检测到 package-lock.json 疑似损坏" |
| 5 | 让 `npm install` 真正失败（如断网）后点「构建前端」 | 报「依赖安装失败（npm install 退出码 N）」，**不会**继续跑 build，也不会出现 `'tsc' 不是内部或外部命令` |
| 6 | 观察启动后前 3 秒的日志 | **不应**出现"已有任务在执行，请等它结束"；启动体检会自动完成 |
| 7 | 点「构建前端」看中文输出 | 日志中文正常（不再有 `�����`），例如 `'tsc' 不是内部或外部命令` |

如果任一项不符，请把 `launcher\dist\data\logs\launcher.log` 对应时段贴回来。
