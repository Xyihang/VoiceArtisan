# 云端接入与部署教程（手把手版）

> 目标：**30 分钟内**把一台 AutoDL（或同类平台）的 GPU 机器接成你的「云端推理后端」，本地界面照常用，差显卡时自动走云端。
>
> 本文是**操作手册**（按顺序照着做）；原理与三种方案对比见 [CLOUD_SETUP.md](CLOUD_SETUP.md)。
> 所有命令、报错、数值都来自本机（Windows 11 + RTX 3050 4GB）对接 **AutoDL RTX 3090 / 24GB** 的实测记录。

---

## 0. 先看懂三个角色

```
┌─────────────┐   SSH 隧道（本机 18443 → 云端 8443）   ┌──────────────────┐
│  本地浏览器  │ ←──────────── 本地后端 8443 ─────────→ │ 云端后端 8443     │
│  界面/任务   │            路由/回退判断               │ 真实 GPU 推理     │
└─────────────┘                                       └──────────────────┘
```

| 角色 | 是什么 | 在哪 |
| --- | --- | --- |
| **云端推理服务** | 同一套后端，带 `--cloud` 启动，只负责合成 | 云主机 `/root/autodl-tmp/QWENTTS` |
| **本地后端** | 你在界面里操作的那个，负责排队、路由、存储 | 本机 `https://localhost:8443` |
| **SSH 隧道** | 把云端 8443 映射到本地 18443，不暴露公网 | 本地后端内置（paramiko），设置页可配置 |

关键约定：
- 云端只跑模型，**不存你的任务记录**（都在本地 `data/`）；
- 云端与本地用同一把 **API Key**（`QWENTTS__CLOUD__API_KEY`）；
- 云端模型放**数据盘**（AutoDL 的 `/root/autodl-tmp`），系统盘只有 30G，撑不住 13G 模型。

---

## 1. 选镜像（决定后面顺不顺）

租机器时的三列选择，按这个顺序挑：

| 列 | 首选 | 次选 | 绝对不要 |
| --- | --- | --- | --- |
| 框架 | **PyTorch 2.4.x** | PyTorch 2.8.x（cu128） | — |
| Python | **3.10** | 3.11 / 3.12（实测 3.12.3 可用） | 3.8 |
| CUDA | **12.1** | 12.4 / 12.6 / 12.8 | **11.8** |

**为什么不能选 CUDA 11.8**：那一列代表驱动支持上限（≈520~529），而 PyTorch 2.8 的官方 wheel 是 cu126/cu128，需要驱动 ≥570；选了 11.8 会出现「装完 `torch.cuda.is_available()` 永远是 False」。

**不建议用社区镜像**（`Qwen3-TTS-ALL-WEB`、`index-tts2.0`、`ChatTTS`、`F5-TTS-webui` 等）：
它们自带 gradio 会抢端口、依赖版本被作者锁死、和你自己的项目冲突。本项目的 UI 本身就是界面，不需要再套一层 webui。

> 唯一例外：如果你只想省掉 12.6GB 模型下载，可考虑 `Qwen3-TTS-ALL-WEB`，但要自己 `find` 权重路径，且换镜像会**重建系统盘**（数据盘 `/root/autodl-tmp` 保留）。

### 开机前自检（无卡模式也能做）

租机器时可以先用**无卡模式**（￥0.1/h）把环境准备好，下模型、装依赖都不需要 GPU：

```bash
nvidia-smi                # 无卡模式下报错是正常的
python -V                 # 可能不存在，见下面「PATH」坑
```

---

## 2. 无卡模式：把东西先备好（约 20~40 分钟）

### 2.1 连上机器

```bash
ssh -p <端口> root@<host>
# AutoDL 每台机器的端口/密码都不同，克隆机也会变
```

### 2.2 上传代码（**不要用 Windows 的 zip**）

Windows `Compress-Archive` 用反斜杠当分隔符，Linux `unzip` 会解出一堆平铺的怪文件名。用 Python 重打包：

```python
# 在你自己的机器上执行
import zipfile, pathlib
root = pathlib.Path(__file__).resolve().parents[1]  # 换成你自己的声匠项目根目录
with zipfile.ZipFile("qwentts-linux.zip", "w", zipfile.ZIP_DEFLATED) as zf:
    for p in root.rglob("*"):
        if p.is_file() and not {"node_modules", "__pycache__", "dist"} & set(p.parts):
            zf.write(p, p.relative_to(root).as_posix())   # ← 正斜杠是关键
```

