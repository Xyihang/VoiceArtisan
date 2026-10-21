# 配置参考

配置优先级（后者覆盖前者）：

```
代码默认值  <  config/config.yaml  <  data/runtime.json（界面保存的覆盖）  <  环境变量 QWENTTS__*
```

- 环境变量写法：嵌套层级用双下划线，键名小写，例如 `QWENTTS__RUN__MODE=cloud`、`QWENTTS__LOCAL__DTYPE=bfloat16`、`QWENTTS__LOCAL__WARMUP_KINDS=custom_voice,base`（逗号分隔即列表）。
- 界面里能改的项写在 `data/runtime.json`（不污染 `config.yaml`），点「恢复默认」可清空。
- 路径支持相对（相对项目根）与绝对路径；默认全部落在项目目录内，避免写入系统盘。
- 预设模板：`config/config.local-gpu.example.yaml`（单机 GPU）、`config/config.cloud-only.example.yaml`（仅云端）。

标 **热改** 的项可在界面保存后立即生效；其余需要重启。

---

## server — 服务

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `host` | `0.0.0.0` | 监听地址；只想本机访问可设 `127.0.0.1` |
| `port` | `8443` | HTTPS 端口 |
| `https` | `true` | 是否启用 HTTPS（首次自动生成自签证书到 `data/certs`） |
| `open_browser` | `true` | 启动后自动打开浏览器（服务端部署设 `false`） |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

## storage — 存储

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `data_dir` | `data` | 运行期数据：`db/app.db`、`uploads`、`outputs`、`voices`、`logs`、`certs`、`runtime.json` |
| `models_dir` | `models` | 模型目录（也用于 faster-whisper 的下载目录） |
| `cache_dir` | `.cache` | HF / ModelScope / Torch 缓存重定向目录 |

## run — 运行模式

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `mode` | `hybrid` | `local` 仅本地 / `cloud` 仅云端 / `hybrid` 混合（热改） |
| `hybrid_long_text_chars` | `300` | 混合模式下超过该字数的克隆任务优先云端（热改） |
| `asr_prefer` | `local` | 混合模式下 ASR 优先位置（热改） |

## local — 本地推理

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `device` | `cuda:0` | 推理设备；`cpu` 表示纯 CPU（自动用 float32，慢 3-5 倍）（热改） |
| `dtype` | `float16` | `float16`（省显存，4GB 跑 1.7B 用它）/ `bfloat16`（数值更稳）/ `float32`（热改） |
| `attn_implementation` | `eager` | `eager` / `sdpa`；部分环境 sdpa 反而更慢 |
| `keep_loaded` | `true` | 模型常驻；`false` 则空闲后卸载（热改） |
| `warmup_on_start` | `true` | 启动时后台预热（热改） |
| `warmup_kinds` | `[custom_voice]` | 启动预热哪些模型：`custom_voice` / `base` / `voice_design`（热改） |
| `unload_after_idle_seconds` | `600` | 空闲多久卸载模型（热改） |
| `vram_reserve_gb` | `0.1` | 加载前保留的空闲显存；4GB 卡请保持很小（热改） |
| `allow_vram_overcommit` | `true` | 允许小幅超额分配（Windows/WDDM 回落共享内存）；4GB 卡跑 1.7B 依赖它（热改） |
| `allow_small_fallback` | `false` | 是否允许回退 0.6B（默认禁止，界面会显式标注）（热改） |
| `max_new_tokens` | `2048` | 单块生成上限（实际按字数动态收紧） |
| `low_vram.enabled` | `false` | **实验特性**：CPU/磁盘 offload；qwen-tts 0.1.1 生成时会报 meta tensor 错误，建议保持关闭（热改） |
| `low_vram.gpu_reserve_gb` | `0.9` | offload 时留给 GPU 的余量 |
| `low_vram.cpu_memory_gb` | `32` | offload 允许使用的内存上限 |
| `low_vram.offload_dir` | `""` | 留空 = `<data_dir>/offload` |
| `models.base` | `models/…1.7B-Base` | 声音克隆模型目录 |
| `models.custom_voice` | `models/…1.7B-CustomVoice` | 内置音色 + 指令控制模型目录 |
| `models.voice_design` | `models/…1.7B-VoiceDesign` | 音色设计模型目录 |
| `fallback_models.*` | `models/…0.6B-*` | 仅当 `allow_small_fallback: true` 时生效 |

