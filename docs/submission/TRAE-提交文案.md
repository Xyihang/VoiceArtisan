# 参赛提交文案 · 声匠 VoiceArtisan（可直接粘贴）

> 对应表单字段：#12 一句话介绍 / #13 详细介绍 / #17 体验入口 / #20 源码 / #21 其它材料

---

## #12 一句话介绍你的作品

声匠·VoiceArtisan 是一款跑在自己电脑上的 AI 配音工作台：创作者把一段文字丢进去，就能在本机完成配音、克隆音色、音色设计与录音转写，成品可直接导出交付——素材不出本机，试用不按次付费，显存不够时还能一键借云端 GPU。

（备选更短版）让创作者用自己的显卡做配音：输入文字 → 本机合成/克隆/转写 → 一键导出，随时可切云端 GPU 借算力。

---

## #13 详细介绍

### 一、已实现的核心功能与亮点

**1. 本地多引擎编排（最难、也最有价值的一块）**
一个界面里管 13 个本地模型：Qwen3-TTS（1.7B/0.6B）、CosyVoice3/2/300M-SFT、VoxCPM1.5、IndexTTS-2、Whisper ×5（Tiny→Large-V3）、CAM++ 声纹。所有引擎走同一套适配器接口，新增引擎 = 写一个适配器 + 注册一行，路由/API/MCP 自动支持。
帮用户解决什么：不用再为每个模型单独配环境、单独记参数。
相比原有方式的改进：
- 每个引擎依赖装在各自 `.venv` 里，靠锁文件 `pkg==版本` 精确安装，**主环境 torch 永不被替换**（之前换引擎会把别人的环境搞崩）；
- 显存先扣「预留值」再判定，支持 float16 让 4GB 卡也能跑 1.7B、允许显存超额回落共享内存、可禁止/允许 0.6B 回退，回退会在任务里明确标注；
- 模型预热 + 常驻，让**首个请求就快**；空闲卸载可配秒级阈值；「全部卸载」顺手关掉自动预热，避免下次启动又被装回显存。

**2. 三种能力 + 一种识别，全在一页完成**
语音生成（9 个内置音色 + 自然语言指令控制语气/情绪/语速）、声音克隆（3–10 秒参考音频零样本克隆）、音色设计（用一句描述造新音色）、音频转文字（可开说话人区分，支持 SPK 重命名与合并）。
改进：参数面板 16 项且**每项写明范围、默认值与建议区间**，空值即交回引擎默认；导出 3 档（模型原生 WAV / 44.1k MP3 192k / 48k 24bit WAV），换档重编**永不覆盖原件**。

**3. 云端借算力（把"租 GPU"做成了产品功能）**
三种连法：直连 HTTPS+API Key、SSH 隧道（粘贴平台给的登录指令一键启用，本地 18443 → 云端 127.0.0.1:8443）、一键接入向导（S0–S8 十步状态机：参数校验 → SSH 连通 → 环境体检 → 上传源码 → 装依赖 → 下模型 → 起服务 → 就绪探测 → 写回本地配置 → 本地验收，每步可单独重跑，挂载模式可跳过上传与装依赖）。
改进：以前接一台 AutoDL 要手抄端口、手配隧道、手敲命令；现在只需连接信息，成功后主机档案自动写回本地配置。另配空闲自动停机（0–240 分钟）与一键关机（SSH shutdown，平台停止计费）。

**4. 任务中心：把"跑完"之后的活也做完了**
取消真的能取消（取消令牌直达推理循环，不是把状态改成 canceled 就算）、失败可**本地重试或云端重试**且参数原样保留、批量提交可逐行覆盖参数、行内播放器、字幕导出（SRT/VTT/ASS，可选每句加 [SPKn] 前缀）、按说话人拆分的只读视图、21 条错误码都给中文提示与修复建议。

**5. 给别的 AI 用：MCP Server**
9 个工具（tts_synthesize / tts_submit / asr_transcribe / asr_submit / task_status / task_cancel / voice_list / voice_clone / voice_design），stdio 传输，失败时 `isError=true` 并带错误码，附 6 步 `--selftest` 自检。Claude Desktop / Cursor / Cline 粘一段 JSON 即可用。

**6. 普通人也装得上：图形化安装器**
PySide6 重写的启动器（PyInstaller onedir 打包，**免装 Python 与 PySide6**）：环境体检（Python/依赖/GPU/前端产物/配置/模型）、7 步清单、单一主按钮、可折叠日志（2000 行上限）、停止服务/打开界面/日志目录/重试上一步。首行体检 ≤0.5 秒出结果。

