# CLOUD-AGENT-1 · 云主机一键接入 Agent 规格

- 版本：v1 · 2026-10-20
- 状态：**已实施**（2026-10-20）。后端 `app/cloud_agent.py`、`api/cloud_agent.py`，前端 `components/CloudAgentWizard.tsx`。已实现：8 步可续做状态机（已完成步骤标记 `skipped`）、严格就绪判定（`/v1/health` 的 `ready=true` **且**一次真实 `/v1/tts` 推理成功）、失败码 + 可执行建议、凭据仅内存、全链路脱敏。LLM 已实现 §5A 所述白名单工具循环（`remote_exec` / `run_step` / `give_up`，审计写 `data/cloud-agent/audit.log`），默认关闭，勾选「AI 自主排障」后生效，异常自动回退确定性流程。
- 关联：`docs/spec/ASR-ENGINE-1.md`（第二部分）、`docs/spec/PHASE-3-BACKLOG.md`（第三部分，后续阶段）

---

## 1. 背景与目标

现状：云端 GPU 机器要能用起来，用户得手工做完下面这一长串（来自 `cloud/README.md` 与既有手工脚本）：

1. 把源码打包上传到云主机；
2. 在云主机上装 Python 依赖、装 ffmpeg；
3. 传模型或下载模型；
4. 手写并启动推理服务（端口 8443、自签证书、API Key）；
5. 回到本地改 `config/config.yaml`：`run.mode`、`cloud.base_url`、`cloud.api_key`、`cloud.tunnel.*`；
6. 重启本地后端验证。

任何一步错（端口没开、密钥不匹配、隧道参数写错）都表现为「连不上」，排查成本极高。

目标：用户只提供 **host / port / user / 密码或私钥**，Agent 自动跑完全部步骤，并在真正可用（能完成一次推理）后结束；中途失败可续做，失败信息可读。

---

## 2. 需求澄清清单

### 2.1 Must Have（本版必须）

| 编号 | 需求 | 说明 |
| --- | --- | --- |
| M1 | 连接校验 | SSH 握手 + 主机指纹确认 + 权限（sudo 可选）判断 |
| M2 | 远端环境体检 | Python 版本、GPU 与显存、CUDA 驱动、磁盘余量、目标端口占用、ffmpeg 是否存在 |
| M3 | 一键布置 | 上传源码包、创建 venv、按锁文件装依赖、必要时安装 ffmpeg |
| M4 | 启动推理服务 | 后台常驻（nohup + pid 文件），端口与 API Key 由 Agent 生成 |
| M5 | 真实可用判定 | 通过 SSH 隧道访问远端 `/v1/health`，要求 `status=ok` 且 `ready=true` |
| M6 | 本地配置落盘 | 事务式写入 `run.mode`、`cloud.base_url`、`cloud.api_key`、`cloud.tunnel.*` |
| M7 | 端到端验收 | 本地发起一次真实推理（可指定短文本）并确认拿到音频字节 |
| M8 | 进度可视化 | 每步开始/成功/失败/日志实时推送到前端（复用 `/ws/events`） |
| M9 | 断点续做 | 任务状态落盘，重启后可读；重复执行时已完成的步骤跳过（幂等） |
| M10 | 失败可读 | 每类失败给出「原因 + 用户下一步动作」，不出现裸 traceback |

### 2.2 Should Have（本版尽量）

| 编号 | 需求 |
| --- | --- |
| S1 | 隧道与直连两种拓扑自动选择（远端 8443 是否对公网可达） |
| S2 | 依赖安装镜像可切换（默认清华，失败自动回退官方） |
| S3 | 模型缺失时提示并给出「在云端下载」的可选步骤（默认不自动下载大模型，遵循项目「不自动下载模型」约束） |
| S4 | 任务取消（用户主动中止） |
| S5 | 重复接入（换一台云主机）时清理旧任务与旧隧道 |

### 2.3 Won't Have（明确不做）

