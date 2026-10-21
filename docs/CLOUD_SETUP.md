# 云 GPU 接入教程（国内厂商 · 从零到跑通）

> **只想照着做、少踩坑**：看手把手版 [CLOUD_ACCESS_TUTORIAL.md](CLOUD_ACCESS_TUTORIAL.md)
> （选镜像 → 无卡准备 → 起服务 → 隧道 → 验收 → 一键关机 → 故障速查）。本文偏原理与方案对比。

本教程带你把自己的云 GPU 实例接成本项目的「云端推理服务」，本地照常用界面，重活交给云卡。
全程约 20-40 分钟，主要时间花在装依赖和下载模型上。

> 不知道该选哪家平台？先看 [云 GPU 平台推荐](CLOUD_PROVIDERS.md)（价格区间 / 适配性 / 避坑）。
>
> 本文以 **AutoDL**（国内最常用的按量计费 GPU 平台）为例，恒源云 / 优云智算 / 阿里云·腾讯云 GPU 抢占式实例的步骤几乎一致，
> 差异点会单独标注。**GPU 建议 ≥12GB 显存**（A10 / L4 / 3090 / 4090 / A100 均可），4GB 卡不必走云端。

---

## 0. 先理解三个角色

```
本地程序（你的 Windows 电脑）                 云端 GPU 实例（租来的机器）
┌───────────────────────────┐              ┌──────────────────────────────┐
│ React 前端 + FastAPI       │  HTTPS/WS    │ 同一个后端，run.mode=local   │
│ run.mode=cloud / hybrid   │ ───────────▶ │ 对外只暴露 /v1/* (X-API-Key) │
│ 任务/队列/数据库都在本地   │              │ 只负责推理，无任务队列        │
└───────────────────────────┘              └──────────────────────────────┘
```

- 本地：界面、任务、文件、数据库都留在你这台机器；
- 云端：同一份代码、同一个镜像，只把 GPU 跑推理；
- 连接方式三选一：**直连公网 HTTPS** / **SSH 隧道**（推荐）/ **Docker 一键**。

---

## 1. 租一台云 GPU

### 1.1 选卡

只租 **1 张**卡即可（`GPU数量 = 1`）。1.7B fp16 单模型约 3.9GB 显存：

| 卡 | 结论 |
| --- | --- |
| **RTX 3090 / 4090 / 4090D（24GB）** | ✅ **首选**，性价比最好，`cu121` 原生支持 |
| vGPU-32GB / vGPU-48GB | ✅ 有货时很好用，显存足够多模型常驻 |
| A10 / L4 / A6000-48G / 3080-20G | ✅ 都行 |
| RTX 5090 / 5080 / 5070、PRO 6000、5000、6000D | ❌ Blackwell（sm_120），本项目锁的 `torch 2.4.x+cu121` 不支持，需换 `cu128` 才能跑 |
| 3080x2（双卡）、CPU 实例 | ❌ 双卡浪费；CPU 慢 25 倍 |

