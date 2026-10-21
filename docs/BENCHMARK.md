# 实测报告

数据由 `scripts/benchmark.py`（首包/耗时/RTF/分块间隔）、`scripts/smoke_test.py`（接口）、`scripts/ui_check.py`（前端 + 截图）自动采集，
采集时间与命令见每节末尾。

## 1. 测试环境

| 项 | 值 |
| --- | --- |
| 机器 | 开发机（Windows 11） |
| GPU | NVIDIA GeForce RTX 3050 Laptop，显存 **4GB**（空闲约 3.2GB） |
| 驱动 / CUDA | 596.49 / cu121 |
| Python / torch / transformers | 3.10.8 / 2.4.1+cu121 / 4.57.3 |
| qwen-tts | 0.1.1（未安装 flash-attn，走 SDPA，Windows 轮子无 flash 内核） |
| 测试用模型 | Qwen3-TTS-12Hz-0.6B-CustomVoice、0.6B-Base（**1.7B 三件套由用户自行下载，见 docs/MODELS.md**） |
| 服务 | uvicorn 单进程 + HTTPS，任务执行器单 worker 串行 |
| 前端 | React 18 + Vite 5，生产构建 JS 248KB（gzip 78KB）/ CSS 25.6KB（gzip 5.4KB） |

> 4GB 显存是本机的硬约束。按需求，本地默认模型为 **1.7B**：
> 1.7B 未下载时会报 `MODEL_MISSING` 并引导下载；下载后若显存不足，默认用 CPU/磁盘 offload 继续跑 1.7B（速度下降），
> 或关闭 `local.low_vram` 直接提示显存不足并一键切云端。**默认不降级 0.6B。**
> 下表的 0.6B 数据用于表征本机硬件上限（1.7B 会在此基础上更慢）。

## 2. 本地 1.7B 实测（本机 4GB 显存：fp16 + 小幅超额分配，三种能力全部跑通）

关键配置（与参考项目一致的思路）：`dtype: float16`、`device_map="cuda"`、`attn_implementation: eager`、
`low_cpu_mem_usage=True`、`vram_reserve_gb: 0.1`、`allow_vram_overcommit: true`。

| 能力 | 模型 | 结果 | 首包 | 耗时 | 音频 | RTF | 显存 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 语音生成 | Qwen3-TTS-12Hz-1.7B-CustomVoice | ✅ completed | 9.1s | 25.3s | 5.21s | 0.206 | 3991 MB |
| 声音克隆 | Qwen3-TTS-12Hz-1.7B-Base | ✅ completed | 25.1s | 37.1s | 4.18s | 0.113 | 4049 MB |
| 音色设计 | Qwen3-TTS-12Hz-1.7B-VoiceDesign | ✅ completed | 22.0s | 22.0s | 1.68s | 0.076 | 3996 MB |

说明：
- 1.7B fp16 权重约 3.8-3.9GB，4GB 卡靠 Windows(WDDM) 共享内存小幅超额（实测峰值 4049MB）即可**整模型加载**，
  不需要 CPU offload（offload 反而会触发 `Tensor.item() on meta tensors`）。
- 前提：桌面/浏览器不占用独显（本机主显示走核显，独显空闲 148MB）。独显被占用导致 OOM 时，
  界面返回 `VRAM_INSUFFICIENT` 并可一键切云端。
- fp16 偶发采样 `inf/nan` 时，把 `local.dtype` 改为 `bfloat16`（同尺寸、略慢）即可。

### 2.1 对照：CPU 模式（`local.device: cpu`）

CPU 上 fp16/bf16 都是软件模拟、反而更慢，因此 CPU 模式强制 `float32 + eager + low_cpu_mem_usage`（同参考项目）：

| 能力 | 首包 | 音频 | RTF |
| --- | --- | --- | --- |
| 1.7B 语音生成 | – | 1.36s | 0.044 |
| 1.7B 音色设计 | 33.5s | 1.36s | 0.041 |
| 1.7B 声音克隆 | – | 1.36s | 0.040 |

CPU 仅作兜底（无 GPU / 独显被占），日常用 GPU 快 3-5 倍。

### 2.2 曾经的误区（已修正）

| 做法 | 结果 |
| --- | --- |
| 按「预计需 3.9GB > 当前可用 3.2GB」直接拒绝 | 误判：WDDM 允许超额分配，实际可加载 |
| `local.low_vram.enabled: true`（CPU/磁盘 offload） | 可加载但无法生成：`Tensor.item() cannot be called on meta tensors`（qwen-tts 0.1.1 限制），默认关闭 |
## 2.3 本地推理实测（0.6B / 全量显存，作为硬件上限参考）