- 不做多云厂商 API 集成（不调用阿里云/腾讯云 OpenAPI 创建实例）；
- 不做远程容器编排（不接管 Docker/K8s，云端仍按 `cloud/` 下的裸机方式部署）；
- 不在 Agent 里自动下载 TTS 大模型（沿用项目约束，只给链接与手动步骤）；
- 不做多机负载均衡。

### 2.4 假设（待用户确认，先按此实现）

| 编号 | 假设 | 影响 |
| --- | --- | --- |
| A1 | 远端为 Linux（Ubuntu 20.04+/Debian 11+）且用户具备 sudo 或已装好 Python 3.10 | 体检阶段不满足则报 `REMOTE_PYTHON_TOO_OLD` 并给安装命令 |
| A2 | 远端无需 GPU 驱动安装（云厂商镜像已带） | 只检测不修复 |
| A3 | 本地与远端版本一致（同一份源码包） | Agent 会校验远端 `VERSION.json` |
| A4 | 用户接受 Agent 生成随机 API Key 并写入本地配置 | 见 §7 安全规格 |
| A5 | **已确认**：sudo 仅用于「用户显式勾选安装 ffmpeg」这一种情况 | 见 §7 第 5 条 |
| A6 | **已确认**：首次 SSH 指纹需要确认，但提供「跳过校验」高级开关（默认关闭，跳过时写 WARN 到审计日志） | 见 §7 第 3 条 |

---

## 3. 现状盘点（复用点）

| 现有能力 | 位置 | 复用方式 |
| --- | --- | --- |
| SSH 连接（paramiko，支持密码/私钥） | `backend/app/tunnel.py`（`SSHTunnel.start/status`） | 抽出连接建立逻辑供 Agent 复用，隧道本身不动 |
| 云端客户端 | `backend/app/engine/cloud.py`（`CloudEngine`、`effective_base_url()`） | M5/M7 直接调用其健康检查与合成分块 |
| 云端配置项 | `backend/app/config.py`：`CloudCfg.base_url/api_key/verify_tls/readiness_timeout_s`、`TunnelCfg.enabled/ssh_host/ssh_port/username/key_path/password/local_port/remote_host/remote_port` | M6 只写这些既有字段，不新增配置结构 |
| 部署物料 | `cloud/Dockerfile`、`cloud/docker-compose.yml`、`cloud/README.md` | 源码包与启动命令以 `cloud/README.md` 的手工步骤为准，Agent 自动化同一流程 |
| 实时事件 | `backend/app/events.py`（`EventBus.publish(topic, event, data)`）、`backend/app/api/ws.py`（`/ws/events`） | Agent 进度走同一总线，前端不轮询 |
| 后台任务与事件推送范式 | `backend/app/engine/manager.py` 的 install 任务、`/api/engines/{id}/install` 返回 202 + job 事件 | Agent 任务照抄这套「202 + job_id + WS 事件」约定 |
| 错误码体系 | `backend/app/errors.py`（`AppError(code, detail)`、`to_http()`） | 新增错误码沿用格式，前端「常见错误速查」同步补条目 |
| 前端交互件 | `Modal` / `Field` / `InlineNote` / `Progress`、设置页 `SECTIONS` 结构 | 向导复用现有组件，不引新 UI 库 |

---

## 4. 用户故事

1. 作为拿到一台新云 GPU 的用户，我只填 host/port/user/密码，点「开始接入」，就能看到 8 个步骤依次变绿，最后提示「已可用，试听一次」。
2. 作为中途断网的用户，我重新打开页面看到上次任务停在「安装依赖」，点「继续」即可从该步接着做。
3. 作为排障的用户，失败卡片告诉我「远端 8443 被占用」，并给出「改用 18443 或释放端口」两个具体动作。
4. 作为已接入的用户，我在设置页能一键切回本地（`run.mode=local`），云配置保留不丢。

---

## 5. 功能规格：Agent 状态机

