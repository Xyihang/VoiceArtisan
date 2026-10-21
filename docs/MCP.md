# MCP Server 使用文档

- 适用实现：`backend/app/mcp_server.py`（v1.1.11 实测）
- 传输：**仅 stdio**，行分隔 JSON-RPC 2.0（协议版本 `2024-11-05`）
- 一句话定位：**给外部 Agent 一个本地 stdio 入口，把「合成 / 转写 / 查任务 / 列音色」当成 4 个工具调用。**

---

## 1. 定位与它跟 REST API 的关系

| 项目 | 说明 |
| --- | --- |
| 谁在用 | Claude Desktop / Cline 等支持 MCP 的客户端（Agent 侧） |
| 传输 | stdio（进程内管道，不是网络端口） |
| 进程干什么 | 只做协议翻译：收 JSON-RPC → 调后端 → 把结果塞回 `content[0].text` |
| 模型谁加载 | **后端进程**。MCP 进程**不加载任何模型** |
| 和 REST 的关系 | MCP 进程通过 `QWENTTS_MCP_BASE_URL`（默认 `https://127.0.0.1:8443`）调**同一份后端**，因此排队、取消、日志、进度、显存回收、说话人区分等逻辑与界面完全一致 |
| 是否绕过 HTTP | **不绕**。原规格设想的「进程内直调 service 层」未采用：4GB 显存卡上再建一套引擎会直接把显存吃满，而本机回环只有几百微秒开销 |

> 结论：**MCP 是 REST 之上的一层壳**，不新增能力，只换调用形式。REST 能做的（克隆、设计、批量、导出档位…）MCP 现在**没有**对应工具，见第 6 节能力边界。

调用链：

```
MCP 客户端 --stdio JSON-RPC--> python -m app.mcp_server
                                      |
                                      | HTTPS 127.0.0.1:8443（自签证书，代码内已跳过校验）
                                      v
                              FastAPI 后端（/api/tasks、/api/assets、/api/voices）
```

---

## 2. 启动与前置条件

### 2.1 先起后端

MCP 自己不出声，所有真实工作都在后端。先后端起服务（任意一种）：

```powershell
# 项目根目录：一键启动（会自动构建前端 + 自检 + 起 HTTPS 服务）
.\scripts\start.ps1
```

```bash
# 或者直接起后端（在 backend/ 下）
cd backend && python -m app.main
```

服务地址默认 `https://127.0.0.1:8443`（端口来自 `config/config.yaml` 的 `server.port`）。

### 2.2 判定「后端就绪」的两条依据

| 判据 | 做法 | 期望 |
| --- | --- | --- |
| 端口活着 | 浏览器打开 `https://localhost:8443`（自签证书点「继续前往」） | 能看到界面 / 返回 JSON |
| 引擎就绪 | `curl -k https://localhost:8443/api/health` 看 `local.ready` 或 `cloud.ready` | 至少一个后端 `ready: true`；`queue.pending` 不堆积 |

`mock.enabled: true`（占位引擎）时也能连通所有工具，只是不出真实语音——联调协议时够用。

### 2.3 手测 MCP 本身

stdio server 不需要后端也能回 `initialize` / `tools/list`（只有真正调工具才需要后端）：

```powershell
cd backend
# 每行一条、必须是单行 JSON
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python -m app.mcp_server
```

```json
{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "qwentts", "version": "1.1.11"}}}
```

```powershell
'{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python -m app.mcp_server
```

`tools/list` 应返回 4 个工具名：`tts_synthesize` / `asr_transcribe` / `task_status` / `voice_list`。

### 2.4 前置依赖

| 依赖 | 要求 |
| --- | --- |
| Python | 3.10+（项目基线 3.10.8），且**是起了后端的那套解释器**（venv 或系统 Python 都行） |
| 包 | 只用标准库（`json/ssl/urllib/uuid`）+ `app.__version__`，无需额外安装 |
| 工作目录 | 必须在 `backend/`（`python -m app.mcp_server` 才能找到 `app` 包），或按 §3.3 用 `-c` 方式绕开 |

---

## 3. 客户端配置

Windows 路径注意：JSON 里反斜杠要写成 `\\`。

### 3.1 Claude Desktop

配置文件：`%APPDATA%\Claude\claude_desktop_config.json`（即 `C:\Users\<你>\AppData\Roaming\Claude\claude_desktop_config.json`），改完**完全退出并重启** Claude Desktop。

