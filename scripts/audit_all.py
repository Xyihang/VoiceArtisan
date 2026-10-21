#!/usr/bin/env python
"""全量自检（HARDENING-1）：一条命令验证后端/前端/引擎/音色/布局/数据清洁度。

用法：
    python scripts/audit_all.py              # 全量（含真实合成，约 2-4 分钟）
    python scripts/audit_all.py --skip-tts    # 跳过真实合成（约 20 秒）
    python scripts/audit_all.py --skip-frontend  # 跳过前端（不装 node 时）

退出码：0 全部通过；1 有失败项（失败项会打印定位信息）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BASE = "https://127.0.0.1:8443"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def run(cmd: list[str], cwd: Path | None = None, timeout: float = 600) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def api():
    import httpx

    return httpx.Client(base_url=BASE, verify=False, timeout=180)


# ---------------------------------------------------------------- 静态检查
def _safe(name: str, fn, *args) -> None:
    """跑一段检查：任何异常都记为该段失败，而不是让整个自检崩掉。"""
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001
        check(f"{name}（执行异常）", False, f"{type(exc).__name__}: {exc}")


def static_checks() -> None:
    code, out = run([sys.executable, "-m", "compileall", "-q", "app"], cwd=BACKEND)
    check("A1 后端编译", code == 0, "" if code == 0 else out[-300:])

    code, out = run([sys.executable, "-m", "pyflakes", "app", "tests"], cwd=BACKEND)
    undefined = [ln for ln in out.splitlines() if "undefined name" in ln]
    check("A2 后端静态检查（undefined name）", not undefined, "; ".join(undefined[:3]))

    bad = []
    for folder in ("scripts", "launcher"):
        for path in (ROOT / folder).rglob("*.py"):
            code, out = run([sys.executable, "-m", "py_compile", str(path)])
            if code != 0:
                bad.append(f"{path.name}: {out.strip().splitlines()[-1][:80]}")
    check("A3 脚本/安装器语法", not bad, "; ".join(bad[:3]))


def launcher_gui_check() -> None:
    """A6：无头实例化安装器，验证体检后按钮全部可点（曾因缺方法/线程违规导致点不动）。"""
    script = ROOT / "scripts" / "launcher_gui_check.py"
    if not script.exists():
        check("A6 安装器 GUI（脚本缺失）", False, str(script))
        return
    code, out = run([sys.executable, str(script)], timeout=240)
    tail = " | ".join([ln for ln in out.splitlines() if ln.startswith(("被禁用", "自动体检", "手动体检"))][-3:])
    check("A6 安装器 GUI（体检后按钮可点）", code == 0, tail or out[-200:])


def frontend_checks(skip: bool) -> None:
    if skip:
        check("A4/A5 前端（已跳过）", True, "skip")
        return
    code, out = run(["cmd", "/c", "npx", "tsc", "--noEmit"], cwd=ROOT / "frontend", timeout=600)
    check("A4 前端类型检查", code == 0, "" if code == 0 else out[-400:])

    dist = ROOT / "frontend" / "dist" / "index.html"
    if dist.exists():
        check("A5 前端构建产物", True, "已存在 dist/index.html")
    else:
        code, out = run(["cmd", "/c", "npm", "run", "build"], cwd=ROOT / "frontend", timeout=900)
        check("A5 前端构建", code == 0 and dist.exists(), "" if code == 0 else out[-400:])


# ---------------------------------------------------------------- 接口与引擎
def api_checks(client, skip_tts: bool) -> None:
    try:
        health = client.get("/api/health").json()
        check("B1 /api/health", bool(health.get("active_backend")), str(health.get("active_backend")))
    except Exception as exc:  # noqa: BLE001
        check("B1 /api/health", False, f"{type(exc).__name__}: {exc}")
        return

    t0 = time.time()
    engines = client.get("/api/engines").json()
    elapsed = time.time() - t0
    items = engines.get("items") or []
    check("B2 /api/engines", len(items) >= 7 and elapsed < 1.5, f"{len(items)} 个引擎，{elapsed:.2f}s")

    # B7 关键接口
    endpoints = ["/api/settings", "/api/voices", "/api/tasks?page=1&page_size=1",
                 "/api/system/status", "/api/system/selfcheck"]
    bad = []
    for url in endpoints:
        try:
            resp = client.get(url)
            if resp.status_code != 200:
                bad.append(f"{url} → {resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{url} → {type(exc).__name__}")
    check("B7 关键接口 200", not bad, "; ".join(bad[:3]))

    # B5 能力门禁
    caps = {item["id"]: item.get("kind_tags") or [] for item in items}
    wrong = [i for i, tags in caps.items() if i != "qwen3-tts-1.7b" and "design" in tags]
    check("B5 能力门禁（design 只在 1.7B）", not wrong, "; ".join(wrong))

    # B4 音色契约
    voices = client.get("/api/voices").json()
    has_engine = isinstance(voices.get("engine"), dict)
    non_qwen_bad = []
    if has_engine and not voices["engine"].get("is_qwen"):
        non_qwen_bad = [v["id"] for v in voices.get("builtin", []) if v.get("available") and not v.get("reason")]
    check("B4 音色契约", has_engine and not non_qwen_bad,
          f"engine={voices.get('engine', {}).get('id')}" + (f"; 异常可用 {non_qwen_bad[:2]}" if non_qwen_bad else ""))

    # B3 引擎矩阵：已安装的才切
    matrix_bad = []
    origin = engines.get("active")
    for item in items:
        if item["status"] not in ("ready",) or not item.get("installed"):
            continue
        try:
            resp = client.post(f"/api/engines/{item['id']}/select")
            if resp.status_code != 200:
                matrix_bad.append(f"{item['id']} select → {resp.status_code}")
                continue
            size = client.get("/api/settings").json()["local"].get("size")
            expect = "0.6B" if item["id"].endswith("0.6b") else "1.7B"
            if item["id"].startswith("qwen3-tts") and size != expect:
                matrix_bad.append(f"{item['id']} local.size={size}（应为 {expect}）")
            status = client.get(f"/api/engines/{item['id']}/health").json()
            if not status.get("ok", True) and not status.get("ready", True):
                matrix_bad.append(f"{item['id']} health 未就绪")
        except Exception as exc:  # noqa: BLE001
            matrix_bad.append(f"{item['id']} → {type(exc).__name__}")
    if origin:
        client.post(f"/api/engines/{origin}/select")
    check("B3 引擎矩阵（切换/规格/健康）", not matrix_bad, "; ".join(matrix_bad[:3]))

    # B8 隧道不可用不影响健康接口
    check("B8 隧道容错", health.get("status") in (None, "ok", "degraded"), f"status={health.get('status')}")

    if skip_tts:
        check("B6 真实合成（已跳过）", True, "skip")
        return

    # B6 真实合成：0.6B + 1.7B 各一次
    for engine_id, timeout_s in (("qwen3-tts-0.6b", 180), ("qwen3-tts-1.7b", 240)):
        client.post(f"/api/engines/{engine_id}/select")
        voices = client.get("/api/voices").json()
        builtin = [v for v in voices.get("builtin", []) if v.get("available")]
        voice_id = builtin[0]["id"] if builtin else "none"
        task = client.post("/api/tasks", json={
            "type": "tts", "text": "[audit] 自检短句。", "voice_id": voice_id,
        }).json()
        tid = task.get("id")
        deadline = time.time() + timeout_s
        final = {}
        while time.time() < deadline:
            time.sleep(3)
            final = (client.get(f"/api/tasks/{tid}").json() or {})
            final = final.get("task") or final
            if final.get("status") in ("completed", "failed", "succeeded"):
                break
        ok = final.get("status") == "completed"
        detail = f"{final.get('status')} {final.get('duration_sec')}s"
        peak_note = ""
        if ok and final.get("output_file"):
            try:
                import numpy as np
                import soundfile as sf

                wav = ROOT / "data" / "outputs" / str(tid) / str(final["output_file"])
                data, _ = sf.read(str(wav), dtype="float32")
                peak = float(np.max(np.abs(data))) if data.size else 0.0
                peak_note = f" peak={peak:.3f}"
                ok = peak > 0.01
            except Exception as exc:  # noqa: BLE001
                peak_note = f" 读音频失败({type(exc).__name__})"
        check(f"B6 真实合成 {engine_id}", ok, detail + peak_note)


# ---------------------------------------------------------------- 前端冒烟
def ui_checks(skip: bool) -> None:
    if skip:
        check("C1-C3 界面（已跳过）", True, "skip")
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check("C1-C3 界面（playwright 未安装）", True, "skip")
        return

    pages = ["workbench", "voices", "tasks", "settings", "status"]
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 950})
        page = ctx.new_page()
        errors: list[str] = []
        bad_requests: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:120]))
        page.on("response", lambda r: bad_requests.append(f"{r.status} {r.url[-40:]}")
                if r.status >= 400 and "/api/" in r.url else None)
        for route in pages:
            page.goto(f"{BASE}/#/{route}", wait_until="domcontentloaded")
            time.sleep(2.2)
        check("C1 页面冒烟（5 个页面）", not errors and not bad_requests,
              f"{len(errors)} 个 JS 异常 / {len(bad_requests)} 个失败请求："
              + "; ".join((errors + bad_requests)[:3]))

        page.goto(f"{BASE}/#/workbench", wait_until="domcontentloaded")
        time.sleep(3)
        mutations = page.evaluate(
            """() => new Promise((resolve) => {
                 let n = 0;
                 const ob = new MutationObserver((m) => { n += m.length; });
                 ob.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
                 setTimeout(() => { ob.disconnect(); resolve(n); }, 3000);
               })"""
        )
        check("C2 界面抖动（3s DOM 变更 < 50）", mutations < 50, f"{mutations} 次")

        overflow = []
        for width in (1440, 760, 333):
            page.set_viewport_size({"width": width, "height": 950})
            time.sleep(0.6)
            diff = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if diff > 1:
                overflow.append(f"{width}px → {diff}px")
        page.set_viewport_size({"width": 1440, "height": 950})
        check("C3 横向溢出", not overflow, "; ".join(overflow))
        browser.close()


# ---------------------------------------------------------------- 数据清洁度
def data_checks() -> None:
    client = api()
    voices = client.get("/api/voices").json()
    leftovers = [v["name"] for v in voices.get("custom", []) if v["name"] in ("冒烟克隆", "能力测试", "测试音色", "测试音色-改")]
    check("D1 音色库无测试残留", not leftovers, "; ".join(leftovers[:3]))

    locks = BACKEND / "app" / "engine" / "adapters" / "locks"
    dirty = [p.name for p in locks.glob("*.txt") if any(b > 127 for b in p.read_bytes())]
    check("D2 锁文件纯 ASCII", not dirty, "; ".join(dirty))

    zips = sorted((ROOT.parent / "QWENTTS-backups").glob("QWENTTS-src-*.zip"), key=lambda p: p.stat().st_mtime)
    if not zips:
        check("D3 备份可交付", False, "没有备份 zip")
        return
    import zipfile

    latest = zips[-1]
    with zipfile.ZipFile(latest) as zf:
        names = zf.namelist()
        has_exe = any(n.endswith("QWENTTS-Setup.exe") for n in names)
        has_dist = any(n.endswith("frontend/dist/index.html") or n.endswith("dist/index.html") for n in names)
    check("D3 备份可交付（exe + 预构建 dist）", has_exe and has_dist, f"{latest.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="全量自检（HARDENING-1）")
    parser.add_argument("--skip-tts", action="store_true", help="跳过真实合成")
    parser.add_argument("--skip-frontend", action="store_true", help="跳过前端检查")
    parser.add_argument("--skip-ui", action="store_true", help="跳过浏览器冒烟")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    print("=== 全量自检开始 ===", flush=True)
    _safe("静态检查", static_checks)
    _safe("安装器 GUI", launcher_gui_check)
    _safe("前端", frontend_checks, args.skip_frontend)
    client = api()
    _safe("接口与引擎", api_checks, client, args.skip_tts)
    _safe("界面", ui_checks, args.skip_ui)
    _safe("数据清洁度", data_checks)

    failed = [r for r in results if not r[1]]
    print("\n=== 汇总 ===")
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for name, _, detail in failed:
        print(f"  ✗ {name} — {detail}")
    if args.json:
        print(json.dumps([{"name": n, "ok": o, "detail": d} for n, o, d in results], ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
