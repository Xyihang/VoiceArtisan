"""前端 UI 冒烟检查（Playwright）：截图各页面并验证关键交互。

用法：python scripts/ui_check.py --base http://127.0.0.1:8099 --out data/logs/shots
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8099")
    parser.add_argument("--out", default="data/logs/shots")
    parser.add_argument("--headless", default="true")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless.lower() != "false",
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.on("console", lambda msg: errors.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

        page.goto(f"{args.base}/", wait_until="networkidle")
        page.wait_for_selector("text=工作台", timeout=20000)
        page.screenshot(path=str(out / "01-workbench-light.png"), full_page=True)

        # 生成一次（使用默认音色 + 示例文本）
        page.fill("#tts-text", "这是前端自动化检查：验证流式播放、首包计时与状态展示是否正常。")
        page.click("button:has-text('开始生成')")
        page.wait_for_selector("text=流式", timeout=30000)
        time.sleep(3)
        page.screenshot(path=str(out / "02-streaming.png"), full_page=True)
        page.wait_for_selector("text=已完成", timeout=60000)
        time.sleep(1)
        page.screenshot(path=str(out / "03-completed.png"), full_page=True)

        # 深色主题
        page.click("button[aria-label^='当前主题']")
        time.sleep(0.6)
        page.screenshot(path=str(out / "04-workbench-dark.png"), full_page=True)
        page.click("button[aria-label^='当前主题']")
        time.sleep(0.4)

        for route, name in (("voices", "05-voices"), ("tasks", "06-tasks"), ("settings", "07-settings"), ("status", "08-status")):
            page.goto(f"{args.base}/#/{route}", wait_until="networkidle")
            time.sleep(1.2)
            page.screenshot(path=str(out / f"{name}.png"), full_page=True)

        # 响应式：平板与手机
        page.set_viewport_size({"width": 1024, "height": 900})
        page.goto(f"{args.base}/#/workbench", wait_until="networkidle")
        time.sleep(1)
        page.screenshot(path=str(out / "09-tablet.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        time.sleep(1)
        page.screenshot(path=str(out / "10-mobile.png"), full_page=True)

        browser.close()

    if errors:
        print("浏览器控制台错误：")
        for item in errors[:20]:
            print(" -", item)
        return 1
    print(f"UI 检查通过，截图见 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
