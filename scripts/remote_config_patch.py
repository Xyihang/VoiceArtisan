"""在云端安全地修改 config/config.yaml 的少量白名单键。

供 Agent 的 `write_remote_config` 工具调用，不开放任意文件写入：
    <venv python> scripts/remote_config_patch.py --set storage.models_dir=/root/autodl-tmp/qwentts-models
改之前会备份成 config.yaml.bak-<时间戳>，改完回显受影响的键。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
from pathlib import Path

ALLOWED = {
    "storage.models_dir",
    "storage.data_dir",
    "server.port",
    "server.host",
    "cloud.api_key",
    "run.mode",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    path = Path(args.config)
    if not path.exists():
        print(f"error: 配置文件不存在 {path}")
        return 2

    try:
        import yaml
    except ImportError:
        print("error: 云端缺少 PyYAML")
        return 2

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def dig(root: dict, parts: list[str]) -> dict:
        node = root
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        return node

    changed: list[str] = []
    for item in args.set:
        if "=" not in item:
            print(f"error: 参数格式应为 KEY=VALUE，收到 {item}")
            return 2
        key, value = item.split("=", 1)
        key = key.strip()
        if key not in ALLOWED:
            print(f"error: 不允许修改 {key}（白名单：{', '.join(sorted(ALLOWED))}）")
            return 3
        parts = key.split(".")
        node = dig(data, parts)
        old = node.get(parts[-1])
        node[parts[-1]] = value
        changed.append(f"{key}: {old!r} -> {value!r}")

    if changed:
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(path.name + f".bak-{stamp}"))
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    for line in changed:
        print("changed:", line)
    if args.show:
        print(path.read_text(encoding="utf-8"))
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