然后上传并解压：

```bash
# 本地
scp -P <端口> qwentts-linux.zip root@<host>:/root/autodl-tmp/
# 云端
cd /root/autodl-tmp && mkdir -p QWENTTS && unzip -q qwentts-linux.zip -d QWENTTS
chmod +x QWENTTS/start_cloud.sh QWENTTS/scripts/*.sh      # zip 会丢执行位
```

### 2.3 装依赖

```bash
export PATH=/root/miniconda3/bin:$PATH          # ← 镜像 PATH 里没有 python
cd /root/autodl-tmp/QWENTTS
$PYTHON -m pip install -r requirements.lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 用 `requirements.lock.txt`（云端版，torch 两行是注释掉的，沿用镜像自带的 torch），
> **不要用** `requirements.pins.txt`（那是本机主环境 `torch==2.4.1+cu121` 的锁定清单，会跟镜像打架）。

装完立刻验一遍：

```bash
python -c "import torch, torchaudio; print(torch.__version__, torchaudio.__version__)"
python -c "import qwen_tts, transformers, sqlalchemy, paramiko; print('deps ok')"
```

### 2.4 下模型（13GB，放数据盘）

```bash
/root/miniconda3/bin/python scripts/download_models.py --size 1.7B --out /root/autodl-tmp/QWENTTS/models
```

实测速度 ~17 MB/s，约 12 分钟；中断就重跑，脚本自带续传。

**四条独立证据确认「真的下完了」**（缺一条都别信）：

```bash
# ① 下载进程已退出
ps -eo pid,cmd | grep download_models | grep -v grep
# ② 日志尾部有 [OK] 三行
tail -6 /root/autodl-tmp/models.log
# ③ 磁盘约 13G（三个模型各 4.3G）
du -sh /root/autodl-tmp/QWENTTS/models
# ④ 脚本自带校验全过
python scripts/download_models.py --size 1.7B --out models --verify
```

---

## 3. 有卡了：一条命令起服务

### 3.1 启动脚本（`start_cloud.sh`，已随项目提供）

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

export PATH=/root/miniconda3/bin:$PATH              # 镜像 PATH 坑
export QWENTTS__RUN__MODE=local                     # 云端用本机 GPU
export QWENTTS__SERVER__HOST=0.0.0.0
export QWENTTS__SERVER__PORT=8443
export QWENTTS__SERVER__OPEN_BROWSER=false
export QWENTTS__CLOUD__API_KEY=sk-你的密钥           # ← 必须与本地设置一致
export QWENTTS__STORAGE__DATA_DIR=/root/autodl-tmp/QWENTTS/data
export QWENTTS__STORAGE__MODELS_DIR=/root/autodl-tmp/QWENTTS/models
export QWENTTS__STORAGE__CACHE_DIR=/root/autodl-tmp/QWENTTS/.cache
export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME=/root/autodl-tmp/QWENTTS/.cache/hf

exec bash ./scripts/start.sh --cloud --no-build "$@"
```

`--no-build` 是因为云端不需要前端产物（界面在本地），省掉 node 构建。

### 3.2 启动与验证

```bash
cd /root/autodl-tmp/QWENTTS && ./start_cloud.sh
# 后台跑：
# setsid nohup ./start_cloud.sh > /root/autodl-tmp/service.log 2>&1 < /dev/null &

curl -sk https://127.0.0.1:8443/v1/health
```

期望返回（实测）：

```json
{"status":"ok","ready":true,"loaded":["custom_voice"],"models_present":["custom_voice","base","voice_design"],
 "model":"Qwen3-TTS-12Hz-1.7B-CustomVoice","device":"NVIDIA GeForce RTX 3090",
 "vram_free_gb":19.03,"vram_total_gb":23.56,"vram_allocated_mb":3981,"mode":"local"}
```

字段含义：`ready` 代表能接单（模型目录在即可，首个请求懒加载）；`vram_allocated_mb` 是已加载模型占用。

### 3.3 云端常用环境变量

| 变量 | 作用 | 建议值 |
| --- | --- | --- |
| `QWENTTS__CLOUD__API_KEY` | 云端鉴权；本地请求必须带同一把 | 自己生成一串 `sk-...` |
| `QWENTTS__RUN__MODE` | 云端固定 `local`（用本机 GPU） | `local` |
| `QWENTTS__STORAGE__*` | 数据/模型/缓存目录 | 指向数据盘 |
| `QWENTTS__LOCAL__VRAM_RESERVE_GB` | 显存预留 | `0.5`（24G 卡无所谓） |
| `HF_ENDPOINT` | HF 镜像（ASR whisper 下载用） | `https://hf-mirror.com` |

