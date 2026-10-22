# 打包、发布与踩坑经验（维护者手册）

> 这份文档记录**代码里看不出来**的东西：怎么打包、怎么发版、哪些坑已经踩过、出错时先看哪里。
> 面向后续维护者（包括未来的我）。改打包/发布流程前请先读第 1、2 节。

---

## 1. 三个产物，三种用途

| 产物 | 生成命令 | 内容 | 给谁 |
| --- | --- | --- | --- |
| 源码包 `QWENTTS-src-v*.zip` | `python .cache/make_backup.py` | 纯代码（**不含** `frontend/dist`、`launcher/dist`） | 开发者 / 上传云主机 / 二次开发 |
| 便携包 `QWENTTS-portable-v*.zip` | `python .cache/make_backup.py portable` | 代码 + `frontend/dist` + `launcher/dist/QWENTTS-Setup.exe` | 最终用户（无需 Node） |
| 安装器 `QWENTTS-Setup.exe` | `powershell -NoProfile -File .\launcher\build.ps1` | PyInstaller 单文件 GUI | 双击安装的用户 |

另有独立小工具（也打包进 portable）：

| 产物 | 生成命令 | 用途 |
| --- | --- | --- |
| `tools/upgrader/dist/QWENTTS-Upgrader.exe` | `python -m PyInstaller --onefile --windowed --name QWENTTS-Upgrader --distpath tools\upgrader\dist --workpath .cache\pyi-upgrader --specpath .cache tools\upgrader\qwentts_upgrader.py` | 老用户选两个目录一键升级（自动备份/回滚） |

**顺序很重要**：先 build 前端 → 再 build exe → 最后打包 zip。反了就会把旧 dist 打进包里。

---

## 2. 发版流程（每次都要走一遍）

1. 改代码
2. `cd backend && python -m compileall -q app && python -m pytest tests -q`（必须 82 passed 量级全绿）
3. `cd frontend && npx tsc --noEmit && npx vite build`
4. 改 `VERSION.json`：`version` + 在 `notes` **最前面**插一条；同步 `backend/app/__init__.py` 的 `__version__`
   - 有测试卡这个：`test_package_version_matches_version_json`，两处必须一致
5. 在 `docs/PROJECT_HISTORY.md` **末尾**追加一条编号条目（现状编号 67，下一条 68）
6. （改了打包工具/安装器时）`powershell -NoProfile -File .\launcher\build.ps1`
7. `python .cache/make_backup.py` + `python .cache/make_backup.py portable`
8. `git add -A && git commit`（提交信息用 `type(scope): 中文摘要`）
9. 抽查 zip 内容（见第 6 节校验清单）

**版本号策略**：修 bug / 加小功能都只递增 patch（1.1.20 → 1.1.21），不做 1.2。

---

## 3. 打包脚本（`.cache/make_backup.py`）的硬规则

这些规则都是踩坑换来的，别随意改：

| 规则 | 原因 |
| --- | --- |
| **产物只增不删**：`E:\trae_projects\QWENTTS-backups` 里的旧 zip 永远不删 | 用户明确要求；出问题时能回退到任意历史版本 |
| **不要用 `Compress-Archive`** | 会写入 Windows 反斜杠路径，解压到 Linux/云主机时目录结构错乱；脚本用 zipfile + POSIX 路径 |
| `SKIP_REL_PREFIX = {"launcher/build"}` | PyInstaller 工作目录漏进去时包会从 13MB 涨到 13.23MB+ |
| `SKIP_REL = {"launcher/QWENTTS-Setup.spec", "config/config.yaml"}` | `config.yaml` 含**云端地址和 API Key**，绝对不能进包 |
| `SKIP_SUFFIX` 含 `.new` | 发布 exe 时旧文件被占用 → 先写 `.exe.new`，别把它打进包（曾导致 29.41MB 的错误包） |
| `BASE_SKIP` 含 `data models .cache .git node_modules .venv __pycache__` | 模型 12.6GB、数据库、密钥都不该进包 |
| src 包额外跳过所有 `dist` 目录 | src 要“纯代码” |

**教训**：每次打完包，用第 6 节的抽查脚本验一遍体积和文件数，体积突然变大八成是混进了 build 目录或 `.new`。

---

## 4. PyInstaller 相关