## cloud — 云端连接

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `base_url` | 占位地址 | 云端服务地址，如 `https://1.2.3.4:8443`；留空或仍是 `your-cloud` 时调用会立即失败并提示（热改） |
| `api_key` | `""` | 云端强制鉴权，需与云端 `QWENTTS__CLOUD__API_KEY` 一致（热改） |
| `verify_tls` | `true` | 自签证书设 `false`；正式证书设 `true`（热改） |
| `request_timeout_s` | `30` | 普通请求超时（热改） |
| `chunk_timeout_s` | `180` | 单块合成超时（长句/慢卡可调大）（热改） |
| `retries` | `3` | 失败重试次数（热改） |
| `retry_backoff_s` | `2` | 指数退避基数（热改） |
| `readiness_timeout_s` | `900` | 等待云端模型加载完成的上限（热改） |
| `models.*` | `Qwen3-TTS-12Hz-1.7B-*` | 云端模型标识（仅用于展示/透传）（热改） |
| `tunnel.enabled` | `false` | 是否启用 SSH 端口转发（热改） |
| `tunnel.ssh_host/ssh_port/username` | — | SSH 连接信息（热改） |
| `tunnel.key_path` / `password` | — | 密钥登录填 `key_path`，密码登录填 `password`（热改） |
| `tunnel.local_port` | `18443` | 本地映射端口（客户端只访问这里）（热改） |
| `tunnel.remote_host/remote_port` | `127.0.0.1` / `8443` | 云端目标地址端口（热改） |
| `idle_shutdown_enabled` | `false` | 空闲自动停机（默认关闭，界面可开）（热改） |
| `idle_shutdown_minutes` | `15` | 空闲多久停机（0 表示不停）（热改） |
| `idle_shutdown_url` | `""` | 外部停机回调（如厂商 API）；留空调云端 `/v1/system/shutdown`（热改） |

## asr — 语音转文字

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `engine` | `local` | `local` / `cloud`（热改） |
| `local.model_size` | `small` | `tiny/base/small/medium/large-v3/large-v3-turbo/distil-large-v3`；`tiny` 快但会听错（如 4GB→四季）（热改） |
| `local.device` | `auto` | `auto` 自动选 GPU/CPU（热改） |
| `local.compute_type` | `int8` | `int8` / `float16` / `int8_float16` / `float32`（热改） |
| `local.beam_size` | `5` | 解码 beam（热改） |
| `cloud.base_url` | `""` | 留空复用 `cloud.base_url`（热改） |
| `cloud.api_key` / `cloud.model` | — | 云端 ASR 鉴权与模型名（热改） |
| `diarize.enabled` | `false` | 区分说话人总开关；工作台可单次覆盖（热改） |
| `diarize.max_speakers` | `4` | 聚类上限（2-8）；超过上限合并最相似的簇（热改） |
| `diarize.threshold` | `0.75` | 余弦距离阈值，越大越倾向于合成更少的人（热改） |
| `diarize.engine_id` | `asr-speaker-campplus` | 声纹引擎（CAM++，Apache-2.0） |

## limits — 上传与文本限制

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `max_upload_mb` | `50` | 单个上传文件大小上限 |
| `max_ref_audio_seconds` | `30` | 参考音频时长上限 |
| `max_asr_audio_seconds` | `1800` | 待转写音频时长上限 |
| `max_text_chars` | `5000` | 单次合成文本上限 |
| `allowed_audio_formats` | `[wav, mp3, flac, m4a, ogg, webm, aac]` | 上传格式白名单 |
| `allowed_output_formats` | `[wav, mp3]` | 输出格式白名单（mp3 需 ffmpeg） |