```
S0 参数校验 → S1 SSH 连通 → S2 环境体检 → S3 上传源码 → S4 建 venv 装依赖
   → S4B 下载模型（缺则自动下，ModelScope）
   → S5 启动服务 → S6 就绪探测 → S7 写本地配置 → S8 端到端验收 → DONE
```

任一步失败 → `FAILED(step, code, message, next_actions)`；可重试步骤标记 `retryable=true`。

| 步骤 | 输入 | 处理 | 输出（落盘） | 失败码 | 幂等策略 |
| --- | --- | --- | --- | --- | --- |
| S0 参数校验 | `host, port, user, auth, options` | 非空/端口范围/host 形如域名或 IP；私钥存在可读 | `job.json.created_at` | `INVALID_INPUT` | 纯校验 |
| S1 SSH 连通 | 同上 | `paramiko.Transport` 连接，读取 `host key`；`exec_command("echo ok")` | `host_fingerprint`（首次需用户确认，见 §7） | `SSH_AUTH_FAILED` / `SSH_UNREACHABLE` / `SSH_HOSTKEY_CHANGED` | 已连通则跳过 |
| S2 环境体检 | 已连通道 | 远端执行体检脚本，收集：`python3 --version`、`nvidia-smi`、`df -h`、`ss -ltnp`、`ffmpeg -version`、`os-release` | `env.json`：`{python, gpu, vram_gb, disk_free_gb, port_free, ffmpeg, sudo}` | `REMOTE_PYTHON_TOO_OLD` / `REMOTE_NO_GPU` / `REMOTE_DISK_LOW` / `REMOTE_PORT_BUSY` | 每次重跑，结果覆盖 |
| S3 上传源码 | 本地源码包（MEMORY: 排除 models/data/.cache） | SFTP 上传 `qwentts-src.tar.gz` 到 `~/qwentts/`，解包，校验 `VERSION.json` 一致 | `remote_path`、`source_sha256` | `UPLOAD_FAILED` / `REMOTE_VERSION_MISMATCH` | 远端 `source_sha256` 相同则跳过上传 |
| S4 依赖 | 远端路径 | `python3 -m venv .venv`；`pip install -r requirements.lock.txt`（镜像可换）；缺 ffmpeg 时提示或 `apt-get install -y ffmpeg`（需 sudo） | `venv_path`、`deps_ok` | `REMOTE_DEPS_FAILED`（附 pip 末 20 行） | `.venv/.installed.stamp` 与锁文件 hash 一致则跳过 |
| S5 启动服务 | venv、端口、API Key | 生成 `config/config.yaml`（云端角色）+ `nohup .venv/bin/python -m app.main --host 0.0.0.0 --port <p> > logs/app.log 2>&1 &`，写 pid 文件 | `remote_pid`、`remote_port`、`remote_api_key`（仅内存+本地配置） | `REMOTE_START_FAILED` / `REMOTE_PORT_BUSY` | pid 存活且健康则复用 |
| S6 就绪探测 | 远端或隧道地址 | 经 `SSHTunnel` 或直连访问 `/v1/health`，轮询至 `status=ok && ready=true`，超时 `cloud.readiness_timeout_s` | `topology`（tunnel/direct）、`base_url` | `CLOUD_NOT_READY` | 已 ready 直接通过 |
| S7 写本地配置 | 上述结果 | 事务式写 `config/config.yaml`：`run.mode`、`cloud.base_url`、`cloud.api_key`、`cloud.tunnel.*`；写前备份 `.bak` | `config_written: true` | `CONFIG_WRITE_FAILED` | 目标值与现值相同则跳过 |
| S8 端到端验收 | 本地状态 | 调本地 `/api/tasks` 提交一条 5 字内文本，等待 `completed`，读取产物大小 | `verify_task_id`、`verify_bytes` | `VERIFY_FAILED` | 每次执行 |

状态枚举：`queued | running | succeeded | failed | cancelled`；每步状态：`pending | running | ok | failed | skipped`。