- `launcher/build.ps1` 用 `--onefile --windowed`，产物固定写到 `launcher/dist/QWENTTS-Setup.exe`
- **`QWENTTS-Setup.exe` 正在运行时就无法覆盖**（WinError 5）。做法：
  1. 先 build 到别的 distpath：`--distpath .cache\launcher-dist`
  2. 自测 `QWENTTS-Setup.exe --self-test`（退出码 0 才算好）
  3. 旧文件被占用就落成 `QWENTTS-Setup.exe.new`，让用户关掉窗口后：
     `move /Y "launcher\dist\QWENTTS-Setup.exe.new" "launcher\dist\QWENTTS-Setup.exe"`
- PyInstaller 会把普通 INFO 写 stderr，PowerShell 里别用 `$ErrorActionPreference='Stop'` 裸接，脚本里已有 `Invoke-Native` 包装
- **Tkinter GUI 必须 `--windowed`**，否则会弹黑窗；但 `--windowed` 下 `print` 看不到，所以关键诊断信息要写日志文件
- 安装器日志位置：`<exe 所在目录>\data\logs\launcher.log`（注意是 exe 旁边，不是项目根）

---

## 5. 已知坑清单（按模块）

### 5.1 依赖与 torch

- `requirements.txt` **不能**写全局 `--extra-index-url`：那会让每个包都去 pytorch.org 找一遍，国内经常卡死。torch 三件套单独放 `requirements-torch.txt`，安装时用 `--index-url` 指定
- **先装 torch 再装普通依赖**：`qwen-tts` 声明了 torch 依赖，先装普通依赖会让 pip 从国内镜像白拉一份 CPU 版 torch（约 900MB），随后又被 cu121 覆盖
- 索引优先级：**上交镜像 → pytorch 官方**（实测上交 0.28s，官方 2.19s）
- 任何 `pip install` 都要带 torch 约束（`-c <constraints>`，约束来自 `requirements-torch.txt`），否则可能把 cu121 换成 CPU 版——这是真实事故
- `requirements.pins.txt` 里**不能出现 torch**：否则 `pip install -r requirements.pins.txt -i 国内镜像` 找不到 `+cu121` 轮子必失败

### 5.2 引擎安装与进度

- 引擎进度**只靠 WebSocket 推送**，事件一丢界面就“假死”（曾出现"排队中 → 突然完成"）。已加三层保护：
  1. `manager` 心跳线程：任何阶段静默 5 秒补一条「仍在进行：…（已等待 Ns）」
  2. `deps.install_main_packages` 用 `Popen` + 每 2 秒心跳（`subprocess.run` 会静默几分钟）
  3. 前端每 3 秒兜底轮询 `install/status`
- 心跳**不能调用 `progress()`**（会覆写 `base` 文案，产出「仍在进行：仍在进行：…」套娃），要单独构造 payload 并只更新 `ts`
- `data/engines_state.json` 会持久化 job；进程被强杀后残留 `running` 状态。判断线程是否真活着要用 `_is_running()`（`thread.is_alive()`），别只看状态文件
- 下载必须带：**断点续传**（`Range: bytes=<已有>-`，`.part` 保留）+ **停滞超时**（30s 无数据即换源）。`urlopen(timeout=120)` 会让 GitHub 卡住静默两分钟

### 5.3 云端（一键接入 / 云端引擎）

- `pkill -f 'app.main --host …'` 会**匹配到自己这条 ssh 命令**（bash -c 命令行含同样文字），把清理脚本自己杀掉 → 用 `app[.]main` 字符类规避
- 云端引擎管理调 `engines_cli` 必须用**部署自带的 `.venv/bin/python`**：一键接入把依赖装在 venv，系统 python 缺 `pydantic-settings` 会直接 import 失败
- 隧道模式下“云端不可达”先查两件事：**隧道 `remote_port` 是否等于云端服务实际端口**、**云端是 http 还是 https**
- 浏览器**不能**自己探活隧道地址（自签证书/协议不匹配），探活必须由后端做（`POST /api/cloud/probe`，自动尝试 隧道(https/http) → 直连 → 反协议）
- `S4B` 即使跳过下载也要写 `models_dir`，否则 S5 写出的配置指向空目录
- 无卡实例跑完整流程必然卡在 S6（云端返回“未检测到可用的 CUDA 设备”）；要绿就跑「仅准备环境」(`prepare_only`) 到 S4B
- 云端上传包时**不要打包 `config/config.yaml`**，云端配置要单独上传，否则会被本地配置覆盖（曾出现 models_dir/diarize 配置错乱）

