# 排障手册

先记住三个位置，90% 的问题都能自己定位：

| 位置 | 看什么 |
| --- | --- |
| 界面「状态与日志」页 | 后端健康、显存、自检项（带修复建议）、最近日志 |
| `data/logs/app.log` | 完整运行日志（8MB × 5 轮转） |
| `cd backend && python -m app.main --selfcheck` | 启动自检：端口 / Python / CUDA / 模型目录 / 数据盘 |

界面报错只显示**可读文案 + 错误码**，不暴露堆栈；堆栈只在日志里。

---

> 安装器（VoiceArtisan-Setup.exe）相关的问题（体检项、依赖门禁、前端构建）请看 [LAUNCHER.md](LAUNCHER.md) 的「缺陷修复记录」与「复核清单」。

## 一、启动阶段

| 现象 / 错误码 | 原因 | 处理 |
| --- | --- | --- |
| `端口 8443 被占用` | 端口冲突 | 改 `server.port`；或查占用：`netstat -ano \| findstr :8443` / `lsof -i:8443` |
| 启动后浏览器打不开 | 自签证书未信任 | 点「高级 → 继续前往」；或先访问 `/api/health` |
| 页面 404 /「前端未构建」 | `frontend/dist` 不存在 | `cd frontend && npm install --registry https://registry.npmmirror.com && npm run build` |
| 依赖安装失败 | 网络/镜像 | `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| PowerShell 脚本报解析错误 | 文件编码 | 项目内 `scripts/start.ps1` 已是 UTF-8 BOM；若自行修改过请保存为「UTF-8 with BOM」 |
| 控制台中文乱码 | Windows 代码页 | `chcp 65001`，或设 `PYTHONIOENCODING=utf-8` |

## 二、模型加载

| 错误码 / 现象 | 原因 | 处理 |
| --- | --- | --- |
| `MODEL_MISSING` | 模型目录不存在或层级不对 | 目录必须**直接包含 `config.json`**（不是它的上一层）；见 [MODELS.md](MODELS.md) |
| `MODEL_LOAD_FAILED` | 文件不完整 / CUDA 异常 | 重新下载；`python scripts/download_models.py --verify` 校验；`python scripts/run_infer.py --dry-run` 单跑定位 |
| `VRAM_INSUFFICIENT`（加载时） | 独显被占用、显存不足 | 关掉游戏/浏览器硬件加速；主显示切核显；或 `local.device: cpu`；或切云端 |
| `VRAM_INSUFFICIENT`（推理时） | 长文本 + 高采样导致峰值超限 | 缩短文本、降低 `max_new_tokens`/`steps`、或用云端 |
| `Tensor.item() cannot be called on meta tensors` | 开了 `local.low_vram`（CPU/磁盘 offload） | 该开关为实验特性且与 qwen-tts 0.1.1 不兼容，**保持 `low_vram.enabled: false`** |
| 采样报 `probability tensor contains inf/nan` | fp16 数值不稳定 | `local.dtype: bfloat16` |
| 加载很慢（>60s） | 首次读盘 + 显存分配 | 正常；开 `warmup_on_start: true` 让启动时就加载 |
| 内存不足（CPU 模式）`可用内存 X GB 不足` | CPU 模式每个 1.7B 约需 5-6GB | 关其它程序；系统会自动卸载其它模型再加载 |

## 三、生成与音频

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 首包特别慢 | 非流式（默认）：首包 = 整段耗时 | 需要边生成边播 → 高级参数里打开「流式合成」 |
| 声音听起来断 / 不连贯 | 流式分块拼接 | 关掉流式（默认已关）；或调大 `chunk_chars` |
| 内容和文字对不上、缺字 | 分块切断了 `1.7B`/`4GB` 之类 | 已修（只在标点/词边界切）；若自行改过 `textsplit.py` 请检查 |
| 语速/音高不生效 | 参数范围被夹取 | 语速 0.5-2.0、音高 ±12 半音 |
| mp3 导出失败 | 没有 ffmpeg | 安装 ffmpeg 并加入 PATH，或输出改 wav |
| 下载没反应 | 自签证书页面的下载被浏览器拦截 | 前端已用 fetch+Blob 规避；仍异常时右键「链接另存为」 |
| 试听没声音 | 浏览器自动播放限制 | 先点一次播放按钮（首次需要用户手势） |

## 四、语音转文字

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ASR_FAILED` | 模型未下 / 依赖缺失 | 首次使用会下到 `models/faster-whisper/`；确认 `python -m pip install --no-cache-dir -r requirements.pins.txt`（`faster-whisper==1.2.1` 已锁） |
| 转写结果离谱（如「4GB」听成「四季」） | 模型太小 | 设置里把 ASR 规模改为 `small`/`medium`/`large-v3` |
| 转写一直失败并提示云端 | 本地失败后回落云端且云端未配置 | 已修：未配置云端时保留本地错误；检查 ASR 日志 |
| 长音频超时 | 默认上限 1800s | 调 `limits.max_asr_audio_seconds`，或切分音频 |