---

## 5A. Agent 决策层：LLM（OpenAI 兼容接口，模型固定）

**定位**：§5 的 8 步状态机是**保底骨架**（不需要任何模型也能跑完）；LLM 是叠加在骨架上的「判断 / 纠错 / 解释」层，负责把「看不懂的报错」变成「下一步做什么」，以及在参数不合适时给出替代方案。二者不可互相替代。

### 5A.1 接口与模型

| 项 | 值 |
| --- | --- |
| 协议 | **OpenAI 兼容** `/chat/completions`（带 `tools` 的 function calling） |
| 默认 base_url | `https://api.deepseek.com/v1`（大陆直连） |
| 默认模型 | `deepseek-chat`（**钉死**）——DeepSeek 官方 API 的稳定别名，始终指向当前最新 chat 模型（现为 V4.1 系列）；官方另有显式版本 ID（形如 `DeepSeek-V4-Pro-0813`）与 `deepseek-reasoner` |
| 其它后端 | 任何 OpenAI 兼容端点只需改 `base_url`：本地 vLLM / Ollama / 通义 / Kimi / 中转站均可 |
| 鉴权 | `agent.llm.api_key`，或环境变量 `QWENTTS_AGENT_KEY`（优先），**不写入日志** |

### 5A.1.1 模型 ID 的写法（避免 400）

DeepSeek 官方 API 的模型标识有两大类：

- **稳定别名**：`deepseek-chat`（对话，随官方升级滚动到最新版本，即当前 V4.1 系列）、`deepseek-reasoner`（推理）；
- **显式版本 ID**：形如 `DeepSeek-V4-Pro-0813`，用于锁死到具体快照。

因此实现规则：

1. 默认写 `deepseek-chat`（不会因为官方换版本而 400）；
2. Agent 启动时调用 `GET {base_url}/models`（OpenAI 兼容端点都提供），把返回的 id 列表缓存并在界面上做成**只读下拉**，用户可从中选显式版本 ID；
3. 若 `agent.llm.model` 不在 `/models` 返回里 → 直接降级为纯状态机并在界面提示「模型 ID 不可用：<id>，可用：<前 5 个>」，**不要**盲发请求浪费 token。

配置片段（新增 `ConfigCfg.agent` 段，全部走既有 `update_runtime` 白名单）：

```yaml
agent:
  llm:
    enabled: true
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat      # 钉死，不在界面暴露成自由输入
    api_key: ""               # 或用 QWENTTS_AGENT_KEY
    temperature: 0            # 0 = 可复现，避免每次给出不同方案
    max_turns: 12             # 单任务最多 12 轮工具调用
    timeout_s: 60
```

### 5A.2 工具集（白名单，模型只能调这些）

| 工具 | 语义 | 安全约束 |
| --- | --- | --- |
| `ssh_probe(target)` | 复用 S1，返回连通性/指纹 | 不暴露密码给模型 |
| `remote_exec(cmd_id, args)` | 执行**预注册命令**（不是自由 shell） | 只有 `env_probe / install_pkg / start_service / stop_service / tail_log / check_port` 等注册项；参数白名单校验 |
| `http_probe(url)` | 探测 `/v1/health` | 只允许本地或已登记的远端地址 |
| `run_step(step_id)` | 触发 §5 中的某一步（幂等） | 只允许「下一步」或「重试当前步」 |
| `ask_user(question)` | 需要用户决策时中断并提问 | 一次任务最多 2 次 |

**硬规则**：模型输出**永远不会**被直接当作 shell 命令执行。所有动作都要落到 `cmd_id + 参数` 的注册表条目上；参数过白名单后才由本地执行器拼装命令。凭据只在执行器内部注入，模型侧只看到 `credential_present: true`。

### 5A.3 循环

