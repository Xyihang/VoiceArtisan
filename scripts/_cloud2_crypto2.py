"""定位 `openssl` 到底解析到哪个文件（cryptography _rust 属性缺失的真凶）。"""
from __future__ import annotations

import warnings

import paramiko

warnings.filterwarnings("ignore")

HOST, PORT, USER, PWD = "connect.nmb2.seetacloud.com", 42049, "root", "OiQ1Y2TyxakA"
PY = "/root/miniconda3/bin/python"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, port=PORT, username=USER, password=PWD, timeout=25)


def run(cmd: str, timeout: float = 300) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    return (out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")).strip()


print("① openssl 子模块解析到哪个文件 + 它有哪些属性")
print(run(f"{PY} - <<'PY'\n"
         "from cryptography.hazmat.bindings._rust import openssl\n"
         "import cryptography.hazmat.bindings as b\n"
         "print('openssl file:', getattr(openssl, '__file__', None))\n"
         "print('has ciphers:', hasattr(openssl, 'ciphers'))\n"
         "print('attrs sample:', [a for a in dir(openssl) if not a.startswith('_')][:15])\n"
         "import cryptography; print('cryptography', cryptography.__version__, cryptography.__file__)\n"
         "PY"))

print("\n② site-packages 里所有 _rust 相关 .so")
print(run("ls -l /root/miniconda3/lib/python3.12/site-packages/cryptography/hazmat/bindings/ | head -10"))

print("\n③ 是否有同名的 openssl 包/文件在 site-packages 或当前目录")
print(run("ls -d /root/miniconda3/lib/python3.12/site-packages/openssl* 2>/dev/null; "
          "ls -l /root/autodl-tmp/QWENTTS/openssl* 2>/dev/null; echo '(以上为空即无遮蔽)'"))

print("\n④ 尝试彻底清掉 cryptography 再装")
print(run("rm -rf /root/miniconda3/lib/python3.12/site-packages/cryptography /root/miniconda3/lib/python3.12/site-packages/cryptography-* "
          "/root/miniconda3/lib/python3.12/site-packages/~* 2>/dev/null; echo cleaned"))
print(run(f"{PY} -m pip install --no-cache-dir --force-reinstall 'cryptography==44.0.1' "
          f"-i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1 | tail -2"))
print("\n⑤ 再测 paramiko")
print(run(f"{PY} -c \"import paramiko; print('paramiko OK', paramiko.__version__)\" 2>&1 | tail -4"))
client.close()
