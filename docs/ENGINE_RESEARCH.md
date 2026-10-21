# 中文语音合成/克隆模型 国内可获取性核查（ENGINE_RESEARCH）

> 纯调研产出，未改动 backend/frontend。核查时间：本次会话；所有体积、许可证、文件名均来自当场抓取的仓库 API / 模型卡，未凭记忆填写。
> 目标机型：RTX 3050 Laptop（4GB 显存）+ CPU 兜底。

> ⚠️ **本文出现的 `pip install <模型包>` 只是调研时抄下来的上游命令，禁止在主环境执行。**
> `voxcpm` 声明 `torch>=2.5`，直接装会把主环境 `torch 2.4.1+cu121` 换成 CPU 版（真实事故）。
> 本项目已按引擎隔离：依赖只装进 `<models_dir>/<engine_id>/.venv`，命令由程序生成
> （见 [ENGINE_ISOLATION.md](ENGINE_ISOLATION.md) 与 `python -m app.engines_cli deps <id>`）。

## 0. 核查方法与网络事实（先看这一节，很多结论由此而来）

### 0.1 本机实测的可达性（HEAD/GET 实测结果）

| 站点 | 结果 | 说明 |
| --- | --- | --- |
| `modelscope.cn` | ✅ 200 | API 与权重 `repo?FilePath=` 直链均可用 |
| `hf-mirror.com` | ✅ 200 | API / `raw/` / `resolve/` 直链均可用 |
| `huggingface.co` | ❌ 无法连接 | 官方站直连不可用 |
| `github.com` | ❌ 超时 | **所有需要 clone 官方代码的模型都因此受影响** |
| `gitee.com` / `gitcode.com` | ✅ 200 | 域名可达（是否有对应仓库镜像：未核实） |
| `gh-proxy.com` | ✅ 200；`ghfast.top` ❌ 超时 | 代理域名可达，但能否拉取具体仓库未核实 |
| `mirrors.aliyun.com/pypi` | ✅ | `pypi.tuna.tsinghua.edu.cn` 反而 403 |

### 0.2 采集手段（可复现）

- ModelScope 文件树（含真实字节数）：
  `https://modelscope.cn/api/v1/models/{owner}/{repo}/repo/files?Revision=master&Root=&Recursive=True`
- ModelScope 单文件直链 / HEAD：
  `https://modelscope.cn/api/v1/models/{owner}/{repo}/repo?Revision=master&FilePath={path}`
- hf-mirror 模型信息与文件树：
  `https://hf-mirror.com/api/models/{repo}`（含 `cardData.license`、`gated`）
  `https://hf-mirror.com/api/models/{repo}/tree/main?recursive=true`（含每个文件 `size`）
- hf-mirror 搜索：`https://hf-mirror.com/api/models?search={kw}`
- **未下载任何 >200MB 权重**；体积来自上述文件列表 + `HEAD` 的 `Content-Length` 交叉验证（例：`llm.pt` 在 MS 与 hf-mirror 都返回 `200 / 2024669519`）。

### 0.3 4GB 判断口径

- 「权重 fp32 文件 ÷ 2」得到 fp16 常驻估算，再叠加 0.4~1.2GB 激活/KV/中间张量。
- 仅当官方 README 给出显存数字时才写「官方值」，否则明确标注「估算」。
- ONNX 前端（campplus / speech_tokenizer）可用 CPU EP，不计入 GPU 常驻。

---

## 1. 结论与推荐（质量 × 4GB 可行性 × 国内可获取性）

| 排名 | 模型 | repo id | 一句话理由 |
| --- | --- | --- | --- |
| 🥇 1 | Fun-CosyVoice3-0.5B-2512 | `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` | 0.5B 官方权重直挂 ModelScope、Apache-2.0、CV3-eval 第一梯队（CER 1.21/SIM 78.0），fp16 常驻 ~2.1–2.6GB，4GB 卡能塞下；代价是必须拿到官方代码仓库（GitHub 不通）。 |
| 🥈 2 | VoxCPM1.5 | `openbmb/VoxCPM1.5` | 唯一「一个 pip 包 `voxcpm` 即可用、完全不需要 GitHub」的高质量中文克隆模型（包必须装进引擎 venv，见文首警告），1.86GB 权重、Apache-2.0、同族 0.5B 在官方评测表里 CER 0.93/SIM 77.2，4GB 稳过。 |
| 🥉 3 | CosyVoice2-0.5B | `iic/CosyVoice2-0.5B` | 与 3.0 同代码基、生态最成熟（vLLM/TensorRT-LLM 支持）、fp16 ~2.4GB、24kHz，作为 3.0 的稳定回退与 A/B 对照最省事。 |

**质量优先但 4GB 需让步的**：`IndexTeam/IndexTTS-2`（CER 1.03，情感解耦最好，但 fp16 权重 ~2.8GB + 外部 w2v-BERT/BigVGAN，官方后继版 2.5 明写「~6GB 显存」）→ 建议只在云端/大卡档位提供。
**不建议接入**：MaskGCT（Amphion 依赖链重 + 非商用）、MegaTTS3（WaveVAE 编码器未公开）、FireRedTTS3（19.8GB）、fish 系与 Spark-TTS（CC-BY-NC 非商用）。

---

## 2. 完整核查表

