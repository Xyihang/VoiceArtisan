"""云端启动（fire-and-forget：发命令后立刻断开通道，避免 paramiko 等待 EOF 超时）。"""
from __future__ import annotations

import json
import time
import warnings

import httpx
import paramiko

warnings.filterwarnings("ignore")

HOST, PORT, USER, PWD = "connect.nmb2.seetacloud.com", 42049, "root", "OiQ1Y2TyxakA"
ROOT = "/root/autodl-tmp/QWENTTS"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, port=PORT, username=USER, password=PWD, timeout=25)


def run(cmd: str, timeout: float = 60) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    try:
        return (out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")).strip()
    except Exception:  # noqa: BLE001 - 后台命令不等 EOF
        return "(后台命令已下发)"


def fire(cmd: str) -> None:
    ch = client.get_transport().open_session(timeout=15)
    ch.exec_command(cmd)
    time.sleep(2)
    ch.close()


print("--- 清端口 ---")
run("for p in $(ss -ltnp 2>/dev/null | grep ':8443' | grep -o 'pid=[0-9]*' | cut -d= -f2); do kill -9 $p; done; echo done", 30)

print("--- 启动（脱离式）---")
fire(f"cd {ROOT} && setsid nohup ./start_cloud.sh > /root/autodl-tmp/service.log 2>&1 < /dev/null &")

ok = False
for i in range(30):
    time.sleep(10)
    health = run("curl -sk --max-time 5 https://127.0.0.1:8443/v1/health 2>/dev/null | head -c 300", 20)
    if health.strip() and not health.startswith("("):
        print(f"[{(i + 1) * 10}s] {health}")
        ok = True
        break
    print(f"[{(i + 1) * 10}s] 等待… {run('tail -1 /root/autodl-tmp/service.log 2>/dev/null', 20)[:100]}")

print("\n--- 日志尾部 ---")
print(run("tail -14 /root/autodl-tmp/service.log 2>/dev/null", 30)[:1400])
client.close()

if ok:
    print("\n--- 本地经隧道访问 ---")
    try:
        c = httpx.Client(base_url="https://127.0.0.1:8443", verify=False, timeout=45)
        h = c.get("/api/health").json()
        print(json.dumps({k: h.get(k) for k in ("active_backend", "compute_device", "cloud_ok")}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print("本地读取失败:", type(exc).__name__, str(exc)[:150])