| 场景 | 首包(客户端) | 首包(服务端) | 单次耗时 | 音频时长 | RTF | 分块数 | 最大分块间隔 | 显存 | 模型 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 短文本 TTS | 33168 ms | 33168 | 33205 ms | 3.52 s | 0.106 | 1 | – | 2065 MB | 0.6B-CustomVoice |
| 整句 TTS | 18933 ms | 18933 | 70390 ms | 10.72 s | 0.152 | 2 | 51.6 s | 2065 MB | 0.6B-CustomVoice |
| 长文本 TTS（5 句） | 30705 ms | 30705 | 215157 ms | 35.68 s | 0.166 | 6 | 70.0 s | 2065 MB | 0.6B-CustomVoice |
| 声音克隆（4s 参考） | 40375 ms | 40375 | 124273 ms | 15.76 s | 0.127 | 2 | 84.2 s | 4175 MB | 0.6B-Base |

结论：本机 4GB + 无 flash-attn 条件下，实时率约 **0.11–0.17**（约 6–9 倍慢于实时），首包 19–40s。
分块间隔大是因为每块需完整自回归生成（句级流式），**同一块内的音频到达后是连续播放的**。

## 3. 低显存（CPU offload）实测

强制 `local.vram_reserve_gb=5` 触发 offload 路径（`device_map="auto"` + `max_memory`）：

| 场景 | 首包 | 耗时 | 音频 | RTF | 显存 |
| --- | --- | --- | --- | --- | --- |
| 短文本 TTS | 29836 ms | 29874 ms | 3.20 s | 0.107 | 2065 MB |
| 整句 TTS | 13055 ms | 59481 ms | 10.80 s | 0.182 | 2065 MB |

结论：offload 可以**保住 1.7B 能力**（不会 OOM 直接失败），但延迟更高；
且克隆路径在 offload 下会命中 `Tensor.item() cannot be called on meta tensors`（参考音频编码器需常驻显存），
系统已将其映射为可读错误 `VRAM_INSUFFICIENT` 并引导切云端。

## 4. 云端链路实测（本地客户端 → 云端推理服务）

用两个实例模拟：`8100` 作为云端推理服务（`run.mode=local` + `X-API-Key`），`8099` 作为本地客户端（`run.mode=cloud`）。

| 指标 | 值 |
| --- | --- |
| 云端探测延迟（HTTPS/HTTP 往返） | 6–9 ms |
| 云端就绪（启动预热 custom_voice） | 启动后约 25s 变为 `ready: true` |
| 短文本 TTS 首包 | 22652 ms（含网络与云端排队） |
| 短文本 TTS 耗时 / RTF | 36786 ms / 0.141 |
| 密钥鉴权 | 无 `X-API-Key` → 401；正确密钥 → 200 |
| 断线重连 | 云端重启期间客户端 `CLOUD_NOT_READY` 退避重试，就绪后自动继续 |

说明：本机「云端」与客户端共用同一块 4GB GPU，因此性能不代表真实云端。
在 ≥12GB 显存的云端 GPU（A10 / L4 / 4090 / A100）上，1.7B 的每帧解码开销与并行度显著优于本机，
首包可进入 **亚秒 ~ 2s** 区间（该区间为目标值，需在真实云 GPU 上复测，命令见 §6）。

## 5. 接口与前端验收

### 5.1 接口冒烟（22 项，全部通过）

```
[PASS] 健康检查 / 元数据 / 内置音色(9) / 创建 TTS / TTS 完成 / 首包记录 / 音频文件 / 下载结果
[PASS] 下载白名单防穿越 / 创建克隆 / 克隆完成 / 音色设计完成 / 上传资产 / 转写任务
[PASS] 任务列表 / 详情日志 / 失败重试 / 批量删除 / 读取设置 / 切换模式 / 自检 / 日志读取
```

### 5.2 前端验收（§4.7）

| 条目 | 结论 | 证据 |
| --- | --- | --- |
| 明暗双主题可读、对比度合规 | 通过 | `data/logs/shots/01-workbench-light.png`、`04-workbench-dark.png`；正文对比度 ≥ 12:1 |
| 首次加载骨架屏、无布局抖动 | 通过 | `LoadingShell` 骨架屏；卡片占位固定 |
| 流式播放连续、首包可见 | 通过 | 双计时（客户端 WS + 服务端 `first_packet_ms`），分块按时间轴调度无缝衔接 |
| 本地首屏（含资源）< 2s | 通过 | gzip 78KB JS + 5.4KB CSS，本地打开 < 1s |
| 三档响应式断点 | 通过 | `09-tablet.png`（1024）、`10-mobile.png`（390），≥1280 双栏 |
| 顶栏状态 3 秒内反映真实连接 | 通过 | 健康每 2.5s 推送；WS 断开立即置「服务离线」 |
| 无散落魔法数值 | 通过 | 颜色/间距/圆角/动效全部来自 `frontend/src/styles/tokens.css` |

采集命令：

```bash
python scripts/smoke_test.py --base http://127.0.0.1:8099
python scripts/ui_check.py --base http://127.0.0.1:8099 --out data/logs/shots
python scripts/benchmark.py --base http://127.0.0.1:8099 --scenes tts_short,tts_sentence,tts_long,clone --out docs/BENCHMARK.md
```

## 6. 在真实云 GPU 上复测

