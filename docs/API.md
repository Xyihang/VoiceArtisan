# 接口文档

- 基础地址：`https://localhost:8443`（本地默认，自签证书）；云端为 `https://<host>:8443`
- 全部接口返回 JSON（音频下载除外）；错误统一为：

```json
{ "code": "VRAM_INSUFFICIENT", "message": "本地显存不足，无法加载 1.7B 模型",
  "hint": "可一键切换到云端模式继续本次任务…", "detail": "…" }
```

- 前端实时能力走 WebSocket（`/ws/events`、`/ws/tasks/{id}`），不使用轮询。
- `/v1/*` 为「云端推理服务」接口，需 `X-API-Key`；本地与云端共用同一套定义。
- `/v1/audio/*`、`/v1/models` 为 OpenAI 兼容接口（见第 8 节），同一套鉴权。

---

## 1. 健康与系统

### GET /api/health
用于前端顶栏状态（本地/云端/离线 + 延迟）。

```json
{
  "status": "ok", "uptime_s": 128, "mode": "hybrid", "active_backend": "local",
  "local": { "backend": "local", "ready": true, "device": "NVIDIA GeForce RTX 3050",
             "vram_total_gb": 4.0, "vram_free_gb": 3.2, "vram_allocated_mb": 0,
             "loaded_models": { "custom_voice": { "path": "...", "degraded": false, "sample_rate": 24000 } } },
  "cloud": { "backend": "cloud", "reachable": true, "ready": true, "latency_ms": 42, "url": "https://…" },
  "tunnel": { "enabled": false, "running": false, "local_port": null, "error": null },
  "idle": { "enabled": true, "idle_seconds": 12, "remaining_seconds": 888 },
  "queue": { "active": null, "pending": 0 },
  "model": "models/Qwen3-TTS-12Hz-1.7B-CustomVoice"
}
```

### GET /api/system/status
健康信息 + 生效配置 + 统计（平均首包、平均 RTF、显存）。

ASR 相关的两处新增字段（CLOUD-DIARIZE-1）：

```json
{ "asr_candidates": ["local", "cloud"],
  "config": {
    "asr_engine": "local",
    "asr_candidates": ["local", "cloud"],
    "asr_diarize": { "enabled": false, "prefer": "local", "min_speakers": 1, "max_speakers": 4 }
  } }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `asr_candidates` | string[] | **实际会按这个顺序尝试**的 ASR 位置（`local` / `cloud`）。由 `asr.engine` 与 `run.asr_prefer` 推导，叠加 `asr.diarize.prefer` 的偏好；计算只读缓存、**不发网络请求**（云端能力由后台探测）。同时出现在顶层与 `config.asr_candidates` |
| `asr_diarize` | object | **说话人区分的权威可用性**（顶层，前端据此判断能否勾选）：`candidates`（勾选后的真实尝试顺序）/`local_available`+`local_reason`（本地 CAM++ 能否用）/`cloud_supported`（`true`/`false`/`null`=尚未探测）/`cloud_engine`/`prefer`/`min_speakers`/`max_speakers` |
| `config.asr_diarize` | object | 生效中的说话人配置：`enabled` / `prefer` / `min_speakers` / `max_speakers` |

> 顺序计算失败不会让状态页 500：`asr_candidates` 退化为 `[]` 并只写一条 warning 日志。

### GET /api/system/logs?limit=200&level=ERROR
最近日志（内存环形缓冲，最多 800 条）：`{ "items": [{ "ts": 1730000000, "level": "INFO", "logger": "app.worker", "message": "…" }] }`

### GET /api/system/selfcheck
启动自检项（端口/环境/模型/存储），每项含 `status`(ok|warn|error)、`message`、`fix`。

### POST /api/system/preload → `{ "result": { "custom_voice": "ready" }, "loaded": ["custom_voice"] }`
### POST /api/system/unload → `{ "ok": true, "loaded": [] }`
### POST /api/system/shutdown → 卸载模型后退出进程（供空闲停机回调）

---

## 2. 设置

### GET /api/settings
返回生效配置（密钥脱敏为 `***`）、`mutable`（允许运行期修改的键）、`config_file`。

### PUT /api/settings
只允许修改运行期可变项（`RUNTIME_MUTABLE`），写入 `data/runtime.json` 并热生效。

```json
{ "run": { "mode": "cloud" },
  "cloud": { "base_url": "https://1.2.3.4:8443", "api_key": "sk-xxx",
             "tunnel": { "enabled": true, "ssh_host": "1.2.3.4", "username": "ubuntu" } },
  "defaults": { "speed": 1.0, "pitch": 0 } }
```

### POST /api/settings/mode
```json
{ "mode": "hybrid" }   // local | cloud | hybrid
```
### POST /api/settings/reset → 清除运行期覆盖
### GET /api/meta → 语言、内置音色目录、限制、默认参数、任务类型

---

## 3. 音色与音频资产

### GET /api/voices?kind=all|custom&q=关键词
```json
{ "builtin": [{ "id": "builtin-vivian", "name": "Vivian · 温柔女声", "kind": "builtin",
                "speaker": "vivian", "gender": "female", "age": "young", "language": "chinese",
                "dialect": "", "accent": "标准普通话", "description": "…", "peaks": [0.1, 0.4],
                "duration_sec": null, "available": true }],
  "custom": [], "languages": [{ "id": "chinese", "label": "中文" }],
  "supported_speakers": ["vivian", "serena", "dylan"], "counts": { "builtin": 9, "custom": 0 } }