## defaults — 新建任务默认参数

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `speed` | `1.0` | 语速（0.5-2.0），合成后 DSP |
| `pitch` | `0` | 音高（半音 −12~12），合成后 DSP |
| `temperature` | `0.9` | 采样温度 |
| `top_p` / `top_k` | `0.8` / `50` | 采样参数 |
| `repetition_penalty` | `1.05` | 重复惩罚 |
| `steps` | `2048` | 生成 token 上限（界面显示为「采样步数」） |
| `output_format` | `wav` | 默认输出格式 |
| `language` | `Auto` | 默认语言 |
| `streaming` | `false` | **默认非流式**（整段生成，语气最连贯）；打开后按句分块、首包更快（热改） |
| `chunk_chars` | `28` | 流式分块字数（热改） |
| `target_duration_s` | `null` | 目标时长（秒）：null/0 不控制；优先级高于 `speed`，生成后保音高伸缩（0.5-2.0 倍）再截断/补静音（热改） |

## post — 后期处理链

生成完成后按顺序处理成品音频：低切 → 高架 → 压缩 → 峰值归一化。`enabled=false` 时零开销（原样返回）。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 总开关（热改） |
| `low_cut_hz` | `80.0` | 低切频率（Hz），去掉低频隆隆声；0 = 关闭 |
| `high_shelf_db` | `0.0` | 高架增益（dB），抬升/衰减 4kHz 以上；0 = 不动 |
| `compress` | `true` | 软压缩开关 |
| `threshold_db` | `-18.0` | 压缩阈值（dB） |
| `ratio` | `2.5` | 压缩比（>1 才压缩） |
| `attack_ms` / `release_ms` | `8.0` / `120.0` | 包络平滑窗口 |
| `normalize_peak_db` | `-1.0` | 峰值归一化目标（dBFS）；null = 不归一化（仍硬限幅到 ±1.0 防削波） |

实测峰值会写回任务 `params._post`（如 `post_peak_before/after`、`target_duration_s`、`stretch_factor`）。实现见 `backend/app/postprocess.py`（纯 numpy 向量化）。

## export — 导出档位

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `tier` | `original` | `original`＝模型原生采样率 + WAV PCM_16；`standard`＝44.1kHz / 16bit / 单声道 / MP3 192k；`editing`＝48kHz / 24bit / 单声道 / WAV（热改） |

单次任务可用 `params.export_tier` 覆盖。档位会覆盖任务的 `output_format`：`standard` 固定出 MP3、`editing` 固定出 24bit WAV、`original` 固定出 WAV。
成品真实规格（采样率/声道/位深/编码/体积）由 `GET /api/tasks/{id}/media-info` 读文件头返回，同时写进任务 `params._export`。

## qc — 内容质检

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 任务完成后用 ASR 回译比对原文，结果写入任务 `transcript`（热改） |
| `min_similarity` | `0.75` | 低于该相似度提示「质检未通过」（热改） |

## tts — 文本规范化

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `normalize` | `true` | 合成前把数字 / 单位 / 型号 / 日期转成中文读法（热改） |
| `normalize_lang` | `zh` | 目前只实现 `zh`；`en` 等其它语言原样返回（热改） |

规范化口径（实现见 `backend/app/textnorm.py`，**幂等**：对结果再跑一次不变）：

