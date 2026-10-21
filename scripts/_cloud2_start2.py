"""新主机启动云端服务（修正：pkill 自匹配问题，用 [a]pp.main 规避）。"""
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


print("=== GPU ===")
print(run("nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader"))
print(run(f"{PY} -c \"import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())\" 2>&1 | tail -2"))

print("\n=== 清端口 / 起服务 ===")
print(run("ss -ltnp 2>/dev/null | grep ':8443' || echo '8443 空闲'"))
run("for p in $(ss -ltnp 2>/dev/null | grep ':8443' | grep -o 'pid=[0-9]*' | cut -d= -f2); do kill -9 $p; done; sleep 1", timeout=30)
run(f"cd {ROOT} && setsid nohup bash ./start_cloud.sh > /root/autodl-tmp/service.log 2>&1 < /dev/null & sleep 1", timeout=30)
print("已下发启动命令")

for i in range(30):
    time.sleep(10)
    health = run("curl -sk --max-time 5 https://127.0.0.1:8443/v1/health 2>/dev/null | head -c 300")
    log = run("tail -2 /root/autodl-tmp/service.log 2>/dev/null")[:200]
    print(f"[{(i + 1) * 10}s] health={health[:160] or '(空)'} | log={log.replace(chr(10), ' ')}")
    if health.strip():
        break

print("\n=== 日志尾部 ===")
print(run("tail -20 /root/autodl-tmp/service.log")[:1500])
client.close()