```

| 接口 | 说明 |
| --- | --- |
| `GET /api/voices/{id}` | 音色详情 |
| `GET /api/voices/{id}/audio` | 试听自定义音色参考音频（wav） |
| `POST /api/voices` | multipart：`file`,`name`,`ref_text`,`meta` → 新建自定义音色 |
| `PATCH /api/voices/{id}` | `{ "name": "新名称" }` |
| `DELETE /api/voices/{id}` | 删除（内置音色不可删） |

### POST /api/assets（multipart `file`）
上传参考音频 / 待转写音频，返回：

```json
{ "id": "5f2c…", "filename": "ref.wav", "duration_sec": 6.2, "sample_rate": 24000,
  "peaks": [0.1, 0.6, …], "size_bytes": 297600, "url": "/api/assets/5f2c…" }
```

| 接口 | 说明 |
| --- | --- |
| `GET /api/assets/{id}` | 读取上传音频 |
| `POST /api/assets/{id}/trim` | `{ "start": 0.5, "end": 5.5 }` → 新资产（选区裁剪） |

限制：格式白名单 `wav/mp3/flac/m4a/ogg/webm/aac`，大小 `limits.max_upload_mb`，参考音频时长 `limits.max_ref_audio_seconds`。

---

## 4. 语音能力

### POST /api/tasks（统一创建）
```json
{ "type": "tts",            // tts | clone | design | asr
  "text": "要合成的文本",
  "voice_id": "builtin-vivian",
  "asset_id": null,          // clone：参考音频资产；asr：待转写音频
  "instruct": "语气自然一点",
  "design": "",              // design 类型必填
  "language": "Auto",
  "params": { "speed": 1.0, "pitch": 0, "temperature": 0.9, "top_p": 0.8,
              "top_k": 50, "repetition_penalty": 1.05, "steps": 2048,
              "output_format": "wav" },
  "force_backend": null      // local | cloud：强制指定执行位置
}
```
返回任务对象（见 §5）。

### 便捷接口（表单）
| 接口 | 表单字段 | 说明 |
| --- | --- | --- |
| `POST /api/tts` | `text`,`voice_id`,`instruct`,`language`,`params`(JSON) | 文本转语音 |
| `POST /api/clone` | `file`,`text`,`ref_text`,`language`,`voice_name`,`params` | 声音克隆（自动建音色） |
| `POST /api/design` | `text`,`design`,`language`,`params` | 音色设计 |
| `POST /api/transcribe` | `file`,`language` | 音频转文字（结果在任务 `transcript`） |

---

## 5. 任务

### 任务对象
```json
{ "id": "6acbdf8ed384", "type": "tts", "status": "completed",
  "text": "…", "language": "chinese", "voice_id": "builtin-vivian", "voice_name": "Vivian · 温柔女声",
  "instruct": "", "design": "", "params": { "…": "…" },
  "backend": "local", "model": "Qwen3-TTS-12Hz-1.7B-CustomVoice", "degraded": false,
  "progress": 1.0, "first_packet_ms": 860, "elapsed_ms": 2400, "rtf": 2.1, "vram_mb": 3620,
  "duration_sec": 5.1, "sample_rate": 24000,
  "output_file": "6acbdf8ed384.wav", "output_format": "wav",
  "transcript": null, "error_code": null, "error_message": null,
  "retry_of": null, "canceled": false,
  "created_at": 1730000000.1, "started_at": 1730000000.2, "finished_at": 1730000002.5,
  "logs": [{ "ts": 1730000001.9, "message": "首包到达：860ms（local）" }] }