### 2.1 CosyVoice 3（Fun-CosyVoice3-0.5B-2512）

| 字段 | 内容 |
| --- | --- |
| 仓库 id | ModelScope `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` · https://modelscope.cn/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512 · hf-mirror 同 id（https://hf-mirror.com/FunAudioLLM/Fun-CosyVoice3-0.5B-2512） |
| 权重体积 | 仓库 9 个权重文件合计 **9291.8MB**。逐个：`llm.pt` 2024669519B(1930.9MB)、`llm.rl.pt` 2024682701B(1930.9MB)、`flow.pt` 1329116148B(1267.5MB)、`flow.decoder.estimator.fp32.onnx` 1326216933B(1264.8MB)、`CosyVoice-BlankEN/model.safetensors` 988097824B(942.3MB)、`speech_tokenizer_v3.onnx` 969451503B、`speech_tokenizer_v3.batch.onnx` 969451579B、`hift.pt` 83202622B、`campplus.onnx` 28303423B。**最小中文零样本集**（llm.pt+flow.pt+BlankEN+hift+campplus+一个 tokenizer onnx）≈ **5.0GB 磁盘** |
| 代码库 | 需要。README 明确 `git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git`，且依赖 `third_party/Matcha-TTS` 子模块与 `from cosyvoice.cli.cosyvoice import AutoModel` |
| 依赖 | conda python=3.10 + `pip install -r requirements.txt`（README 指向阿里云镜像）；需 sox（Linux 需 `libsox-dev`）；`third_party/Matcha-TTS` 子模块；可选 `ttsfrd`（**只有 cp310 linux wheel**，见 `iic/CosyVoice-ttsfrd` 目录里 `ttsfrd-0.4.2-cp310-cp310-linux_x86_64.whl`），不装则回退 `wetext`（PyPI 有 `wetext-0.1.8-py3-none-any.whl`，纯 python，无需 pynini） |
| 许可证 | hf-mirror `cardData.license = apache-2.0`；**ModelScope 仓库内没有 LICENSE 文件**（文件树 19 项已全列，无 LICENSE）→ 商用依据只能引用 HF 模型卡声明，建议保留出处截图 |
| 显存需求 | 官方 README 未给数字（**未核实**）。按体积估算 fp16 常驻：llm ≈1.01GB + flow ≈0.66GB + BlankEN ≈0.94GB + hift 0.08GB ≈ **2.1–2.6GB**（ONNX 走 CPU）；CPU 可跑（`cosyvoice.yaml` 未限制 device，官方 RTF 优势靠 GPU/流式） |
| 中文能力 | 中文零样本克隆 ✅；9 语言 + 18 种中文方言/口音（README Key Features 原文列出广东、闽南、四川、东北、陕西、山西、上海、天津、山东、宁夏、甘肃等）；instruct 控制语言/方言/情感/语速/音量；支持拼音与 CMU 音素发音修正（`inference_instruct2`、hotfix 示例）；**无预置音色**（仓库无 `spk2info.pt`，只有 SFT 版有） |
| 4GB 可行性 | ✅ 可行（fp16，ONNX 前端放 CPU）。风险点是 `flow.decoder.estimator.fp32.onnx` 与实际 `flow.pt` 二选一加载，若两条路径都驻留显存会接近 4GB 上限 |
| 采集证据 | 文件树 19 项 + 上面 6 个权重的字节数；`cosyvoice3.yaml`：`sample_rate: 24000`、`token_frame_rate: 25`、`llm_input_size: 896`、`speech_token_size: 6561`、`get_qwen_tokenizer`；README roadmap「2025/12 release Fun-CosyVoice3-0.5B-2512 base model, rl model and its training/inference script」 |

### 2.2 CosyVoice2-0.5B（Clone / Instruct 变体）

| 字段 | 内容 |
| --- | --- |
| 仓库 id | ModelScope `iic/CosyVoice2-0.5B` · https://modelscope.cn/models/iic/CosyVoice2-0.5B · hf-mirror `FunAudioLLM/CosyVoice2-0.5B`（MS 上 `FunAudioLLM/CosyVoice2-0.5B` **404**，hf 上 `iic/...` 也不通用，注意别写错） |
| 权重体积 | 9 个权重合计 **5057.0MB**：`llm.pt` 2023316821B(1929.6MB)、`flow.pt` 450575567B(429.7MB)、`flow.cache.pt` 450496991B、`CosyVoice-BlankEN/model.safetensors` 988097824B、`speech_tokenizer_v2.onnx` 496082973B、`speech_tokenizer_v2.batch.onnx` 496095794B、`flow.decoder.estimator.fp32.onnx` 286317026B、`hift.pt` 83390254B、`campplus.onnx` 28303423B。最小集 ≈ **3.9GB 磁盘** |
| 代码库 | 需要，同一套 CosyVoice 仓库（克隆 CosyVoice2-0.5B 时 entry 为 `cosyvoice.cli.cosyvoice.CosyVoice2`，见 README「25hz CosyVoice2-0.5B released」） |
| 依赖 | 同 2.1（Matcha-TTS 子模块 + sox + onnxruntime 系列） |
| 许可证 | hf-mirror `cardData.license = apache-2.0` |
| 显存需求 | 官方无数字（未核实）。估算 fp16：llm 0.97GB + flow 0.22GB + BlankEN 0.94GB + hift 0.08GB ≈ **2.2–2.7GB**；CPU 可跑（RTF 差） |
| 中文能力 | 中文零样本克隆 ✅；官方定位为多语言（中英日韩等）+ 细粒度控制/方言（instruct 与「25hz」版本）；无预置音色（无 `spk2info.pt`）；22.05→24kHz 输出（`cosyvoice2.yaml: sample_rate: 24000`） |
| 4GB 可行性 | ✅ 可行。比 3.0 少一个 1.27GB 的 flow，显存余量更宽 |
| 采集证据 | 文件树 21 项；`cosyvoice2.yaml` 关键行 `sample_rate: 24000`、`upsample_rates: [8, 5, 3]`；README 与 3.0 共用同一份文档（两份 README 前 60 行逐字相同） |

