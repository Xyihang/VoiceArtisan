# 部署与运维

## 1. 环境要求

| 项 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11、Ubuntu 20.04+、macOS 13+（本地 GPU 需要 NVIDIA 驱动 + CUDA） |
| Python | 3.10 及以上 |
| Node.js | 18+（仅构建前端时需要） |
| GPU（本地） | 显存 ≥ 8GB 跑 1.7B bf16 更稳；4GB 会触发「显存不足 → 一键切云端」提示 |
| ffmpeg | 可选，导出 mp3 / 解码 m4a 需要 |
| 磁盘 | 1.7B 三件套约 12.6GB；0.6B 两件套约 4.7GB |

> 版本锁定：想复刻已验证环境用 `requirements.lock.txt`（`pip install -r requirements.lock.txt`）；`requirements.txt` 是宽松范围版本。

## 2. 首次启动

```powershell
# Windows
.\scripts\start.ps1
```

```bash
# Linux / macOS
chmod +x scripts/start.sh && ./scripts/start.sh
```

脚本会依次完成：检查 Python → 重定向缓存到 `<项目>/.cache` → 安装缺失依赖（国内镜像）→ 构建前端 →
生成 `config/config.yaml` → 打印启动自检 → 启动 HTTPS 服务并打开浏览器。

常用参数：

```bash
.\scripts\start.ps1 -SelfCheck     # 只跑自检
.\scripts\start.ps1 -NoHttps       # 调试用 HTTP
.\scripts\start.ps1 -Port 9443     # 指定端口
.\scripts\start.ps1 -Cloud         # 以云端推理服务角色启动
```

## 3. 配置说明（config/config.yaml）

优先级：**环境变量 > data/runtime.json（界面修改）> config/config.yaml > 代码默认值**。
全部路径支持相对（相对项目根）与绝对路径；密钥、地址、模型路径均无硬编码。

| 配置段 | 关键项 | 说明 |
| --- | --- | --- |
| `server` | `host`/`port`/`https`/`open_browser` | 监听地址、端口、是否 HTTPS |
| `storage` | `data_dir`/`models_dir`/`cache_dir` | 数据、模型、缓存目录（默认项目内，避免写 C 盘） |
| `run` | `mode`/`hybrid_long_text_chars`/`asr_prefer` | 运行模式与混合路由阈值 |
| `local` | `device`/`dtype`/`models`/`fallback_models`/`vram_reserve_gb`/`low_vram`/`allow_small_fallback`/`warmup_on_start` | 本地模型与显存策略 |
| `cloud` | `base_url`/`api_key`/`models`/`tunnel`/`idle_shutdown_*`/`readiness_timeout_s` | 云端连接、隧道、成本控制 |
| `asr` | `engine`/`local`/`cloud` | 转写引擎 |
| `limits` | `max_upload_mb`/`max_ref_audio_seconds`/`max_text_chars` | 上传与文本限制 |
| `defaults` | `speed`/`pitch`/`temperature`/`top_p`/`top_k`/`steps`/`output_format` | 新建任务默认参数 |
| `mock` | `enabled` | 仅联调用的占位引擎（界面显式标注） |

环境变量示例：

```bash
QWENTTS__RUN__MODE=cloud
QWENTTS__CLOUD__BASE_URL=https://1.2.3.4:8443
QWENTTS__CLOUD__API_KEY=sk-xxx
QWENTTS__SERVER__PORT=9443
```

## 4. HTTPS 与证书

- 首次启动自动生成 10 年期自签证书（`data/certs/cert.pem`、`key.pem`），SAN 覆盖 `localhost`、主机名与所有本机 IP；
- 浏览器提示「不是私密连接」属正常：点击「高级 → 继续前往」，或先访问 `/api/health` 完成一次信任；
- 需要完全消除提示：把 `data/certs/cert.pem` 导入系统「受信任的根证书颁发机构」（Windows `certlm.msc` / Linux `/usr/local/share/ca-certificates` + `update-ca-certificates`）；
- 生产环境建议用 Nginx/Caddy 反向代理 + 有效证书（Let's Encrypt）。

## 5. Linux systemd 托管

```ini
# /etc/systemd/system/qwentts.service
[Unit]
Description=Qwen3-TTS 语音生成系统
After=network-online.target

[Service]
Type=simple
User=qwentts
WorkingDirectory=/opt/qwentts/backend
Environment=PYTHONPATH=/opt/qwentts/backend
Environment=QWENTTS_CONFIG=/opt/qwentts/config/config.yaml
Environment=QWENTTS__SERVER__OPEN_BROWSER=false
ExecStart=/usr/bin/python3 -m app.main --no-browser
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now qwentts
sudo journalctl -u qwentts -f
```

## 6. 模型获取

