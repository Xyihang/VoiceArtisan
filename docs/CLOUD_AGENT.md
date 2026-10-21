# 云主机一键接入 Agent · 使用说明

## 1. 入口

设置 → **云端连接** → 底部「**一键接入云主机**」。

## 2. 你需要准备

- 云主机 IP / 域名、SSH 端口（默认 22）
- 用户名（通常 root）与密码，或私钥文件路径
- 云端需为 Linux + Python 3.9+（推荐 3.10）、有 NVIDIA GPU 与驱动

## 3. 流程（8 步，界面实时显示）

| 步骤 | 内容 | 失败时常见码 |
| --- | --- | --- |
| S0 | 参数校验 | `INVALID_INPUT` |
| S1 | SSH 连通 + 主机指纹固定 | `SSH_UNREACHABLE` / `SSH_AUTH_FAILED` / `SSH_HOSTKEY_CHANGED` |
| S2 | 环境体检（Python/GPU/磁盘/端口/ffmpeg） | `REMOTE_PYTHON_TOO_OLD` / `REMOTE_NO_GPU` / `REMOTE_DISK_LOW` / `REMOTE_PORT_BUSY` |
| S3 | 上传源码包并解包 | `UPLOAD_FAILED` |
| S4 | 建 venv + 装依赖（默认清华镜像） | `REMOTE_DEPS_FAILED` |
| S4B | **自动下载模型**（`scripts/download_models.py --size 1.7B`，ModelScope 国内源，断点续传，约 12.6GB；已存在则跳过） | `REMOTE_MODEL_DOWNLOAD_FAILED` |
| S5 | 生成云端配置并后台启动服务 | `REMOTE_START_FAILED` |
| S6 | 就绪探测 `/v1/health` | `CLOUD_NOT_READY` |
| S7 | 写本地配置（`run.mode=cloud`、`cloud.*`） | `CONFIG_WRITE_FAILED` |
| S8 | 本地按新配置探活 | `VERIFY_FAILED` |

失败卡片会给出 2–3 条可执行建议；若配置了 AI 诊断（见 §5），建议由模型生成。

## 3.1 「真正可用」的判定标准

S6 只有同时满足两条才算通过：

1. `GET /v1/health` 返回 `status=ok` **且** `ready=true`（模型已加载/目录存在，`ready=false` 会提示「请在云端下载模型」）；
2. 用 `POST /v1/tts` 真实合成一句「你好」并拿到音频（HTTP 200/206）。

只满足第 1 条不算成功——避免「服务起来了但一推理就报错」的假成功。

## 3.2 续做

失败卡片上有「续做（跳过已完成步骤）」：重新输入密码后，**已 `ok` 的步骤直接标记 `skipped`**，
从失败那步继续；S3 用源码 sha256 比对跳过重复上传，S4 用 venv 依赖自检跳过重复安装，S5 复用存活 pid。

## 4. 安全

- **SSH 密码只存在内存**，不写状态文件、不进日志；续做需重新输入。
- 主机指纹首次记录到 `data/cloud-agent/known_hosts`，之后不一致直接中止。
- 全部日志过脱敏（`password` / `api_key` / `X-API-Key` → `***`）。
- 云端 API Key 由 Agent 随机生成并写入本地 `cloud.api_key`（与设置页保存行为一致）。

## 5. AI 诊断（可选，默认不用也能跑完）

`config/config.yaml`：

```yaml
agent:
  llm:
    enabled: true
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat      # 官方稳定别名（当前 V4.1 系列）；可换成 /models 返回的显式版本 ID
    api_key: ""               # 或用环境变量 QWENTTS_AGENT_KEY
    temperature: 0
    max_turns: 12
    timeout_s: 60
```

- 任何 OpenAI 兼容端点都能用（vLLM / Ollama / 中转站），改 `base_url` 与 `model` 即可；
- 未配置、401、429、模型 ID 不存在 → **自动降级**，任务照常按标准流程跑完；
- 可通过 `GET /api/cloud/agent/models` 查看当前可用模型与降级原因。

## 5.0 在前端直接填 AI 配置（推荐）

设置 → 云端连接 → 「一键接入云主机」→ 点「**AI 设置（DeepSeek / OpenAI 兼容）**」：

| 字段 | 说明 |
| --- | --- |
| 接口地址 | 默认 `https://api.deepseek.com/v1`，可换中转站 / 本地 Ollama / vLLM |
| 模型 | 默认 `deepseek-chat`；点「拉取模型列表」可从 `GET /models` 选显式版本 ID |
| API 密钥 | 保存后**用 Windows DPAPI 加密**写入 `data/runtime.json`（`api_key_enc`），文件里没有明文；界面只显示 `sk-t***56` |

对应接口：`GET/POST /api/cloud/agent/llm`、`GET /api/cloud/agent/models`。
非 Windows 平台没有系统级加密，此时密钥不落盘，只注入当前进程环境变量（界面会标注）。

## 5.1 AI 自主排障（可选，勾选后才生效）

向导里勾选「AI 自主排障」后，某一步失败时会进入工具循环：

| 工具 | 能做什么 |
| --- | --- |
| `remote_exec` | 只能选预注册 `cmd_id`：`env_probe` / `check_port` / `tail_log` / `disk_free` / `pip_sync` / `start_service` / `stop_service` / `read_remote_file`（路径限制在项目目录、禁止 `..`） |
| `run_step` | 让确定性流程重跑某一步（修好环境后用） |
| `give_up` | 判定修不了，写原因并回退 |

- 模型**不能**自由拼 shell：参数经白名单校验，凭据在执行器内部注入；
- 最多 `agent.llm.max_turns` 轮（默认 12）；
- 全部调用落审计：`data/cloud-agent/audit.log`（`turn / tool / args / result`，已脱敏）；
- 未配 key、401/429、模型 ID 不存在、轮次用尽 → 自动回退标准流程，任务不会卡住；
- 默认关闭（不勾选时行为与之前完全一致，零 token 消耗）。

## 6. 相关接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/cloud/agent/connect` | 发起接入，返回 `{job_id}` |
| GET | `/api/cloud/agent/jobs` | 最近 10 个任务 |
| GET | `/api/cloud/agent/jobs/{id}` | 任务详情（步骤/日志/错误/建议） |
| POST | `/api/cloud/agent/jobs/{id}/resume` | 续做（需重新提交 `auth`） |
| POST | `/api/cloud/agent/jobs/{id}/cancel` | 取消 |
| DELETE | `/api/cloud/agent/jobs/{id}` | 删除记录 |
| GET | `/api/cloud/agent/models` | LLM 可用模型 |

## 7. 验收步骤

1. 填一台未做准备的 Linux GPU 主机 → 点「开始接入」→ 8 步依次变绿，显示「已可用」。
2. 工作台合成一句话 → 任务 `completed`，音频可播放（走云端）。
3. 填错误密码 → S1 失败，提示 `SSH_AUTH_FAILED`，日志无明文密码。
4. 远端 8443 被占用 → S2 报 `REMOTE_PORT_BUSY`，改端口后成功。
5. 关闭 AI（`agent.llm.enabled=false`）重跑 → 仍成功，界面标注 AI 不可用。
6. 检查 `data/cloud-agent/jobs/*.json` 与 `logs/app.log` → 无密码、无 api_key 明文。

## 8. 已知边界

- 云端模型不会自动下载（遵守项目约束）：若 S6 因模型缺失未就绪，按提示在云端执行下载脚本。
- 本次实现：LLM 用于**失败诊断**；完整的多轮工具调用（模型自主选择 `remote_exec` 等）为后续增强项。