### 2.3 CosyVoice-300M / 300M-SFT / 300M-Instruct

| 字段 | 内容 |
| --- | --- |
| 仓库 id | `iic/CosyVoice-300M`、`iic/CosyVoice-300M-SFT`、`iic/CosyVoice-300M-Instruct`（三个都在 ModelScope；hf-mirror 侧为 `FunAudioLLM/CosyVoice-300M{,-SFT,-Instruct}`） |
| 权重体积 | 三者各 **2502.8MB**（同构）：`llm.pt` 1242994835B(1185.4MB)、`speech_tokenizer_v1.onnx` 522624269B(498.4MB)、`flow.pt` 419900943B(400.4MB)、`flow.decoder.estimator.fp32.onnx` 328628196B(313.4MB)、`hift.pt` 81896716B(78.1MB)、`campplus.onnx` 28303423B(27MB)；另有 ONNX 导出中间件 `llm.llm.fp16.zip` 809092599B / `llm.llm.fp32.zip` 1577090487B / `llm.text_encoder.*.zip` / `flow.encoder.*.zip`；**SFT 版多一个 `spk2info.pt` 7772B** |
| 代码库 | 需要，同 CosyVoice 仓库（`cosyvoice.cli.cosyvoice.CosyVoice`，classic 路径） |
| 依赖 | 同 2.1 |
| 许可证 | hf-mirror `cardData.license = apache-2.0`（三个都是） |
| 显存需求 | 官方无数字。估算 fp16 ≈ **0.9–1.3GB**（llm 0.62 + flow 0.21 + hift 0.08）→ 4GB 卡最宽裕的一档 |
| 中文能力 | 中文零样本克隆（base）✅；**SFT 版有预置音色**（`spk2info.pt` 存在，对应 README 里 `sft` 用法）；Instruct 版支持自然语言指令控制（情感/方言/语速）；`cosyvoice.yaml: sample_rate: 22050`（输出 22.05kHz，与 2.0/3.0 的 24kHz 不一致） |
| 4GB 可行性 | ✅ 可行（最省显存），但质量是 2024 年一代，作为「轻量回退」而不是主力 |
| 采集证据 | 上面文件树（19/18/18 项）；`cosyvoice.yaml` 关键行 `sample_rate: 22050`、`upsample_rates: [8, 8]` |

### 2.4 F5-TTS（含中文）

| 字段 | 内容 |
| --- | --- |
| 仓库 id | hf-mirror `SWivid/F5-TTS`（https://hf-mirror.com/SWivid/F5-TTS）；ModelScope 镜像 `AI-ModelScope/F5-TTS`（文件树与 hf 一致，含 `configuration.json`） |
| 权重体积 | 5 个 ckpt 合计 6430.5MB，**实际只需 1 个**：`F5TTS_v1_Base/model_1250000.safetensors` 1348435761B(1286.0MB) + `vocab.txt` 13800B（HEAD 实测 200/1348435761）；另有 `F5TTS_Base/model_1200000.{pt,safetensors}` 各 1286MB、`F5TTS_Base_bigvgan`、`F5TTS_v1_Base_no_zero_init` |
| 代码库 | 需要（`github.com/SWivid/F5-TTS`，README 原文）；但 PyPI 有 `f5-tts` 包（阿里云镜像 48 个文件，最新 `f5_tts-1.1.9.tar.gz`）→ 有可能绕过 GitHub，**未实测安装** |
| 依赖 | `f5-tts` 包 + torch/torchaudio + `vocos` 声码器（仓库内未附 requirements，**逐条未核实**）；不含 pynini/WeTextProcessing |
| 许可证 | hf-mirror `cardData.license = cc-by-nc-4.0` → **禁止商用** |
| 显存需求 | 官方无数字。fp16 ≈ 0.67GB + 激活，**~1.5GB 级别**；CPU 可跑（慢） |
| 中文能力 | 中文零样本克隆 ✅（中文走拼音 token：`F5TTS_v1_Base/vocab.txt` 里 `bei`/`bei1`…`beng4` 等带调拼音音节，**无任何 CJK 字符**，故中文靠拼音化）；**无预置音色、无情感/指令控制**；采样率本次未在仓库文件中核实 |
| 4GB 可行性 | ✅ 完全可行（最省显存），但能力面向窄：只有零样本克隆，没有情感/指令/预置音色 |
| 采集证据 | 文件树 11 项；`vocab.txt` 实测 13800B、CJK 计数 0、样例行 `bei bei1 bei2 bei3 bei4 ben1 ben2 ben3 ben4 beng beng1 beng2`；README 630B 全文（license 头 + ckpts 目录约定 + Github/Paper 链接） |