```

| 接口 | 说明 |
| --- | --- |
| `GET /api/tasks?status=&type=&page=1&page_size=20&order=desc&q=` | 列表（分页、筛选、搜索） |
| `GET /api/tasks/{id}` | 详情（含日志与转录文本）；有时间轴时另带只读字段 `segments` / `speaker_aliases` / `transcript_by_speaker`（见下） |
| `POST /api/tasks/{id}/cancel` | 取消（保留已生成片段，标注「已截断」） |
| `POST /api/tasks/{id}/retry` | `{ "force_backend": "cloud", "params": {…} }` → 新建重试任务；params 白名单覆盖（diarize/max_speakers/min_speakers/force_backend 及常用合成参数） |
| `PATCH /api/tasks/{id}/speakers` | `{ "mapping": {"SPK1": "张三"} }` → 说话人改名（空字符串还原），返回 ok/segments/speaker_aliases |
| `POST /api/tasks/{id}/speakers/merge` | `{ "from": ["SPK2"], "to": "SPK1" }` → 合并说话人（时间轴不变） |
| `POST /api/tasks/{id}/split-by-speaker` | 按说话人切分轨 wav，返回清单数组（不返回音频本体） |
| `GET /api/tasks/{id}/speakers/{name}` | 下载单条分轨（文件名白名单，仅 `.wav`；不存在报 `SPEAKER_SPLIT_NOT_FOUND`） |
| `GET /api/tasks/{id}/speakers/zip` | 打包下载上一步产出的分轨（`speakers-<id>.zip`） |
| `POST /api/tasks/{id}/export` | `{ "tier": "original\|standard\|editing" }` → 从无损原件 `source.wav` 重新导出 |
| `DELETE /api/tasks/{id}` | 删除任务与结果文件 |
| `POST /api/tasks/batch-delete` | `{ "ids": ["a","b"] }` |
| `POST /api/tasks/batch` | 批量提交，items 每项可带 text/voice_id/asset_id/instruct/design/language/params |
| `GET /api/tasks/batch/{id}` | 批次进度聚合（counts/progress/items） |
| `POST /api/tasks/batch/{id}/cancel` | 取消批次里所有未结束任务 |
| `POST /api/tasks/batch/{id}/retry-failed` | 把失败项复制成新批次重跑（保留逐项参数） |
| `GET /api/tasks/batch/{id}/zip` | 成功项打包 + manifest.json |
| `GET /api/tasks/{id}/download` | 按任务下载结果（wav/mp3/txt） |
| `GET/PUT /api/cloud/engines/host` | 云端主机档案（主机/端口/用户/项目目录/解释器）；密码 DPAPI 加密，永不回显 |
| `POST /api/cloud/engines/host/test` | 试连云端 SSH，返回指纹与云端 Python 版本 |
| `GET /api/cloud/engines` | **云端**引擎列表（远端 engines_cli 实时计算，含手工直传的模型）；`?kind=` 可筛 |
| `POST /api/cloud/engines/{id}/install` | 在云端安装引擎（202 + job_id，SSH 流式进度） |
| `GET /api/cloud/engines/jobs/{id}` | 云端安装任务详情（phase/progress/logs/error） |
| `DELETE /api/cloud/engines/{id}` | 删除云端模型，返回释放体积 |
| `POST /api/cloud/engines/{id}/select` | 切换云端当前引擎 |
| `GET /api/tasks/{id}/media-info` | 成品真实规格（读文件头）：sample_rate / channels / bits / codec / bytes / tier |
| `GET /api/files/{task_id}/{filename}` | 按文件名下载（白名单 + 防路径穿越） |

状态流转：`queued → running → completed | failed | canceled`；
服务重启时 `running` 任务标记为 `failed(INTERRUPTED)` 可一键重试，`queued` 任务自动重新入队。

---

### GET /api/tasks/{id}/subtitles?format=srt|vtt|ass&speakers=true|false

按句导出字幕与时间轴（时间码精度到毫秒）。需要该任务生成时记录了时间轴（`data/outputs/<id>/chunks.json`）。

- `format`：`srt`（默认）/ `vtt` / `ass`；其它值 → 400 `INVALID_INPUT`
- `speakers`：`true`（默认，带说话人前缀 [SPKn] 或改名后的姓名）/ `false`（纯文本字幕）
- 响应：对应 `Content-Type` + `Content-Disposition: attachment; filename="<id>.<ext>"`
- 任务不存在 → 404 `NOT_FOUND`；任务无时间轴（旧任务）→ 404 `SUBTITLE_NOT_FOUND`

```bash
curl -k https://localhost:8443/api/tasks/<id>/subtitles?format=srt -o out.srt
```

```srt
1
00:00:00,000 --> 00:00:06,175
这是字幕接口测试。模型需要 四 G B 显存，售价 九十九元，

2
00:00:06,235 --> 00:00:09,024
2026-10-18 上线。
```

> 时间轴按「各块时长累加 + 60ms 句间间隔」计算，并按成品音频总时长等比缩放、末句末尾对齐成品时长
> （拼接用了交叉淡化，成品会比累计略短）。

---

### 说话人与导出（B6/B7/B8）

所有说话人操作都读写同一份时间轴 `data/outputs/<id>/chunks.json`（`chunks[]` + `speaker_aliases`），
**不改任务表里的 `transcript`**；因此字幕、`media-info` 与派生文本始终一致。

#### GET /api/tasks/{id} — 只读派生字段

```json
{ "id": "6acbdf8ed384", "type": "asr", "status": "completed",
  "transcript": "原始转写文本（库里的原件，永不改写）",
  "segments": [ { "start_s": 0.0, "end_s": 2.4, "text": "第一句", "speaker": "张三", "speaker_id": "SPK1" } ],
  "speaker_aliases": { "SPK1": "张三" },
  "transcript_by_speaker": "[张三] 第一句\n[SPK2] 第二句",
  "params": { "diarize": true, "max_speakers": 4, "min_speakers": 1,
              "diarize_backend": "local", "min_speakers_effective": 2 } }
```

| 字段 | 说明 |
| --- | --- |
| `segments` / `speaker_aliases` | 有 `chunks.json` 时才出现；旧任务（无时间轴）不返回这三个键 |
| `transcript_by_speaker` | 只读派生视图：每行 `[别名] 文本`（无说话人的分段只输出文本），由 `speaker` 优先、回落到 `speaker_id` |
| `params.diarize_backend` | 说话人由谁识别：`local`（本地 CAM++）/ `cloud`（云端声纹）/ `null`（本次未做区分） |
| `params.min_speakers_effective` | 本次实际生效的说话人数下限（引擎返回值存在时才写） |

#### POST /api/tasks/{id}/export — 按档位重新导出

```json
{ "tier": "standard" }        // original | standard | editing
```

```json
{ "ok": true, "task_id": "6acbdf8ed384", "tier": "standard",
  "tier_label": "Standard（44.1k MP3 192k）",
  "file": "6acbdf8ed384.mp3",
  "media": { "file": "6acbdf8ed384.mp3", "bytes": 88123, "sample_rate": 44100, "channels": 1,
             "duration_sec": 5.1, "tier": "standard", "tier_label": "…",
             "source_file": "source.wav", "source_sample_rate": 24000 },
  "task": { "id": "6acbdf8ed384", "output_file": "6acbdf8ed384.mp3", "…": "…" } }