**效果与体量（可核对的数字）**：本地引擎 13 个、语言与方言 13 种、内置音色 9 个、可调设置项 60+、MCP 工具 9 个；自动化测试 pytest 114 用例、界面回归 24 项、MCP 自测 6 步全部通过；对外交付 3 种包（源码包 1.1MB / 便携包 60.5MB / 销售包含 EULA）。

### 二、TRAE 在开发过程中的具体帮助（挑 4 个有实际贡献的任务）

**任务 1：把 4 个"阻塞级" bug 一次修对（2026-11 的一次集中修复）**
当时的问题：任务中心的取消按钮点了没用、重试只能走云端、生成中不能再提交、任务列表只显示 1–3 行——四个 bug 互相掩盖，凭感觉改会来回踩。
我的选择：先让 TRAE 按模板产出规格文档（`docs/spec/SPEC-TASKS-FIX-1.md`），把"什么叫修好"写成可验证的验收条件，再动代码。
TRAE 具体做了什么：定位到"取消只是改了数据库状态、没通知推理循环"，给出用 `threading.Event` 取消令牌贯通 worker → 引擎 → 分块下发的方案；补了本地/云端两条重试路径；把列表高度问题追到 `.page--fill` 的高度公式重复扣了一次 dock 安全区。
最终体现：取消/重试/追加/长列表全部可用，界面回归 24 项全绿。

**任务 2：把"卡顿"这件事量出来再改**
当时的问题：一进任务中心明显掉帧，任务列表还只能显示几行。
我的判断：不先加动画或加虚拟列表，先量。TRAE 帮我逐层量到三处根因——页面进场动画作用在滚动容器上、行高与 `content-visibility` 没开、WebSocket 每条消息都触发一次全量重载。
最终体现：改成 300ms 合并刷新、首屏之后不再出骨架、进场动画对填充型页面关闭；一次能看到十几行，滚动不再顿。

**任务 3：图形化启动器（从命令行到双击）**
当时的问题：用户拿到包第一句话是"怎么装"，批处理与 PowerShell 脚本又踩了两个坑（cmd 用 GBK 读 UTF-8 → 中文全乱码；PowerShell 5.1 读无 BOM 脚本 → 解析器直接报错）。
我的选择：脚本一律改成纯 ASCII，交互全部搬进 GUI，并把 tk 版保留为兜底。
TRAE 具体做了什么：把启动器重写为 PySide6，按要求读设计规范（单一强调色、8px 圆角、只保留 180ms 的 toast 动效），做出"顶部品牌+环境切换 / 左侧 7 步 / 中央大号进度 / 底部单一主按钮"的结构；再写 PyInstaller 打包脚本，产出 106MB 的 onedir 免依赖版本。
最终体现：`start.bat`（纯 ASCII）优先拉起 Qt 启动器 → 体检 → 一键装依赖与模型 → 起服务 → 自动打开界面。

**任务 4：MCP Server 与对外文档**
当时的问题：想让别的 AI 直接调用本机算力，第一版所有写操作都返回 422、失败还报 `isError:false`，等于对外撒谎。
我的判断：错误语义必须真，产物路径必须和真实落盘一致。
TRAE 具体做了什么：补齐 `Content-Type`、把 `ok=false` 映射成 `isError=true` 并带错误码、把产物路径按 `data/outputs/<task_id>/<file>` 修正、加语言别名归一与 `--selftest`；同时把教程里所有写死的本地绝对路径清掉，换成"直接发给你的 AI、让它自己探测路径"的可粘贴文本。
最终体现：9 个工具可用，6 步自检通过，教程页新增"用其它 AI 调用"章节。

### 三、创作过程中印象最深的一段