### 2.5 IndexTTS-2 / IndexTTS-2.5 / IndexTTS-1.5（Bilibili）

| 字段 | 内容 |
| --- | --- |
| 仓库 id | ModelScope `IndexTeam/IndexTTS-2`、`IndexTeam/IndexTTS-2.5`、`IndexTeam/IndexTTS-1.5`（均在 MS 可列目录）；hf-mirror 同名 |
| 权重体积 | **IndexTTS-2**：7 个权重合计 **5607.5MB**（`gpt.pth` 3323.2MB、`s2mel.pth` 1146.5MB、`qwen0.6bemo4-merge/model.safetensors` 1136.9MB、`bpe.model` 0.5MB、`feat1.pt` 0.1MB、`feat2.pt` 0.4MB、`wav2vec2bert_stats.pt` ~0）。**IndexTTS-2.5**：**5220.8MB**（`gpt.pth` 3108.6 + `qwen0.6bemo4-merge` 1136.9 + `codec.pth` 579.2 + `s2mel.pth` 395.7）。**IndexTTS-1.5**：3435.8MB，其中 `bigvgan_discriminator.pth` 1575MB 是训练用判别器，推理可不下 |
| 代码库 | 需要。2.5 README：`git clone https://github.com/index-tts/index-tts.git` + `uv sync --all-extras`；辅助模型 **w2v-BERT-2.0、MaskGCT semantic codec、CAMPPlus、BigVGAN 首次运行自动下到 `checkpoints/hf_cache/`**（2 的 `config.yaml` 里写死 `vocoder: nvidia/bigvgan_v2_22khz_80band_256x`）→ 需要 `HF_ENDPOINT=https://hf-mirror.com` |
| 依赖 | Python 3.10–3.11、`uv`、NVIDIA GPU；无 pynini/WeTextProcessing 需求（README 未提） |
| 许可证 | IndexTTS-2/2.5：**bilibili 模型使用许可协议**（`LICENSE.txt` 10554B / `LICENSE_ZH.txt` 7336B，hf `license: other`，`license_name: bilibili-model-license`）。中文原文 2.2：月活 >1 亿或上年营收 >1 亿人民币需单独申请商业许可 → **一般规模商用允许**，但属自定义协议而非 OSI 许可。IndexTTS-1.5 为 `apache-2.0` |
| 显存需求 | **官方值**：IndexTTS-2.5 README「requires … roughly **6 GB of VRAM** for inference」。IndexTTS-2 fp16 估算 ≈ 2.8GB 权重 + BigVGAN + 激活≈3.5GB+（官方无数字，未核实） |
| 中文能力 | 中文零样本克隆 ✅；情感控制（`emo_vector` 8 维：happy/angry/sad/afraid/disgusted/melancholic/surprised/calm），文本情感需 `qwen_emo_path`(qwen0.6bemo4)；拼音/CMU 音素/假名发音控制 `<行|XING2>`；2.5 增加语速 `duration_factor` 与日/西/阿语；**无预置音色**；输出 22.05kHz（`s2mel.preprocess_params.sr: 22050`） |
| 4GB 可行性 | ⚠️ 勉强（2.5 官方 6GB 明确超标；2 估算 3.5GB+ 且要额外下 BigVGAN/w2v-BERT，实际极易 OOM）。**不建议作为 4GB 默认档**，可作云端档 |
| 采集证据 | 两份文件树（23/24/11 项）；`config.yaml` 全文（`gpt.model_dim: 1280`、`layers: 24`、`vocoder.name: nvidia/bigvgan_v2_22khz_80band_256x`、`emo_num: [3,17,2,8,4,5,10,24]`、`version: 2.0`）；`LICENSE.txt` 首行 `bilibili Model Use License Agreement`、2.2 条款原文；2.5 README 的 6GB 与 arXiv:2601.03888 |

### 2.6 Fish Speech 1.5 / OpenAudio S1-mini

| 字段 | 内容 |
| --- | --- |
| 仓库 id | `fishaudio/fish-speech-1.5`（MS+jf 均可列）、`fishaudio/openaudio-s1-mini`（MS 可列；hf `gated=auto` 需点同意） |
| 权重体积 | fish-speech-1.5：**1396.6MB**（`model.pth` 1216.8MB + `firefly-gan-vq-fsq-8x1024-21hz-generator.pth` 179.8MB）。S1-mini：**3439.2MB**（`model.pth` 1654.7MB，MS HEAD 实测 1735122974B + `codec.pth` 1784.4MB） |
| 代码库 | 需要（README 指向 fish-speech 的 GitHub 仓库做推理） |
| 依赖 | 仓库内无 requirements（**逐条未核实**）；历史版本对 `torch`/`triton` 版本敏感，Windows 侧兼容性未核实 |
| 许可证 | 两者 hf `cardData.license = cc-by-nc-sa-4.0`，模型卡原文「permissively licensed under the CC-BY-NC-SA-4.0 license」，并带 `extra_gated_fields` 勾选「I agree to use this model for non-commercial use ONLY」→ **禁止商用** |
| 显存需求 | 官方无数字。fish 1.5 fp16 ≈ 0.7GB；S1-mini ≈ 1.7GB（双 AR，`n_layer 28`、10 codebooks）→ 4GB 能跑 |
| 中文能力 | 中文零样本克隆 ✅（fish1.5 中文 >300k 小时）；S1 支持 50+ 情感标记 `(angry)(sad)…`、语气标记 `(whispering)(shouting)`、特殊 `(laughing)(sighing)`；无预置音色；采样率仓库内未声明（未核实） |
| 4GB 可行性 | ✅ 硬件可行，但 **许可证直接否掉商用场景** → 作为「研究/自用」备选 |
| 采集证据 | 两份 README 全文（含 gated 字段与 license 段）；`model.pth` HEAD `200/1735122974`；`config.json`：fish1.5 `dim 1024 / n_layer 24 / num_codebooks 8`，S1-mini `dim 1024 / n_layer 28 / 10 codebooks / vocab 155776` |

