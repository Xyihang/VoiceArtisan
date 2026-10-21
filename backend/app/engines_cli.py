"""引擎管理命令行（给安装器 launcher 用，不依赖 HTTP）。

用法（两种写法都支持）：
    cd backend && python -m app.engines_cli list --json
    python backend/app/engines_cli.py list --json      # 在项目根目录直接跑

子命令：
    list [--json] | deps <id> [--json] | verify <id> [--json]
    install <id> [--dry-run] [--verify-only] [--no-deps-guard]
    remove <id> | select <id> | health <id> [--json]

版本钉死：安装只按 `backend/app/engine/adapters/locks/<id>.txt`（逐行 `包名==精确版本`）执行，
装完用 `pip list --format=json` 逐包比对，任何不一致都会报 `VERSION_MISMATCH` 并给出修复命令。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    """把 backend/ 放进 sys.path，保证从项目根直接执行脚本也能 import app.*。"""
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


_bootstrap_path()

# 控制台编码兜底：中文/音标字符在 GBK 终端不再抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

from app.config import config_manager  # noqa: E402
from app.engine import registry  # noqa: E402
from app.engine.adapters import deps as deps_probe  # noqa: E402
from app.engine.adapters import torchguard  # noqa: E402
from app.engine.manager import EngineManager  # noqa: E402
from app.errors import AppError  # noqa: E402

PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

PHASE_LABEL = {
    "queued": "排队",
    "pip": "依赖",
    "download": "下载",
    "verify": "校验",
    "done": "完成",
    "error": "失败",
}


def _manager() -> EngineManager:
    return EngineManager(config_manager, local_engine=None, tasks=None)


def _print_progress(payload: dict) -> None:
    phase = PHASE_LABEL.get(payload.get("phase", ""), payload.get("phase", ""))
    pct = int(round(float(payload.get("progress") or 0.0) * 100))
    print(f"[{phase:>4}] {pct:3d}%  {payload.get('message', '')}", flush=True)


def _fail(exc: AppError) -> int:
    print(
        json.dumps(
            {"ok": False, "code": exc.code, "message": exc.message, "hint": exc.hint, "detail": exc.detail},
            ensure_ascii=False,
            indent=2,
        ),
        file=sys.stderr,
    )
    return 1


def _print_table(payload: dict) -> None:
    print(f"当前引擎：{payload['active']}")
    print(f"{'id':<18}{'名称':<24}{'状态':<16}{'体积MB':>10}  {'依赖':<10}{'隔离':<12}能力")
    for item in payload["items"]:
        missing = item.get("packages_missing") or []
        dep = "就绪" if item.get("packages_ok") else f"缺 {len(missing)}"
        isolation = item.get("isolation") or "venv"
        env = "主环境" if isolation == "inprocess" else ("venv✓" if item.get("venv_exists") else "venv未建")
        print(
            f"{item['id']:<18}{item['name']:<24}{item['status']:<16}"
            f"{item['size_mb']:>10.1f}  {dep:<10}{env:<12}{'/'.join(item['kind_tags'])}"
        )


def cmd_list(args: argparse.Namespace) -> int:
    payload = _manager().items()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_table(payload)
    return 0


def deps_payload(engine_id: str) -> dict:
    """单个引擎的依赖探测结果（只读，不安装）。

    隔离引擎（isolation=venv）的依赖状态取自**它自己的 venv**，命令也全部指向该 venv；
    默认引擎（isolation=inprocess）取自主环境。命令一律来自锁文件（精确版本）。
    """
    adapter = registry.get(engine_id)
    statuses = adapter.packages_status()
    missing = deps_probe.missing_of(statuses)
    snapshot = torchguard.snapshot()
    isolated = str(getattr(adapter, "isolation", "venv")) == "venv"
    venv_python = str(getattr(adapter, "venv_python", "") or "") if isolated else ""
    venv_constraints = str(getattr(adapter, "venv_constraints", "") or "") if isolated else ""
    commands = adapter.install_commands() if hasattr(adapter, "install_commands") else []
    lock_file = str(getattr(adapter, "lock_file", "") or "")
    versions_ok, versions_mismatch = (
        adapter.versions_status() if hasattr(adapter, "versions_status") else (False, [])
    )
    return {
        "ok": not missing,
        "engine_id": adapter.id,
        "name": adapter.name,
        "isolation": "venv" if isolated else "inprocess",
        "venv_path": str(getattr(adapter, "venv_dir", "") or "") if isolated else "",
        "venv_python": venv_python,
        "venv_exists": bool(adapter.venv_exists()) if isolated else False,
        "venv_ready": bool(adapter.venv_ready()) if isolated else False,
        "venv_size_mb": float(adapter.venv_size_mb()) if isolated else 0.0,
        "venv_constraints": venv_constraints,
        "lock_file": lock_file,
        "lock_packages": adapter.lock_packages() if hasattr(adapter, "lock_packages") else [],
        "versions_ok": bool(versions_ok),
        "versions_mismatch": list(versions_mismatch),
        "packages": list(adapter.package or adapter.modules),
        "main_package": list(getattr(adapter, "main_package", []) or []),
        "packages_source": "venv" if isolated else "main",
        "packages_status": statuses,
        "packages_ok": not missing,
        "packages_missing": missing,
        "missing_count": len(missing),
        "suggested_command": (
            adapter.install_command()
            if missing and hasattr(adapter, "install_command")
            else ""
        ),
        "install_commands": list(commands),
        "guarded_install_command": f"python -m app.engines_cli install {adapter.id}",
        "verify_command": f"python -m app.engines_cli verify {adapter.id}",
        "install_note": (
            "隔离引擎的依赖全部装进 <models_dir>/<engine_id>/.venv，合成走 "
            "<venv_python> runner.py 子进程；安装严格按 backend/app/engine/adapters/locks/<id>.txt "
            "（逐行 包名==精确版本）执行：主包 --no-deps、其余 -r 锁文件，所有 pip 调用都带 "
            "-c <venv>/constraints.txt 与 --no-cache-dir；装完用 pip list 逐包比对，不一致直接报 "
            "VERSION_MISMATCH。"
        ),
        "torch": snapshot,
        "torch_venv": torchguard.snapshot_venv(venv_python) if isolated else None,
        "torch_constraints": torchguard.constraints(),
        "torch_repair_command": torchguard.default_repair_command(),
    }


def cmd_deps(args: argparse.Namespace) -> int:
    try:
        payload = deps_payload(args.engine_id)
    except AppError as exc:
        return _fail(exc)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    torch_info = payload["torch"]
    print(f"{payload['name']}（{payload['engine_id']}）")
    if payload["isolation"] == "venv":
        print(f"隔离：venv（{payload['venv_path']}）")
        print(
            "venv 解释器：{python}  已创建：{exists}  就绪：{ready}  体积：{size}MB".format(
                python=payload["venv_python"],
                exists="是" if payload["venv_exists"] else "否",
                ready="是" if payload["venv_ready"] else "否",
                size=payload["venv_size_mb"],
            )
        )
        venv_torch = payload.get("torch_venv") or {}
        print(
            "venv 内 torch：{ver}（与主环境无关）".format(ver=venv_torch.get("torch") or "未安装")
        )
    else:
        print("隔离：inprocess（主环境就地推理）")
    print(
        "主环境 torch：{ver}  CUDA：{cuda}  可用：{ok}".format(
            ver=torch_info.get("torch") or "未安装",
            cuda=torch_info.get("cuda") or "无",
            ok="是" if torch_info.get("cuda_available") else "否",
        )
    )
    print(f"{'依赖':<24}{'状态':<8}版本")
    for entry in payload["packages_status"]:
        print(f"{entry['name']:<24}{'✓' if entry['installed'] else '✗':<8}{entry['version'] or '—'}")
    if payload["packages_missing"]:
        print("缺失：" + "、".join(payload["packages_missing"]))
        print("安全安装命令：# 见下方「完整安全命令」（锁文件精确版本）")
    else:
        print("依赖已就绪")
    print(f"锁文件：{payload['lock_file']}")
    if payload["versions_ok"]:
        print(f"版本校验：✓ 全部与锁文件一致（{len(payload['lock_packages'])} 个包）")
    elif payload["versions_mismatch"]:
        print(f"版本校验：✗ {len(payload['versions_mismatch'])} 个包与锁文件不一致")
        for item in payload["versions_mismatch"][:10]:
            print("  - " + item)
    else:
        print("版本校验：— 不可判定（锁文件缺失或 venv 尚未创建）")
    if payload.get("install_commands"):
        print("完整安全命令（复制即用，不会碰主环境 torch）：")
        for command in payload["install_commands"]:
            print("  " + command)
    return 0


def verify_payload(engine_id: str) -> dict:
    """只校验、不安装：按锁文件逐包比对，任一不一致即报 VERSION_MISMATCH。"""
    adapter = registry.get(engine_id)
    lock_file = getattr(adapter, "lock_file", None)
    isolated = str(getattr(adapter, "isolation", "venv")) == "venv"
    python = str(getattr(adapter, "venv_python", "") or "") if isolated else ""
    lock_packages = getattr(adapter, "lock_packages", lambda: [])()
    payload = {
        "ok": False,
        "verifiable": True,
        "engine_id": adapter.id,
        "lock_file": str(lock_file or ""),
        "interpreter": python or sys.executable,
        "checked": len(lock_packages),
        "versions_mismatch": [],
        "reason": "",
    }
    if not lock_file or not lock_file.exists():
        payload.update(verifiable=False, reason=f"该引擎没有锁文件：{lock_file}")
        return payload
    if isolated and not getattr(adapter, "venv_python").exists():
        payload.update(
            verifiable=False,
            reason=f"引擎 venv 尚未创建（{getattr(adapter, 'venv_dir', '')}），先执行 install 再校验",
        )
        return payload
    mismatches = list(adapter.versions_status()[1])
    payload.update(ok=not mismatches, versions_mismatch=mismatches)
    if mismatches:
        from app.engine.adapters import installer as installer_mod

        installer_mod.verify_lock(
            python,
            lock_file,
            getattr(adapter, "venv_constraints", None) if isolated else None,
        )
    return payload


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        payload = verify_payload(args.engine_id)
    except AppError as exc:
        if args.json:
            return _fail(exc)
        print(f"{exc.message}：{exc.detail}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not payload["verifiable"]:
        print(f"{payload['engine_id']}：无法校验 —— {payload['reason']}", file=sys.stderr)
    else:
        print(
            f"{payload['engine_id']}：版本校验通过（{payload['checked']} 个包与 "
            f"{payload['lock_file']} 完全一致）"
        )
    return 0 if payload["ok"] else 2


def cmd_install(args: argparse.Namespace) -> int:
    if getattr(args, "verify_only", False):
        return cmd_verify(args)
    if getattr(args, "no_deps_guard", False):
        torchguard.set_guard_enabled(False)
        print(
            "⚠ 已关闭 torch 守卫（--no-deps-guard）：pip 可能把 cu121 的 torch 替换成 CPU 版，"
            "导致本地推理只能跑 CPU。安装后请用 `engines_cli deps <id>` 复核 torch 与 CUDA。",
            file=sys.stderr,
        )
    if getattr(args, "dry_run", False):
        import inspect

        try:
            adapter = registry.get(args.engine_id)
        except AppError as exc:
            return _fail(exc)
        fn = getattr(adapter, "install", None)
        if fn is None or "dry_run" not in inspect.signature(fn).parameters:
            print(json.dumps({"ok": False, "engine_id": args.engine_id,
                              "message": "该引擎不支持安装预览（无白名单下载清单）"},
                             ensure_ascii=False, indent=2))
            return 1
        plan = fn(None, dry_run=True)
        print(json.dumps({"ok": True, "engine_id": args.engine_id, "plan": plan},
                         ensure_ascii=False, indent=2))
        return 0
    manager = _manager()
    try:
        status = manager.install_blocking(args.engine_id, on_progress=_print_progress)
    except AppError as exc:
        return _fail(exc)
    item = registry.get(args.engine_id).info()
    print(
        json.dumps(
            {
                "ok": not status.get("error"),
                "engine_id": args.engine_id,
                "installed": item["installed"],
                "size_mb": item["size_mb"],
                "path": item["path"],
                "status": item["status"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if item["installed"] else 1


def cmd_remove(args: argparse.Namespace) -> int:
    try:
        result = _manager().remove(args.engine_id)
    except AppError as exc:
        return _fail(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    try:
        result = _manager().select(args.engine_id)
    except AppError as exc:
        return _fail(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    try:
        payload = _manager().health(args.engine_id)
    except AppError as exc:
        return _fail(exc)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{args.engine_id}: ready={payload.get('ready')} {payload.get('detail', '')}")
    return 0 if payload.get("ready") else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engines_cli", description="多引擎管理（安装/删除/切换/健康）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出全部引擎及安装状态")
    p_list.add_argument("--json", action="store_true", help="输出 JSON（供安装器解析）")
    p_list.set_defaults(func=cmd_list)

    p_install = sub.add_parser("install", help="安装引擎（流式打印 phase/progress）")
    p_install.add_argument("engine_id")
    p_install.add_argument("--dry-run", action="store_true", help="只预览下载清单与代码仓库，不下载不安装")
    p_install.add_argument(
        "--no-deps-guard",
        action="store_true",
        help="关闭 torch 守卫（默认开启：主包 --no-deps + torch 约束文件 + 安装前后快照对比）",
    )
    p_install.add_argument(
        "--verify-only", action="store_true", help="只按锁文件校验版本，不安装（等价于 verify 子命令）"
    )
    p_install.set_defaults(func=cmd_install)

    p_deps = sub.add_parser("deps", help="只检测某个引擎的 Python 依赖（不安装）")
    p_deps.add_argument("engine_id")
    p_deps.add_argument("--json", action="store_true", help="输出 JSON（含缺失清单与建议命令）")
    p_deps.set_defaults(func=cmd_deps)

    p_verify = sub.add_parser(
        "verify", help="只按锁文件校验版本（不下载、不安装；不一致即返回非 0）"
    )
    p_verify.add_argument("engine_id")
    p_verify.add_argument("--json", action="store_true")
    p_verify.set_defaults(func=cmd_verify)

    p_remove = sub.add_parser("remove", help="删除引擎模型文件")
    p_remove.add_argument("engine_id")
    p_remove.set_defaults(func=cmd_remove)

    p_select = sub.add_parser("select", help="切换当前引擎（持久化到 runtime 配置）")
    p_select.add_argument("engine_id")
    p_select.set_defaults(func=cmd_select)

    p_health = sub.add_parser("health", help="引擎健康检查")
    p_health.add_argument("engine_id")
    p_health.add_argument("--json", action="store_true")
    p_health.set_defaults(func=cmd_health)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AppError as exc:
        return _fail(exc)


if __name__ == "__main__":
    sys.exit(main())
