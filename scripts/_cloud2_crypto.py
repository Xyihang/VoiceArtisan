"""修复 cryptography 的 _rust 与包文件不匹配（AttributeError: module 'openssl' has no attribute 'ciphers'）。"""
from __future__ import annotations

import warnings

import paramiko

warnings.filterwarnings("ignore")

HOST, PORT, USER, PWD = "connect.nmb2.seetacloud.com", 42049, "root", "OiQ1Y2TyxakA"
PY = "/root/miniconda3/bin/python"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, port=PORT, username=USER, password=PWD, timeout=25)


def run(cmd: str, timeout: float = 600) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    return (out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")).strip()


print("--- 强制重装 cryptography（同版本，修 .so 与 py 文件不一致）---")
print(run(f"{PY} -m pip install --no-cache-dir --force-reinstall 'cryptography==50.0.0' "
          f"-i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1 | tail -3"))

print("\n--- 验证 paramiko ---")
print(run(f"{PY} -c \"import paramiko; print('paramiko OK', paramiko.__version__)\" 2>&1 | tail -4"))

print("\n--- 若仍失败，降到 44.x 兜底 ---")
broken = "OK" not in run(f"{PY} -c \"import paramiko; print('OK')\" 2>&1 | tail -1")
if broken:
    print(run(f"{PY} -m pip install --no-cache-dir --force-reinstall 'cryptography==44.0.1' "
              f"-i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1 | tail -3"))
    print(run(f"{PY} -c \"import paramiko; print('paramiko OK', paramiko.__version__)\" 2>&1 | tail -4"))
client.close()