### 2.7 MaskGCT（Amphion）

| 字段 | 内容 |
| --- | --- |
| 仓库 id | MS `amphion/MaskGCT` ✅（10 项文件树）、hf `amphion/MaskGCT` ✅ |
| 权重体积 | 6 个权重合计 **6276.1MB**：`t2s_model/model.safetensors` 2847.3MB（HEAD 实测 2985622968B）、`s2a_model/s2a_model_full` 1348.3MB、`s2a_model/s2a_model_1layer` 1260.2MB（与 full 二选一）、`acoustic_codec/model.safetensors` 162.3MB + `model_1.safetensors` 489.1MB、`semantic_codec/model.safetensors` 169MB → 最小推理集 ≈ **5.0GB 磁盘** |
| 代码库 | 需要，且是 Amphion 子模块（README 用法 `MaskGCT_Inference_Pipeline` 来自 Amphion 仓库）；Amphion 依赖重（多模型+训练框架） |
| 依赖 | 仓库内无 requirements（未核实）；README 输出示例 `sf.write(save_path, recovered_audio, 24000)` → 24kHz |
| 许可证 | `cc-by-nc-4.0` → **禁止商用** |
| 显存需求 | 官方无数字。fp16 估算 ≈ 2.5–3.2GB（t2s 1.4GB + s2a 0.67GB + codecs）+ 激活 |
| 中文能力 | 中文支持 ✅（README：Emilia 语料「we use English and Chinese data from Emilia, each with 50K hours」）；零样本克隆；无预置音色；无情感指令 |
| 4GB 可行性 | ⚠️/❌：显存勉强，但真正的问题是**多模块管线 + Amphion 代码栈**（GitHub 不通时基本装不起来） |
| 采集证据 | 两份文件树；README 关键句 `sf.write(save_path, recovered_audio, 24000)`、Emilia 100K 小时说明、license 头 `cc-by-nc-4.0` |

### 2.8 Spark-TTS-0.5B

| 字段 | 内容 |
| --- | --- |
| 仓库 id | MS `SparkAudio/Spark-TTS-0.5B` ✅（31 项）、hf `SparkAudio/Spark-TTS-0.5B` ✅ |
| 权重体积 | 3 个权重合计 **3740.1MB**：`LLM/model.safetensors` 1932.7MB、`wav2vec2-large-xlsr-53/pytorch_model.bin` 1210.9MB、`BiCodec/model.safetensors` 596.5MB |
| 代码库 | 需要（`github.com/SparkAudio/Spark-TTS`，README 原文 `python -m cli.inference`） |
| 依赖 | conda python=3.12 + `pip install -r requirements.txt`；含 transformers/ whisper 类依赖（具体条目未核实） |
| 许可证 | `CC-BY-NC-SA-4.0`（MS README 头 `license: CC-BY-NC-SA-4.0`），并有「⚠ License Update」段落：**由 Apache 2.0 改为 CC BY-NC-SA，只能非商用** |
| 显存需求 | 官方无数字。fp16：LLM 0.97 + BiCodec 0.3 + wav2vec2 可 CPU ≈ **1.3–1.8GB** |
| 中文能力 | 中英双语零样本克隆 + 跨语种 ✅；可调性别/音高/语速做「虚拟音色」（`Voice Creation`）；**无预置真人音色**；输出 **16kHz**（`config.yaml: sample_rate: 16000`）→ 质量天花板偏低 |
| 4GB 可行性 | ✅ 显存完全够，但 16kHz + 非商用许可 → 只适合实验 |
| 采集证据 | 文件树 31 项；`config.yaml` 全文（`sample_rate: 16000`、`segment_duration: 2.4`、`latent_hop_length: 320`）；README 的 License Update 段落原文 |

### 2.9 MegaTTS 3（ByteDance）