完整对照表见 [CLOUD_PROVIDERS.md 第九节](CLOUD_PROVIDERS.md#九选卡对照表按你这张平台列表逐项判断)。

### 1.2 选镜像（关键，照着选）

在「官方镜像」里按下表挑，**不要自己重装 CUDA**：

| 选项 | 选什么 | 说明 |
| --- | --- | --- |
| 框架名称 | **PyTorch** | 本项目只需 PyTorch |
| 框架版本 | **2.4.x**（首选）或 **2.9.1** | 2.4.x 与本机验证环境一致；2.9.1 在新卡上更快，同样可用 |
| CUDA 版本 | **12.1.1**（首选）→ 没有就 12.4 / 12.6 / 12.8 | 3090/4090 都支持；**别选 11.x**（fp16/bf16 支持差） |
| Python 版本 | **3.10**（首选）或 **3.11** | 两者都已验证可用；3.12 未验证，先别选 |

镜像组合示例（平台下拉框里直接能选到）：

```
PyTorch / 2.4.0 / 12.1.1 / 3.11
```

> 镜像预装的 torch 就够用：`pip install -r requirements.txt` 不会覆盖它（本项目 `torch>=2.3` 已满足）。
> 想完全复刻本机环境（torch 2.4.1+cu121），再执行一次：
> `pip install torch==2.4.1 torchaudio==2.4.1 --index-url https://download.pytorch.org/whl/cu121`

### 1.3 计费与连接信息

1. 计费方式选**按量计费**（用完关机，只收存储费）；
2. 创建后记下三样东西：`SSH 登录指令`、`SSH 端口`、`实例密码/密钥`（在「容器实例」页右侧可查）；
3. 关机 ≠ 销毁：关机后数据盘仍计费；不用了记得「释放实例」。

### 1.4 开机后 30 秒验环境

```bash
nvidia-smi                                                     # 显存、驱动版本
python -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
python -c "import torch;print(torch.cuda.get_device_capability())"   # 期望 (8,6)/(8,9)/(9,0)；(12,0)=Blackwell，需换 torch
python -c "import torch;print(torch.randn(8,8,device='cuda').sum())" # 能出数 = kernel 可用
```

驱动要求：`cu121` 需驱动 ≥ 530，`cu128` 需 ≥ 570（`nvidia-smi` 右上角显示的 CUDA 版本即驱动支持上限）。

### 1.5 依赖版本小提示

`pip install -r requirements.txt` 会拉最新的 `transformers`（≥4.51）。若 `qwen-tts` 报不兼容，
固定到本机验证过的版本即可：

```bash
pip install "transformers==4.57.3"
```

### 其它厂商要点

| 厂商 | 差异点 |
| --- | --- |
| 恒源云 / 优云智算 / 矩池云 | 与 AutoDL 类似，镜像同样选 **PyTorch 2.4.x + CUDA 12.1.1 + Python 3.10/3.11**；需在控制台「端口映射」放行端口 |
| 阿里云 / 腾讯云 GPU（ECS/CVM） | 自己装 CUDA 12.1 + Python 3.10；**安全组必须放行入站 TCP 8443** |
| RunPod / Vast.ai | 需境外支付；镜像选 `nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04` 自己装，或直接用本项目的 `cloud/Dockerfile` |

---

## 2. 把代码和模型放到云端

### 2.1 上传代码（任选一种）

```bash
# 方式 A：AutoDL 网页「文件传输」上传 zip 后解压
unzip QWENTTS-src-xxx.zip -d /root/QWENTTS

# 方式 B：从你自己的 Git 仓库拉
git clone <你的仓库> /root/QWENTTS

# 方式 C：本地 scp（AutoDL 的 SSH 端口不是 22）
scp -P <SSH端口> -r ./QWENTTS root@<实例IP>:/root/QWENTTS
```

### 2.2 在云端下载模型（强烈建议在云端下，走 ModelScope 很快）

```bash
cd /root/QWENTTS
python scripts/download_models.py --size 1.7B --out /root/QWENTTS/models
# 只下两个也能跑：--models base voice_design（想要 9 个内置音色再下 custom_voice）
```

国内直链（也可浏览器下载后上传）：

- `https://www.modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`
- `https://www.modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-Base`
- `https://www.modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

### 2.3 装依赖

```bash
cd /root/QWENTTS
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
# torch 若需匹配 CUDA 12.1（必须带 == 版本，禁止无版本安装）：
pip install "torch==2.4.1+cu121" "torchaudio==2.4.1+cu121" --index-url https://download.pytorch.org/whl/cu121
```

---

## 3. 启动云端推理服务

### 3.1 生成密钥（务必设置，否则云端只允许本机访问）

```bash
export QWENTTS__CLOUD__API_KEY=$(openssl rand -hex 16)
echo "先把密钥记下来：$QWENTTS__CLOUD__API_KEY"
```

### 3.2 启动（云端角色）

```bash
cd /root/QWENTTS
export QWENTTS__RUN__MODE=local          # 云端自己用本地 GPU 推理
export QWENTTS__SERVER__OPEN_BROWSER=false
export QWENTTS__LOCAL__WARMUP_KINDS=custom_voice   # 启动即预热，首个请求更快
./scripts/start.sh --cloud
```

看到 `Uvicorn running on https://0.0.0.0:8443` 即成功。

### 3.3 自检云端是否就绪

```bash
curl -fk https://127.0.0.1:8443/v1/health
# {"status":"ok","ready":true,"loaded":["custom_voice"],"model":"Qwen3-TTS-12Hz-1.7B-CustomVoice",...}
```

### 3.4 Docker 方式（可选）

```bash
cd /root/QWENTTS
export QWENTTS_API_KEY=<刚才的密钥>
docker compose -f cloud/docker-compose.yml up -d --build
docker logs -f qwentts-cloud
```

---

## 4. 打通网络（三种方式，选一个）

### 方式 A：直连公网 HTTPS（最简单，需放行端口）

1. 云厂商控制台/安全组放行 **入站 TCP 8443**；
2. 本地浏览器访问一次 `https://<云端IP>:8443/v1/health`，确认能通（自签证书点「继续前往」）；
3. 本地 `config/config.yaml`：

```yaml
run:
  mode: hybrid            # 或 cloud
cloud:
  base_url: https://<云端IP>:8443
  api_key: "<第 3.1 步的密钥>"
  verify_tls: false       # 自签证书先关闭校验；有正式证书时改 true
```

### 方式 B：SSH 隧道（推荐，不用暴露公网端口）

AutoDL 的 SSH 端口不是 22，按控制台给的填：

```yaml
cloud:
  base_url: https://127.0.0.1:18443    # 逻辑地址，客户端无感知
  api_key: "<密钥>"
  verify_tls: false
  tunnel:
    enabled: true
    ssh_host: <实例IP>
    ssh_port: <控制台给的 SSH 端口，如 42345>
    username: root
    key_path: ""            # 用密钥登录则填私钥绝对路径
    password: "<实例密码>"
    local_port: 18443
    remote_host: 127.0.0.1
    remote_port: 8443
```

保存后启动本地服务，隧道会自动建立；「状态与日志」页能看到隧道运行状态与错误。

### 方式 C：本地反向代理（有域名/证书时）

用 Nginx/Caddy 反代云端的 8443 到 443 并挂正式证书，本地 `base_url` 填域名、`verify_tls: true` 即可。

---

## 5. 在本地界面里切换与验证

1. 打开 `https://localhost:8443` → **设置**；
2. 「运行模式」选 **仅云端** 或 **混合**（混合=本地能跑就本地，跑不动自动转云端）；
3. 填好 `服务地址` / `API 密钥`，保存（自动保存，切页不丢）；
4. 回到「工作台」生成一句话：
   - 顶栏状态应显示 **云端 + 延迟 xx ms**；
   - 任务详情里 `后端 = cloud`、`模型 = Qwen3-TTS-12Hz-1.7B-*`；
5. 也可直接命令行验证：

```bash
curl -fk https://<云端地址>/v1/health      # ready: true
curl -fk https://<云端地址>/v1/engine/synth \
  -H "X-API-Key: <密钥>" -H "Content-Type: application/json" \
  -d '{"kind":"tts","text":"你好","speaker":"vivian"}' -o out.wav
```

---

## 6. 成本控制

| 做法 | 说明 |
| --- | --- |
| 按量计费 + 用完关机 | AutoDL/恒源云关机只收存储费（约几毛/小时） |
| 空闲自动停机 | 设置 → 云端连接 → 空闲自动停机（**默认关闭**），开启后空闲 N 分钟自动调云端停机接口 |
| 外部停机回调 | `cloud.idle_shutdown_url` 填厂商停机 API，可实现真正关机；留空则调云端 `/v1/system/shutdown`（仅卸模型并退出进程，容器需配置重启策略才生效） |
| 只下需要的模型 | 只下 `base + voice_design` 省 ~4GB 存储 |

参考成本（按量计费，仅示意，以厂商现价为准）：3090 约 1-2 元/小时，A10 约 2-3 元/小时。

---

## 7. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 云端 `401 密钥无效` | 本地 `api_key` 与云端不一致 | 重设 `QWENTTS__CLOUD__API_KEY`，重启云端，再更新本地设置 |
| 本地报 `CLOUD_UNREACHABLE` | 端口没放行 / IP 变了 / 隧道没起 | 浏览器先访问 `/v1/health`；隧道方式看「状态与日志 → 隧道」 |
| `CLOUD_NOT_READY` 超时 | 模型还在加载 | 云上看 `docker logs` 或 `/v1/health` 的 `loading`；`readiness_timeout_s` 可调大 |
| 自签证书报错 | `verify_tls: true` 但证书是自签 | 改 `verify_tls: false`，或换成正式证书 |
| 云端 `CUDA out of memory` | 显存不够 / 同时装了两个模型 | 换 ≥12GB 卡；本项目会自动卸载其它模型再加载 |
| 云端 `Model not found` | 模型目录结构与配置不一致 | `cd backend && python -m app.main --selfcheck` 看具体缺哪个目录 |
| SSH 隧道建立失败 | 端口/密钥/密码错 | 先用命令行 `ssh -p <端口> root@<IP>` 手动连一次确认 |
| 任务跑在本地而不是云端 | 模式是 hybrid 且本地能跑 | 改成 **仅云端**，或任务里 `force_backend=cloud` |

---

## 8. 安全清单

- [x] 云端**必须**配置 `cloud.api_key`（未配置时只允许本机访问）
- [x] 优先用 **SSH 隧道**，不把推理端口暴露到公网
- [x] 生产环境用受信任证书（Caddy/Nginx 自动 HTTPS），并把 `verify_tls` 打开
- [x] 不要在公网明文传密钥（走 HTTPS 或隧道）
- [x] 上传的参考音频仅用于本次推理，云端数据目录可随时清理
- [ ] 商用前确认 Qwen3-TTS 模型许可证与音色授权

---

## 9. 最短路径清单（复制即用）

```bash
# ===== 云端 =====
cd /root/QWENTTS
python scripts/download_models.py --size 1.7B
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
export QWENTTS__CLOUD__API_KEY=sk-你的密钥
export QWENTTS__RUN__MODE=local
./scripts/start.sh --cloud
curl -fk https://127.0.0.1:8443/v1/health     # ready:true 即可

# ===== 本地（config/config.yaml 或设置页） =====
# run.mode: cloud
# cloud.base_url: https://<云端IP>:8443
# cloud.api_key: sk-你的密钥
# cloud.verify_tls: false
```

本地启动后打开 `https://localhost:8443`，顶栏显示「云端 + 延迟」，即可把 1.7B 的画质与速度都交给云卡。

## 十、云端部署实录：五个真实坑（2026-10-19）

以下是本机（Windows 11 + RTX 3050 4GB）对接 AutoDL 云主机时**实际踩到并修好**的问题，
照着做可以少走几小时弯路。

### 坑 1：镜像里的 `cryptography` Rust 扩展是旧的，paramiko 一导入就崩

```
AttributeError: module 'openssl' has no attribute 'ciphers'
  at cryptography/hazmat/primitives/ciphers/base.py
```

**原因**：AutoDL 镜像里残留了 2024 年的旧 `_rust.cpython-312-x86_64-linux-gnu.so`，
而 Python 会优先加载「带 ABI 标签」的 `.so`，不加载新的 `_rust.abi3.so` → 加载到旧扩展，
缺少 `ciphers` 属性。**升级/降级 cryptography 都没用，必须删目录重装**。

```bash
rm -rf /root/miniconda3/lib/python3.12/site-packages/cryptography        /root/miniconda3/lib/python3.12/site-packages/cryptography-*
/root/miniconda3/bin/python -m pip install --no-cache-dir --force-reinstall \
    "cryptography==44.0.1" -i https://pypi.tuna.tsinghua.edu.cn/simple
/root/miniconda3/bin/python -c "import paramiko; print('paramiko OK', paramiko.__version__)"
```

> 影响面：`paramiko`（SSH 隧道）、`pooch`（librosa 依赖）都会连带失败，表现为「服务起不来」。

### 坑 2：镜像 PATH 里没有 `python` / `python3`

AutoDL 把解释器放在 `/root/miniconda3/bin/`，但非交互式 shell 的 PATH 里没有它，
`./scripts/start.sh` 第一步就 `python: command not found`。

**做法**：启动脚本开头显式加 PATH：

```bash
export PATH=/root/miniconda3/bin:$PATH
```

### 坑 3：Windows 打的 zip 到 Linux 上解压成全是一层「带反斜杠的文件名」

`Compress-Archive` 用反斜杠当分隔符，`unzip` 只会 warn：
`appears to use backslashes as path separators`，然后解出一堆平铺文件，目录结构全废。

**做法**：用 Python 重新打包（POSIX 路径）：

```python
import zipfile, pathlib
root = pathlib.Path("项目根")
with zipfile.ZipFile("qwentts-linux.zip", "w", zipfile.ZIP_DEFLATED) as zf:
    for p in root.rglob("*"):
        if p.is_file() and "node_modules" not in p.parts:
            zf.write(p, p.relative_to(root).as_posix())
```

### 坑 4：`--no-build` 与可执行位

zip 传输会丢失 `+x`，`./scripts/start.sh` 报 `Permission denied`：

```bash
chmod +x start_cloud.sh scripts/*.sh
```

云端不需要构建前端（用本机界面），启动时带 `--no-build` 即可。

### 坑 5：克隆主机后 SSH 端口/密码变了，本地隧道必须重写并**重启本地服务**才生效

克隆出来的新实例：`ssh -p 42049 root@connect.nmb2.seetacloud.com`（端口、密码都变了）。
在设置页改完隧道参数后，`/api/system/status` 里可能仍显示 `enabled: false, ssh_host: ""` ——
**必须重启本地后端**才能把新配置读进隧道服务。

判断隧道是否真的通了（三条硬证据）：

```bash
# ① 本地 18443 在监听
netstat -ano | findstr 18443
# ② 状态接口
curl -k https://localhost:8443/api/system/status   # tunnel.running 应为 true
# ③ 跑一个任务看 backend 字段
curl -k https://localhost:8443/api/tasks/<id>      # "backend": "cloud"
```

### 附：本次部署的关键数据（可对照）

| 项 | 值 |
| --- | --- |
| 云主机 | AutoDL `connect.nmb2.seetacloud.com:42049`（克隆机，端口会变） |
| 显卡 | RTX 3090 / 23.56 GB（CustomVoice 1.7B 占 3981 MB） |
| 解释器 | `/root/miniconda3/bin/python`（3.12.3） |
| torch | 2.8.0+cu128（镜像自带），torchaudio 需 `--no-deps` 对齐到 2.8.0+cu128 |
| 代码与模型 | `/root/autodl-tmp/QWENTTS`（13 GB，模型三件套） |
| 启动 | `cd /root/autodl-tmp/QWENTTS && ./start_cloud.sh` |
| 本地隧道 | `local 18443 → 云端 127.0.0.1:8443`，`base_url = https://127.0.0.1:18443` |
| 端到端 | 任务 `backend=cloud`，12 秒出音，wav 241,964 字节 |
