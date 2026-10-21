"""修正云端启动脚本（PATH / 权限），然后拉起服务并验证。"""
from __future__ import annotations

import time
import warnings

import paramiko

warnings.filterwarnings("ignore")

HOST, PORT, USER, PWD = "connect.nmb2.seetacloud.com", 42049, "root", "OiQ1Y2TyxakA"
ROOT = "/root/autodl-tmp/QWENTTS"
API_KEY = "sk-qwentts-cloud-2026"

START = f"""#!/usr/bin/env bash
# 云端启动（有卡）。API key 必须与本地设置一致。
set -euo pipefail
cd "$(dirname "$0")"

export PATH=/root/miniconda3/bin:$PATH          # 镜像 PATH 里没有 python/python3
export QWENTTS__RUN__MODE=local
export QWENTTS__SERVER__OPEN_BROWSER=false
export QWENTTS__SERVER__HOST=0.0.0.0
export QWENTTS__SERVER__PORT=8443
export QWENTTS__CLOUD__API_KEY={API_KEY}
export QWENTTS__STORAGE__DATA_DIR={ROOT}/data
export QWENTTS__STORAGE__MODELS_DIR={ROOT}/models
export QWENTTS__STORAGE__CACHE_DIR={ROOT}/.cache
export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME={ROOT}/.cache/hf

python - <<'PY' || true
import torch
print("[cloud] torch", torch.__version__, "cuda", torch.cuda.is_available())
PY

exec bash ./scripts/start.sh --cloud --no-build "$@"
"""

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, port=PORT, username=USER, password=PWD, timeout=25)


def run(cmd: str, timeout: float = 180) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    return (out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")).strip()


sftp = client.open_sftp()
with sftp.file(f"{ROOT}/start_cloud.sh", "w") as fh:
    fh.write(START)
sftp.close()
print(run(f"chmod +x {ROOT}/start_cloud.sh {ROOT}/scripts/*.sh && ls -l {ROOT}/scripts/start.sh {ROOT}/start_cloud.sh"))

print("\n--- 清端口并启动 ---")
run("for p in $(ss -ltnp 2>/dev/null | grep ':8443' | grep -o 'pid=[0-9]*' | cut -d= -f2); do kill -9 $p; done; sleep 1", timeout=30)
run(f"cd {ROOT} && setsid nohup ./start_cloud.sh > /root/autodl-tmp/service.log 2>&1 < /dev/null & sleep 1", timeout=30)

for i in range(24):
    time.sleep(10)
    health = run("curl -sk --max-time 5 https://127.0.0.1:8443/v1/health 2>/dev/null | head -c 260")
    if health.strip():
        print(f"[{(i + 1) * 10}s] health: {health}")
        break
    print(f"[{(i + 1) * 10}s] 等待中… {run('tail -1 /root/autodl-tmp/service.log 2>/dev/null')[:110]}")

print("\n--- 日志 ---")
print(run("tail -14 /root/autodl-tmp/service.log 2>/dev/null")[:1200])
client.close()