| 字段 | 内容 |
| --- | --- |
| 仓库 id | hf `ByteDance/MegaTTS3` ✅（含 `g2p/`、`wavvae/`、`duration_lm/`、`diffusion_transformer/`、`aligner_lm/` 五个子目录）；**ModelScope 上没有对应仓库**（`ByteDance/MegaTTS3`、`MegaTTS3/MegaTTS3`、`iic/MegaTTS3` 均 404） |
| 权重体积 | 5 个权重合计 **4049.1MB**：`diffusion_transformer/model_only_last.ckpt` 1751.3MB、`g2p/model.safetensors` 971.3MB、`wavvae/decoder.ckpt` 862.6MB、`duration_lm/model_only_last.ckpt` 255.5MB、`aligner_lm/model_only_last.ckpt` 208.3MB |
| 代码库 | 需要（`github.com/bytedance/MegaTTS3`）+ `PYTHONPATH` 手工设置 |
| 依赖 | `requirements.txt`；**Windows 必须 `conda install -c conda-forge pynini==2.1.5` 再 `pip install WeTextProcessing==1.0.3`**（README 明写），Linux 用 `WeTextProcessing==1.0.4.1` |
| 许可证 | `apache-2.0`（README「licensed under the Apache-2.0 License」+ hf cardData） |
| 显存需求 | 官方无数字；README 只说「We also support cpu inference, but it may take about 30 seconds (for 10 inference steps)」。fp16 估算 ≈ 2.7GB 权重 + 激活 |
| 中文能力 | 中英零样本克隆、口音控制（`--p_w`/`--t_w`）、无预置音色、无情感向量 |
| 4GB 可行性 | ❌ **实质不可用**：README 明写「For security issues, we do not upload the parameters of WaveVAE encoder … You can only use the pre-extracted latents from link1(Google Drive)」→ 任意说话人都要先拿 `.npy` latent，而 latent 目录在 **Google Drive**（国内不通），新音色还得排队申请 |
| 采集证据 | hf 文件树与 5 个权重字节数；README 的 Windows 安装段、WaveVAE 未公开段落、CPU 30 秒说明 |

### 2.10 其它 2025–2026 值得关注的开源中文 TTS（含额外候选）

| 模型 | 仓库 id（MS/hf 均查过） | 权重体积 | 许可证 | 显存 | 中文能力 | 4GB |
| --- | --- | --- | --- | --- | --- | --- |
| **VoxCPM1.5** | `openbmb/VoxCPM1.5` ✅MS ✅hf | 1859.2MB（`model.safetensors` 1529.2 + `audiovae.pth` 330） | apache-2.0 | 官方无数字；bf16 权重 ≈1.9GB → 估 2.5–3GB | 中英为主 + 拼音/CMUDict 音素；零样本克隆；16kHz（开 denoise 时）~44.1kHz | ✅ |
| **VoxCPM2** | `openbmb/VoxCPM2` ✅MS ✅hf | 4727.4MB（`model.safetensors` 4367.9 + `audiovae.pth` 359.5） | apache-2.0（模型卡：free for commercial use） | **官方「VRAM: ~8 GB」** | 2B、30 语言、48kHz、声音设计 + 可控克隆 + 9 种中文方言 | ❌ |
| **GLM-TTS** | MS `ZhipuAI/GLM-TTS` ✅（24 项）· hf `zai-org/GLM-TTS` | 8470.9MB（`llm` 两片共 5920.4 + `speech_tokenizer` 1552.7 + `flow` 859.6 + `hift` 79.5 + `vocos2d` 57.6） | **mit** | 官方无数字；fp16 权重 ≈3.0GB（llm）+0.43+0.78 → 估 4–5GB | 中英混合、零样本克隆、GRPO 强化情感、音素级控制、流式 | ⚠️ 偏紧 |
| **VibeVoice-1.5B** | `microsoft/VibeVoice-1.5B` ✅MS ✅hf | 5157.7MB（3 片 safetensors） | mit（但模型卡写「limited to research purpose」） | 官方无数字 | 仅中/英，长音频播客多说话人（90 分钟/4 人），**不是零样本克隆**定位 | ⚠️ |
| **IndexTTS-1.5** | `IndexTeam/IndexTTS-1.5` ✅MS ✅hf | 3435.8MB（推理只需 `gpt.pth` 1117 + `bigvgan_generator` 511.3 + `dvae` 232） | **apache-2.0** | 估算 fp16 ~1.5–2GB | 中英零样本克隆，情感弱于 2.x | ✅ |
| **FireRedTTS3** | `FireRedTeam/FireRedTTS3` ✅MS ✅hf | **19800.6MB**（base 8089.6 + instruct 8082.6 + redae 3600.3） | apache-2.0 | 8B 级 → 远超 4GB | 中文强，但体积/显存双超标 | ❌ |

---

## 3. 各候选接入要点（下载 → 依赖 → 适配器 → 音色来源）

### 3.1 CosyVoice 3（首选）
1. 权重：`modelscope download --model FunAudioLLM/Fun-CosyVoice3-0.5B-2512 --local_dir models/Fun-CosyVoice3-0.5B`；如需更好文本规范化再下 `iic/CosyVoice-ttsfrd`（**Windows 无法用 ttsfrd**，只有 cp310-linux wheel，回退 wetext）。
2. 依赖：拿到 CosyVoice 代码（GitHub 不通 → 需 gitee/gitcode/gh-proxy 之一能出镜像，**未核实**），`third_party/Matcha-TTS` 子模块必须齐全，python3.10 + sox。
3. 适配器：包一层 `AutoModel(model_dir=...)`，暴露 `inference_zero_shot(text, prompt_text, prompt_wav)`、`inference_instruct2(text, instruct, prompt_wav)`、`inference_cross_lingual`；流式用 `stream=True`。注意 instruct 文本必须以 `You are a helpful assistant.<|endofprompt|>` 结尾（README 示例）。
4. 默认音色：仓库**无预置音色**，参考音频只能自备（README 用 `asset/zero_shot_prompt.wav`）→ 项目需内置 1–2 条自有授权 wav + 转写文本。
5. 输出 24kHz 单声道；与现有 1.7B 引擎采样率不同 → 需在适配器里统一重采样或在响应里带上 `sample_rate`。