```
每轮：
  输入 = 用户目标 + 当前步骤状态 + 最近 20 行脱敏日志 + 已收集证据 + 允许的工具清单
  模型 → 选择一个工具调用（或 ask_user / 直接回答）
  执行器 → 校验参数 → 执行 → 结果脱敏 → 回填
直到：步骤全部成功 / 达到 max_turns / 模型给出「无法自动修复」
```

- `temperature=0`；单任务 token 预算约 30k；网络/5xx 重试 2 次（指数退避）。
- 全程审计：`data/cloud-agent/audit.log`，记录 `turn / tool / args(脱敏) / result_size / duration_ms`。

### 5A.4 隐私边界

上传给 LLM 的只有：操作系统与 Python 版本、GPU 名称与显存、磁盘余量、端口占用、日志尾部、错误码、步骤状态。**不上传**：源码、模型文件、任何凭据、完整文件内容。

### 5A.5 降级（必须有）

以下任一情况自动回退到 §5 纯状态机，界面显示「AI 增强不可用（原因）」，任务**仍要能跑完**：

- `agent.llm.enabled=false` 或未配置 `api_key`；
- 请求超时 / 401 / 429 连续 2 次；
- 模型返回非法工具名或参数校验失败；
- 用户选择「不用 AI，直接按标准流程跑」。

### 5A.6 新增验收

| 编号 | 步骤 | 预期 |
| --- | --- | --- |
| AC11 | 把 `agent.llm.api_key` 改成无效值后接入 | 仍然成功（自动降级），界面标注「AI 增强不可用（401）」 |
| AC12 | 远端 8443 被占用后接入 | LLM 判断出端口冲突并建议改 18444，改后成功；日志显示调用了 `remote_exec(check_port)` |
| AC13 | 检查 `audit.log` 与 `logs/app.log` | 无密码、无 api_key、无源码片段 |
| AC14 | 把 `base_url` 指向本地 Ollama（OpenAI 兼容） | 任务同样可跑（模型名改 `qwen2.5:7b` 之类），证明未绑定 DeepSeek |

---

## 6. 接口规格

### 6.1 REST

| 方法 | 路径 | 说明 | 返回 |
| --- | --- | --- | --- |
| `POST` | `/api/cloud/agent/connect` | 创建接入任务 | `202 {job_id}` |
| `GET` | `/api/cloud/agent/jobs` | 任务列表（含历史） | `{items:[JobSummary]}` |
| `GET` | `/api/cloud/agent/jobs/{job_id}` | 任务详情（步骤、日志尾部、失败原因） | `Job` |
| `POST` | `/api/cloud/agent/jobs/{job_id}/resume` | 从第一个未成功步骤续做 | `202 {job_id}` |
| `POST` | `/api/cloud/agent/jobs/{job_id}/cancel` | 取消（设置停止标志，S3/S4 检查点生效） | `200 {status}` |
| `DELETE` | `/api/cloud/agent/jobs/{job_id}` | 删除任务记录（不删远端） | `204` |
| `POST` | `/api/cloud/agent/hostkey/confirm` | 首次指纹确认 | `200 {}` |
| `GET` | `/api/cloud/agent/preflight/{job_id}` | 单独跑 S0–S2（只体检不改远端） | `Env` |

请求体 `ConnectRequest`：

```json
{
  "host": "1.2.3.4",
  "ssh_port": 22,
  "username": "root",
  "auth": { "type": "password", "password": "***" },
  "options": {
    "remote_dir": "~/qwentts",
    "remote_port": 8443,
    "pip_index": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "install_ffmpeg": false,
    "verify_after": true
  }
}
```

> `auth.type` 为 `password` 或 `key`（`key` 时给 `key_path`，可带 `passphrase`）。密码**只存在于请求体与内存**，响应/日志/状态文件一律脱敏。

### 6.2 事件（复用 `/ws/events`，topic = `cloud-agent`）

| event | data |
| --- | --- |
| `step` | `{job_id, step, state, detail}` |
| `progress` | `{job_id, step, percent, message}` |
| `log` | `{job_id, step, line}`（脱敏后的远端输出，最多 200 行滚动） |
| `need_input` | `{job_id, kind:"hostkey", fingerprint}` |
| `finished` | `{job_id, status, step}` |

