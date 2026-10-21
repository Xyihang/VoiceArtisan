"""检查新主机云端服务状态，并把本地设置里的隧道改到新主机。"""
from __future__ import annotations

import json
import warnings

import httpx
import paramiko

warnings.filterwarnings("ignore")

HOST, PORT, USER, PWD = "connect.nmb2.seetacloud.com", 42049, "root", "OiQ1Y2TyxakA"
ROOT = "/root/autodl-tmp/QWENTTS"
PY = "/root/miniconda3/bin/python"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, port=PORT, username=USER, password=PWD, timeout=25)


def run(cmd: str, timeout: float = 120) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    return (out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")).strip()


print("=== 云端服务状态 ===")
print(run("ss -ltnp 2>/dev/null | grep ':8443' || echo '8443 未监听'"))
print("health:", run("curl -sk --max-time 6 https://127.0.0.1:8443/v1/health | head -c 400") or "(空)")
print("进程:", run("ps -eo pid,etime,cmd | grep '[a]pp.main' | head -3 || echo 无"))
print("\n=== 日志尾部 ===")
print(run("tail -15 /root/autodl-tmp/service.log 2>/dev/null")[:1500] or "(没有日志)")
client.close()

print("\n=== 本地设置：隧道指向新主机 ===")
c = httpx.Client(base_url="https://127.0.0.1:8443", verify=False, timeout=30)
s = c.get("/api/settings").json()
payload = {
    "cloud": {
        "base_url": "https://127.0.0.1:18443",
        "api_key": "sk-qwentts-cloud-2026",
        "verify_tls": False,
        "tunnel": {
            **s["cloud"]["tunnel"],
            "enabled": True,
            "ssh_host": HOST,
            "ssh_port": PORT,
            "username": USER,
            "password": PWD,
            "key_path": "",
            "local_port": 18443,
            "remote_host": "127.0.0.1",
            "remote_port": 8443,
        },
    }
}
r = c.put("/api/settings", json=payload)
print("PUT /api/settings ->", r.status_code)
now = c.get("/api/settings").json()["cloud"]
print(json.dumps({"base_url": now["base_url"], "tunnel": now["tunnel"]["enabled"],
                  "target": f"{now['tunnel']['username']}@{now['tunnel']['ssh_host']}:{now['tunnel']['ssh_port']}",
                  "local_port": now["tunnel"]["local_port"]}, ensure_ascii=False, indent=1))