| 类型 | 输入 → 输出 |
| --- | --- |
| 数字 | `123` → `一百二十三`；`1005` → `一千零五`（支持到亿级） |
| 小数 | `1.7` → `一点七`；`3.14` → `三点一四` |
| 型号/单位 | `1.7B` → `一点七 B`；`4GB` → `四 G B`；`32GB` → `三十二 G B`；`12Hz` → `十二赫兹`；`MP3` → `M P 3`；`RTX 3050` → `R T X 三千零五十` |
| 百分比 | `85%` → `百分之八十五` |
| 日期 | `2026-10-18` → `二零二六年十月十八日`；`10/18` → `十月十八日` |
| 时间 | `9:30` → `九点三十分`；`20:51` → `二十点五十一分` |
| 金额/区间 | `¥99` → `九十九元`；`1-3` → `一到三` |
| 电话/长数字 | `400-800-1234` → `四零零八零零一二三四`（逐位读） |
| 英文缩写 | 全大写 2-6 字母按字母读：`API` → `A P I`、`HTTP` → `H T T P`（`OK` 保留原样） |
| 连字符型号 | `GPT-4o` → `G P T 负四 O`（连字符读「负」，尾部字母按字母名读） |

- 单次任务可用 `params.normalize: false` 覆盖；
- 「设置」页暂未暴露 `tts` 分区（属 `api/system.py` 的编辑范围）；请改 `config/config.yaml`、`data/runtime.json` 或环境变量 `QWENTTS__TTS__NORMALIZE=false`，**下一次任务即生效**（`tts.normalize` 已在 `RUNTIME_MUTABLE` 白名单内）；
- 规范化发生在分块（`textsplit.chunk_text`）之前，型号 token 不会被切碎；
- 质检（ASR 回译）比对的是**规范化后**的文本，避免数字读法差异误判；
- URL / 邮箱原样保留，不参与规范化。

## mock — 联调占位引擎

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 无模型/无 GPU 时跑通界面用；界面会标注「模拟引擎」（热改） |

## engine — 多引擎/模型

```yaml
engine:
  active: qwen3-tts-1.7b      # 当前使用的引擎
  allow_parallel: false       # 4GB 卡保持 false：一次只驻留一个引擎
```

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `active` | `qwen3-tts-1.7b` | 当前引擎 id（**热改**）：保存后立即卸载旧引擎、清理显存，再按新引擎推理 |
| `allow_parallel` | `false` | 是否允许同时驻留多个引擎（**热改**）；4GB 显存请保持 `false` |

内置引擎（`backend/app/engine/registry.py`）：

| id | 名称 | 能力 | 显存 | 安装体积 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `qwen3-tts-1.7b` | Qwen3-TTS 1.7B（默认） | tts / clone / design / instruct | 约 4GB | 约 12.6GB | 三件套；克隆与音色设计**仅**本引擎支持 |
| `cosyvoice3-0.5b` | Fun-CosyVoice3-0.5B（2512） | tts / clone / instruct | 约 3GB | 约 5.05GB（13 个文件，白名单非整仓） | 质量最好（CER 1.21 / SIM 78.0）；**需 clone 代码仓库**（gitee 镜像 → gitcode 子模块 → GitHub 兜底）；apache-2.0 |
| `cosyvoice2-0.5b` | CosyVoice2-0.5B | tts / clone / instruct | 约 3GB | 约 3.79GB（13 个文件） | 与 CV3 共用同一代码库，质量略低但更稳，作为回退档；apache-2.0 |
| `cosyvoice-300m-sft` | CosyVoice-300M-SFT | tts / clone | 约 1.5GB | 约 2.44GB（9 个文件） | **唯一带预置音色**（`spk2info.pt`），装上即可出声；22.05kHz；apache-2.0 |
| `voxcpm1.5` | VoxCPM1.5 | tts / clone | 约 3GB | 约 1.82GB（6 个文件） | 纯 PyPI 包（**零 GitHub 依赖**），44.1kHz，中英双语；无预置音色。依赖装进它自己的 venv，见下方「依赖隔离」 |
| `indextts2` | IndexTTS-2 | tts / clone | 约 5GB | 约 5.49GB（17 个文件） | 22.05kHz；**bilibili 自定义许可，商用需另行签约**；首次运行还会额外拉约 1GB 声码器（自动注入 `HF_ENDPOINT=https://hf-mirror.com`） |

