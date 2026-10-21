"""解析用户直接粘贴的 SSH 登录指令。

支持这些写法（都能从云厂商控制台原样复制）：
    ssh -p 13485 root@connect.cqa1.seetacloud.com
    ssh root@1.2.3.4
    ssh -p2222 -i ~/.ssh/id_rsa ubuntu@host
    ssh -l root -p 22 host
    ssh://root@host:2222
    或者干脆只贴主机名 / IP
"""
from __future__ import annotations

import shlex
from pathlib import Path
from urllib.parse import urlparse

_DEFAULT_PORT = 22


def _looks_like_path(value: str) -> bool:
    if not value:
        return False
    if any(ch in value for ch in ("/", "\\", "~", ".")):
        return True
    return Path(value).expanduser().exists()


def parse_ssh_command(text: str) -> dict:
    """把 SSH 登录指令解析成 {host, port, username, key_path}；无法解析的字段返回空串/默认端口。"""
    raw = (text or "").strip().strip('"').strip("'")
    result = {"host": "", "port": _DEFAULT_PORT, "username": "", "key_path": "", "password": ""}
    if not raw:
        return result

    if raw.startswith("ssh://"):
        parsed = urlparse(raw)
        result["host"] = parsed.hostname or ""
        result["port"] = parsed.port or _DEFAULT_PORT
        result["username"] = parsed.username or ""
        return result

    if not raw.lower().startswith("ssh"):
        token = raw.split()[0]
        if "@" in token:
            raw = "ssh " + token
        else:
            result["host"] = token.split(":")[0]
            return result

    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    parts = [p for p in parts if p]

    target = ""
    i = 1
    while i < len(parts):
        token = parts[i]
        if token in ("-p", "-P") and i + 1 < len(parts):
            try:
                result["port"] = int(parts[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if token.startswith("-p") and len(token) > 2 and token[2:].isdigit():
            result["port"] = int(token[2:])
            i += 1
            continue
        if token in ("-l",) and i + 1 < len(parts):
            result["username"] = parts[i + 1]
            i += 2
            continue
        if token in ("-i",) and i + 1 < len(parts):
            value = parts[i + 1]
            # 用户常把密码写在 -i 后面（没有路径特征），这里按密码处理
            if _looks_like_path(value):
                result["key_path"] = value
            else:
                result["password"] = value
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        if not target:
            target = token
        i += 1

    if target and not result.get("password"):
        rest = [t for t in parts[parts.index(target) + 1:] if not t.startswith("-")]
        if rest and not _looks_like_path(rest[0]):
            result["password"] = rest[0]
    if target:
        if "@" in target:
            user, _, host = target.partition("@")
            result["username"] = result["username"] or user
            target = host
        if ":" in target and target.count(":") == 1:
            host, _, port = target.partition(":")
            if port.isdigit():
                result["port"] = int(port)
            target = host
        result["host"] = target

    if result["key_path"]:
        result["key_path"] = str(Path(result["key_path"]).expanduser())
    return result


def looks_like_ssh_command(text: str) -> bool:
    low = (text or "").strip().lower()
    return low.startswith("ssh") or low.startswith("ssh://") or (("@" in low) and (" " not in low))
