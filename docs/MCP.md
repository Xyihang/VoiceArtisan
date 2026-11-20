# MCP Server 使用文档

- 适用实现：`backend/app/mcp_server.py`（**v1.1.52 实测**）
- 传输：**仅 stdio**，行分隔 JSON-RPC 2.0（协议版本 `2024-11-05`）
- 一句话定位：**给外部 Agent 一个本机 stdio 入口，把「合成 / 转写 / 克隆 / 音色设计 / 查任务 / 取消任务 / 列音色」当成 9 个工具调用。**

---

## 1. 定位与它跟 REST API 的关系

| 项目 | 说明 |
| --- | --- |
| 谁在用 | DSH / Claude Desktop / Cursor / Cline 等支持 MCP 的客户端（Agent 侧） |
| 传输 | stdio（进程内管道，不是网络端口） |
| 进程干什么 | 只做协议翻译：收 JSON-RPC → 调后端 → 把结果塞回 `content[0].text` + `structuredContent` |
| 模型谁加载 | **后端进程**。MCP 进程**不加载任何模型** |
| 和 REST 的关系 | MCP 进程通过 `QWENTTS_MCP_BASE_URL`（默认 `https://127.0.0.1:8443`）调**同一份后端**，因此排队、取消、日志、进度、显存回收、说话人区分等逻辑与界面完全一致 |
| 是否绕过 HTTP | **不绕**。原规格设想的「进程内直调 service 层」未采用：4GB 显存卡上再建一套引擎会直接把显存吃满 |

```
MCP 客户端 --stdio JSON-RPC--> python -m app.mcp_server
                                      |
                                      | HTTPS 127.0.0.1:8443（自签证书，代码内已跳过校验）
                                      v
                              FastAPI 后端（/api/tasks、/api/assets、/api/voices）
```

---

## 2. 启动与自检

```powershell
cd backend

# 方式一：作为 MCP server（客户端会这样启动它）
python -m app.mcp_server

# 方式二：端到端自检（initialize → tools/list → health → voice_list → 真实合成 → 转写回环）
python -m app.mcp_server --selftest
```

`--selftest` 期望输出：

```
voiceartisan-mcp selftest  (server v1.1.52, base=https://127.0.0.1:8443)
  PASS  initialize
  PASS  tools/list          工具 9 个：tts_synthesize, tts_submit, ...
  PASS  health
  PASS  voice_list
  PASS  tts_synthesize (真实合成)
  PASS  asr_transcribe (回环)
selftest 全部通过
```

> 注意：自检会**真的跑一次合成与转写**（占用显存、耗时几秒到几十秒），CI 里请作为可选烟测。

### 安装成命令（可选，省掉 cwd）

```powershell
pip install -e backend
# 之后客户端配置里只要： command = "voiceartisan-mcp"
```

---

## 3. 客户端配置

### 3.1 标准写法（mcpServers）

```json
{
  "mcpServers": {
    "qwentts": {
      "type": "stdio",
      "command": "<PYTHON_EXE>",
      "args": ["-m", "app.mcp_server"],
      "cwd": "<QWENTTS_BACKEND>",
      "env": {
        "QWENTTS_MCP_BASE_URL": "https://127.0.0.1:8443",
        "QWENTTS_MCP_TIMEOUT": "600"
      }
    }
  }
}
```

- Claude Desktop：`%APPDATA%\Claude\claude_desktop_config.json`，改完完全退出再启动
- Cursor / Cline / Continue：MCP 设置里按 command / args / cwd 三个框填
- **`cwd` 必须是 backend 目录**，否则 `ModuleNotFoundError: No module named 'app'`（装了 `voiceartisan-mcp` 命令后可以省掉）

### 3.2 环境变量

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `QWENTTS_MCP_BASE_URL` | `https://127.0.0.1:8443` | 后端地址 |
| `QWENTTS_MCP_API_KEY` | 空 | 服务端开了 `X-API-Key` 鉴权时填 |
| `QWENTTS_MCP_TIMEOUT` | `600` | 单个工具调用等待任务完成的总时长（秒） |
| `QWENTTS_MCP_REQUEST_TIMEOUT` | `300` | 单次 HTTP 请求超时（秒） |
| `QWENTTS_DATA_DIR` | `<repo>/data` | 用于把 `output_path` 拼成本机绝对路径 |

---

## 4. 工具清单（9 个）

| 工具 | 阻塞？ | 作用 |
| --- | --- | --- |
| `tts_synthesize` | 是（等完成） | 文本转语音 |
| `tts_submit` | 否（立即返回 task_id） | 提交合成任务，配 `task_status` 轮询 |
| `asr_transcribe` | 是（等完成） | 音频转文字，可选区分说话人 |
| `asr_submit` | 否（立即返回 task_id） | 提交转写任务 |
| `task_status` | 否 | 查状态 / 进度 / 错误码 / 输出路径 |
| `task_cancel` | 否 | 取消排队中或运行中的任务 |
| `voice_list` | 否 | 列音色（含 `language` / `gender` / `available`） |
| `voice_clone` | 否 | 用参考音频克隆自定义音色，返回 `voice_id` |
| `voice_design` | 默认是（可 `wait:false`） | 自然语言描述音色并合成 |

### 4.1 `tts_synthesize`

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `text` | string（必填） | 要合成的文本 |
| `voice_id` | string | 音色 id，先用 `voice_list` 查 |
| `language` | string | 语言 id 或别名（`zh`→`chinese`、`en`→`english`、`ja`→`japanese`…），默认 `Auto` |
| `wait` | boolean | 默认 `true`；`false` 等同 `tts_submit` |
| `speed` | number | 1.0 原速 |
| `target_duration_s` | number | 目标时长，0 表示不控制 |