### 5.4 其它（历史事故，别再犯）

- 相对导入层级：`engine/` 下的模块引用 `errors` 要用 `..errors`，写成 `...errors` 会在启动时炸
- `pydantic` 对 `model_` 前缀有保护：字段名 `model_size` 必须配 `model_config = ConfigDict(protected_namespaces=())`
- `huggingface_hub 2.2.0` 的 `snapshot_download` 有 bug（`process() takes no keyword arguments`）→ 钉 `huggingface_hub<1`（0.36.2）
- PyAV 19.x 与 faster-whisper 1.2.1 不兼容（`open() got an unexpected keyword argument 'metadata_errors'`）→ ASR 自己用 `read_audio` + `resample` 解码成 16k float32 再喂 `transcribe()`
- 4GB 显存下 ASR 与 TTS 同时驻留会崩 → worker 的 `finally` 里卸载 ASR
- 一次性 CLI 拿到的信息可能是**陈旧缓存**（`data/engine-info.json`，timestamp 0.0）→ `engines_cli` 必须用 `items_fresh()`；常驻进程才用 stale-while-revalidate
- Tkinter `Treeview.insert(iid=...)` 的 iid **必须唯一**：1.7B/0.6B 同名会撞出 `TclError: Item model_base already exists`，key 要带规格段
- 本地后端启动：`Start-Process python -ArgumentList '-u','-m','app.main','--no-browser' -WorkingDirectory <root>\backend`，**要等 35–50 秒**才监听 8443，别提前探活就判死
- 本机 `pwsh` 在只读沙箱里是 ConstrainedLanguage：`[System.IO.*]`、`Add-Type` 都会失败；复杂脚本写到 `.cache\*.py` 用 `python` 跑，别硬塞进 `python -c`（引号会被 PowerShell 吃掉）
- 读取 UTF-8 文件判断中文时别用 `Get-Content -Raw`（PS5 默认按 ANSI），用 `grep` 工具或 Python

---

## 6. 打包后必做的抽查

```powershell
# 1) 版本一致性
python -c "import json;print(json.load(open('VERSION.json',encoding='utf-8'))['version'])"

# 2) zip 内容抽查（文件数、体积、关键文件、不该有的东西）
python .cache/verify_1121_zips.py   # 按版本复制改名，检查：
#   VERSION.json 版本、requirements-torch.txt、cloud_engines.py、
#   frontend/dist 是否在 portable 里、config/config.yaml 是否被排除、.exe.new 是否混入
```

体积参考（v1.1.21）：src 3.77 MB / 242 文件，portable 21.02 MB / 248 文件。
**超过 25 MB 或文件数突然 +8 以上，先怀疑混进了 build 目录或临时文件。**

---

## 7. 排障速查

| 现象 | 先看哪里 |
| --- | --- |
| 界面打不开 / 白屏 | 后端是否 8443 在监听；`frontend/dist/index.html` 是否存在 |
| 进度条不动 | `data/logs/app.log` 有无「仍在进行」心跳；F12 → Network → WS 的 `engine/events` 消息 |
| 安装器报错 | `<exe 目录>\data\logs\launcher.log` |
| 云端连不上 | `POST /api/cloud/probe` 的返回（会直接说端口是否一致） |
| 模型下载失败 | 日志里的源域名；`models/<引擎目录>/*.part` 是否残留（可续传） |
| 版本没变 | `VERSION.json` 与 `backend/app/__init__.py` 是否同时改了；exe 是否重新 build |

---

## 8. 不要做的事

- ❌ 不要用 `Compress-Archive` 打包
- ❌ 不要删 `E:\trae_projects\QWENTTS-backups` 里的历史 zip
- ❌ 不要只改 `VERSION.json` 不改 `app/__init__.py`（测试会红）
- ❌ 不要在没有重启后端的情况下用浏览器验证后端改动（旧进程跑旧代码）
- ❌ 不要把 `config/config.yaml`、`data/`、`models/` 打进包