### 3.2 CosyVoice2-0.5B（3.0 的回退档）
1. 权重：`modelscope download --model iic/CosyVoice2-0.5B`。
2. 依赖：同 3.1（同一份代码，入口类 `CosyVoice2`）。
3. 适配器：与 3.1 同一套接口，可共用 `engine=cosyvoice, version=2|3` 参数切换。
4. 音色：同样零样本；`spk2info.pt` 不存在 → 没有 SFT 预置音色可用。
5. 24kHz。

### 3.3 CosyVoice-300M-SFT（预置音色兜底）
1. 权重：`iic/CosyVoice-300M-SFT`（2502.8MB）。
2. 适配器：`inference_sft(text, spk_id)` 走 `spk2info.pt`，**这是唯一自带音色的 CosyVoice 版本**。
3. 音色：直接读 `spk2info.pt` 的 key 列表，无需参考音频。
4. 22.05kHz（与 2.0/3.0 不同，必须重采样）。

### 3.4 VoxCPM1.5（零 GitHub 依赖路径）
1. 权重：`modelscope download --model openbmb/VoxCPM1.5`，或直接 `VoxCPM.from_pretrained("openbmb/VoxCPM1.5")`（走 MS/HF 缓存）。
2. 依赖：pip 包 `voxcpm`（阿里云镜像有 `voxcpm-2.0.3-py3-none-any.whl`，PyTorch ≥2.5、CUDA ≥12）——**只能装进该引擎的 venv，且必须带精确版本**：
   `"<models>\voxcpm1.5\.venv\Scripts\python.exe" -m pip install --no-cache-dir --no-deps -c "<models>\voxcpm1.5\.venv\constraints.txt" "voxcpm==2.0.3"`，
   其余依赖按锁文件装：`"<models>\voxcpm1.5\.venv\Scripts\python.exe" -m pip install --no-cache-dir -c "<models>\voxcpm1.5\.venv\constraints.txt" -r "backend\app\engine\adapters\locks\voxcpm1.5.txt"`；
   可选 `iic/speech_zipenhancer_ans_multiloss_16k_base` 做参考音降噪（会把采样率压到 16kHz）。
3. 适配器：`model.generate(text=..., reference_wav_path=..., prompt_wav_path=..., prompt_text=..., cfg_value=2.0, inference_timesteps=10)` + `generate_streaming`。
4. 音色：零样本克隆；无预置音色；中文音素走拼音，英文走 CMUDict。

### 3.5 F5-TTS
1. 权重：只下 `F5TTS_v1_Base/model_1250000.safetensors` + `vocab.txt`（1.35GB），放 `ckpts/F5TTS_v1_Base/`。
2. 依赖：pip 包 `f5-tts`（PyPI 已有，可绕开 GitHub；**未实测**）。⚠️ 同样只能装进引擎 venv，主环境直接装会牵连 torch。
3. 适配器：`ref_audio + ref_text → generate`，无 instruct；需自己做长文本切分。
4. 音色：零样本，参考音频 + 精确转写（转写照抄错会明显掉质量）。中文走拼音。

### 3.6 IndexTTS-2 / 2.5
1. 权重：`modelscope download --model IndexTeam/IndexTTS-2`（或 2.5）；另需首次运行自动拉取 `w2v-bert-2.0`、MaskGCT semantic codec、CAMPPlus、`nvidia/bigvgan_v2_22khz_80band_256x` → 必须设 `HF_ENDPOINT=https://hf-mirror.com`。
2. 依赖：Python 3.10–3.11 + `uv`；代码仍需 GitHub。
3. 适配器：`IndexTTS2(cfg_path="checkpoints/config.yaml", model_dir="checkpoints", use_bf16=True)`；接口 `infer(spk_audio_prompt, text, lang="ZH", output_path, emo_vector=[...], duration_factor=...)`。
4. 音色：零样本 + 8 维情感向量；文本情感需 `use_qwen_emo=True`（加载 `qwen0.6bemo4-merge`）。
5. 22.05kHz；显存按 6GB 档规划（云端）。

### 3.7 MaskGCT / MegaTTS3 / fish 系 / Spark-TTS
- MaskGCT：需要 Amphion 代码栈 + 5GB 权重 + 非商用许可 → 不建议排期。
- MegaTTS3：动工前必须解决 WaveVAE 编码器缺失（只能取 Google Drive 的 `.npy` latent）→ 对「用户上传参考音频即克隆」的产品形态**不可行**。
- fish-speech-1.5 / openaudio-s1-mini：可下（MS 直链 200），但 CC-BY-NC-SA-4.0 + 代码需 GitHub；仅作内部实验。
- Spark-TTS-0.5B：16kHz 输出 + 非商用，建议不进产品线。

---

## 4. 风险与坑（按严重度）