**"窗口在自己的机器上是好的，在别人的机器上少了一截。"**
背景：启动器打包成 EXE 后，我在 1536×864、125% 缩放的笔记本上打开，发现窗口比屏幕还高——底部的"启动服务"主按钮被折叠日志挤到了屏幕外，用户根本点不到。最糟的是，它在我的台式机（100% 缩放）上完全正常。
我承担的角色：既是开发者也是唯一的测试者，没有第二台机器可以对照，只能在自己的屏幕上复现别人的条件。
最难的一环：前两轮我都在"改布局"——缩日志、减行高、加滚动条，改完还是偏。直到我把 `availableGeometry()` 的实际值打出来，才发现它返回的是**物理像素**，而 `resize()` 吃的是**逻辑像素**：125% 缩放下两者差 1.25 倍，所以"算出来能放下"的窗口一定放不下。除以 `devicePixelRatio` 之后，窗口严丝合缝贴合可用区域。
应对方式与细节：因为离屏渲染没有字体（截出来全是豆腐块），我改用 PowerShell 抓真实窗口截图来验收，最终确认真机 775×571 完整可见；顺手补了三件事——日志默认折叠、步骤行单行省略、启动时检查窗口是否超出屏幕并自动收窄。
第二个同样难缠的问题是：打包后的 EXE 每次启动都"自动开始装依赖"，看起来像自动跑。查下来是两件事叠加：上一个进程没退干净，加上主按钮带了 default 属性，焦点落在按钮上时一个回车就触发。修法是 `setAutoDefault(False)/setDefault(False)`、把焦点交给体检表，并把每次动作以 `[动作] 启动任务：…` 的形式写进日志——现在"它到底在干什么"一眼可查。
这段经历让我确认了一件事：**判断"做好了"的标准不是我这台机器上好看，而是在别人的机器上也可用**，所以后面每个功能我都尽量配一个可量化的验收条件（114 个 pytest、24 项界面回归、6 步 MCP 自检），而不是"我看了一眼没问题"。

---

## #17 评委可进入体验的入口

**作品形态：Windows 桌面客户端（免安装便携包，解压即用；非网页、非网盘）**

**运行环境（请按此准备）**
- 系统：Windows 10 / 11 64 位；内存 8GB 起（CPU 模式建议 16GB）
- 显卡（可选）：NVIDIA GPU，≥4.5GB 显存可跑 1.7B（4GB 卡用 float16），无独显可用纯 CPU（约慢 25 倍）
- 磁盘：预留约 10GB 用于模型
- 不需要 GPU 也能体验：设置里切「仅云端」并填自己的 GPU 主机地址与 API Key（作品内置 SSH 隧道与一键接入向导）

**无需账号、无需密码、无需邀请码；本地模式下不联网也能用。**

**打开方式（约 1 分钟到界面）**
1. 解压便携包到任意目录（路径不要带中文特殊字符亦可）
2. 双击根目录 `start.bat`（脚本会自动拉起图形化启动器；也可直接运行 `launcher\dist-qt\VoiceArtisan-Setup-Qt\VoiceArtisan-Setup-Qt.exe`，免装 Python / PySide6）
3. 启动器先跑「环境体检」，按提示点唯一的主按钮：装依赖 → 装前端产物 →（在「引擎与模型」里下载模型）→ 启动服务
4. 服务起来后浏览器会自动打开界面；进入「工作台」
5. 输入一句话 → 选音色 → 点「开始生成」→ 首包到达即播放；成品在「任务中心」可播放、下载、换档重编、导出字幕

**进入核心功能的最短路径**：工作台 › 语音生成（体验合成与指令控制）；音色库 › 克隆新音色（体验零样本克隆）；任务中心 › 任一行展开（体验播放器/字幕/按说话人拆分）；设置 › 13 个分区（体验参数与云端配置）。
**不需要模型也能看的路径**：教程页 21 章内置文档、设置页 13 分区、MCP 说明，均可离线浏览。

**安装包/下载链接**：（按主办方要求提供可公开访问的直链，不使用网盘）＿＿＿＿＿＿＿＿
如需我提供下载地址或安排一次线上演示（可临时开一台云端 GPU 实例，提供临时地址与密钥），请通过提交表单邮箱与我联系。

---

## #20 源码或代码片段

**源码暂不公开**（作品按买断制授权使用，仓库为私有）。以下是 3 段体现核心思路的代码与实现说明。

**技术栈**
后端 FastAPI + SQLite + 自研 worker 队列与引擎适配器注册表；前端 React 19 + Vite + TypeScript + 自研 CSS 设计系统；桌面启动器 PySide6 + PyInstaller（onedir）；对外接口 MCP Server（stdio）；测试 pytest 114 用例 + Playwright 界面回归 24 项 + MCP `--selftest` 6 步。

**片段 A：引擎适配器的统一接口与"依赖隔离"（`backend/app/engine/adapters/base.py`）**