---

## 4. 网络接入：三选一

| 方式 | 适用 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **A. SSH 隧道（推荐）** | 全部场景 | 不暴露公网、不用改防火墙 | 本地要跑一个隧道（后端内置） |
| B. 公网直连 | 云厂商给你公网 IP + 端口 | 简单 | 必须开 8443、必须强 API Key + TLS |
| C. frp / 反代 | 自建机房、内网机器 | 可复用 | 配置多一层 |

### A. SSH 隧道（本文默认）

本地后端自带（paramiko 实现，不依赖系统 `ssh`）。设置页填写：

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| 启用隧道 | 开 | — |
| SSH 主机 | `connect.nmb2.seetacloud.com` | 平台给的那个 |
| SSH 端口 | `42049` | **克隆机会变** |
| 用户名 | `root` | — |
| 密码 / 私钥 | 平台密码 | 二选一 |
| 本地映射端口 | `18443` | 随便选空闲端口 |
| 远端地址 / 端口 | `127.0.0.1` / `8443` | 云端服务监听处 |
| 云端 Base URL | `https://127.0.0.1:18443` | **填隧道后的本地地址** |

**改完必须重启本地后端**，否则隧道服务仍读旧配置（这是实测踩过的坑）。

### 关于平台自带的「SSH 隧道工具」

AutoDL 客户端那个工具是**通用端口转发**：
「代理到本地端口 6006 / 代理到远程端口 1080」= 本地 6006 → 云端 1080。
如果你想用它代替内置隧道，就把填法改成 **代理到本地端口 `18443` / 代理到远程端口 `8443`**，
同时在设置里**关闭「启用隧道」**（避免两个隧道抢同一个本地端口）。

### B. 公网直连（不推荐但可行）

```bash
# 云端
export QWENTTS__CLOUD__API_KEY=sk-强密钥
./start_cloud.sh
# 平台安全组放行 8443；本地设置里 base_url = https://<公网IP>:8443（自签证书需关掉「校验 TLS」）
```

---

## 5. 本地配置（两种方式）

### 方式一：界面（推荐）

设置 → **云端连接**：

1. `运行模式` 选 `hybrid`（本地优先、显存不够自动切云端）或 `仅云端`；
2. `服务地址` = `https://127.0.0.1:18443`（走隧道时）；
3. `API 密钥` = 云端那把；
4. 关掉 `校验 TLS 证书`（自签证书）；
5. 往下填 SSH 隧道参数（见上表）；
6. 点 **测试连接** → 出现「连接成功（延迟 xx ms）」。

### 方式二：API（脚本化）

```bash
curl -k -X PUT https://127.0.0.1:8443/api/settings \
  -H 'Content-Type: application/json' \
  -d '{
    "run": {"mode": "hybrid"},
    "cloud": {
      "base_url": "https://127.0.0.1:18443",
      "api_key": "sk-你的密钥",
      "verify_tls": false,
      "tunnel": {
        "enabled": true, "ssh_host": "connect.nmb2.seetacloud.com", "ssh_port": 42049,
        "username": "root", "password": "平台密码", "local_port": 18443,
        "remote_host": "127.0.0.1", "remote_port": 8443
      }
    }
  }'
```

---

## 6. 验收：四条硬证据

```bash
# ① 隧道在监听
netstat -ano | findstr 18443

# ② 状态接口里 tunnel.running = true
curl -k https://localhost:8443/api/system/status

# ③ 跑一个任务，看 backend 字段
curl -k -X POST https://localhost:8443/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"type":"tts","text":"云端连通性测试","voice_id":"<音色id>","mode":"cloud"}'

# ④ 任务详情里 "backend": "cloud"，并能下载到 wav
curl -k https://localhost:8443/api/tasks/<id>
curl -k https://localhost:8443/api/tasks/<id>/download -o out.wav
```

实测参考值：149 字 → 12 秒完成、wav 约 **272 KB**、RTF ≈ 0.5（3090）。

> ⚠️ `mode` 字段写 `cloud` 只是**首选**云端；如果隧道没通，会静默回落到本地（日志会出现
> `primary=cloud fallback=local`）。**判断是否真走云端，只看任务详情的 `backend` 字段。**