1. **GitHub 全域不可达（本次实测超时）**：除 VoxCPM（pip）与 F5-TTS（PyPI 有包，未验证）外，CosyVoice / IndexTTS / Spark / MaskGCT / fish / GLM-TTS / MegaTTS3 全部要 clone 官方代码。CosyVoice 还额外依赖 `third_party/Matcha-TTS` **git 子模块**——这是本项目接入 CosyVoice 的最大单点风险，必须先在本机验证 gitee/gitcode/gh-proxy 是否能取到仓库（本次只验证了域名可达）。
2. **pynini / WeTextProcessing 在 Windows 上的坑**：`pynini` 在 PyPI 镜像里 63 个文件**没有一个 Windows wheel**（只有 manylinux/macos + sdist），所以 MegaTTS3 README 明确要求 Windows 用 `conda install -c conda-forge pynini==2.1.5`。好消息：CosyVoice 不强制 pynini（不装 ttsfrd 时回退 `wetext`，该包有 `py3-none-any` 纯 python wheel）。
3. **许可证陷阱**：Spark-TTS 曾从 Apache-2.0 **改成** CC-BY-NC-SA-4.0（README 有「License Update」段）；fish 系与 MaskGCT 是 CC-BY-NC*；F5-TTS 是 cc-by-nc-4.0；IndexTTS 用的是 bilibili 自定义协议（>1 亿 MAU / >1 亿营收需另签）；CosyVoice3 的 apache-2.0 **只写在 HF 模型卡**，ModelScope 仓库里没有 LICENSE 文件。任何「商用」承诺都要带上出处。
4. **体积幻觉**：CosyVoice3 仓库 9.29GB 里有两份 llm（base + rl）和两份 tokenizer onnx，真正推理只需一半；IndexTTS-1.5 的 1575MB 判别器是训练用。下载脚本必须按文件白名单下，不要 `snapshot_download` 整仓。
5. **显存被低估的地方**：IndexTTS-2 的 BigVGAN / w2v-BERT 是**运行时另下**的，估算时要加进去；CosyVoice 若同时驻留 `flow.decoder.estimator.fp32.onnx` 与 `flow.pt` 会顶到 4GB 上限。
6. **采样率不一致**：CosyVoice3/CosyVoice2 = 24kHz、CosyVoice-300M = 22.05kHz、IndexTTS = 22.05kHz、Spark = 16kHz、VoxCPM2 = 48kHz、VoxCPM1.5 视 denoise 在 16k~44.1k 之间切换。前端/接口层不能假设固定采样率。
7. **参考音频来源与合规**：除 CosyVoice-300M-SFT 的 `spk2info.pt` 外，**所有候选都没有预置音色**，默认音色必须由项目自备授权录音 + 转写文本；IndexTTS-2.5 README 明确「不验证参考音频中被克隆者是否同意」。
8. **跨源数据不一致**：CosyVoice2 的 test-zh CER 在 CosyVoice 团队表里是 1.45、在 GLM-TTS 团队表里是 1.38；F5-TTS 的 SIM 在两表里是 74.1 与 76.0。任何「谁更强」的结论都要注明评测方。

---

## 5. 本次未能核实的内容

1. 所有模型的**实际推理显存占用**：仅 IndexTTS-2.5（~6GB）与 VoxCPM2（~8GB）有官方数字；其余都是按权重体积的 fp16 估算，未在 4GB 卡上实测。
2. **官方 requirements.txt 具体条目**：CosyVoice / Spark-TTS / MaskGCT / fish-speech / F5-TTS 的依赖清单都在 GitHub 仓库里，本次 GitHub 不可达，只能引用 README 里出现的片段（sox、ttsfrd、pynini、uv）。
3. **gitee / gitcode / gh-proxy 上是否存在这些代码仓库的可用镜像**：只验证了域名可达，未验证仓库存在性。
4. **`f5-tts` 与 `voxcpm` 两个 pip 包能否在 Windows + CUDA 12 的引擎 venv 里装成功**（切记不要在主环境试）：只看到了 PyPI 包存在（f5_tts 1.1.9、voxcpm 2.0.3），未实测安装。
5. **F5-TTS / fish-speech / VoxCPM1.5 的输出采样率**：仓库内没有可直接引用的配置声明（F5-TTS 无 config、fish 只有 `config.json` 的模型结构、VoxCPM 只在 README 表格里写 16kHz/44.1kHz），本次未做推理验证。
6. **CosyVoice3 的 `CosyVoice-BlankEN` 是否在推理时真正加载**：`cosyvoice3.yaml` 里 `qwen_pretrain_path: ''`，但目录里存在 988MB 的 `model.safetensors`；是否计入显存未核实（估算里按「计入」保守处理）。
7. **VoxCPM 评测表行与具体子版本的对应关系**：CosyVoice3 README 的 CV3 表只写 `VoxCPM 0.5B`，未标明是 VoxCPM-0.5B / 1.5 / 2，本报告按「0.5B 级」处理。
8. **IndexTTS-2（非 2.5）的官方显存数字**：模型卡里没有，只有后继版 2.5 的 ~6GB。
9. **MaskGCT / Spark-TTS / GLM-TTS 在 4GB 卡上的可行性**：无官方数字，也未找到可追溯的实测记录。
10. **CosyVoice3 的商用许可边界**：ModelScope 仓库无 LICENSE 文件，只能引用 HF 模型卡的 `apache-2.0` 声明；若要以法务口径使用，建议向官方确认 ttsfrd/wetext 与训练数据侧的额外限制。