```python
"""适配器统一接口与公共基类。

设计原则（与「新加的引擎不需要调参就能用」对应）：
- 每个适配器把自家采样率、噪声参数、音色、文本切分粒度等**全部固化在类里**；
- 对外只暴露统一的 `TTSParams`（语速/音高/语言等），用户无需了解任何引擎私有参数；
- 缺包、缺模型、装不上都只影响该引擎自身：`health()` 返回可读原因，服务照常运行。

**隔离（isolation）**：除默认引擎 qwen3-tts-1.7b 走主环境就地推理（`isolation="inprocess"`），
其余引擎默认 `isolation="venv"`——依赖与 torch 全部关在 `<models_dir>/<engine_id>/.venv`，
合成时用 `<venv_python> runner.py ...` 子进程跑，主环境 torch 永不被替换。
"""

# 每个引擎的精确版本清单：`<adapters>/locks/<engine_id>.txt`，逐行 `pkg==x.y.z`
LOCKS_DIR = Path(__file__).resolve().parent / "locks"

# 统一能力标签：任务类型 tts/clone/design 与「指令控制」instruct
CAPABILITIES = ("tts", "clone", "design", "instruct")
```

**片段 B：显存先估算、再判定，用完及时还（`backend/app/engine/vram.py`）**

```python
def estimate_model_gb(model_path: str | Path, dtype_bytes: int = 2) -> float:
    """按主权重文件体积估算显存占用（语音解码器权重通常不常驻显存）。

    经验值：实测 0.6B 权重 1.73GB → 显存 1745MB，比例约 1.0；这里留 8% 余量。
    """
    total = 0
    for f in sorted(Path(model_path).glob("*.safetensors")) + sorted(Path(model_path).glob("*.bin")):
        total += f.stat().st_size
    base = total / 1024**3
    if dtype_bytes == 4:      # float32 大约翻倍
        base *= 2
    return round(base * 1.08, 2)


def trim_process_memory() -> None:
    """把进程工作集交还系统（CPU 模式下切换 1.7B 模型时必须，否则内存不回落）。"""
    gc.collect()
    if sys.platform == "win32":
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        # -1/-1 表示由系统决定裁剪，立即把未使用页还给系统
        ctypes.windll.kernel32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
```

**片段 C：启动器把阻塞操作放线程，只通过信号回到 UI（`launcher/voiceartisan_launcher_qt.py`）**

```python
class TaskSignals(QObject):
    log = Signal(str)
    progress = Signal(str, object, object, str)   # phase, done, total, text
    done = Signal(str, bool)


class Task(QThread):
    def __init__(self, label: str, fn, signals: TaskSignals) -> None:
        super().__init__()
        self.label, self.fn, self.signals = label, fn, signals

    def log(self, message: str) -> None:
        self.signals.log.emit(str(message))

    def progress(self, phase: str, done=None, total=None, text: str = "") -> None:
        self.signals.progress.emit(phase, done, total, text)

    def run(self) -> None:
        ok = True
        try:
            self.fn(self)
        except Exception:
            ok = False
            ...  # 记日志 + 标记失败，不抛到 UI 线程
        finally:
            self.signals.done.emit(self.label, ok)
```

**片段 D：取消是真的取消，不是改状态（`backend/app/worker.py`）**

```python
def cancel(self, task_id: str) -> bool:
    with self._cancel_lock:
        event = self._cancel.get(task_id)
    ...
    # 排队中：直接置 canceled 并从队列移除
    # 运行中：set() 取消令牌，引擎在分块下发处检查后立即中断
    event.set()
    bus.publish("task." + task_id, "task.cancel_requested", {"task_id": task_id, "stage": "running"})
```

---

## #21 其他与作品相关的材料

- **作品全功能总览图（5 张连续横屏，4000×2250）**：总览与架构 / 工作台与音色库 / 任务中心 / 设置 13 分区 / 引擎·云端·MCP·启动器 —— 已随包提供，可直接作为产品说明书阅读
- **《运行教程》12 章**（随包 markdown）：安装、运行模式、云端连接、SSH 隧道、语音识别、引擎与模型、质检与后期、导出与时长、批量、MCP、常见错误速查
- **内置教程页 21 章**：产品内可直接浏览，与包内路径逐一对应
- **《MCP 接入文档》**：9 个工具的入参/返回、3 种客户端配置写法、可直接发给别的 AI 的整段文本
- **《最终用户许可协议（EULA）》**：买断制，仅授予使用许可，不得公开发布修改版
- **界面与验收截图**：工作台、任务中心（行内播放器）、云端引擎同步、安装任务续跑、启动器深/浅主题真机截图
- **验证数据**：pytest 114 用例通过 / 界面回归 24 项通过 / MCP 自测 6 步通过 / TypeScript 编译 0 错误 / 启动器体检首行 ≤0.5 秒
- **交付包清单**：源码包（1.10MB / 237 文件）、便携包（60.80MB / 411 文件）、销售包（60.50MB / 380 文件，含 EULA 与运行教程）