---

## 7. 一键关机 / 停止服务（省钱）

设置 → 云端连接 → 红色按钮 **「一键关机」**，弹窗里两个动作：

| 动作 | 后端行为 | 结果 |
| --- | --- | --- |
| **关机（省费用）** | SSH 执行 `nohup bash -c 'sleep 1; shutdown -h now \|\| poweroff \|\| halt'` | 实例几秒内离线，平台停止计费；**下次用要去控制台点「开机」** |
| 仅停止服务（不关机） | SSH 执行 `pkill -f "[a]pp.main"` | 显存释放、服务停，实例仍在线（继续计费） |

底层接口：

```bash
curl -k -X POST https://127.0.0.1:8443/api/cloud/stop \
  -H 'Content-Type: application/json' -d '{"action":"shutdown","stop_tunnel":true}'
```

返回示例：

```json
{"ok":true,"action":"shutdown","shutting_down":true,"port_closed":true,
 "message":"已下发关机命令：云端实例会在几秒内断开（平台将停止计费），需要时到平台控制台点「开机」恢复"}
```

**实测结论**：在 AutoDL 容器内 `shutdown` 会**立刻**关机（不会等 `+2` 分钟的延迟），
所以脚本里用 `sleep 1` 让 SSH 命令先返回。关机后 SSH 端口直接 `ConnectionRefused`，属正常现象。
云端不可达时会返回可读错误：

```json
{"code":"CLOUD_UNAVAILABLE","detail":"连不上云端 root@host:42049（NoValidConnectionsError）：实例可能已关机、SSH 端口变了，或网络不通"}
```

---

## 8. 故障速查（按现象查）

| 现象 | 根因 | 修法 |
| --- | --- | --- |
| `python: command not found` | 镜像 PATH 没有 miniconda | `export PATH=/root/miniconda3/bin:$PATH` |
| `Permission denied`（脚本） | zip 丢了执行位 | `chmod +x start_cloud.sh scripts/*.sh` |
| 解压后目录全乱、全平铺 | Windows zip 用反斜杠 | 用 Python `zipfile` 写 POSIX 路径重打包 |
| `AttributeError: module 'openssl' has no attribute 'ciphers'` | 镜像里残留旧的 `_rust.cpython-312-*.so`，顶掉了新的 `_rust.abi3.so` | `rm -rf site-packages/cryptography*` 后 `--force-reinstall cryptography==44.0.1` |
| torch 说 CUDA 不可用 | 镜像 CUDA/驱动与 wheel 不匹配（常见于选 11.8） | 换镜像或 `pip install torch --index-url .../cu121` |
| `libcudart.so.13: cannot open shared object file` | torchaudio 版本与 torch 不一致 | `pip install --no-deps --force-reinstall torchaudio==<与torch同版本> --index-url .../cu<ver>` |
| `ModuleNotFoundError: No module named 'sqlalchemy'` | `requirements.lock.txt` 旧版漏了它 | 已在新版补上；老包手动 `pip install SQLAlchemy==2.0.52` |
| pip 报 `UnicodeDecodeError: 'gbk' codec ...` | 锁文件里有中文注释，pip 用 GBK 读 | 锁文件必须纯 ASCII（`locks/*.txt` 已全部转为 ASCII） |
| pip 报 `No matching distribution found for voxcpm==2.0.3` | 清华镜像没有该包 | 换官方源；程序会自动换源并标注「镜像缺包」 |
| pip 报 `ResolutionImpossible` | 引擎要求的 torch 与主环境冲突 | 该引擎的 `locks/<id>.txt` 里钉它自己的 torch，只装进它自己的 venv |
| 任务全跑在本地（`backend=local`） | 隧道没起来 / 改了配置没重启 | 重启本地后端；`status.tunnel.running` 必须为 true |
| `WinError 10061 目标计算机积极拒绝` | 本地 18443 没监听（隧道没起）或云端已关机 | 检查隧道状态；确认云端 `curl -sk https://127.0.0.1:8443/v1/health` |
| 云端 `ready:false` | 模型目录不在数据盘路径上 | 检查 `QWENTTS__STORAGE__MODELS_DIR` |

---

## 9. 成本控制清单

