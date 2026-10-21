# 快速开始（10 分钟跑起来）

只想要「装好 → 出第一段音频」，看这一页就够。其它文档见 [docs/README.md](README.md)。

---

## 0. 前置条件

| 项 | 要求 | 检查命令 |
| --- | --- | --- |
| 操作系统 | Windows 10/11 或 Ubuntu 20.04+ | — |
| Python | **3.10 及以上** | `python --version` |
| 磁盘 | ≥15GB 空闲（1.7B 三件套约 12.6GB） | — |
| 显卡（可选） | NVIDIA ≥4GB 显存；**没有独显也能用**（切云端或 CPU） | `nvidia-smi` |
| Node.js（可选） | 18+，仅首次构建前端需要 | `node --version` |

> 没有独显、或不想在本机跑模型？直接看 [云 GPU 接入教程](CLOUD_SETUP.md)，本机只当界面用。

---

## 1. 一条命令启动

### Windows（最简单：双击）

```
双击项目根目录的 start.bat
```

它会自动检查 Python、安装依赖、构建前端、生成配置、跑自检、起服务并打开浏览器，出错时窗口会停住显示原因。

### Windows（PowerShell，在项目根目录）

```powershell
.\scripts\start.ps1
```

依赖想锁到本机验证过的版本（推荐换机器时用）：

