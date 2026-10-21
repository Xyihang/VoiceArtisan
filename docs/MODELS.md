# 模型获取（中国大陆可访问）

本项目**不自动下载模型**，请按下表链接自行下载后放到指定目录。全部链接均为 ModelScope（国内直连，无需翻墙）。

> 一键下载（可选）：双击项目根目录的 `download-models.bat`（Windows）或执行 `./download-models.sh`（Linux/macOS），
> 等价于 `python scripts/download_models.py --size 1.7B`，支持断点续传与完整性校验。

## 0. 与参考项目的模型对照

MD 里的参考项目（Xyihang/voice-generation-system-based-on-QwenTTS）只用了 **2 个** TTS 模型 + openai-whisper：

| 能力 | 参考项目 | 本项目 |
| --- | --- | --- |
| 语音生成 + 声音克隆 | `1.7B-Base`（共用） | `1.7B-Base`（克隆）+ `1.7B-CustomVoice`（内置音色 + 指令控制） |
| 音色设计 | `1.7B-VoiceDesign` | `1.7B-VoiceDesign` |
| 音频转文字 | openai-whisper `large-v3-turbo`（`.pt`） | faster-whisper，可选 `small / medium / large-v3 / large-v3-turbo` |

即：只对齐参考项目时，下载 **Base + VoiceDesign** 两个即可跑通全部四类能力的基础形态；
额外下载 `CustomVoice` 才能使用 9 个内置音色与指令控制（推荐）。

## 1. 需要哪些模型

| 能力 | 模型仓库 | 下载后目录（放在项目 `models/` 下） | 大小 |
| --- | --- | --- | --- |
| 语音生成（内置音色 + 指令控制） | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` | `models/Qwen3-TTS-12Hz-1.7B-CustomVoice` | ≈4.2 GB |
| 声音克隆（零样本） | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | `models/Qwen3-TTS-12Hz-1.7B-Base` | ≈4.2 GB |
| 音色设计（自然语言描述生成音色） | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | `models/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | ≈4.2 GB |

合计约 **12.6 GB**。三个都要下载：本地「音色定制」= CustomVoice（内置音色+指令）+ VoiceDesign（描述生成音色）+
Base（参考音频克隆），缺哪个，对应能力就会报 `MODEL_MISSING`。

## 2. 下载链接（国内直连）

**模型主页（浏览器打开，点「下载模型」或逐个文件下载）**

- CustomVoice：https://www.modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
- Base：https://www.modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-Base
- VoiceDesign：https://www.modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign

**Git 克隆（需先装 git-lfs）**

```bash
git lfs install
cd <项目>/models
git clone https://www.modelscope.cn/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice.git
git clone https://www.modelscope.cn/Qwen/Qwen3-TTS-12Hz-1.7B-Base.git
git clone https://www.modelscope.cn/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign.git
```

**ModelScope CLI（推荐，支持断点续传）**

```bash
pip install "modelscope==1.40.2"
modelscope download --model Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice --local_dir ./models/Qwen3-TTS-12Hz-1.7B-CustomVoice
modelscope download --model Qwen/Qwen3-TTS-12Hz-1.7B-Base        --local_dir ./models/Qwen3-TTS-12Hz-1.7B-Base
modelscope download --model Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign --local_dir ./models/Qwen3-TTS-12Hz-1.7B-VoiceDesign
```

**Hugging Face 镜像（备选）**

- `https://hf-mirror.com/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`
- `https://hf-mirror.com/Qwen/Qwen3-TTS-12Hz-1.7B-Base`
- `https://hf-mirror.com/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

```bash
export HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice --local-dir ./models/Qwen3-TTS-12Hz-1.7B-CustomVoice
```

## 3. 目录校验

下载完成后，`models/` 应长这样（每个目录都要有 `model.safetensors` 与 `speech_tokenizer/model.safetensors`）：

```
models/
├─ Qwen3-TTS-12Hz-1.7B-Base/
│  ├─ config.json  generation_config.json  model.safetensors  tokenizer_config.json …
│  └─ speech_tokenizer/{config.json, model.safetensors}
├─ Qwen3-TTS-12Hz-1.7B-CustomVoice/  （同上结构）
└─ Qwen3-TTS-12Hz-1.7B-VoiceDesign/  （同上结构）
```

自检命令（会逐项报告缺失与修复建议）：

```bash
cd backend && python -m app.main --selfcheck
```

界面「状态与日志 → 运行环境自检」也会列出三个模型的路径与大小。

## 4. 显存与 1.7B

| 显存 | 表现 |
| --- | --- |
| ≥12GB | 1.7B fp16/bf16 常驻显存，首包最快 |
| 8-12GB | 可常驻，`local.dtype` 用 float16 或 bfloat16 均可 |
| **4-8GB** | **可以跑 1.7B**：`dtype: float16` + `vram_reserve_gb: 0.1` + `allow_vram_overcommit: true`，靠 WDDM 共享内存小幅超额；实测 4GB 卡三种能力全部跑通（显存峰值 4049MB） |
| 无 GPU | `local.device: cpu`（float32 + eager），功能完整但慢 3-5 倍 |

实测（RTX 3050 **4GB**，桌面走核显、独显空闲，见 `docs/BENCHMARK.md`）：

| 能力 | 首包 | 耗时 | 音频 | RTF |
| --- | --- | --- | --- | --- |
| 语音生成（1.7B-CustomVoice） | 9.1s | 25.3s | 5.21s | 0.206 |
| 声音克隆（1.7B-Base） | 25.1s | 37.1s | 4.18s | 0.113 |
| 音色设计（1.7B-VoiceDesign） | 22.0s | 22.0s | 1.68s | 0.076 |

注意事项：
- **桌面/浏览器不要占用独显**（主显示走核显时独显只占约 150MB，正好够 1.7B 整模型加载）；
  独显被占导致 OOM 时返回 `VRAM_INSUFFICIENT`，界面可一键切云端；
- fp16 偶发采样 `inf/nan` → 把 `local.dtype` 改为 `bfloat16`（同尺寸、略慢）；
- 不要开 `local.low_vram`（CPU/磁盘 offload）：qwen-tts 0.1.1 加载后生成会报 `Tensor.item() on meta tensors`，默认已关闭。

## 5. 可选：语音转文字模型

本地 ASR 使用 faster-whisper，首次调用会自动下载到 `models/faster-whisper/`（默认走 `HF_ENDPOINT=https://hf-mirror.com`，国内可用）。
也可离线获取：https://hf-mirror.com/Systran/faster-whisper-small
（可选规模：`tiny / base / small / medium / large-v3 / large-v3-turbo / distil-large-v3`，在设置页切换；
参考项目用的是 openai-whisper `large-v3-turbo`，本项目对应选项为 `large-v3-turbo`。）

