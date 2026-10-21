"""引擎卡片「依赖检测」视觉检查（Playwright）：截图 + 关键断言。

用法（先起后端与前端 dev server）：
    python scripts/ui_check_engine_deps.py --base http://localhost:5173 --out data/logs/shots

覆盖四种状态：
1. 依赖已就绪（voxcpm1.5，绿色小字）；
2. 依赖缺失（cosyvoice3-0.5b，黄色提示块 + 缺失清单）；
3. 展开后的逐包状态（`hydra-core 1.3.7 ✓` / `conformer — ✗`）；
4. torch 被替换的安装失败（用 mock WebSocket 推 `engine.install` 的 error 事件，
   不真的装任何包）→ 红色提示 + 恢复命令复制按钮。
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

TORCH_ERROR = (
    "安装过程改动了 torch（torch 2.4.1+cu121 → 2.14.1+cpu；CUDA 12.1 → 未知；"
    "CUDA 可用性 可用 → 不可用），已中止；请用 "
    "`pip install torch==2.4.1+cu121 --index-url https://download.pytorch.org/whl/cu121` 恢复"
)


def card(page, engine_id: str):
    return page.locator(f'[data-engine-id="{engine_id}"]')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:5173")
    parser.add_argument("--out", default="data/logs/shots")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda exc: problems.append(f"pageerror: {exc}"))
        page.on(
            "console",
            lambda msg: problems.append(f"console: {msg.text}")
            if msg.type == "error" and "WebSocket connection" not in msg.text
            else None,
        )

        # ---- 1) 依赖已就绪 ----
        page.goto(f"{args.base}/#/settings", wait_until="networkidle")
        page.wait_for_selector('[data-engine-id="cosyvoice3-0.5b"]', timeout=30000)
        time.sleep(1.0)
        vox = card(page, "voxcpm1.5")
        vox.screenshot(path=str(out / "engine-deps-ready.png"))
        ready_text = vox.inner_text()
        if "依赖已就绪" not in ready_text:
            problems.append(f"voxcpm1.5 卡片没有显示「依赖已就绪」：{ready_text[:200]}")

        # ---- 2) 依赖缺失 + 3) 逐包状态 ----
        cosy = card(page, "cosyvoice3-0.5b")
        cosy.screenshot(path=str(out / "engine-deps-missing.png"))
        cosy_text = cosy.inner_text()
        for expected in ("需要安装依赖", "conformer、diffusers、HyperPyYAML", "复制安装命令"):
            if expected not in cosy_text:
                problems.append(f"cosyvoice3 卡片缺少文案「{expected}」")
        page.screenshot(path=str(out / "engine-deps-page-light.png"), full_page=True)

        cosy.get_by_role("button", name="查看 19 个依赖").click()
        time.sleep(0.5)
        expanded = cosy.inner_text()
        if "✗" not in expanded or "✓" not in expanded:
            problems.append("展开后没有同时出现 ✓ / ✗ 状态")
        if "—" not in expanded:
            problems.append("展开后缺失项没有显示「—」占位")
        cosy.screenshot(path=str(out / "engine-deps-expanded.png"))

        # ---- 4) torch 被替换：mock WebSocket 推一条安装失败事件 ----
        fake = {
            "topic": "engines",
            "event": "engine.install",
            "ts": time.time(),
            "data": {
                "engine_id": "cosyvoice3-0.5b",
                "phase": "error",
                "progress": 1.0,
                "message": TORCH_ERROR,
                "done": True,
            },
        }

        sockets: list = []

        def on_ws(ws) -> None:
            ws.on_message(lambda message: None)  # 前端不发 subscribe 帧，仅占位
            if "/ws/events" in ws.url:
                sockets.append(ws)

        page.route_web_socket("**", on_ws)
        # 新开一个页面：mock WebSocket 必须在首次导航前注册才生效
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.route_web_socket("**", on_ws)
        page.goto(f"{args.base}/#/settings", wait_until="networkidle")
        page.wait_for_selector('[data-engine-id="cosyvoice3-0.5b"]', timeout=30000)
        cosy = card(page, "cosyvoice3-0.5b")
        for _ in range(60):  # 最多等 15s，直到前端连上 /ws/events
            if sockets:
                break
            time.sleep(0.25)
        if not sockets:
            problems.append("mock WebSocket 未被拦截（/ws/events 没连上）")
        else:
            # 等前端把 socket.onmessage 绑好再推，避免消息在 JS 挂载前到达被丢弃
            time.sleep(0.5)
            payload_text = json.dumps(fake, ensure_ascii=False)
            for sock in list(sockets):  # 可能同时存在多个页面/重连的 socket，逐个推
                try:
                    sock.send(payload_text)
                except Exception as exc:  # noqa: BLE001 - 旧连接已断开就跳过
                    print(f"  ! 推送失败（{sock.url}）：{type(exc).__name__}: {exc}")
        try:
            page.wait_for_selector("text=安装已中止：torch 被替换", timeout=8000)
            torch_text = cosy.inner_text()
            if "复制恢复命令" not in torch_text:
                problems.append("torch 错误块没有「复制恢复命令」按钮")
            if "pip install torch==2.4.1+cu121" not in torch_text:
                problems.append("torch 错误块没有展示恢复命令")
            cosy.screenshot(path=str(out / "engine-deps-torch-error.png"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"torch 错误块未渲染：{type(exc).__name__}: {exc}")

        # ---- 深色主题下的同一状态（主题存在 localStorage，直接置位后重载） ----
        page.evaluate("localStorage.setItem('qwentts.theme', 'dark')")
        page.reload(wait_until="networkidle")
        page.wait_for_selector('[data-engine-id="cosyvoice3-0.5b"]', timeout=30000)
        for _ in range(40):
            if any("/ws/events" in sock.url for sock in sockets):
                break
            time.sleep(0.25)
        time.sleep(0.8)
        for sock in list(sockets):
            try:
                sock.send(json.dumps(fake, ensure_ascii=False))
            except Exception:  # noqa: BLE001 - 旧连接已断开
                pass
        page.wait_for_selector("text=安装已中止：torch 被替换", timeout=8000)
        if page.evaluate("document.documentElement.dataset.theme") != "dark":
            problems.append("深色主题没有生效")
        time.sleep(0.4)
        card(page, "cosyvoice3-0.5b").screenshot(path=str(out / "engine-deps-torch-error-dark.png"))
        page.evaluate("localStorage.setItem('qwentts.theme', 'light')")
        browser.close()

    for name in sorted(out.glob("engine-deps-*.png")):
        print(f"截图：{name}（{name.stat().st_size // 1024}KB）")
    if problems:
        print("\n发现问题：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\n全部检查通过（4 种状态均已渲染）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