```powershell
pip install -r requirements.lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Linux / macOS

```bash
chmod +x scripts/*.sh && ./scripts/start.sh
```

脚本会自动做 5 件事：检查 Python → 把缓存重定向到项目内 `.cache`（不写 C 盘）→ 装缺失依赖 →
构建前端（有 npm 时）→ 生成 `config/config.yaml` → 打印启动自检 → 起 HTTPS 服务并打开浏览器。

**首次访问**浏览器会提示证书不安全：点「高级 → 继续前往」，或先访问一次 `https://localhost:8443/api/health` 完成信任。

---

## 2. 放模型（没有模型也能先看界面）

### 想先看界面（占位引擎）

```powershell
# 编辑 config/config.yaml
mock:
  enabled: true
```

重启后界面照常，会标注「模拟引擎」——只用于联调，不产生真实语音。

### 想真的出声：下载 1.7B 三件套

```bash
python scripts/download_models.py --size 1.7B      # ModelScope 直连，约 12.6GB
```

下载完自查：

```bash
cd backend && python -m app.main --selfcheck       # 三个 1.7B 都应显示 [ OK ]
```

模型放哪、直链、断点续传细节见 [模型获取](MODELS.md)。

---

## 3. 生成第一段音频

1. 打开 `https://localhost:8443`；
2. **工作台** → 输入一句中文 → 选一个内置音色（如 Vivian）→ 点「开始生成」；
3. 右侧「结果」出现播放器即成功，首包耗时 / 实时率 / 显存都在「运行状态」里。

首包慢是正常的：模型首次要加载到显存（1.7B 约 5-15 秒），之后复用。

---

## 4. 验收清单（按需勾选）

| 检查项 | 期望 |
| --- | --- |
| 顶栏状态 | 显示「本地 / 云端 + 延迟」，断线 3 秒内变红 |
| 启动自检 | 端口、Python、CUDA、模型目录、数据盘全部 `[ OK ]` |
| 生成 | 任务中心出现任务，状态 `已完成`，可下载 wav/mp3 |
| 声音克隆 | 工作台上传 3-10 秒人声 → 生成后音色接近参考音频 |
| 音色设计 | 输入「三十岁女性，温柔，语速稍慢」→ 得到对应风格 |
| 音频转文字 | 上传音频 → 任务详情里出现转写文本（首次会下 ASR 模型） |
| 设置保存 | 改任一项，约 0.7 秒后提示「已自动保存」，切页不丢 |
| 数据位置 | `data/` 在项目盘，自检显示「数据盘 E:（非系统盘）」 |

---

## 5. 下一步

| 想做的事 | 看这里 |
| --- | --- |
| 接云 GPU（省本机显存、要更快） | [CLOUD_SETUP.md](CLOUD_SETUP.md) |
| 改配置（模式/显存/ASR/限额…） | [CONFIG.md](CONFIG.md) |
| 部署成服务、反代、备份升级 | [DEPLOY.md](DEPLOY.md) |
| 报错怎么修 | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| 接口/二次开发 | [API.md](API.md) |

---

## 6. 常见首次启动问题

| 现象 | 处理 |
| --- | --- |
| `端口 8443 被占用` | 改 `config/config.yaml` 的 `server.port`，或结束占用进程 |
| 依赖装不上 | 手动：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 前端未构建（页面 404） | `cd frontend && npm install --registry https://registry.npmmirror.com && npm run build` |
| `MODEL_MISSING` | 模型没下或路径不对，见 [MODELS.md](MODELS.md) |
| `VRAM_INSUFFICIENT` | 独显被其它程序占用 → 关闭游戏/浏览器硬件加速，或切云端/CPU |
| 采样报 `inf/nan` | 把 `local.dtype` 改成 `bfloat16` |
| 控制台中文乱码（Windows） | 已修：脚本内置 UTF-8；若仍乱码执行 `chcp 65001` |

## 新设备从零开始（0.6B 或 1.7B，二选一）

新机器上**依赖和模型是两件独立的事**，都要处理，但顺序很重要：先装依赖 → 再下模型（避免白下 12.6GB）。

### 第 1 步：装依赖（必须，约 2.5GB）

```bash
python -m pip install -r requirements.pins.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python -c "import torch, qwen_tts; print('deps ok')"     # 必须打印 deps ok
```

> 安装器里就是「安装依赖」按钮；**没装依赖时「启动服务」会被直接拦下**（会提示缺 torch / qwen-tts）。

### 第 2 步：按显存选规格

| 你的显存 | 选哪个 | 下载体积 | 在界面上怎么点 |
| --- | --- | --- | --- |
| ≥ 6 GB | **1.7B**（质量最好，支持音色设计） | 约 12.6 GB | 设置 → 引擎与模型 → 「Qwen3-TTS 1.7B（默认）」→ 安装 |
| 2 ~ 6 GB | **0.6B**（约 2GB 可跑，不含音色设计） | 约 3 GB | 设置 → 引擎与模型 → 「Qwen3-TTS 0.6B（小显存）」→ 安装 |
| 没有 GPU | 0.6B 也行但极慢，建议用云端 | 3 GB 或 0 GB | 见 [CLOUD_ACCESS_TUTORIAL.md](CLOUD_ACCESS_TUTORIAL.md) |

**两套可以都装**（互不覆盖，各占各自目录），用哪套就在卡片上点「使用」。

### 第 3 步：下载模型（命令行等价写法）

```bash
python scripts/download_models.py --size 0.6B      # 约 3GB（Base + CustomVoice）
python scripts/download_models.py --size 1.7B      # 约 12.6GB（三件套）
python scripts/download_models.py --size all       # 两套都下（约 15.6GB）
python scripts/download_models.py --size 0.6B --verify   # 只校验，不下载
```

模型目录（默认 `<项目根>/models`）：

```
models/Qwen3-TTS-12Hz-1.7B-Base/          ← 1.7B 声音克隆
models/Qwen3-TTS-12Hz-1.7B-CustomVoice/   ← 1.7B 内置音色
models/Qwen3-TTS-12Hz-1.7B-VoiceDesign/   ← 1.7B 音色设计（仅 1.7B 有）
models/Qwen3-TTS-12Hz-0.6B-Base/          ← 0.6B 声音克隆
models/Qwen3-TTS-12Hz-0.6B-CustomVoice/   ← 0.6B 内置音色
```

> 想把模型放别的盘：设置里改 `storage.models_dir`，或下载时 `--out D:/models`。
> 此时引擎卡片的「安装」也会自动跟随该目录（已显式传 `--out`）。

### 第 4 步：启动并确认

```bash
python -m app.main            # 或安装器点「启动服务」
```

界面上确认：

```bash
curl -k https://localhost:8443/api/engines | grep -o '"id":"[^"]*"'
curl -k https://localhost:8443/api/settings | grep -o '"size":"[^"]*"'
```

- 卡片「已就绪」= 该规格模型齐全；
- 点「使用」后 `size` 会变成 `1.7B` 或 `0.6B`，本地推理按它选目录。

### 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 卡片显示「未安装」但目录里明明有文件 | 文件不全（缺 `model.safetensors` 或 `speech_tokenizer/model.safetensors`） | 点「安装」补齐（已下载会跳过），或 `--verify` 看缺哪个 |
| 装了 0.6B 但「音色设计」报错 | 0.6B 没有 VoiceDesign | 要么装 1.7B 的 VoiceDesign，要么改用内置音色/克隆 |
| 显存不足报错 | 1.7B 需要 ≥6GB | 换 0.6B（点卡片「使用」）或切云端 |
| 下载很慢 / 中断 | 单线程 + 国内镜像 | 重跑同一命令会续传；不建议同时开多个下载 |