项目**不会自动下载模型**。本地默认使用 **1.7B 三件套**（CustomVoice / Base / VoiceDesign），
国内直连链接、目录结构与校验方式见 **[MODELS.md](MODELS.md)**。

```bash
python scripts/download_models.py --links      # 打印国内可访问链接与目标目录
python scripts/download_models.py              # 可选：脚本从 ModelScope 直连下载（约 12.6GB）
cd backend && python -m app.main --selfcheck   # 校验模型目录
```

- 三个目录缺任何一个，对应能力会报 `MODEL_MISSING` 并给出修复指引；
- 语音转文字（本地 faster-whisper）首次使用时自动下载到 `models/faster-whisper/`（走 `HF_ENDPOINT=https://hf-mirror.com`，国内可用）；
- 0.6B 仅在 `local.allow_small_fallback: true` 时作为**显式标注**的回退，默认关闭。

## 7. 运行期数据与备份

```
data/
├─ db/app.db            # 任务、音色、资产元数据（SQLite）
├─ uploads/             # 上传的参考音频与待转写音频
├─ outputs/<task_id>/   # 生成结果（wav/mp3/txt）
├─ voices/              # 自定义音色参考音频
├─ logs/app.log         # 运行日志（8MB × 5 轮转）
├─ certs/               # 自签证书
└─ runtime.json         # 界面修改的运行期配置覆盖
```

备份：直接复制 `data/`（含 `db/app.db` 与 `outputs/`）即可；恢复后任务与结果不丢失。

## 8. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 启动即报「端口被占用」 | 端口冲突 | 改 `server.port` 或结束占用进程（`netstat -ano \| findstr :8443`） |
| 自检报「缺少模型目录」 | 未下载模型 | 按 [MODELS.md](MODELS.md) 下载，或改 `local.models.*` |
| 任务失败 `VRAM_INSUFFICIENT` | 独显被其它程序占用，1.7B 无法整模型加载 | 把主显示/浏览器切到核显；或界面「一键切换到云端」；或设 `local.device: cpu` |
| 开启 `local.low_vram` 后报 `Tensor.item() ... meta tensors` | qwen-tts 0.1.1 的生成循环不兼容 accelerate offload | 保持 `low_vram.enabled: false`，用整模型加载（4GB 卡 fp16 即可） |
| 采样报 `inf/nan` | fp16 数值不稳定 | 把 `local.dtype` 改为 `bfloat16` |
| CPU 模式报「可用内存 X GB 不足」 | 内存不足（每个 1.7B 约需 5-6GB） | 关闭其它程序；系统会自动卸载其它已加载模型 |
| 任务失败 `MODEL_LOAD_FAILED` | 模型文件不完整 / CUDA 环境异常 | 状态页看日志；重下模型；`python scripts/run_infer.py --dry-run` 定位 |
| 云端 `CLOUD_UNREACHABLE` | 地址/网络/隧道 | 检查 `cloud.base_url`、安全组、「状态与日志」页隧道状态 |
| 云端 `CLOUD_NOT_READY` 超时 | 模型加载慢 | 调大 `cloud.readiness_timeout_s`；看云端 `/v1/health` |
| 云端 401 | 密钥不一致 | 核对 `cloud.api_key` 与云端 `QWENTTS__CLOUD__API_KEY` |
| mp3 导出失败 | 无 ffmpeg | 安装 ffmpeg 或改用 wav |
| 浏览器点「下载」没反应/被拦截 | 自签证书页面里 Chrome/Safari 会拦截导航式下载（"已阻止不安全的下载"） | 前端已改为 fetch + Blob 触发下载，不会触发该拦截；仍异常时可在下载图标上右键「链接另存为」，或信任证书后重试 |
| 浏览器录音不可用 | 非安全上下文 | 使用 HTTPS（自签也可以）访问 |
| 界面显示「服务离线」 | 后端未启动/证书未信任 | 启动后端；信任证书后刷新 |

单独定位推理：

```bash
python scripts/run_infer.py --kind custom_voice --text "测试一下" --dry-run   # 只加载模型
python scripts/run_infer.py --kind clone --text "克隆测试" --ref-audio ref.wav
python scripts/run_infer.py --kind voice_design --text "设计测试" --design "年轻女性，温柔"
```

接口冒烟与 UI 检查：

```bash
python scripts/smoke_test.py --base https://localhost:8443
python scripts/ui_check.py --base https://localhost:8443
```

## 9. 性能与显存

- 首次加载模型较慢（1.7B 约 20-60s），界面显示「等待首包」计时；加载后复用；
- 每次生成结束自动 `torch.cuda.empty_cache()` 回收缓存块；`local.unload_after_idle_seconds` 空闲后可整体卸载；
- 流式采用句级分块（首块更小），首包更快；`params.chunk_chars` 可调（越小首包越快、分块开销越大）；
- 首包目标 ≤ 1.5s（本地）/ ≤ 1.5s（云端含网络），实测见 `docs/BENCHMARK.md`。

