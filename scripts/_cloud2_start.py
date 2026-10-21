"""新主机（有卡）接入：探环境 → 起云端服务 → 本地验证。"""
from __future__ import annotations

import time
import warnings

import paramiko

warnings.filterwarnings("ignore")

HOST, PORT, USER, PWD = "connect.nmb2.seetacloud.com", 42049, "root", "OiQ1Y2TyxakA"
ROOT = "/root/autodl-tmp/QWENTTS"
PY = "/root/miniconda3/bin/python"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, port=PORT, username=USER, password=PWD, timeout=25)


def run(cmd: str, timeout: float = 180) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    return (out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")).strip()


print("=== 1) GPU / 环境 ===")
print(run("nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader"))
print(run(f"{PY} -c \"import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available(),"
          "torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')\" 2>&1 | tail -2"))

print("\n=== 2) 东西还在吗 ===")
print(run(f"du -sh {ROOT}/models 2>/dev/null; ls {ROOT}/models | head -5; ls {ROOT}/start_cloud.sh 2>/dev/null"))

print("\n=== 3) 起服务（后台）===")
run(f"pkill -f 'app.main' 2>/dev/null; sleep 1; "
    f"setsid bash -c 'cd {ROOT} && ./start_cloud.sh > /root/autodl-tmp/service.log 2>&1' </dev/null >/dev/null 2>&1 &",
    timeout=20)
for i in range(20):
    time.sleep(10)
    log = run("tail -3 /root/autodl-tmp/service.log 2>/dev/null")[:300]
    health = run("curl -sk --max-time 5 https://127.0.0.1:8443/v1/health | head -c 400")
    print(f"[{(i + 1) * 10}s] {health[:220] or log[:200]}")
    if '"status"' in health or 'ok' in health.lower()[:40]:
        break

print("\n=== 4) 服务日志尾部 ===")
print(run("tail -12 /root/autodl-tmp/service.log")[:1200])
client.close()