```json
{
  "mcpServers": {
    "qwentts": {
      "command": "C:\\Users\\xiyih\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
      "args": ["-m", "app.mcp_server"],
      "cwd": "E:\\trae_projects\\QWENTTS\\backend",
      "env": {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "QWENTTS_MCP_BASE_URL": "https://127.0.0.1:8443",
        "QWENTTS_MCP_TIMEOUT": "600",
        "QWENTTS_MCP_REQUEST_TIMEOUT": "300"
      }
    }
  }
}
```

字段含义：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `command` | 是 | **解释器绝对路径**。装了 venv 就写 `E:\\trae_projects\\QWENTTS\\.venv\\Scripts\\python.exe` 之类；不确定就 `python -c "import sys;print(sys.executable)"` |
| `args` | 是 | 固定 `["-m", "app.mcp_server"]` |
| `cwd` | 强烈建议 | 必须是 `backend` 目录；缺了会 `ModuleNotFoundError: No module named 'app'` |
| `env` | 可选 | 不写就继承父进程环境。中文乱码时加 `PYTHONUTF8`/`PYTHONIOENCODING` |

### 3.2 Cline（VS Code）

配置文件：`%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`（也可以在 Cline 面板 → MCP Servers → Configure MCP Servers 打开）：

```json
{
  "mcpServers": {
    "qwentts": {
      "command": "C:\\Users\\xiyih\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
      "args": ["-m", "app.mcp_server"],
      "cwd": "E:\\trae_projects\\QWENTTS\\backend",
      "env": {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "QWENTTS_MCP_BASE_URL": "https://127.0.0.1:8443"
      },
      "disabled": false,
      "autoApprove": [],
      "timeout": 600
    }
  }
}
```

与 Claude Desktop 的字段差异：

| 字段 | Claude Desktop | Cline |
| --- | --- | --- |
| `command` / `args` / `env` | 一致 | 一致 |
| `cwd` | 支持 | 支持 |
| `disabled` | 无 | 有，`false` 表示启用 |
| `autoApprove` | 无 | 有，列在里面的工具调用不弹确认（建议留空，合成很慢别自动放行） |
| `timeout` | 无（按客户端默认） | 有，单位秒；长文本合成建议 ≥ 300 |
| 配置生效方式 | 重启整个应用 | 面板里 Reload / 保存即生效 |

> Cursor 用的是 `%USERPROFILE%\.cursor\mcp.json`，键同为 `mcpServers`；部分版本**不认 `cwd`**，按 §3.3 换写法。

### 3.3 客户端不支持 `cwd` 时

用 `-c` 把 `backend` 塞进 `sys.path`（已实测可用）：

```json
{
  "mcpServers": {
    "qwentts": {
      "command": "C:\\Users\\xiyih\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
      "args": [
        "-c",
        "import sys; sys.path.insert(0, r'E:\\trae_projects\\QWENTTS\\backend'); from app.mcp_server import main; main()"
      ]
    }
  }
}
```

### 3.4 首次连不上先看这四件事