1. **不用就关机**：界面「一键关机」或控制台关机；关机后**数据盘仍保留**，只收少量存储费；
2. **无卡模式做准备工作**（下模型、装依赖），有卡后直接开工；
3. **先下模型再开卡**：13GB 下载是最耗时的部分，跟 GPU 无关；
4. **`stop_service` 只用于短期腾显存**（例如你要换引擎），长期不用请关机；
5. 平台上「15 天后释放」这类策略要留意，重要数据自己备份。

---

## 10. 安全清单

- [ ] API Key 用强随机串，别用 `sk-qwentts-cloud-2026` 这种示例值；
- [ ] 优先走 SSH 隧道，**不要**把 8443 直接暴露公网；
- [ ] 若必须公网直连：设置页打开「校验 TLS 证书」并使用受信任证书（或 Nginx/Caddy 反代）；
- [ ] `config/config.yaml`、`.env`、平台密码不要进 Git（本项目 `.gitignore` 已排除）；
- [ ] 多人共用一台云机时，给每人不同 API Key（当前实现是单 key，需要多 key 请自行加反代）；
- [ ] 关闭后确认端口不可达：`curl -sk --max-time 3 https://<host>:8443/v1/health` 应超时。

---

## 附录 A：命令速查

```bash
# 起服务（有卡）
cd /root/autodl-tmp/QWENTTS && ./start_cloud.sh

# 后台起 + 日志
cd /root/autodl-tmp/QWENTTS && setsid nohup ./start_cloud.sh > /root/autodl-tmp/service.log 2>&1 & tail -f /root/autodl-tmp/service.log

# 健康检查
curl -sk https://127.0.0.1:8443/v1/health

# 自检（端口/环境/模型/存储）
cd /root/autodl-tmp/QWENTTS/backend && PYTHONPATH=. python -m app.main --selfcheck

# 模型校验
/root/miniconda3/bin/python scripts/download_models.py --size 1.7B --out models --verify

# 停服务 / 关机
pkill -f "[a]pp.main"          # 只停服务
shutdown -h now                # 整机关机（平台停止计费）

# 磁盘
df -h /root/autodl-tmp && du -sh /root/autodl-tmp/QWENTTS/*
```

## 附录 B：云端文件清单（部署后应有）

```
/root/autodl-tmp/QWENTTS/
├── backend/                 # 后端源码
├── frontend/                # 前端源码（云端不需要构建）
├── scripts/                 # start.sh / download_models.py / 测试脚本
├── docs/                    # 文档
├── config/                  # 配置模板（config.yaml 运行时生成）
├── models/                  # 13G：1.7B 三件套
│   ├── Qwen3-TTS-12Hz-1.7B-Base/
│   ├── Qwen3-TTS-12Hz-1.7B-CustomVoice/
│   └── Qwen3-TTS-12Hz-1.7B-VoiceDesign/
├── data/                    # SQLite + 任务音频
├── .cache/                  # HF / pip 缓存
└── start_cloud.sh           # 一键启动
```

## 附录 C：本次实测数据（可对照）

| 项 | 值 |
| --- | --- |
| 云主机 | AutoDL `connect.nmb2.seetacloud.com:42049`（克隆机端口会变） |
| GPU | RTX 3090 / 23.56 GB（CustomVoice 占 3981 MB，空闲 19 GB） |
| 解释器 | `/root/miniconda3/bin/python` 3.12.3 |
| torch / torchaudio | 2.8.0+cu128 / 2.8.0+cu128（torchaudio 需 `--no-deps` 对齐） |
| 模型下载 | 12.96 GB，~17 MB/s，约 12 分钟 |
| 磁盘 | 数据盘 50G，用 13G，剩 38G |
| 首包 / 总耗时 | 10.1s / 10.3s（28 字）；12s（149 字） |
| RTF | 0.44 ~ 0.49 |
| wav 体积 | 28 字 ≈ 242 KB；149 字 ≈ 273 KB |

## 附录 D：相关文档

| 文档 | 关系 |
| --- | --- |
| [CLOUD_SETUP.md](CLOUD_SETUP.md) | 三种接入方案的原理与对比（本文偏操作） |
| [CLOUD_PROVIDERS.md](CLOUD_PROVIDERS.md) | 各平台价格/显卡/可用性对比 |
| [CONFIG.md](CONFIG.md) | 全部配置项与默认值 |
| [API.md](API.md) | `/v1/*` 与 `/api/*` 接口细节 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 更全的排错清单（含本地问题） |
| [VERSION_POLICY.md](VERSION_POLICY.md) | 版本锁定与 pip 相关硬性约束 |
