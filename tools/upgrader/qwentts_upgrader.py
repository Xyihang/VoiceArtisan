#!/usr/bin/env python3
"""声匠 · VoiceSmith —— 独立升级工具（不依赖项目代码，纯标准库）。

用法：
    QWENTTS-Upgrader.exe                        # 图形界面
    python qwentts_upgrader.py                  # 同上
    python qwentts_upgrader.py --cli 旧目录 新目录            # 只预检
    python qwentts_upgrader.py --cli 旧目录 新目录 --apply    # 执行升级
    python qwentts_upgrader.py --rollback 旧目录 backup-xxx.zip
    python qwentts_upgrader.py --selftest

规则：
    永不覆盖  data/  models/  .cache/  .venv/  logs/  config/config.yaml
    只覆盖    其余代码与构建产物（backend/ frontend/dist/ scripts/ ...）
    只新增不删  新版本里没有的旧文件保持不动
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

KEEP_DIRS = {"data", "models", ".cache", ".venv", "venv", "logs"}
KEEP_REL_FILES = {"config/config.yaml"}
SKIP_DIR_NAMES = {"__pycache__", ".git", "node_modules", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIX = {".pyc", ".pyo", ".log"}
UPGRADE_DIR = Path("data") / "upgrade"
MUST_HAVE = ("VERSION.json", "backend/app/main.py")


# ---------------------------------------------------------------- 基础工具
def read_version(root: Path) -> str:
    try:
        return str(json.loads((root / "VERSION.json").read_text(encoding="utf-8")).get("version", ""))
    except Exception:  # noqa: BLE001
        return ""


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for piece in str(text or "").replace("-", ".").split("."):
        parts.append(int(piece) if piece.isdigit() else 0)
    return tuple(parts) or (0,)


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIR_NAMES for part in rel.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIX:
            continue
        yield path, rel


def _kept(rel: Path) -> bool:
    if rel.parts and rel.parts[0] in KEEP_DIRS:
        return True
    return rel.as_posix() in KEEP_REL_FILES


def _running_tasks(root: Path) -> int:
    db = root / "data" / "db" / "app.db"
    if not db.is_file():
        return 0
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        try:
            row = con.execute(
                "select count(*) from tasks where status in ('queued','running')"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------- 预检
def inspect(current: Path, new: Path) -> dict:
    cur = str(current)
    if not (current / "backend" / "app" / "main.py").is_file():
        return {"ok": False, "error": f"「当前目录」不像本程序：{cur}\\backend\\app\\main.py 不存在"}
    if not new.is_dir():
        return {"ok": False, "error": f"「新版本目录」不存在：{new}"}
    if current.resolve() == new.resolve():
        return {"ok": False, "error": "两个目录是同一个，无法升级"}
    missing = [name for name in MUST_HAVE if not (new / name).exists()]
    if missing:
        return {"ok": False, "error": "新版本目录缺少关键文件：" + "、".join(missing)}

    cur_ver, new_ver = read_version(current), read_version(new)
    if parse_version(new_ver) <= parse_version(cur_ver):
        return {
            "ok": False,
            "error": f"新版本 {new_ver or '未知'} 不高于当前 {cur_ver or '未知'}，无需升级",
        }

    files = [(p, rel) for p, rel in _iter_files(new) if not _kept(rel)]
    total_mb = round(sum(p.stat().st_size for p, _ in files) / 1048576, 1)
    overwrite = [rel.as_posix() for _, rel in files if (current / rel).exists()]
    fresh = len(files) - len(overwrite)
    has_dist = (new / "frontend" / "dist" / "index.html").is_file()
    has_exe = (new / "launcher" / "dist" / "QWENTTS-Setup.exe").is_file()
    running = _running_tasks(current)

    kept = sorted(
        {rel.parts[0] for p, rel in _iter_files(current) if rel.parts and rel.parts[0] in KEEP_DIRS}
    )
    try:
        free_mb = round(shutil.disk_usage(current).free / 1048576)
    except OSError:
        free_mb = 0

    return {
        "ok": True,
        "current": str(current),
        "new": str(new),
        "current_version": cur_ver,
        "new_version": new_ver,
        "copy_count": len(files),
        "copy_mb": total_mb,
        "overwrite_count": len(overwrite),
        "new_count": fresh,
        "overwrite_sample": overwrite[:10],
        "keep_dirs": kept,
        "has_frontend_dist": has_dist,
        "has_installer": has_exe,
        "running_tasks": running,
        "free_mb": free_mb,
        "need_mb": int(total_mb * 3),
        "warnings": (
            ([f"有 {running} 个任务正在运行，建议先等它们结束"] if running else [])
            + ([] if has_dist else ["新版本里没有 frontend/dist：升级后前端需要 npm 构建"])
            + ([f"磁盘剩余 {free_mb}MB，建议 ≥ {int(total_mb * 3)}MB"] if free_mb < total_mb * 3 else [])
        ),
    }


# ---------------------------------------------------------------- 备份
def _backup_root(current: Path) -> Path:
    d = current / UPGRADE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_backup(current: Path, rels: list[str], version: str, log=print) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = _backup_root(current) / f"backup-{version or 'unknown'}-{stamp}.zip"
    count = 0
    no = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in rels:
            src = current / rel
            if not src.is_file():
                continue
            try:
                zf.write(src, rel)
                count += 1
            except OSError as exc:  # noqa: BLE001
                log(f"  [跳过] {rel}：{exc}")
            no += 1
            if no % 50 == 0:
                log(f"  备份中… {no}/{len(rels)}")
    log(f"  已备份 {count} 个文件 → {target.name}（{round(target.stat().st_size / 1048576, 2)}MB）")
    return target


# ---------------------------------------------------------------- 执行
def apply(current: Path, new: Path, force: bool = False, log=print) -> dict:
    info = inspect(current, new)
    if not info.get("ok"):
        raise RuntimeError(info.get("error"))
    if info["running_tasks"] and not force:
        raise RuntimeError(f"有 {info['running_tasks']} 个任务在运行，请先等它们结束（或勾选强制继续）")

    files = [(p, rel) for p, rel in _iter_files(new) if not _kept(rel)]
    overwrite = [rel.as_posix() for _, rel in files if (current / rel).exists()]

    log(f"① 备份将被覆盖的 {len(overwrite)} 个旧文件…")
    backup = make_backup(current, overwrite, info["current_version"], log=log)

    log(f"② 复制 {len(files)} 个文件（{info['copy_mb']}MB）…")
    done = 0
    try:
        for src, rel in files:
            dst = current / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            done += 1
            if done % 40 == 0:
                log(f"  复制中… {done}/{len(files)}")
    except Exception as exc:  # noqa: BLE001
        log(f"③ 复制失败（{done}/{len(files)}）：{exc}")
        log("   正在自动回滚…")
        restore(current, backup, log=log)
        raise RuntimeError(f"升级失败已自动回滚：{exc}") from exc

    log(f"③ 复制完成，共 {len(files)} 个文件")
    _write_history(current, backup, info, len(files))
    log(f"✅ 升级完成：{info['current_version']} → {info['new_version']}")
    log("   请关闭本程序与旧服务窗口，然后双击 start.bat（或 QWENTTS-Setup.exe）重新启动")
    return {"ok": True, "backup": backup.name, "copied": len(files), **info}


def _write_history(current: Path, backup: Path, info: dict, copied: int) -> None:
    path = _backup_root(current) / "history.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except Exception:  # noqa: BLE001
        items = []
    items.append(
        {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "from": info["current_version"],
            "to": info["new_version"],
            "copied": copied,
            "backup": backup.name,
        }
    )
    path.write_text(json.dumps(items[-50:], ensure_ascii=False, indent=2), encoding="utf-8")


def restore(current: Path, backup: Path, log=print) -> dict:
    if not backup.is_file():
        raise RuntimeError(f"备份不存在：{backup}")
    count = 0
    with zipfile.ZipFile(backup) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            target = current / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    log(f"✅ 已回滚 {count} 个文件（来自 {backup.name}）")
    return {"ok": True, "restored": count, "backup": backup.name}


def list_backups(current: Path) -> list[dict]:
    d = current / UPGRADE_DIR
    if not d.is_dir():
        return []
    out = []
    for path in sorted(d.glob("backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        out.append(
            {
                "name": path.name,
                "path": str(path),
                "mb": round(path.stat().st_size / 1048576, 2),
                "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)),
            }
        )
    return out


def selftest() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        cur = Path(tmp) / "old"
        new = Path(tmp) / "new"
        for root, ver in ((cur, "1.1.12"), (new, "1.1.18")):
            (root / "backend" / "app").mkdir(parents=True)
            (root / "backend" / "app" / "main.py").write_text("print('x')\n", encoding="utf-8")
            (root / "VERSION.json").write_text(json.dumps({"version": ver}), encoding="utf-8")
        (cur / "data" / "db").mkdir(parents=True)
        (cur / "data" / "db" / "app.db").write_bytes(b"SQLite-fake")
        (cur / "config").mkdir()
        (cur / "config" / "config.yaml").write_text("run:\n  mode: local\n", encoding="utf-8")
        (cur / "models").mkdir()
        (cur / "models" / "keep.bin").write_bytes(b"model")
        (new / "requirements-torch.txt").write_text("torch==2.4.1+cu121\n", encoding="utf-8")
        (new / "config").mkdir()
        (new / "config" / "config.yaml").write_text("SHOULD NOT OVERWRITE\n", encoding="utf-8")

        info = inspect(cur, new)
        assert info["ok"], info
        assert info["current_version"] == "1.1.12" and info["new_version"] == "1.1.18"
        apply(cur, new, force=True, log=lambda *a: None)
        assert read_version(cur) == "1.1.18", "版本未更新"
        assert (cur / "requirements-torch.txt").is_file(), "新文件未复制"
        assert (cur / "config" / "config.yaml").read_text(encoding="utf-8").startswith("run:"), "config 被覆盖了"
        assert (cur / "models" / "keep.bin").read_bytes() == b"model", "models 被动了"
        assert (cur / "data" / "db" / "app.db").read_bytes() == b"SQLite-fake", "data 被动了"
        backups = list_backups(cur)
        assert backups, "没有生成备份"
        restore(cur, Path(backups[0]["path"]), log=lambda *a: None)
        assert read_version(cur) == "1.1.12", "回滚失败"
        print("SELFTEST OK：升级/保留/备份/回滚 全部通过")
    return 0


# ---------------------------------------------------------------- 图形界面
def run_gui() -> int:
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("声匠 VoiceSmith · 升级工具")
    root.geometry("820x620")

    cur_var = tk.StringVar(value=str(Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent))
    new_var = tk.StringVar()
    force_var = tk.BooleanVar(value=False)

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    def row(label: str, var: tk.StringVar, browse_title: str, line: int) -> None:
        ttk.Label(frm, text=label).grid(row=line, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=var, width=72).grid(row=line, column=1, sticky="we", padx=6)
        ttk.Button(frm, text="浏览…", command=lambda: _pick(var, browse_title)).grid(row=line, column=2)
        frm.columnconfigure(1, weight=1)

    def _pick(var: tk.StringVar, title: str) -> None:
        chosen = filedialog.askdirectory(title=title)
        if chosen:
            var.set(os.path.normpath(chosen))

    row("当前安装目录", cur_var, "选择你现在正在用的程序目录（含 backend、VERSION.json）", 0)
    row("新版本目录", new_var, "选择解压后的新版本目录（含 VERSION.json、backend）", 1)
    ttk.Checkbutton(frm, text="有任务在跑也强制升级（不推荐）", variable=force_var).grid(
        row=2, column=1, sticky="w", pady=4
    )

    log_box = tk.Text(frm, height=20, wrap="word")
    log_box.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=8)
    frm.rowconfigure(4, weight=1)

    def log(msg: str) -> None:
        log_box.insert("end", str(msg) + "\n")
        log_box.see("end")
        root.update_idletasks()

    def do_inspect() -> None:
        try:
            info = inspect(Path(cur_var.get()), Path(new_var.get()))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("预检失败", str(exc))
            return
        if not info.get("ok"):
            log("❌ " + str(info.get("error")))
            messagebox.showerror("预检失败", info.get("error"))
            return
        log(f"✅ 预检通过：{info['current_version']} → {info['new_version']}")
        log(f"   将复制 {info['copy_count']} 个文件（{info['copy_mb']}MB），其中覆盖 {info['overwrite_count']} 个、新增 {info['new_count']} 个")
        log(f"   保留不动的目录：{', '.join(info['keep_dirs']) or '（无）'}")
        log(f"   前端构建产物：{'有' if info['has_frontend_dist'] else '无'}｜安装器：{'有' if info['has_installer'] else '无'}")
        for warn in info["warnings"]:
            log(f"   ⚠ {warn}")
        log("   覆盖示例：" + ", ".join(info["overwrite_sample"][:6]))

    def do_apply() -> None:
        if not messagebox.askyesno("确认升级", "确定要升级吗？\n会先自动备份被覆盖的旧文件。"):
            return

        def worker() -> None:
            try:
                apply(Path(cur_var.get()), Path(new_var.get()), force=force_var.get(), log=log)
            except Exception as exc:  # noqa: BLE001
                log("❌ " + str(exc))
                messagebox.showerror("升级失败", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def do_rollback() -> None:
        backups = list_backups(Path(cur_var.get()))
        if not backups:
            messagebox.showinfo("回滚", "还没有备份")
            return
        win = tk.Toplevel(root)
        win.title("选择要回滚到的备份")
        win.geometry("620x320")
        lb = tk.Listbox(win)
        lb.pack(fill="both", expand=True, padx=8, pady=8)
        for item in backups:
            lb.insert("end", f"{item['at']}  {item['name']}  ({item['mb']}MB)")

        def pick() -> None:
            sel = lb.curselection()
            if not sel:
                return
            target = Path(backups[sel[0]]["path"])
            if not messagebox.askyesno("确认回滚", f"用 {target.name} 覆盖当前文件？"):
                return
            try:
                restore(Path(cur_var.get()), target, log=log)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("回滚失败", str(exc))
            win.destroy()

        ttk.Button(win, text="回滚到这个备份", command=pick).pack(pady=6)

    bar = ttk.Frame(frm)
    bar.grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
    ttk.Button(bar, text="① 预检", command=do_inspect).pack(side="left")
    ttk.Button(bar, text="② 开始升级", command=do_apply).pack(side="left", padx=8)
    ttk.Button(bar, text="回滚…", command=do_rollback).pack(side="left")

    log("说明：data/ · models/ · config/config.yaml 永远不会被覆盖；升级前会自动备份。")
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="声匠 VoiceSmith 独立升级工具")
    parser.add_argument("--cli", nargs=2, metavar=("当前目录", "新版本目录"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--rollback", nargs=2, metavar=("当前目录", "备份文件"))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()
    if args.rollback:
        return 0 if restore(Path(args.rollback[0]), Path(args.rollback[1])) else 1
    if args.cli:
        cur, new = Path(args.cli[0]), Path(args.cli[1])
        info = inspect(cur, new)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        if not info.get("ok") or not args.apply:
            return 0 if info.get("ok") else 1
        apply(cur, new, force=args.force)
        return 0
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