| 顺序 | 检查项 | 症状 | 修法 |
| --- | --- | --- | --- |
| 1 | **工作目录**是不是 `backend` | 客户端报 `ModuleNotFoundError: No module named 'app'` / 进程秒退 | 补 `cwd`，或用 §3.3 写法 |
| 2 | **解释器路径**是否真实存在 | `spawn ... ENOENT` / 进程起不来 | `command` 写绝对路径，用后端同款解释器；路径里别留未转义的单反斜杠 |
| 3 | **UTF-8** | 中文音色名/文本变乱码，或 `UnicodeDecodeError` | `env` 里加 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`；代码里已对 stdin/stdout 做了 UTF-8 reconfigure |
| 4 | **超时** | 工具调到一半客户端断开 / 前端显示 timeout | Cline 的 `timeout` 调大；`QWENTTS_MCP_TIMEOUT`（等任务）>`QWENTTS_MCP_REQUEST_TIMEOUT`（单次请求） |
| 5 | 后端没起 | 工具报 `URLError: <urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>` | 先按 §2.1 起后端；换端口就改 `QWENTTS_MCP_BASE_URL` |

---

## 4. 工具参考

所有工具的返回都是「一个 JSON 字符串塞在 `content[0].text` 里」，外层 `isError` 表示这次调用成功与否：

```json
{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{…业务 JSON…}"}],"isError":false}}
```

### 4.1 `tts_synthesize` —— 文本合成语音

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `text` | string | 是 | — | 要合成的文本；**空字符串会被后端以 422 拒绝** |
| `voice_id` | string | 否 | 后端默认音色 | 音色 id，如 `builtin-vivian`；留空/不传则不指定 |
| `language` | string | 否 | `Auto` | 语言提示 |
| `speed` | number | 否 | 后端默认（1.0） | 语速；只有 `null` 视为不传，`0` 会被原样提交并被后端参数校验拒绝 |
| `target_duration_s` | number | 否 | 0（不控制） | 目标时长（秒），优先级高于 `speed`；**传 0 / 不传都表示不控制** |

不支持：`instruct`、`pitch`、`temperature`、`output_format`、`force_backend`（要这些请直连 REST `POST /api/tasks`）。

返回：

```json
{
  "task_id": "6acbdf8ed384",
  "status": "completed",
  "duration_sec": 5.1,
  "backend": "local",
  "download_url": "https://127.0.0.1:8443/api/tasks/6acbdf8ed384/download",
  "error": null
}
```

| 字段 | 说明 |
| --- | --- |
| `status` | `queued/running` 不会出现在这里（工具内部已等到结束），正常是 `completed` / `failed` / `canceled`；等超时为 `timeout` |
| `error` | 仅 `status=failed` 时为后端的 `error_message`，其余为 `null`（`canceled`/`timeout` 也是 `null`） |
| `download_url` | 按 `QWENTTS_MCP_BASE_URL` 拼的绝对地址，自签证书需 `curl -k` |

最小调用报文：

```json
{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"tts_synthesize","arguments":{"text":"你好，这是 MCP 测试。","voice_id":"builtin-vivian","speed":1.0}}}
```

常见错误：`text` 为空 → `HTTP 422`；`voice_id` 不存在 → `HTTP 4xx`（后端校验）；任务失败 → `isError:false` 但 `status:"failed"`、`error` 有内容（**注意：工具不会因此报错**）；显存不足 → 同上，`error` 里是 `VRAM_INSUFFICIENT` 之类文案。

### 4.2 `asr_transcribe` —— 本地音频转写

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `audio_path` | string | 是 | — | **本机绝对路径**（MCP 进程所在机器）。文件不存在直接报错，不建任务 |
| `language` | string | 否 | `auto` | 语言提示 |
| `diarize` | boolean | 否 | `false` | 是否区分说话人（需装 CAM++；云端引擎不支持，会退回纯文本） |
| `max_speakers` | integer | 否 | 4 | 最多说话人数；**`0` 视为不传** |

流程：`POST /api/assets`（multipart 上传，文件名取 basename）→ `POST /api/tasks`（`type=asr`）→ 轮询到结束。上传受 `limits.max_upload_mb`、格式白名单（wav/mp3/flac/m4a/ogg/webm/aac）与 `limits.max_asr_audio_seconds` 约束。

返回：

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "completed",
  "transcript": "整段文本…",
  "segments": [
    { "start": 0.0, "end": 3.2, "text": "第一句…", "speaker": "SPK1" },
    { "start": 3.2, "end": 6.8, "text": "第二句…", "speaker": "SPK2" }
  ],
  "error": null
}
```

- `segments` 来自任务详情（`outputs/<id>/chunks.json`）；不开 `diarize` 时 `speaker` 为 `null`。
- 说话人改名（`SPK1` → 真实姓名）没有 MCP 工具，走 REST `PATCH /api/tasks/{id}/speakers`。

最小调用报文：

```json
{"jsonrpc":"2.0","id":11,"method":"tools/call","params":{"name":"asr_transcribe","arguments":{"audio_path":"E:\\trae_projects\\QWENTTS\\data\\uploads\\demo.wav","language":"zh","diarize":true,"max_speakers":2}}}
```

常见错误：路径不存在 → `RuntimeError: 文件不存在：…`；音频过长 → `HTTP 4xx AUDIO_TOO_LONG`；后端没起 → `URLError`。

### 4.3 `task_status` —— 查任务状态

| 参数 | 类型 | 必填 | 默认 |
| --- | --- | --- | --- |
| `task_id` | string | 是 | — |

返回（字段直取后端任务对象，未做加工）：

```json
{
  "task_id": "6acbdf8ed384",
  "status": "completed",
  "progress": 1.0,
  "error_code": null,
  "error_message": null,
  "output_file": "6acbdf8ed384.wav"
}
```

最小调用报文：

```json
{"jsonrpc":"2.0","id":12,"method":"tools/call","params":{"name":"task_status","arguments":{"task_id":"6acbdf8ed384"}}}
```

常见错误（已实测）：不存在的 id →

