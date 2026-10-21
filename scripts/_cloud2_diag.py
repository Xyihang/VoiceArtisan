"""诊断 cloud 上 paramiko/cryptography 的 openssl 属性错误。"""
from __future__ import annotations

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


print("① 仓库里有没有叫 openssl 的东西（会遮蔽真模块）")
print(run(f"cd {ROOT} && find . -maxdepth 3 -name 'openssl*' | head -10; echo '---'; ls /root/autodl-tmp/QWENTTS | head -20"))

print("\n② cryptography / cffi 版本")
print(run(f"{PY} -m pip list 2>/dev/null | grep -iE '^(cryptography|cffi|pyopenssl|paramiko|bcrypt) '"))

print("\n③ 单独导入 cryptography 的 rust openssl")
print(run(f"{PY} -c \"from cryptography.hazmat.bindings._rust import openssl; print('rust openssl OK', openssl.__name__)\" 2>&1 | tail -5"))

print("\n④ 只导入 paramiko")
print(run(f"{PY} -c \"import paramiko; print('paramiko OK', paramiko.__version__)\" 2>&1 | tail -6"))

print("\n⑤ 在仓库目录里导入 paramiko（看是否被本地文件遮蔽）")
print(run(f"cd {ROOT} && {PY} -c \"import sys; print(sys.path[:3]); import paramiko; print('ok')\" 2>&1 | tail -5"))

print("\n⑥ 后端是在哪一步崩的（完整 traceback 头部）")
print(run("grep -n -m1 -B5 'paramiko.transport' /root/autodl-tmp/service.log | head -30"))
client.close()