## 四之二、说话人区分与重导出（B6/B7/B8/B9 + CLOUD-DIARIZE-1）

这五个错误码都是「可读错误 + 修复指引」，不会把堆栈丢到界面上；先看错误 `detail` 里括号中的具体原因。

| 错误码 | 现象 | 原因 | 三步解决 |
| --- | --- | --- | --- |
| `EXPORT_SOURCE_MISSING` | 点「重新导出 / 换档位」或「按说话人拆分」时报「缺少原始音频（source.wav）」 | 该任务是升级前生成的，目录里没有无损原件 `outputs/<id>/source.wav`（或原件为空），无法从原件重编码 | ① 打开 `data/outputs/<task_id>/` 确认只有成品、没有 `source.wav`；② **重新生成该任务**（新任务会自动留原件）；③ 若只是想换个格式，先用原任务已有的成品下载，或改 `export.tier` 后重新生成 |
| `SPEAKER_TIMELINE_MISSING` | 合并说话人 / 按说话人拆分时报「缺少说话人时间轴」 | 有 `chunks.json` 但分段里没有任何说话人（旧任务，或转写时没勾「区分说话人」）；没有 `chunks.json` 的任务报的是 `SUBTITLE_NOT_FOUND` | ① 看任务详情有没有 `segments` 且每段带 `speaker`；② 重新转写并勾选「区分说话人」（`asr.diarize.enabled` 或任务级 `params.diarize=true`）；③ 确认声纹引擎已装（本地 CAM++ / 云端 `asr-speaker-campplus`）后重试 |
| `CLOUD_DIARIZE_UNSUPPORTED` | 报「云端未安装说话人区分引擎」，任务没出说话人标签 | `asr.diarize.prefer=cloud`（或 auto 选中云端）时，云端 `/v1/health.asr.diarize=false`：云端没装 CAM++；或旧版云端压根没有 `asr` 块 | ① 在云端执行 `engines_cli install asr-speaker-campplus`（或设置 → 引擎与模型 安装「CAM++ 声纹」）；② 或把 `asr.diarize.prefer` 改成 `local` 用本地 CAM++；③ 用 `curl -fk https://<云端>/v1/health` 确认 `asr.diarize=true` 后重试 |
| `DIARIZE_UNAVAILABLE` | 报「本地与云端都没有可用的说话人区分能力」 | 本地声纹引擎/依赖未装，且云端也不支持（`prefer=auto` 两条路都不通）；或声纹抽取失败（音频过短/采样率不支持） | ① 本地：设置 → 引擎与模型 → 语音识别，安装「CAM++ 声纹」并确认 `scikit-learn` 可用；② 云端：`engines_cli install asr-speaker-campplus`；③ 音频至少含 2 段可分辨人声（单人长静音会被判为无法区分），换 `prefer=local` 再试一次 |
| `SPEAKER_SPLIT_NOT_FOUND` | 打包下载分轨时 404 | 还没调过 `POST /api/tasks/{id}/split-by-speaker`，`outputs/<id>/speakers/` 里没有 wav | ① 先调拆分接口（界面「按说话人拆分」）；② 看返回数组是否非空（每条含 `file`/`duration_s`/`segments`）；③ 再下载 `GET /api/tasks/{id}/speakers/zip` |

排查顺序建议：`GET /api/system/status` 看 `asr_candidates`（实际尝试顺序）与 `config.asr_diarize`（生效中的 prefer/min/max）→
`GET /v1/health` 看云端 `asr.diarize` → 任务详情看 `params.diarize_backend`（`local`/`cloud`/`null`）与 `segments[].speaker`。

## 五、云端与隧道

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `CLOUD_UNREACHABLE`（含「未配置云端地址」） | `base_url` 还是占位值 / 端口没通 | 填真实地址；浏览器先访问 `https://<云端>/v1/health`；见 [CLOUD_SETUP.md](CLOUD_SETUP.md) |
| `CLOUD_AUTH_FAILED` / 401 | 本地与云端密钥不一致 | 重设云端 `QWENTTS__CLOUD__API_KEY` 并同步到设置页 |
| `CLOUD_NOT_READY` | 云端模型还在加载 | 等；或调大 `readiness_timeout_s`；云端开 `warmup_on_start` |
| 隧道建立失败 | 端口/用户名/密钥错 | 先命令行 `ssh -p <端口> user@host` 验证；AutoDL 的 SSH 端口不是 22 |
| 任务总在本地跑 | 模式是 hybrid 且本地能跑 | 改「仅云端」，或任务里勾 `force_backend=cloud` |
| 云端重启后任务丢失 | 任务在本地队列，已落库 | 本地任务不受影响；云端重新就绪后重试即可 |