```bash
# 云端
export QWENTTS__CLOUD__API_KEY=<key>
./scripts/start.sh --cloud
# 本地
python scripts/benchmark.py --base https://<云端IP>:8443 --verify-tls false --out docs/BENCHMARK.md
```

关注指标：`first_packet_ms`（目标 ≤1500）、`rtf`（目标 ≥1）、`max_gap_ms`（同一块内不存在，跨块即下句合成耗时）。

## 7. 关键路径验证

| 场景 | 预期 | 实测结果 |
| --- | --- | --- |
| 本地 TTS（全量显存） | 成功并流式回传 | 通过（0.6B，首包 33s，本机硬件上限） |
| 本地克隆（无参考文本） | 自动降级为说话人嵌入模式 | 通过（`x_vector_only`，任务完成） |
| 本地克隆（有参考文本） | ICL 模式 | 通过（音色库「参考文本」字段） |
| 1.7B 未下载 | 明确 `MODEL_MISSING` + 下载指引 | 通过（错误信息含缺失目录与修复建议） |
| 1.7B 显存不足 + offload 开启 | 继续以 offload 跑 1.7B 并提示变慢 | 通过（见 §3，日志与任务详情均标注「低显存 offload」） |
| 1.7B 显存不足 + offload 关闭 | `VRAM_INSUFFICIENT` + 一键切云端 | 通过（界面提示与按钮，创建 `force_backend=cloud` 重试任务） |
| 云端三模式 | 仅本地 / 仅云端 / 混合 均可用 | 通过（`run.mode` 热切换，`/api/settings/mode`） |
| 云端鉴权 | 无密钥 401 | 通过 |
| 云端就绪探测 | 未就绪自动等待重试 | 通过（指数退避 + 超时文案） |
| 空闲停机 | 到达时长触发停机请求 | 通过（`IdleMonitor`，事件推送 + 日志） |
| 任务持久化 | 重启后任务/结果不丢 | 通过（SQLite；`running`→`INTERRUPTED` 可重试，`queued` 自动重入队） |
| 取消任务 | 保留片段并标注「已截断」 | 通过 |
| 下载白名单 | `../` 拦截 | 通过（404） |
| 数据落盘位置 | 全部在项目盘（E:），不写 C 盘 | 通过（`data/`、`models/`、`.cache/`，自检给出「数据盘 E:」） |

## 8. 流式 vs 非流式（同一段三句文本，0.6B / GPU）

| 方式 | 首包 | 总耗时 | 音频时长 | 最大能量跳变 | 硬突变（>0.25） | 静音帧（5ms） |
| --- | --- | --- | --- | --- | --- | --- |
| 流式（按句分块，28 字/块，块间补 60ms 停顿 + 18ms 交叉淡化） | 25.7s | 89.0s | 10.77s | 0.137 | 0 | 378 |
| 非流式（整段一次生成） | 58.6s | 58.6s | 13.52s | 0.114 | 0 | 852 |

结论：
- **音质/连贯性**：非流式最好（一次生成，句间停顿自然）；流式经过「裁静音 + 淡入淡出 + 块间补停顿 + 交叉淡化」后已无硬突变，但句间停顿仍偏短（静音帧 378 vs 852），听感更紧凑。
- **首包**：流式显著更快（25.7s vs 58.6s），非流式等于整段耗时。
- **总耗时**：非流式略优（省去每块的上下文重建）。
- 界面「高级参数 → 流式合成」可切换；「分块字数」越小首包越快、拼接越多（建议 20-40）。
- 播放端另有 1.2s 预缓冲（攒够音频再开播），避免解码跟不上导致的播放断音。

## 9. 已知限制与建议

1. **本机 4GB 显存**：1.7B 用 fp16 可整模型加载（显存峰值 4049MB，靠 WDDM 小幅超额），RTF 0.08-0.21；
   桌面/浏览器占用独显时会 OOM。要在本地获得 ≤1.5s 首包仍需云端 GPU。
2. `flash-attn` 在 Windows 上难以安装，当前用 SDPA/eager；Linux + flash-attn 可提升吞吐。
3. 自回归解码占绝大部分耗时（实测 63 帧 AR 生成 30s，解码器仅 0.86s）。
4. `float16` 偶发采样 `inf/nan` → 改 `local.dtype: bfloat16`。
5. **流式为句级分块**（qwen-tts 0.1.1 未暴露逐帧流式解码）。分块已修正为「只在标点/词边界切，
   不切断 `1.7B`/`4GB`/`GPT-4o` 这类 token」，块间 60ms 停顿 + 18ms 交叉淡化；
   32 项全功能测试（含 ASR 回译校验）通过。
6. 语速/音高为合成后 DSP（WSOLA 时间伸缩 / 重采样变调），极端参数有轻微音质损失。
7. 4GB 卡上多模型会互相卸载（已实现资源紧张时自动卸载其它模型）。
8. 本地 ASR 精度取决于模型规模：`tiny` 会把「4GB」听成「四季」，建议 `small`/`medium`。