## 6. 可选：0.6B 回退模型（默认不启用）

如果确实需要在极小显存上跑（并接受降级），可额外下载：

- https://www.modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice
- https://www.modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-0.6B-Base

然后在配置中把 `local.allow_small_fallback` 设为 `true`（界面会显式标注「0.6B 回退」）。
注意：ModelScope 上**没有** 0.6B 的 VoiceDesign，音色设计只能用 1.7B。

## 7. 许可

Qwen3-TTS 模型许可以官方仓库 README 为准；商用前请自行确认，并确保所用音色/参考音频已获授权。

> 新设备从零开始（装依赖 → 选规格 → 下模型）的完整步骤见 [QUICKSTART.md](QUICKSTART.md#新设备从零开始06b-或-17b二选一)。

## 0.6B 小显存档（2GB 显存可用）

不是所有人都有一块 ≥6GB 的卡。因此项目内置了 **Qwen3-TTS 0.6B** 的独立引擎卡片，
和 1.7B 并列，**由你选择下哪一个**（也可以两个都下，互不影响）。

| 项 | 1.7B（默认） | 0.6B（小显存） |
| --- | --- | --- |
| 引擎 id | `qwen3-tts-1.7b` | `qwen3-tts-0.6b` |
| 对话里显示 | Qwen3-TTS 1.7B（默认） | Qwen3-TTS 0.6B（小显存） |
| 模型目录 | `models/Qwen3-TTS-12Hz-1.7B-Base` / `-CustomVoice` / `-VoiceDesign` | `models/Qwen3-TTS-12Hz-0.6B-Base` / `-CustomVoice`（由 `local.fallback_models` 指定） |
| 下载体积 | 约 **12.6 GB**（3 个目录） | 约 **3 GB**（2 个目录） |
| 显存 | 建议 **≥6 GB**（4GB 卡靠共享内存可跑但慢） | 约 **2 GB** 即可 |
| 能力 | 语音生成 / 声音克隆 / **音色设计** / 指令控制 | 语音生成 / 声音克隆（**音色设计只有 1.7B 提供**） |
| 语言 | 12 种（含北京/四川方言） | 12 种 |

### 怎么装

**方式一（推荐）：界面里点**

设置 → 引擎与模型 → 找到 **「Qwen3-TTS 0.6B（小显存）」** 卡片 → 点「预览」看体积 → 点「安装」。
它调用的是同一套下载脚本：`scripts/download_models.py --size 0.6B`。

**方式二：命令行**

```bash
# 只下 0.6B（约 3GB）
python scripts/download_models.py --size 0.6B
# 两个规格都下（约 15.6GB）
python scripts/download_models.py --size all
# 只下 1.7B（默认）
python scripts/download_models.py --size 1.7B
```

### 怎么切换

卡片上点 **「使用」**，后端会自动：

1. 卸载当前引擎并清显存；
2. 把 `engine.active` 改成对应 id；
3. 把 **`local.size` 同步改成 `1.7B` / `0.6B`**（本地推理按这个选目录）。

验证方式：

```bash
curl -k https://localhost:8443/api/settings | grep -o '"size":"[^"]*"'
curl -k https://localhost:8443/api/engines   | grep -o '"active":"[^"]*"'
```

### 注意

- **两个规格可以共存**，磁盘各占各的；删掉其中一个不影响另一个；
- 0.6B 的 `voice_design` 在配置里仍指向 1.7B 的 VoiceDesign 目录（`local.fallback_models.voice_design`），
  所以**用 0.6B 时如果没装 1.7B 的 VoiceDesign，音色设计会用不了**（其它能力正常）；
- 显存不足时后端不会静默降级：要么明确报错并建议切云端，要么在开启 `local.allow_small_fallback`
  后回退到 0.6B，并在任务与界面标注「0.6B 回退」。