## 五之二、引擎安装失败（最常见三类）

| 报错 / 现象 | 根因 | 处理 |
| --- | --- | --- |
| `UnicodeDecodeError: 'gbk' codec can't decode byte`（三个镜像都报同样错） | pip 读 `-r`/`-c` 文件时按系统 locale（中文 Windows = GBK）解码，而锁文件里有中文注释 | 已修：锁文件强制纯 ASCII + pip 子进程 `PYTHONUTF8=1` + `_ascii_copy()` 净化。自己改锁文件时**不要写中文注释** |
| `No matching distribution found for xxx==y` | 该镜像没有这个包（如清华镜像没有 `voxcpm`） | 程序自动换下一个源（日志写「镜像缺包」）；也可手动用官方 PyPI 安装 |
| `ResolutionImpossible` / `conflicting dependencies` | 引擎与主环境版本冲突（如引擎要 `torch>=2.5`，约束钉 2.4.1） | 程序**立即中止**，不再三源各下一遍大包。正确做法：在 `locks/<engine_id>.txt` 里给该引擎钉它自己的 torch（只装进它的 venv），主环境保持 `2.4.1+cu121` |
| 反复下载 2.4GB / 安装很慢 | 早期每个镜像重试都重下 wheel | 已改为 pip 缓存 `.cache/pip`，重试复用 wheel |

## 六、任务与数据

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 任务卡在「运行中」很久 | CPU 模式 / 长文本，正在逐帧生成 | 看日志里的「生成中：已完成 x/y 字」心跳；必要时取消 |
| 服务重启后任务变「失败」 | 运行中的任务被中断 | 一键「重试」；排队中的任务会自动重新入队 |
| 磁盘越来越满 | 结果与上传累积 | 任务中心批量删除；`data/uploads` 可定期清 |
| 数据想搬到别的盘 | 默认在项目内 | 改 `storage.data_dir` 为绝对路径后重启；迁移旧数据可直接拷贝目录 |

## 七、性能调优速查

| 目标 | 做法 |
| --- | --- |
| 更快首包 | 打开流式（`defaults.streaming: true`）、`chunk_chars` 调小（24-32） |
| 更好音质/连贯 | 关闭流式（默认）；文本按句分段手动提交 |
| 省显存 | `dtype: float16`、`vram_reserve_gb: 0.1`、`allow_vram_overcommit: true` |
| 省内存（CPU 模式） | 一次只用一个模型（`warmup_kinds` 只留一个）、关掉其它程序 |
| 省云端费用 | 开启空闲自动停机、只下需要的能力模型、用完后在厂商控制台关机 |
| 减少等待 | `warmup_on_start: true` + `warmup_kinds` 只放常用的那个 |

## 八、一键自检命令

```bash
cd backend && python -m app.main --selfcheck      # 环境自检（不启动服务）
python scripts/run_infer.py --dry-run             # 只加载模型，验证路径与显存
python scripts/download_models.py --verify        # 校验模型完整性
curl -fk https://127.0.0.1:8443/api/health        # 服务是否在线
curl -fk https://127.0.0.1:8443/api/system/selfcheck   # 界面同款自检（JSON）
```

## SoX 报错（`SoX could not be found` / `Failed to load sox`）

**原因**：torchaudio 2.x 在 Windows 默认用 `sox_io` 后端，需要系统里能找到 `sox.exe` 和
`libsox` 及其格式库。从官网装了 SoX 仍报错通常是：安装目录没进当前进程的 PATH（要重启程序才生效）、
或缺少依赖 DLL。

**已在程序里解决**：后端启动时（`backend/app/soxfree.py`）会把 `torchaudio.load / save / info`
的默认后端切换成 `soundfile`，引擎子进程（`adapters/runner.py`）也一样先打这个补丁。
`soundfile` 的 wheel 自带 libsndfile（wav/flac/ogg/mp3 全支持），**不需要任何外部可执行文件**。

**soundfile 由程序自己装，不需要用户动手**：
- 新建引擎 venv 时已包含在 `requirements.engine-core.txt` 里；
- 老 venv 缺 soundfile 时，程序会在两个时机自动补装（多镜像回退：清华 → 阿里 → 官方）：
  1. 每次隔离引擎推理前自检一次（缺失即装，幂等）；
  2. 引擎「安装」完成后。
- 仍然失败时看 `data/logs/` 里对应引擎日志，把 `soundfile 检查` 那行发出来。