```json
{"jsonrpc":"2.0","id":12,"result":{"content":[{"type":"text","text":"RuntimeError: HTTP 404: {\"detail\": {\"code\": \"NOT_FOUND\", \"message\": \"任务不存在\"}}"}],"isError":true}}
```

> `output_file` 只是文件名，**没有下载地址**；下载地址用 `tts_synthesize` 返回的 `download_url`，或自己拼 `/api/tasks/{id}/download`。

### 4.4 `voice_list` —— 列音色

无参数（`inputSchema` 是空对象）。

```json
{"jsonrpc":"2.0","id":13,"method":"tools/call","params":{"name":"voice_list","arguments":{}}}
```

返回**只有 id 和 name**，裁掉了 `kind/description/available/peaks` 等字段：

```json
{
  "builtin": [{ "id": "builtin-vivian", "name": "Vivian · 温柔女声" }],
  "custom": []
}
```

要完整字段（性别/年龄/语言/可用性/描述）请用 REST `GET /api/voices`。

---

## 5. 环境变量

全部由 MCP 进程读取（在客户端配置的 `env` 里设，或在启动 MCP 的 shell 里 `export` / `$env:`）。

| 变量 | 默认值 | 作用 | 什么时候改 |
| --- | --- | --- | --- |
| `QWENTTS_MCP_BASE_URL` | `https://127.0.0.1:8443` | 后端基础地址，`download_url` 也按它拼 | 后端换端口（`server.port`）、走 SSH 隧道（如 `https://127.0.0.1:18443`）、直连云主机 |
| `QWENTTS_MCP_API_KEY` | 空 | 非空时每个请求带 `X-API-Key` 头 | 前面挂了需要鉴权的反代 / 对接云端网关时 |
| `QWENTTS_MCP_TIMEOUT` | `600` | **等任务结束**的总时长（秒）。超时返回 `status:"timeout"` | 长文本合成、大音频转写、4GB 卡首包慢时调大 |
| `QWENTTS_MCP_REQUEST_TIMEOUT` | `300` | **单次 HTTP 请求**超时（秒），含音频上传 | 上传大文件或反代慢时调大 |

补充说明：

- 轮询间隔固定 1.0s（代码内写死，无环境变量）。
- 证书校验被显式关闭（自签证书场景），所以 `https://127.0.0.1:8443` 与 `https://localhost:8443` 都能用。
- `QWENTTS_MCP_API_KEY` 对本地 `/api/*` 不生效（本地不校验），设了也不会报错。

---

## 6. 能力边界（**只做 tools + stdio**）

| 能力 | 状态 | 客户端会看到什么 |
| --- | --- | --- |
| `initialize`（返回 `protocolVersion` / `capabilities` / `serverInfo`） | ✅ 已实现 | 握手成功，`serverInfo.name = "qwentts"`，version 取自 `app.__version__` |
| `tools/list`、`tools/call` | ✅ 已实现 | 4 个工具出现在工具列表里 |
| `ping` | ✅ 已实现 | 回 `{}` |
| 通知 `notifications/initialized`、`notifications/cancelled` | ✅ 静默接受 | 不回包（符合规范：通知不应有响应） |
| **stdio 传输** | ✅ 唯一传输 | 只能配 `command`/`args` 的 stdio server |
| **SSE / Streamable HTTP 传输** | ❌ 未实现（本次不做） | 没有任何 HTTP 端点（无 `/sse`、无 `/mcp`）。客户端若选 SSE/URL 方式配置，会连接失败或一直「连接中」 |
| **resources**（`resources/list`、`resources/read`、订阅） | ❌ 未实现 | `capabilities` 里只有 `{"tools": {}}`，客户端不显示资源面板；硬调 `resources/list` 回 `-32601 Method not found` |
| **prompts**（`prompts/list`、`prompts/get`） | ❌ 未实现 | 无提示词/模板面板；硬调回 `-32601` |
| **sampling**（server 反向请求客户端取样） | ❌ 未实现 | MCP 进程从不主动发 `sampling/createMessage` |
| **logging / progress / 其它 server→client 通知** | ❌ 未实现 | 任务进度不会推送，只能轮询 `task_status` |
| **completions（参数补全）** | ❌ 未实现 | 硬调回 `-32601` |

**一句话**：本项目的 MCP Server = **stdio + 4 个 tools**，协议面就是这么多。缺的能力不打算用「假实现」凑，登记为后续项（见 [spec/PHASE-3-BACKLOG.md §7](spec/PHASE-3-BACKLOG.md)）。

---

## 7. 排错

