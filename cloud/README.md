# 云端 GPU 部署说明

云端与本地运行**同一份后端代码**：同一个镜像既是「本地程序」，也是「云端推理服务」。
区别只在于角色：

| 角色 | 配置 | 对外接口 |
| --- | --- | --- |
| 本地客户端 | `run.mode: local/cloud/hybrid` | `/api/*` 给前端，`/v1/*` 供自身被调试 |
| 云端推理服务 | `run.mode: local` + `cloud.api_key` | `/v1/engine/*`、`/v1/health`、`/v1/system/shutdown` |

## 1. 准备模型

云端机器的模型目录结构与本地一致（默认 `<repo>/models`）：

```
models/
├── Qwen3-TTS-12Hz-1.7B-Base/          # 声音克隆
├── Qwen3-TTS-12Hz-1.7B-CustomVoice/   # 内置音色 + 指令控制
└── Qwen3-TTS-12Hz-1.7B-VoiceDesign/   # 音色设计
```

下载（支持国内镜像）：

```bash
python scripts/download_models.py --size 1.7B --out /data/models
```

显存粗估（含解码开销）：1.7B 约 4.5-6GB（bf16）。建议 ≥12GB 显存的 GPU（A10 / L4 / 4090 / A100 均可）。

## 2. Docker 部署（推荐）

```bash
cd <repo>
export QWENTTS_API_KEY=$(openssl rand -hex 16)
docker compose -f cloud/docker-compose.yml --env-file <(echo "QWENTTS_API_KEY=$QWENTTS_API_KEY") up -d --build
docker logs -f qwentts-cloud
```

启动后：

```bash
curl -fk https://<云端IP>:8443/v1/health
# {"status":"ok","ready":false,...}      # 尚未加载模型（首次请求会加载）
```

自签证书首次访问会有提示；生产请在前面挂 Nginx/Caddy 使用受信任证书。

## 3. 裸机部署

```bash
sudo apt install -y python3.10 ffmpeg libsndfile1
pip install -r requirements.txt
export QWENTTS__CLOUD__API_KEY=<你的密钥>
export QWENTTS__RUN__MODE=local
./scripts/start.sh --cloud
```

建议用 systemd 托管（示例见 `docs/DEPLOY.md`）。

## 4. 本地如何连云端

在本机 `config/config.yaml`（或设置页）配置：

```yaml
run:
  mode: cloud           # 或 hybrid
cloud:
  base_url: https://<云端IP>:8443
  api_key: "<与云端一致的密钥>"
  verify_tls: false     # 自签证书调试；生产用有效证书时改为 true
```

两种连接方式：

1. **直连**：云端安全组放行 8443（或通过 Nginx 反代 443），客户端直接访问。
2. **SSH 端口转发**（N2，不暴露公网端口）：

```yaml
cloud:
  base_url: https://127.0.0.1:18443   # 逻辑地址，客户端无感知
  tunnel:
    enabled: true
    ssh_host: <云端IP>
    ssh_port: 22
    username: ubuntu
    key_path: ~/.ssh/id_ed25519
    local_port: 18443
    remote_host: 127.0.0.1
    remote_port: 8443
```

启用后客户端实际访问 `127.0.0.1:18443`，由内置隧道（paramiko direct-tcpip）转发到云端 8443。
状态页可看到隧道运行状态与错误信息。

## 5. 成本控制（N5）

```yaml
cloud:
  idle_shutdown_enabled: true
  idle_shutdown_minutes: 15      # 空闲 15 分钟自动停机
  idle_shutdown_url: ""          # 留空则调用云端 /v1/system/shutdown
```

- 空闲判定：无运行中任务且无新请求；
- `idle_shutdown_url` 可指向云厂商停机 API（如 `https://api.example.com/stop`），实现真正关机；
- 云端 `/v1/system/shutdown` 会先卸载模型释放显存，再退出进程（容器 `restart: unless-stopped` 下容器退出即停机）。

## 6. 就绪探测与重试（N6）

- 云端启动后模型按需懒加载；`/v1/health` 的 `ready` 字段表示是否已加载；
- 客户端 `CloudEngine.ensure_ready()` 会以指数退避探测，最长等待 `cloud.readiness_timeout_s`（默认 900s）；
- 任务级重试：`cloud.retries`（默认 3）失败后指数退避重试，云端重启后客户端可在模型就绪后继续；
- 中途断线：已完成的分块已落盘/已推送，重试从失败分块继续。

## 7. 安全清单

- [x] 云端强制 `X-API-Key` 鉴权（未配置密钥时仅允许本机访问）
- [x] HTTPS（本地自签 / 生产受信任证书）
- [x] 上传格式白名单 + 大小上限 + 时长上限
- [x] 下载白名单校验，防路径穿越
- [x] 参考音频上云前的隐私确认
- [ ] 商用前确认 Qwen3-TTS 模型许可证