### 6.3 错误码（新增到 `errors.py` 与教程页速查表）

`SSH_UNREACHABLE`、`SSH_AUTH_FAILED`、`SSH_HOSTKEY_CHANGED`、`REMOTE_PYTHON_TOO_OLD`、`REMOTE_NO_GPU`、`REMOTE_DISK_LOW`、`REMOTE_PORT_BUSY`、`UPLOAD_FAILED`、`REMOTE_VERSION_MISMATCH`、`REMOTE_DEPS_FAILED`、`REMOTE_START_FAILED`、`CLOUD_NOT_READY`、`CONFIG_WRITE_FAILED`、`VERIFY_FAILED`。

每个错误码必须给出 `next_actions: string[]`（1–3 条，动词开头，可执行）。

---

## 7. 安全规格（硬约束）

1. **密码不落盘**：SSH 密码只存在任务对象内存中；`data/cloud-agent/jobs/*.json` 不含 `password`/`passphrase` 字段（序列化时白名单字段，黑名单过滤）。
2. **日志脱敏**：Agent 写日志前统一过 `redact()`，命中 `password|passphrase|api_key|X-API-Key|-----BEGIN` 的片段替换为 `***`；远端命令里传的密码走 stdin，不进命令行（避免 `ps` 泄漏）。
3. **主机指纹**：首次连接把 host key 指纹写入 `data/cloud-agent/known_hosts`，再次连接不一致时中止（`SSH_HOSTKEY_CHANGED`），需显式确认。高级开关「跳过指纹校验」默认关闭；开启后仍记录 `WARN skipped_hostkey_check <fingerprint>` 到审计日志。
4. **本地 API Key 落盘**：与现有 `CloudCfg.api_key` 行为一致（明文在 `config/config.yaml`，该文件本就不随包分发），但 UI 输入框不回显、接口返回 `api_key_set: true/false`。
5. **权限最小化**：只有用户勾选「需要安装 ffmpeg」时才尝试 `sudo`（已确认）；其余步骤一律以普通用户执行，禁止 `sudo` 全流程。
6. **远端不写本地路径**：远端只写 `remote_dir` 下内容；删除/覆盖前先 `mv` 到 `<remote_dir>/.bak-<ts>`。

---

## 8. 数据规格

`data/cloud-agent/jobs/<job_id>.json`：

```json
{
  "job_id": "uuid",
  "created_at": 0, "updated_at": 0,
  "target": {"host": "", "ssh_port": 22, "username": "", "auth_type": "password"},
  "options": {},
  "status": "running",
  "current_step": "S4",
  "steps": [{"id": "S3", "state": "ok", "detail": "", "started_at": 0, "ended_at": 0}],
  "env": {}, "remote": {"path": "", "port": 0, "pid": 0},
  "result": {"topology": "tunnel", "base_url": "", "verify_bytes": 0},
  "error": {"code": "", "message": "", "next_actions": []},
  "logs": ["...最多 200 行脱敏输出"]
}
```

保留最近 10 个任务，超出按 `created_at` 淘汰。

---

## 9. 界面规格

位置：**设置页 → 云端连接**分区底部，新增一行「一键接入云主机」按钮 + 最近任务状态摘要。

向导（Modal，4 屏，沿用现有 `Modal` 尺寸与按钮式样）：

1. **连接信息**：host / SSH 端口（默认 22）/ 用户名 / 认证方式（密码 | 私钥路径）/ 高级选项（折叠：远端目录、远端端口、pip 镜像、是否装 ffmpeg、是否接入后验收）。
2. **进度**：8 个步骤竖排，每步左侧状态点（灰/转/绿/红），下方滚动日志最多 8 行；右上角「取消」。
3. **首次指纹确认**（仅当未知主机）：展示指纹与「信任并继续」。
4. **结果**：成功 → 显示拓扑（隧道/直连）、`base_url`、验收音频时长与体积，按钮「试听一次」「关闭」；失败 → 红色卡片：原因 + `next_actions` 列表 + 「重试本步」「查看日志」。