## 10. 安全清单

- [x] 云端强制 `X-API-Key` 鉴权（未配置密钥时仅允许本机访问）
- [x] HTTPS（自签自动生成 / 生产受信任证书）
- [x] 上传：格式白名单 + 大小上限 + 时长上限
- [x] 下载：任务白名单 + `resolve()` 路径穿越校验
- [x] 参考音频上云前的隐私确认（工作台克隆页）
- [x] 日志不输出密钥；设置接口返回脱敏密钥
- [ ] 商用前确认模型许可证与音色授权


---

## 11. 端口与进程清单

| 端口 | 用途 | 是否对外 |
| --- | --- | --- |
| 8443 | 本地 HTTPS 服务（前端 + `/api` + `/ws` + `/v1`） | 一般仅本机；云端需放行或走隧道 |
| 18443 | SSH 隧道本地映射端口（`cloud.tunnel.local_port`，可改） | 仅 127.0.0.1 |
| 5173 | `npm run dev` 前端开发服务器（仅开发用） | 仅本机 |

进程只有一个：`python -m app.main`（内含 API 服务 + 任务执行器线程 + 可选 SSH 隧道线程）。
SQLite 是内嵌库，不需要单独起数据库。

## 12. Windows 服务（NSSM）

Linux 用上文 systemd；Windows 想开机自启、后台常驻可以用 NSSM：

```powershell
# 安装 NSSM 后
nssm install QWENTTS "C:\Users\<你>\AppData\Local\Programs\Python\Python310\python.exe" "-m app.main --no-browser"
nssm set QWENTTS AppDirectory "E:\trae_projects\QWENTTS\backend"
nssm set QWENTTS AppEnvironmentExtra "PYTHONPATH=E:\trae_projects\QWENTTS\backend" "QWENTTS_CONFIG=E:\trae_projects\QWENTTS\config\config.yaml"
nssm set QWENTTS AppStdout "E:\trae_projects\QWENTTS\data\logs\service.out.log"
nssm set QWENTTS AppStderr "E:\trae_projects\QWENTTS\data\logs\service.err.log"
nssm start QWENTTS
```

或者最省事：把 `scripts\start.ps1` 的快捷方式放进「启动」文件夹（`shell:startup`）。

## 13. 反向代理（Nginx / Caddy）

需要域名 + 正式证书时，把 8443 藏在反代后面（本地或云端都适用）：

```nginx
server {
    listen 443 ssl http2;
    server_name tts.example.com;

    ssl_certificate     /etc/letsencrypt/live/tts.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tts.example.com/privkey.pem;

    client_max_body_size 60m;      # 与 limits.max_upload_mb 对齐

    location / {
        proxy_pass https://127.0.0.1:8443;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # WebSocket（任务状态与流式音频）
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;   # 长任务别被反代掐断
    }
}
```

Caddy 更简单：

```
tts.example.com {
    reverse_proxy 127.0.0.1:8443 {
        transport http { tls_insecure_skip_verify }
    }
    request_body { max_size 60MB }
}
```

配好后把 `server.host` 改成 `127.0.0.1`（只监听本机，由反代对外），并把客户端 `cloud.verify_tls` 改成 `true`。

## 14. 升级与回滚

```bash
# 1) 先备份（源码 + 数据 + 配置）
powershell: Compress-Archive -Path backend,frontend,scripts,docs,cloud,config,data -DestinationPath backup-$(Get-Date -f yyyyMMdd).zip
linux:      tar czf backup-$(date +%F).tgz backend frontend/src scripts docs cloud config data

# 2) 更新代码（git pull 或解压新版源码，注意保留 config/ 与 data/）
# 3) 装依赖 + 重建前端
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
cd frontend && npm install --registry https://registry.npmmirror.com && npm run build && cd ..

# 4) 重启并自检
cd backend && python -m app.main --selfcheck
```

回滚：停服务 → 换回旧代码目录 → 恢复 `data/` 与 `config/` → 重启。
数据库是 SQLite 单文件（`data/db/app.db`），升级不会自动改表结构；跨大版本升级前务必先备份该文件。

## 15. 相关文档

- 首次部署 → [QUICKSTART.md](QUICKSTART.md)
- 模型下载 → [MODELS.md](MODELS.md)
- 全部配置项 → [CONFIG.md](CONFIG.md)
- 接云 GPU → [CLOUD_SETUP.md](CLOUD_SETUP.md)
- 报错排查 → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- 接口对接 → [API.md](API.md)