### 7.1 JSON-RPC 层错误（有 `error` 字段）

| 错误码 | 触发条件 | 实际响应（实测） | 修法 |
| --- | --- | --- | --- |
| `-32700` | 整行不是合法 JSON（多行 pretty JSON、BOM、stdout 混入日志） | `{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}` | **一行一条 JSON**，不要格式化换行；别往 stdout print 任何东西 |
| `-32601` | 方法不存在（`resources/list`、`prompts/list` 等） | `{"jsonrpc":"2.0","id":3,"error":{"code":-32601,"message":"Method not found: resources/list"}}` | 见第 6 节：这些能力本来就没有 |
| 无响应 | 收到的是通知（无 `id`）或空行 | 不回包 | 正常行为 |

### 7.2 工具执行失败（`isError: true`，但没有 `error` 字段）

失败在 `result.content[0].text` 里，格式固定为 `异常类名: 消息`：

| 文本形态 | 含义 | 排查 |
| --- | --- | --- |
| `RuntimeError: HTTP 404: {"detail": {"code": "NOT_FOUND", ...}}` | 后端明确拒绝，`detail` 是后端错误体 | 按 `code` 查 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) / [API.md](API.md) |
| `RuntimeError: HTTP 422: {...}` | 请求体没过后端校验（如 `text` 为空、参数超范围） | 对照 §4 的参数表 |
| `RuntimeError: 文件不存在：<路径>` | `asr_transcribe` 的 `audio_path` 找不到 | 用绝对路径，注意 Windows 盘符与反斜杠转义 |
| `RuntimeError: 未知工具：<name>` | `tools/call` 的 `name` 拼错 | 只支持 §4 的 4 个名字 |
| `URLError: <urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>` | 后端没起 / 端口不对 | 起后端，核对 `QWENTTS_MCP_BASE_URL` |
| `RuntimeError: HTTP 401 ...` | 网关要求鉴权但 `QWENTTS_MCP_API_KEY` 没配或不匹配 | 补 API key |

分层关系：**协议层错误 → `error` 对象；业务层错误 → `isError:true` + 文本；任务业务失败（如 VRAM 不足、合成报错）→ `isError:false` + `status:"failed"` + `error` 字段。** 客户端只看 `isError` 会把最后一种当成功，Agent 提示词里应当同时判 `status`。

### 7.3 stdout 被日志污染

表现：客户端显示 `Parse error`、`Unexpected token`，或 MCP 直接从工具列表里消失；手工跑 `python -m app.mcp_server` 能看到 stdout 里混着非 JSON 行。

原因：stdio 传输把 **stdout 当作协议通道**，一行只能是一条 JSON-RPC 报文。

规避：

| 做法 | 说明 |
| --- | --- |
| 诊断信息写 **stderr** | 客户端一般会把 stderr 收进日志面板，stdout 保持纯净 |
| 不要 import 会 print 的模块 | 现有实现只用标准库 + `app.__version__`，不触发任何模型/日志初始化 |
| 不要加 `-u` 之外的缓冲花样 | 实现里每条响应都 `flush()`，无需 `PYTHONUNBUFFERED` |
| 关掉第三方库的进度条 | tqdm/rich 默认走 stderr 还好，配置成 stdout 就会污染 |
| 别用 `python -m app.main` 之类带日志输出的入口 | 只跑 `python -m app.mcp_server` |

### 7.4 其它常见现象

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 工具列表有但一调就超时 | 后端在跑长任务、显存排队 | 看 `GET /api/health` 的 `queue`；调大 `QWENTTS_MCP_TIMEOUT` |
| `tts_synthesize` 返回 `status:"timeout"` 且 `error:null` | 等任务超时，任务可能还在跑 | 用 `task_status` 复查，别直接重试（会产生重复任务） |
| 转写结果没有 `speaker` | `diarize=false`，或引擎链只有云端 | 打开 `diarize` 并装 CAM++；看 CONFIG 的 `asr.diarize` |
| 中文乱码 | 客户端启动进程时用的是 cp936 | `env` 加 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` |

---

## 8. 相关文档

| 文档 | 用途 |
| --- | --- |
| [API.md](API.md) | MCP 底层调用的 REST 接口全集 |
| [CONFIG.md](CONFIG.md) | 端口、模型、`asr.diarize`、限额（影响上传与时长上限） |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 后端错误码逐条解释 |
| [spec/PHASE-3-BACKLOG.md §7](spec/PHASE-3-BACKLOG.md) | MCP 的后续项登记（SSE / resources / prompts） |