约束：与现有文案风格一致（短句、动宾、不使用「哦/啦」）；不使用新 UI 组件库；窄屏（<720px）下向导改为整页。

---

## 10. 非功能需求

| 项 | 要求 |
| --- | --- |
| 单步超时 | S1 15s；S2 30s；S3 10min；S4 30min；S5 2min；S6 `cloud.readiness_timeout_s`（默认 900s）；S8 10min |
| 并发 | 全局同时只允许 1 个接入任务（互斥锁），第二个请求返回 `409 BUSY` |
| 可取消 | `cancel` 设置停止标志，S3/S4 每个分片检查 |
| 不破坏现有行为 | 未执行 Agent 时，`tunnel.py`/`cloud.py` 路径零改动；S7 写配置前备份 `config.yaml.bak`；写入失败自动回滚 |
| 性能 | Agent 空闲时不占线程（仅在任务执行期占用 1 个工作线程） |
| 兼容 | 无新依赖：paramiko / httpx / tarfile / hashlib 全为现有依赖 |

---

## 11. 验收标准

| 编号 | 步骤 | 预期结果 |
| --- | --- | --- |
| AC1 | 填一台**未做任何准备**的 Linux 云主机（host/port/root/密码），点「开始接入」 | 8 步依次变绿，最终显示「已可用」，`config/config.yaml` 的 `run.mode=cloud`、`cloud.base_url`、`cloud.tunnel.*` 被正确填写 |
| AC2 | 接入完成后在本地工作台合成一句话 | 任务 `completed`，音频可播放，`backend=cloud` |
| AC3 | 接入过程中拔网线/断 Wi-Fi | 任务停在当前步并 `failed`，错误卡片显示 `SSH_UNREACHABLE` + 「检查网络后点重试本步」，不出现 traceback |
| AC4 | 重启本地后端后打开设置页 | 任务状态仍在（停在失败那步），点「继续」从该步续做，已完成的步骤显示 `skipped` |
| AC5 | 对同一台已接入主机再点一次「开始接入」 | S3/S4 显示 `skipped`，总耗时 < 60s，最终仍 `succeeded` |
| AC6 | 填错误密码 | S1 失败，`SSH_AUTH_FAILED`，提示「确认用户名/密码或改用私钥」，日志中无明文密码 |
| AC7 | 远端 8443 被占用 | S2 报 `REMOTE_PORT_BUSY`，向导建议改端口，改 18444 后成功 |
| AC8 | 检查 `data/cloud-agent/jobs/*.json` 与 `logs/app.log` | 无 `password`、无 `api_key` 明文（仅 `***`） |
| AC9 | 取消任务 | 状态变 `cancelled`，远端无残留服务进程（pid 文件已清理） |
| AC10 | 断点续做时远端 venv 已存在 | S4 跳过 pip 全量安装，日志显示「依赖已就绪」 |

---

## 12. 风险与依赖

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 远端无 sudo 且缺 ffmpeg | S4 失败 | 提示手动安装命令；`install_ffmpeg` 默认 false |
| 依赖安装受网络限制 | S4 超时 | 默认清华镜像，失败回退官方；日志给出失败包名 |
| 云端模型缺失 | S5 启动成功但 `ready=false` | S2 检测模型目录，缺失时在结果页给「云端下载」链接（不自动下载） |
| 首次指纹确认打断无人值守 | AC1 不满足 | 已写 `known_hosts` 的主机不再询问；首次可选「跳过校验」高级项（记录告警） |
| 远端 Python 版本过低 | 无法装依赖 | S2 提前拦截并给 `apt install python3.10` 命令 |
| 凭据泄漏 | 合规风险 | §7 全链路脱敏 + 内存态 + 指纹固定 |
