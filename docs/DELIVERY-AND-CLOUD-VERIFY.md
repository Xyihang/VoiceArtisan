# 交付与云端实证：操作手册 + 踩坑记录（2026-10-21）

> 适用场景：要发一个可用版本（exe + 源码包 + 便携包），并且要在真云主机上证明「云端也能区分说话人」。
> 一句话结论：**打包脚本必须排除 `config/config.yaml` 和 PyInstaller 中间目录；云端模型用本地 SFTP 直传，比在云端下 HF 快且稳；ASR 不要依赖 PyAV 解码。**

---

## 一、打包交付

### 1.1 产物矩阵

| 产物 | 命名 | 含 `frontend/dist` | 含 exe | 含 `config.yaml` |
| --- | --- | --- | --- | --- |
| 源码包 | `QWENTTS-src-v<版本>-<时间戳>.zip` | ❌ | ❌ | ❌ |
| 便携包 | `QWENTTS-portable-v<版本>-<时间戳>.zip` | ✅ | ✅ | ❌ |
| 安装器 | `launcher\dist\VoiceArtisan-Setup.exe` | — | — | — |

### 1.2 标准流程

```powershell
# 0) 版本号：VERSION.json 的 version / release_date / notes 首行，并写一条 PROJECT_HISTORY

# 1) 前端构建（不构建就只能发源码包，用户得自己装 Node）
cd frontend
npm install --registry https://registry.npmmirror.com --no-audit --no-fund
npx tsc --noEmit
npx vite build

# 2) 后端自检
cd ..\backend
python -m compileall -q app
python -m pytest tests -q
python -m app.main --selfcheck        # 服务在跑时「端口占用」必然 FAIL，属正常

# 3) 安装器（约 20s；缺 PyInstaller 会自动装）
cd ..
powershell -NoProfile -File .\launcher\build.ps1
launcher\dist\VoiceArtisan-Setup.exe --self-test   # 期望：13 通过 / 2 提醒 / 0 失败，退出码 0

# 4) 两个 zip
python .cache\make_backup.py
python .cache\make_backup.py portable
```

`make_backup.py` 的关键点（这些都是踩坑后的结论）：

```python
BASE_SKIP = {"node_modules", "__pycache__", ".venv", "venv", "data", "models", ".cache", ".git"}
SKIP_SUFFIX = {".pyc", ".log"}
SKIP_REL_PREFIX = {"launcher/build"}                       # PyInstaller 中间产物
SKIP_REL = {"launcher/VoiceArtisan-Setup.spec", "config/config.yaml"}   # ← 必排，含密钥
skip = BASE_SKIP | ({"dist"} if not portable else set())   # src 不含任何 dist；portable 都含
...
zf.write(path, rel.as_posix())      # POSIX 路径：Linux unzip 才不会解出反斜杠怪名
```

### 1.3 踩过的坑