返回（`structuredContent`）：

```json
{
  "ok": true,
  "task_id": "49e8882a2ccd",
  "status": "completed",
  "type": "tts",
  "backend": "local",
  "duration_sec": 1.84,
  "elapsed_ms": 5120,
  "first_packet_ms": 1830,
  "rtf": 0.36,
  "output_file": "49e8882a2ccd.wav",
  "output_path": "<QWENTTS_BACKEND>\\..\\data\\outputs\\49e8882a2ccd\\49e8882a2ccd.wav",
  "download_url": "https://127.0.0.1:8443/api/tasks/49e8882a2ccd/download"
}
```

> `output_path` 是为了让 Agent 直接读文件：`download_url` 是 HTTPS 回环 + 自签证书，命令行客户端通常要先 `curl -k` 才能拿到。

### 4.2 `asr_transcribe`

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `audio_path` | string（必填） | 本机音频绝对路径（会先上传成 asset 再建任务） |
| `language` | string | 默认 `auto` |
| `diarize` | boolean | 是否区分说话人 |
| `max_speakers` | integer | 默认 4 |
| `wait` | boolean | 默认 `true` |

返回额外字段：

- `transcript`：整段文本
- `segments[]`：**`start_s` / `end_s`**（秒，浮点）+ `speaker` + `text`
  （旧文档写的 `start` / `end` 已作废）

### 4.3 `voice_list`

```json
{
  "ok": true,
  "count": 9,
  "engine": "qwen3-tts-0.6b",
  "languages": ["Auto", "chinese", "english", "japanese", "..."],
  "voices": [
    {"id": "builtin-vivian", "name": "Vivian · 温柔女声", "kind": "builtin",
     "language": "chinese", "gender": "female", "age": "young",
     "accent": "标准普通话", "available": true}
  ]
}
```

### 4.4 `voice_clone`

```json
{"audio_path": "E:\\...\\ref.wav", "name": "我的声音", "ref_text": "这段录音里说的话"}
→ {"ok": true, "voice_id": "custom-xxxx", "hint": "已创建自定义音色，可用 tts_synthesize 指定 voice_id 合成"}
```

### 4.5 `voice_design`

```json
{"design": "低沉磁性的中年男声，语速偏慢", "text": "欢迎使用声匠", "language": "chinese"}
```

---

## 5. 约定与错误语义

| 情况 | 表现 |
| --- | --- |
| 非法 JSON 行 | `-32700 Parse error` |
| 未知方法 | `-32601 Method not found` |
| 工具内部异常（参数错、语言不支持、文件不存在） | `isError: true`，`content[0].text` 说明原因 |
| 任务 `failed` / `canceled` / `timeout` | **`isError: true`**，`structuredContent.ok=false`，带 `error_code` / `error_message` |
| 任务 `completed` | `isError: false`，`ok=true` |

> 历史 bug（v1.1.51 及以前）：任务失败仍然 `isError:false`，Agent 会当成功。已修。

### 语言校验

`language` 会先做别名映射（`zh`/`cn`/`中文` → `chinese`），再对照后端 `/api/voices` 的合法语言列表校验。
**不合法会在建任务之前就报错**，不会白占一个队列位（历史 bug：`language: "zh"` 先建任务成功、再在引擎侧 400）。

### Content-Type

`_request()` **只要带 body 就显式设置 Content-Type**。
历史 bug（v1.1.51 及以前）：JSON 分支没设，urllib 默认发 `application/x-www-form-urlencoded`，FastAPI 直接
`422 {"type":"model_attributes_type", ...}`；因为 `initialize` / `tools/list` 无 body，只会在真调工具时暴露。

---

## 6. 能力边界（已知）

- 不能切换「本地 / 云端」运行模式（那是工作台设置，MCP 只按当前模式跑）
- 不做批量批次（batch）管理，逐条提交
- 单个 worker，任务串行；不同 Agent 同时调用会排队
- `voice_clone` 只创建音色，不校验克隆质量；效果差请回界面听参考音频

---

## 7. 自测用请求

```powershell
cd backend
# 握手
echo {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0"}}} | python -m app.mcp_server

# 列工具
echo {"jsonrpc":"2.0","id":2,"method":"tools/list"} | python -m app.mcp_server

# 调工具（注意 JSON 里转义）
echo {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"voice_list","arguments":{}}} | python -m app.mcp_server
```

---

## 8. 变更记录（针对外部 Agent 反馈）

| 反馈 | 处理 |
| --- | --- |
| P0 `_request()` 未设 Content-Type → 工具全 422 | 已修：带 body 必设；`--selftest` 会真调工具，能挡住这类问题 |
| P1 `language` 无校验，错误值到引擎才炸 | 已修：别名映射 + 建任务前对照后端语言列表校验 |
| P1 任务失败仍 `isError:false` | 已修：`ok=false` → `isError:true`，并返回 `error_code` |
| P2 长任务挂住整个 `tools/call` | 已加 `tts_submit` / `asr_submit` 立即返回 + `task_status` 轮询；其余工具支持 `wait:false` |
| P2 `download_url` 对 Agent 不可用 | 已加 `output_path`（本机绝对路径），实际目录为 `data/outputs/<task_id>/<file>` |
| P3 克隆 / 设计缺失 | 已加 `voice_clone` / `voice_design` / `task_cancel` |
| P3 `voice_list` 字段太少 | 已加 `language` / `gender` / `age` / `accent` / `available`，支持按语种过滤 |
| P4 文档漂移 | 本文档已按 v1.1.52 重写（含 `start_s`/`end_s`、9 个工具、`--selftest`） |
| P4 强依赖 `cwd` | 已加 `backend/pyproject.toml` 的 `voiceartisan-mcp` console script |