- **依赖隔离**：除默认引擎 `qwen3-tts-1.7b`（`isolation=inprocess`，主环境就地推理），其余引擎
  `isolation=venv`——依赖与 torch 全部装进 `<models_dir>/<engine_id>/.venv`，合成走
  `<venv_python> runner.py` 子进程。**切勿在主环境直接 `pip install <模型包>`**：
  `voxcpm` 这类包声明 `torch>=2.5`，会把主环境 `torch 2.4.1+cu121` 换成 CPU 版（真实事故）。
  安全命令形如（版本一律来自 `backend\app\engine\adapters\locks\<engine_id>.txt`）：
  ```powershell
  # 1) 引擎主包（--no-deps，精确版本）
  "<models>\<engine_id>\.venv\Scripts\python.exe" -m pip install --no-cache-dir --no-deps -c "<models>\<engine_id>\.venv\constraints.txt" "voxcpm==2.0.3"
  # 2) 引擎依赖（按锁文件精确安装）
  "<models>\<engine_id>\.venv\Scripts\python.exe" -m pip install --no-cache-dir -c "<models>\<engine_id>\.venv\constraints.txt" -r "backend\app\engine\adapters\locks\<engine_id>.txt"
  ```
  装完会用 `pip list --format=json` 与锁文件逐包比对，不一致直接报 `VERSION_MISMATCH`。
  详见 [ENGINE_ISOLATION.md](ENGINE_ISOLATION.md) 与 [VERSION_POLICY.md](VERSION_POLICY.md)；
- 切换引擎：`POST /api/engines/{id}/select`，或界面「设置 → 引擎与模型」，等价于热改 `engine.active`；
- 若当前引擎不具备任务所需能力（例如 `voxcpm1.5` 下发起音色设计），任务直接失败并返回 `ENGINE_CAPABILITY_MISSING`；
- `allow_parallel` 为 `false` 时切换引擎会先 `unload()` 旧引擎并 `torch.cuda.empty_cache()`，避免两个引擎抢显存；
- 引擎的模型统一落在 `<storage.models_dir>/<engine_id>/`（Qwen 三件套沿用 `local.models` 既有路径）；
- 环境变量优先级最高：QWENTTS__ENGINE__ACTIVE=voxcpm1.5 会覆盖界面/运行期设置。
- 注册表共 13 个引擎（7 个 TTS + 5 个 ASR + 1 个声纹）全部已注册；未安装的会在卡片上显示体积与依赖，点「安装」才会真正下载。选型依据见 [ENGINE_RESEARCH.md](ENGINE_RESEARCH.md)。

---

## 环境变量速查

```bash
QWENTTS_CONFIG=/path/to/config.yaml          # 指定配置文件
QWENTTS__RUN__MODE=cloud
QWENTTS__SERVER__PORT=9443
QWENTTS__SERVER__OPEN_BROWSER=false
QWENTTS__CLOUD__API_KEY=sk-xxx
QWENTTS__CLOUD__BASE_URL=https://1.2.3.4:8443
QWENTTS__CLOUD__VERIFY_TLS=false
QWENTTS__LOCAL__DEVICE=cpu
QWENTTS__LOCAL__DTYPE=bfloat16
QWENTTS__LOCAL__WARMUP_KINDS=custom_voice,base
QWENTTS__ASR__LOCAL__MODEL_SIZE=medium
QWENTTS__TTS__NORMALIZE=false                  # 关闭文本规范化
QWENTTS__ENGINE__ACTIVE=voxcpm1.5             # 指定当前引擎（优先级最高）
HF_ENDPOINT=https://hf-mirror.com            # ASR 模型下载镜像（脚本已默认设置）
```