| 坑 | 现象 | 原因 | 修法 |
| --- | --- | --- | --- |
| PyInstaller 中间产物进包 | 源码包从 3.72MB 变成 13.23MB | `launcher/build/` 在打包期间被并发写，正好被扫到 | 排除 `launcher/build`，并且**先打 exe 再打 zip**，不要并发 |
| **`config/config.yaml` 进包** | 用户/云端拿到的是**你自己的**云端地址、`api_key`、SSH 配置 | 该文件在仓库里，脚本没排除 | 排除 `config/config.yaml`，包内只留 `config.example.yaml` |
| `Compress-Archive` 打 zip | Linux 解压出一堆带反斜杠的平铺文件 | PowerShell 用 `\` 当分隔符 | 用 Python `zipfile` + `rel.as_posix()` |
| zip 丢执行位 | `./scripts/start.sh` 报 Permission denied | zip 不保留 `+x` | 解压后 `chmod +x start_cloud.sh scripts/*.sh` |
| 服务在跑时打包 | zip 缺文件 / 写入失败 | SQLite、日志被占用 | 先停服务 |

### 1.4 交付前检查清单

- [ ] 干净目录解压 → `scripts\start.ps1` 能起服务，`/api/health` 返回 ok
- [ ] 前端 6 个页面 0 JS 异常；教程页分区数正确（当前 19）
- [ ] 跑一条 TTS + 一条 ASR，均 `completed`
- [ ] zip 内**没有** `data/`、`models/`、`.cache/`、`*.db`、`config/config.yaml`
- [ ] `VERSION.json` 与 `app.__version__`、MCP `serverInfo` 三者一致
- [ ] 便携包文件数 ≥ 源码包（多的正是 `frontend/dist` + exe）

---

## 二、云端实证（说话人区分）

### 2.1 模型：本地直传，别在云端下

云端直连 HuggingFace 常失败（本次是 `ConnectError: [Errno 99] Cannot assign requested address`）。
`hf-mirror` 也慢。**本地已经有模型时，直接 SFTP 传最快**：实测 2.5 MB/s，464MB + 27MB 约 3.5 分钟。

```python
# 本机：whisper 的 HF 缓存里 snapshots 是符号链接，Length 显示 0，必须先复制成实体文件
snap = models/faster-whisper/models--Systran--faster-whisper-small/snapshots/<rev>
复制 config.json / model.bin / tokenizer.json / vocabulary.txt 到本地暂存目录（Copy-Item 会跟随链接）

# 上传目标（faster-whisper 适配器认的规范布局）
/root/autodl-tmp/qwentts-models/faster-whisper/small/{model.bin,config.json,tokenizer.json,vocabulary.txt}
# CAM++（diarize 用）
/root/autodl-tmp/qwentts-models/speaker/campplus/campplus_zh_16k.onnx
```

传完不需要 `engines_cli install`：适配器 `is_installed()` 只检查目录里有没有 `model.bin` / `.onnx`。
记得删掉之前失败留下的 `*.part`。

### 2.2 云端配置单独维护

```yaml
# /root/autodl-tmp/QWENTTS/config/config.yaml（云端专用，别被源码包覆盖）
server:  {port: 18443, https: false}
storage: {data_dir: /root/autodl-tmp/qwentts-data,
          models_dir: /root/autodl-tmp/qwentts-models,
          cache_dir: /root/autodl-tmp/qwentts-cache}
run:     {mode: local}
local:   {device: cpu, dtype: float32, warmup_on_start: false}
asr:
  engine: local
  local:  {model_size: small, device: cpu, compute_type: int8, engine_id: asr-whisper-small}
  diarize: {enabled: true, prefer: local, min_speakers: 1, max_speakers: 4, engine_id: asr-speaker-campplus}
cloud:   {api_key: "cloudtest123"}
```

启动并验收：

```bash
cd /root/autodl-tmp/QWENTTS/backend
PYTHONPATH=$PWD setsid nohup /root/miniconda3/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 18443 > /root/autodl-tmp/qwentts-data/uvicorn.log 2>&1 < /dev/null &

curl -s -H "X-API-Key: cloudtest123" http://127.0.0.1:18443/v1/health
# 期望 asr: {backend:local, engine_id:asr-whisper-small, diarize:true, diarize_engine:asr-speaker-campplus}

curl -s -H "X-API-Key: cloudtest123" \
  -F "file=@/root/autodl-tmp/diarize_demo.wav" -F "language=auto" \
  -F "diarize=true" -F "max_speakers=2" \
  http://127.0.0.1:18443/v1/engine/asr
# 期望 diarize_backend:local，segments 里 SPK1 / SPK2 正确切换
```

### 2.3 踩过的坑

| 坑 | 现象 | 原因 | 修法 |
| --- | --- | --- | --- |
| 源码包覆盖云端配置 | `diarize:false`、`models_dir` 指回 `/root/autodl-tmp/QWENTTS/models`、`mode:hybrid` | 解压源码包把 `config/config.yaml` 覆盖成本机那份 | 云端配置单独上传；打包排除 `config/config.yaml` |
| **`av.open` 报 `metadata_errors`** | 转写直接 `TypeError: open() got an unexpected keyword argument 'metadata_errors'`，且被上层误标成「说话人区分失败」 | PyAV 19.x 移除了该参数，faster-whisper 1.2.1 内部固定传它 | **ASR 自己用 soundfile 解码成 16k 单声道 float32，再以 numpy 传给 `model.transcribe()`**；不再走 PyAV 文件解码，任何 av 版本都能跑 |
| `engines_cli list` 与实际不符 | 列表显示 `not_installed`，但 `/v1/health` 能用 | CLI 与服务的配置/路径解析时机不同 | 以 `/v1/health` 和真实请求为准 |
| 误读 `asr.min_speakers` | 以为是数值，实际是 `true` | 它是「服务端支持该参数」的**能力布尔值**，与 `diarize` 同语义；数值在 `GET /api/system/status` 的 `asr_diarize` 里 | 无需改代码，文档写清 |
| `pkill` + 后台启动的写法 | `uvicorn` 起了又立刻消失 | 在 `exec_command` 里用 `&` 起的进程会随 channel 关闭被杀 | 用 `setsid nohup ... < /dev/null`，放到脚本里执行 |

### 2.4 本次实测数据（region-42，无卡模式 / CPU）

| 项 | 结果 |
| --- | --- |
| `/v1/health` | `ready:true`，`asr.diarize:true`，`diarize_engine:asr-speaker-campplus` |
| `POST /v1/engine/asr`（diarize=true, max_speakers=2） | `diarize_backend:local`，`model:asr-whisper-small` |
| segments | `SPK1 0.05–5.05`、`SPK2 6.05–11.05` |
| 本地同素材 | `completed / 8.0s / SPK1+SPK2` |
| 模型传输 | whisper small 464MB + CAM++ 27MB，2.5 MB/s ≈ 3.5 min |

---

## 三、复用命令速查

```powershell
# 本地打包
python .cache\make_backup.py            # 源码包
python .cache\make_backup.py portable   # 便携包
powershell -NoProfile -File .\launcher\build.ps1
launcher\dist\VoiceArtisan-Setup.exe --self-test

# 云端（本机执行，走 paramiko）
python .cache\cloud_script.py .cache\steps\03_start.sh     # 起服务 + 健康检查
python .cache\cloud_put_models.py                          # 直传 whisper + CAM++
python -u .cache\cloud_asr_test.py                         # 双人音频 diarize 实证
```

> `.cache/` 不随包分发；这些脚本属于一次性工具，需要长期留存的内容以本文为准。

---

## 四、依赖安装：两段式 + 镜像优先（2026-10-21 补充）

### 4.1 为什么拆

原先 `requirements.txt` 第一行是 `--extra-index-url https://download.pytorch.org/whl/cu121`，**对所有包生效**：
装 fastapi/numpy 时 pip 也会去 pytorch.org 找一遍，国内经常几十 KB/s 甚至超时，看起来像"依赖装不完"。
真正需要它的只有 torch / torchaudio / torchvision。

### 4.2 现在的做法

| 文件 | 内容 |
| --- | --- |
| `requirements.txt` | 普通依赖（无 torch、无全局索引行） |
| `requirements-torch.txt` | 只有 torch/torchaudio/torchvision 的精确 pin（cu121） |
| `requirements.pins.txt` | 锁定清单，已移除 torch 三行（避免 `-i 国内镜像` 找不到 `+cu121`） |

安装顺序（`start.ps1` / `start.sh` / 安装器 / 云端 Agent S4 全部一致）：

1. **先装 torch**：`pip install -r requirements-torch.txt --index-url <镜像>`
   —— 先装是因为 `qwen-tts` 声明了 torch 依赖；若先装普通依赖，pip 会从国内镜像白拉一份 CPU 版 torch（约 900MB），随后又被 cu121 覆盖。
2. **再装普通依赖**：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

索引优先级：**上交镜像 → pytorch 官方**（实测上交索引 0.28s、官方 2.19s，官方常卡）。

### 4.3 无卡机器

- 照样装 cu121 版，不降级成 CPU 版 —— 实例开卡后直接可用
- S4 的 CUDA 断言在 `allow_no_gpu` / `prepare_only` 下只提示不失败
- **注意**：无卡机器跑到 S6 仍会失败（云端服务真的用不了 GPU，返回 `未检测到可用的 CUDA 设备`）。
  无卡准备环境的正确用法是勾 **「仅准备环境」**，跑到 S4B 即成功，S5–S8 标 skipped，开卡后点「续做」。

### 4.4 实测（region-42，无卡）

| 场景 | 结果 |
| --- | --- |
| 完整流程（prepare_only=false） | S0–S5 全绿（S4 用时约 16 分钟：torch 2.5GB 走上交镜像 + 其余依赖走清华）；S6 失败——无 GPU，属预期 |
| 仅准备环境（prepare_only=true） | **S0–S4B 全绿，succeeded**；并自动写入云端主机档案 `project_dir=~/qwentts` |
| 安装后自检 | `.venv/bin/python -c "import torch;print(torch.__version__)"` → `2.4.1+cu121` |

### 4.5 顺带修的两个问题

1. `cloud_engines` 调云端 `engines_cli` 时原来固定用系统 python；一键接入把依赖装在 `<project>/.venv`，
   系统 python 缺 `pydantic-settings` 会直接 import 失败 → 改为**优先用部署自带的 venv 解释器**。
2. `pkill -f 'app.main --host …'` 会匹配到执行它的那条 ssh 命令自身（命令行里含同样文字）→
   脚本里改用 `app[.]main` 字符类规避（排障时踩过）。