```

- 数据来源是无损原件 `outputs/<id>/source.wav`（**永不覆盖**），所以原文件不会被反复转码劣化；
- 新成品落盘后才删除旧成品（不含 `source.wav`）；删除失败只记 warning，旧成品仍可下载；
- `params._export` 写入本次规格；任务的 `output_file` / `output_format` / `sample_rate` / `duration_sec` 同步更新。

| 错误码 | HTTP | 场景 |
| --- | --- | --- |
| `INVALID_INPUT` | 400 | `tier` 不是 `original` / `standard` / `editing`（不静默改成默认档） |
| `EXPORT_SOURCE_MISSING` | 404 | 该任务没有 `source.wav`（升级前的旧任务）或原件为空 → 只能重新生成任务 |
| `INTERNAL` | 500 | 编码失败（如缺 ffmpeg），detail 带可读原因 |

#### POST /api/tasks/{id}/speakers/merge — 合并说话人

```json
{ "from": ["SPK2", "SPK3"], "to": "SPK1" }
```

```json
{ "ok": true, "merged": { "from": ["SPK2", "SPK3"], "to": "SPK1" },
  "segments": [ { "start_s": 0.0, "end_s": 2.4, "text": "…", "speaker": "SPK1", "speaker_id": "SPK1" } ],
  "speaker_aliases": { "SPK1": "张三" } }
```

- 只改 `chunks.json` 的 `speaker_id` / `speaker` 与 `speaker_aliases`；`start_s` / `end_s` 原样保留，字幕与拆分结果随之变化；
- `from` 是 Python 关键字，请求体字段名为 `sources` + 别名 `from`，**两种写法都接受**；
- 目标保留自己的别名（没有别名就是 id 本身）；来源里的重复项会自动去重，与 `to` 相同的项被忽略；
- 合并完成后推一条 `task.update` 事件。

| 错误码 | HTTP | 场景 |
| --- | --- | --- |
| `INVALID_INPUT` | 400 | `to` 为空 / `from` 为空或只有 `to` / `from` 里含时间轴中不存在的说话人（detail 列出已有说话人） |
| `SPEAKER_TIMELINE_MISSING` | 400 | 该任务的分段**没有任何说话人信息**（旧任务或未开区分说话人） |
| `SUBTITLE_NOT_FOUND` | 404 | 没有 `chunks.json`（旧任务），需重新转写 |

#### POST /api/tasks/{id}/split-by-speaker — 按说话人拆分音频

```bash
curl -k -X POST https://localhost:8443/api/tasks/6acbdf8ed384/split-by-speaker
```

```json
[ { "speaker": "张三", "speaker_id": "SPK1", "file": "张三.wav",
    "duration_s": 8.42, "bytes": 404160, "segments": 5 },
  { "speaker": "SPK2", "speaker_id": "SPK2", "file": "SPK2.wav",
    "duration_s": 3.10, "bytes": 148800, "segments": 2 } ]
```

- 返回**数组**（不是对象），顺序按各说话人首次出现时间；音频落在 `outputs/<id>/speakers/<别名>.wav`；
- 文件名用别名并做安全化（Windows 保留字符/设备名处理，重名自动加序号）；
- **不做重叠检测**：两人同时说话的重叠区会被两个文件各含一次；
- 每次调用会先清掉上一次的分轨 wav，避免旧文件混进清单或压缩包；
- 拆完再用 `GET /api/tasks/{id}/speakers/zip` 打包下载。

| 错误码 | HTTP | 场景 |
| --- | --- | --- |
| `SPEAKER_TIMELINE_MISSING` | 400 | 没有分段，或分段里没有说话人 / 缺起止时间 |
| `EXPORT_SOURCE_MISSING` | 404 | 既没有 `source.wav`、成品也不是 wav/mp3/flac（旧任务）→ 重新生成 |
| `SUBTITLE_NOT_FOUND` | 404 | 没有 `chunks.json` |
| `INTERNAL` | 500 | 切分/写盘失败，detail 带原因 |

#### GET /api/tasks/{id}/speakers/zip — 打包下载分轨

- 响应：`application/zip` + `Content-Disposition: attachment; filename="speakers-<id>.zip"`，
  响应头 `X-Speaker-Files` 给出打包的文件数；zip 内是 `speakers/*.wav` 的扁平文件名。
- 必须先调用 `split-by-speaker`；目录里没有 wav → 404 `SPEAKER_SPLIT_NOT_FOUND`。

## 6. 实时推送（WebSocket）

### WS /ws/events
订阅全局事件：`system.health`（每 2.5s）、`system.settings`、`system.idle_shutdown`、`task.queued`、`task.update`、`task.deleted`。

### WS /ws/tasks/{task_id}
```json
{ "topic": "task.6acbdf8ed384", "event": "task.chunk", "ts": 1730000001.9,
  "data": { "task_id": "6acbdf8ed384", "index": 0, "is_last": false, "sample_rate": 24000,
            "duration": 1.8, "backend": "local", "model": "…", "wav_b64": "<base64 wav>" } }
```
事件序列：`task.snapshot` → (`task.first_packet`、`task.chunk`\*n、`task.notice`) → `task.final`。
前端把每段 wav 解码进 Web Audio 队列，首包到达即播放，波形随分块增长。

---

## 7. 云端推理接口（/v1，需 X-API-Key）

| 接口 | 说明 |
| --- | --- |
| `GET /v1/health` | 就绪探测（无需鉴权）：`{ "ready": true, "loaded": ["custom_voice"], "models_present": ["custom_voice","base"], "loading": "", "vram_free_gb": 8.1, "asr": {…} }`（新增 `asr` 块，见下） |
| `POST /v1/engine/assets` | multipart `file` → 参考音频资产 id |
| `POST /v1/engine/synth` | 合成一段文本，返回 `audio/wav` 字节（响应头 `X-Sample-Rate`、`X-Model`、`X-Elapsed-Ms`） |
| `POST /v1/engine/asr` | multipart `file`,`language`,`model`,`diarize`,`max_speakers`,`min_speakers` → 转写结果（含 `diarize_backend`，见下） |
| `POST /v1/system/shutdown` | 卸载模型并停机（空闲停机回调） |

`POST /v1/engine/synth` 请求体：

```json
{ "kind": "clone", "text": "一段文本", "language": "chinese",
  "speaker": null, "instruct": "", "design": "",
  "ref_asset_id": "5f2c…", "ref_text": "参考文本",
  "params": { "speed": 1.0, "pitch": 0, "temperature": 0.9, "output_format": "wav" },
  "model": "Qwen3-TTS-12Hz-1.7B-Base" }
```

```bash
curl -fk https://localhost:8443/v1/engine/synth \
  -H "X-API-Key: $QWENTTS_API_KEY" -H "Content-Type: application/json" \
  -d '{"kind":"tts","text":"你好","speaker":"vivian"}' -o out.wav
```

### ASR 能力矩阵（本地 / 云端）

本项目的转写能力由「本机引擎」与「云端 `/v1/engine/asr`」两部分组成，同一套定义、可互相回退：

| 位置 | 文本 | 时间戳（分段） | 说话人区分 | 最少人数 `min_speakers` | 语种 |
| --- | --- | --- | --- | --- | --- |
| 本地 faster-whisper（`asr.local`） | ✅ | ✅ 句级 `start`/`end` | ✅ 需装声纹引擎 CAM++（`asr.diarize.engine_id`，默认 `asr-speaker-campplus`） | ✅ 1-8（默认 1） | ✅ 多语种 + `auto` |
| 云端 `/v1/engine/asr`（`asr.engine=cloud`） | ✅ | ✅ 云端 `segments` 原样透传 | ✅ **需云端安装 CAM++**（`engines_cli install asr-speaker-campplus`） | ✅ 本地按本次 `min_speakers` 原样下发；云端 `asr.min_speakers=true` 表示它认这个字段（本实现恒为 `true`） | ✅ 由云端 ASR 引擎决定 |
| mock 联调（`mock.enabled`） | ✅ 占位文本 | ✅ 单条占位分段 | ✅ 标签固定 `SPK1`（仅联调用） | — | — |

- **能力协商**：客户端（本地服务）每次 `/v1/health` 读云端 `asr` 块，`asr.diarize=true` 才把说话人区分交给云端；
  字段缺失（旧版云端）一律按**不支持**处理，绝静默降级成纯文本。
- 云端已声明支持但实际没返回 `diarize_backend` → 视为未执行区分，走下一个候选并最终报 `CLOUD_DIARIZE_UNSUPPORTED`。
- 偏好由 `asr.diarize.prefer` 决定（`local` 默认 / `cloud` / `auto`），实际尝试顺序见 `GET /api/system/status` 的 `asr_candidates`。

### GET /v1/health — `asr` 能力块

```json
{ "ready": true, "loaded": ["custom_voice"], "vram_free_gb": 8.1,
  "asr": { "backend": "local",
           "engine_id": "asr-whisper-small",
           "diarize": true,
           "diarize_engine": "asr-speaker-campplus",
           "min_speakers": true } }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `backend` | string | 固定 `local`——云端自己在本机推理 |
| `engine_id` | string | 真实 ASR 引擎 id（`asr.local.engine_id`，为空时回落 `asr-whisper-<model_size>`） |
| `diarize` | bool | 云端本机是否**真的**能做说话人区分（唯一判据：CAM++ 是否可用） |
| `diarize_engine` | string \| null | 声纹引擎 id；`diarize=false` 时为 `null` |
| `min_speakers` | bool | 是否支持最少说话人约束（本实现恒为 `true`） |

### POST /v1/engine/asr（云端转写）

multipart 表单字段（`diarize` / `max_speakers` / `min_speakers` 三个都是**可选**，不传时行为与改造前完全一致）：

| 字段 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- |
| `file` | ✅ | 音频文件 | 受 `limits.max_asr_audio_seconds` 限制 |
| `language` | — | `auto`（默认）/ `zh` / `en` … | 交给 ASR 引擎 |
| `model` | — | `""` | 旧字段，仅为兼容老客户端保留；**实际引擎 id 由服务端按安装情况回填** |
| `diarize` | — | `"true"` / `"false"` / `""` | 空串 = 跟随云端全局配置 `asr.diarize.enabled`；真值还接受 `1/on/yes/y`，假值 `0/off/no/n`（大小写不敏感） |
| `max_speakers` | — | 整数 | 人数上限；超出 2-8 会被**夹取**（保留老客户端宽容行为） |
| `min_speakers` | — | 整数 | 人数下限，**1-8**，越界或 `min > max` 直接 `INVALID_INPUT`（不静默夹取） |

```bash
curl -fk https://<云端>:8443/v1/engine/asr \
  -H "X-API-Key: $QWENTTS_API_KEY" \
  -F file=@meeting.wav -F language=zh \
  -F diarize=true -F min_speakers=2 -F max_speakers=4
```

```json
{ "text": "第一句。第二句。",
  "segments": [ { "start": 0.0, "end": 2.4, "text": "第一句。", "speaker": "SPK1" },
                { "start": 2.4, "end": 4.1, "text": "第二句。", "speaker": "SPK2" } ],
  "language": "zh", "language_probability": 0.99, "duration": 4.1,
  "backend": "local",
  "model": "asr-whisper-small",
  "diarize_backend": "local",
  "elapsed_ms": 1830 }
```

- `model`：改为返回**真实 ASR 引擎 id**（不再是无意义的 `whisper-1`）；
- `diarize_backend`：`"local"` = 说话人由云端本机（CAM++）识别；`null` = 本次没做区分。
  对上游调用方而言，「云端本机」就是 `local`；
- 未开区分说话人时 `segments[].speaker` 为 `null`（或整个 `segments` 不含该键）。

| 错误码 | HTTP | 场景 |
| --- | --- | --- |
| `INVALID_INPUT` | 400 | `diarize` 不是真/假/空串；`max_speakers`/`min_speakers` 非整数；`min_speakers` 越界（1-8）或 `min > max` |
| `CLOUD_DIARIZE_UNSUPPORTED` | 400 | `diarize=true` 但云端未装声纹引擎 → detail 给出 `engines_cli install asr-speaker-campplus`；**不会偷偷改成不区分** |
| `DIARIZE_UNAVAILABLE` | 400 | 本地与云端都没有可用的说话人区分能力，或声纹抽取失败 |
| `ASR_FAILED` | 400 | 云端引擎其它失败（音频损坏等） |

---

## 8. OpenAI 兼容接口（/v1/audio/*、/v1/models）

给现成的 OpenAI SDK / 第三方客户端直接用：不新起服务、不另建引擎，请求内部复用同一套
本地/云端/mock 引擎、音色库与 ASR，**不落库为任务**（轻量、同步返回）。

鉴权与第 7 节同一套：配置了 `cloud.api_key` 时要求 `Authorization: Bearer <key>`
或 `X-API-Key: <key>`；未配置时仅允许本机（`127.0.0.1`）访问。
鉴权/参数错误一律返回 OpenAI 风格错误体：

```json
{ "error": { "message": "未知音色 'alloy'。可用音色：builtin-vivian, …",
             "type": "invalid_request_error", "param": "voice", "code": "VOICE_NOT_FOUND" } }
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/v1/audio/speech` | 文本转语音，返回二进制音频 |
| POST | `/v1/audio/transcriptions` | 语音转文字（multipart） |
| GET | `/v1/audio/voices` | 本项目可用音色清单（便于非 OpenAI 客户端选音色） |
| GET | `/v1/models` | 本项目可用「模型」清单（`object: "list"`） |

### POST /v1/audio/speech

```json
{ "model": "qwen3-tts", "input": "你好，这是 OpenAI 兼容测试。",
  "voice": "vivian", "response_format": "wav", "speed": 1.0, "language": "Auto" }
```

- `voice`：内置音色的 id / speaker / 名称（大小写不敏感，`vivian`、`builtin-vivian`、
  `Vivian` 都行），或自定义音色 id（自动走 clone + 参考音频）。未知音色 → 400 `VOICE_NOT_FOUND`。
- `response_format`：`mp3`（默认，`audio/mpeg`）、`wav`（`audio/wav`）、`opus`（`audio/ogg`）、
  `aac`、`flac`；后三者依赖 ffmpeg，缺少时返回 400 并说明原因。
- `speed`：映射到引擎语速，引擎稳定区间 `0.5–2.0`，超出会被夹取并记 warning 日志。
- `input` 受 `limits.max_text_chars`（默认 5000）限制，空串 → 400 `TEXT_EMPTY`。
- 输出固定 24kHz 单声道；响应头附 `X-Sample-Rate`、`X-Voice`、`X-Model`、
  `X-Audio-Duration`、`X-Elapsed-Ms`。

```bash
curl -s http://127.0.0.1:8443/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-tts","input":"你好，这是 OpenAI 兼容测试。","voice":"vivian","response_format":"mp3"}' \
  -o out.mp3
```

### POST /v1/audio/transcriptions

multipart 字段：`file`（音频）、`model`、`language`、`response_format`（`json`|`verbose_json`|`text`）。

```bash
curl -s http://127.0.0.1:8443/v1/audio/transcriptions \
  -F file=@out.mp3 -F model=qwen3-asr -F language=zh
# → {"text": "…"}
# response_format=verbose_json →
# {"task":"transcribe","language":"zh","duration":3.6,"text":"…","backend":"local","model":"…","elapsed_ms":820}
```

### GET /v1/audio/voices / GET /v1/models

```json
{ "object": "list",
  "data": [ { "id": "builtin-vivian", "name": "Vivian · 温柔女声", "language": "chinese",
              "gender": "female", "tags": ["builtin", "young", "标准普通话"], "kind": "builtin",
              "speaker": "vivian", "description": "…", "has_audio": false } ] }
```

```json
{ "object": "list",
  "data": [ { "id": "qwen3-tts", "object": "model", "created": 1792369769, "owned_by": "qwentts",
              "capability": "内置音色 + 指令控制", "backend": "local", "kind": "custom_voice",
              "mode": "hybrid", "ready": true },
            { "id": "qwen3-tts-base", … }, { "id": "qwen3-tts-design", … },
            { "id": "qwen3-tts-cloud", … }, { "id": "qwen3-asr", … } ] }
```

### 与 OpenAI 官方接口的差异

| 项 | OpenAI | 本项目 |
| --- | --- | --- |
| `stream=true` | 分块流式返回 | **不支持**，直接 400 `STREAM_NOT_SUPPORTED`（避免客户端挂死）；要边生成边播用 `/api/tasks` + WebSocket |
| `voice` 取值 | `alloy`/`echo`/`nova`… 固定集合 | 本项目音色库（内置 9 个 + 自定义），`GET /v1/audio/voices` 查询；`alloy` 等会 400 |
| `opus`/`aac`/`flac` | 官方支持 | 取决于本机 ffmpeg；缺失或编码器不可用时 400 并给出原因 |
| `model` | `tts-1`、`tts-1-hd`、`whisper-1` | 本项目模型 id（见 `/v1/models`）；该字段仅记录与回显，实际后端由 `run.mode`/路由决定，传 `tts-1` 之类不会报错 |
| `instructions` | gpt-4o-mini-tts 专用 | 映射到引擎的 `instruct`（指令控制语气/风格） |
| 采样率 | 24kHz（pcm 可指定） | 固定 24kHz 单声道；不支持 `response_format=pcm` |
| 鉴权错误体 | `invalid_api_key` | 同 OpenAI 形状；未配置 `cloud.api_key` 时仅本机，非本机 401 `api_key_required` |
| 任务可见性 | 无 | 不创建任务记录（轻量同步），任务中心/下载接口无对应条目 |

---

## 9. 错误码

| 错误码 | 场景 | 界面处理 |
| --- | --- | --- |
| `VRAM_INSUFFICIENT` | 本地显存不足（加载或推理时） | 提示 + 「一键切换到云端」 |
| `MODEL_MISSING` | 模型目录不存在 | 引导运行下载脚本 |
| `MODEL_LOAD_FAILED` | 模型加载异常 | 状态页日志 + 重试 |
| `CLOUD_UNREACHABLE` | 云端连接失败/隧道断开 | 提示切换本地 |
| `CLOUD_NOT_READY` | 云端模型未就绪且超时 | 自动重试后提示 |
| `CLOUD_AUTH_FAILED` | 密钥错误 | 引导检查设置页密钥 |
| `TEXT_EMPTY` / `TEXT_TOO_LONG` | 文本校验 | 表单提示 |
| `UPLOAD_FORMAT` / `UPLOAD_TOO_LARGE` / `AUDIO_TOO_LONG` / `AUDIO_DECODE` | 上传校验 | 上传区提示 |
| `NO_REF_AUDIO` | 克隆缺参考音频 | 引导上传 |
| `CANCELED` | 用户取消 | 保留片段并标注已截断 |
| `INTERRUPTED` | 服务重启中断 | 一键重试 |
| `INTERNAL` | 未预期异常 | 仅显示可读信息，不展示堆栈 |
| `ENGINE_NOT_FOUND` | 引擎 id 不存在 | 刷新引擎列表 |
| `ENGINE_NOT_INSTALLED` | 引擎模型未安装 | 引导点击安装 |
| `ENGINE_BUSY` | 引擎正在使用/安装中，或删除当前生效引擎 | 提示先切换/等待 |
| `ENGINE_CAPABILITY_MISSING` | 当前引擎不支持该任务类型（如 voxcpm1.5 上做音色设计） | 引导切换 `qwen3-tts-1.7b` |
| `PACKAGE_MISSING` | 引擎依赖包缺失/安装失败 | 给出**安全安装命令**（指向该引擎 venv 的 python，且只用 `locks/<id>.txt` 的精确版本，绝不裸 `pip install`） |
| `ENGINE_TIMEOUT` | 隔离引擎子进程推理超时 | 缩短文本或调高超时后重试 |
| `VENV_FAILED` | 引擎 venv 创建/装核心依赖失败 | 磁盘空间、杀软拦截、路径含空格，见 [ENGINE_ISOLATION.md](ENGINE_ISOLATION.md) |
| `VERSION_MISMATCH` | 装完的版本与 `locks/<id>.txt` 不一致（或包缺失） | 详情里给出不匹配清单 + 修复命令（`-c <venv>\constraints.txt -r locks\<id>.txt`）；校验不通过不会置为已安装 |
| `CLOUD_DIARIZE_UNSUPPORTED` | 云端未安装说话人区分引擎（`asr.diarize.prefer=cloud`，或云端 `/v1/health.asr.diarize=false`） | 提示在云端执行 `engines_cli install asr-speaker-campplus`；或改成 `prefer=local` 用本地 CAM++ |
| `DIARIZE_UNAVAILABLE` | 本地与云端都没有可用的说话人区分能力，或声纹抽取失败 | 引导装本地「CAM++ 声纹」或云端 `asr-speaker-campplus` |
| `SPEAKER_TIMELINE_MISSING` | 拆分/合并时说「缺少说话人时间轴」 | 引导重新转写并勾选「区分说话人」 |
| `SPEAKER_SPLIT_NOT_FOUND` | 还没拆分就请求 `speakers/zip`（404） | 提示先调用 `split-by-speaker` |
| `EXPORT_SOURCE_MISSING` | 旧任务没有 `source.wav`，无法换档重导出 / 拆分（404） | 说明原因并引导重新生成任务（新任务会自动保留原件） |

---

## 10. 引擎与模型管理

多引擎注册表的增删改查：安装与删除都是**后台任务**，进度通过 WebSocket 事件推送（`engine.install`），
HTTP 请求立即返回，不阻塞。所有接口仅本机可访问（沿用全局约定），错误按第 9 节错误码返回。

### GET /api/engines

```json
{
  "active": "qwen3-tts-1.7b",
  "items": [
    {
      "id": "voxcpm1.5",
      "name": "VoxCPM1.5",
      "vendor": "OpenBMB（面壁智能）",
      "params": "medium（ONNX）",
      "kind_tags": ["tts"],
      "languages": ["chinese"],
      "vram_gb": 0.0,
      "sample_rate": 22050,
      "supports_stream": true,
      "installed": true,
      "size_mb": 60.3,
      "path": "E:\\…\\models\\voxcpm1.5",
      "status": "ready",
      "detail": "单音色（huayan 女声）中文轻量引擎，ONNX CPU 推理，可实时；…",
      "packages": ["voxcpm"],
      "package_installed": true,
      "notes": "单音色（huayan 女声）…",
      "experimental": false,
      "available": true
    }
  ]
}
```

`status` 取值：`ready` / `not_installed` / `package_missing` / `error` / `experimental`。

每条 item 里与**版本钉死**相关的字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `lock_file` | string | 该引擎的锁文件绝对路径 `backend\app\engine\adapters\locks\<id>.txt`（逐行 `包名==精确版本`） |
| `lock_packages` | string[] | 锁文件内容，形如 `["voxcpm==2.0.3", "wetext==0.1.8", …]` |
| `versions_ok` | bool | 实际安装版本是否与锁文件**完全一致**（venv 未创建时恒为 `false`） |
| `versions_mismatch` | string[] | 不一致清单，形如 `["wetext 0.1.8→9.9.9", "onnx 1.23.2→缺失"]`；非空即前端标红 |
| `install_commands` | string[] | 两条命令：`--no-deps "<主包>==<版本>"` 与 `-r "<locks>\<id>.txt"`，均带 `-c <venv>\constraints.txt` |
| `venv_constraints` | string | venv 的 `constraints.txt` 路径（精确钉 torch/transformers/numpy） |


### POST /api/engines/{id}/install → 202

```json
{ "job_id": "be5bb4880762", "engine_id": "voxcpm1.5" }
```

同一引擎同时只允许一个安装任务（重复点击返回同一个 `job_id`）。安装过程事件
（`topic: engines`，`event: engine.install`）：

```json
{ "topic": "engines", "event": "engine.install", "ts": 1730000000.1,
  "data": { "engine_id": "voxcpm1.5", "job_id": "be5bb4880762",
            "phase": "download", "progress": 0.56,
            "message": "下载 model.safetensors 1024.0/1529.2MB", "done": false } }
```

`phase`：`pip`（装依赖）→ `download`（下模型，支持 .part 断点续传）→ `verify`（校验 + 真机试合成）→ `done` / `error`。

### GET /api/engines/{id}/install/status

```json
{ "engine_id": "voxcpm1.5", "job_id": "be5bb4880762", "phase": "done", "progress": 1.0,
  "message": "VoxCPM1.5 安装完成", "done": true, "error": null,
  "started_at": 1792371538.8, "finished_at": 1792371607.2, "running": false }
```

最近一次状态会持久化到 `data/engines_state.json`，刷新页面/重启服务后仍可显示。

### DELETE /api/engines/{id} → `{ "ok": true, "freed_mb": 312.6 }`

先 `unload()` 并释放显存/内存，再删除 `<models_dir>/<id>/`；返回实际释放体积。
删除**当前生效**的引擎、或该引擎正在安装/有任务在跑时返回 `ENGINE_BUSY`。

### POST /api/engines/{id}/select → `{ "ok": true, "active": "qwen3-tts-1.7b" }`

等价于热改 `engine.active`：卸载旧引擎 → `torch.cuda.empty_cache()` → 写 `data/runtime.json`。
引擎未安装返回 `ENGINE_NOT_INSTALLED`，缺依赖返回 `PACKAGE_MISSING`。

### GET /api/engines/{id}/health

```json
{ "ready": true, "detail": "单音色（huayan 女声）中文轻量引擎…", "vram_allocated_mb": 0,
  "engine_id": "voxcpm1.5", "active": true }
```

### 命令行（安装器用，不依赖 HTTP）

```bash
cd backend
python -m app.engines_cli list --json
python -m app.engines_cli install voxcpm1.5 --dry-run   # 只预览文件清单/体积，不下载
python -m app.engines_cli install voxcpm1.5      # 流式打印 phase/progress（真下载）
python -m app.engines_cli remove voxcpm1.5
python -m app.engines_cli select voxcpm1.5
python -m app.engines_cli health voxcpm1.5 --json
# 也可以从项目根直接跑：python backend/app/engines_cli.py list
```

