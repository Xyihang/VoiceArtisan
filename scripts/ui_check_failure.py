"""失败路径 UI 检查：在缺少 1.7B 模型时，验证错误提示与「一键切换到云端」可达。

用法：python scripts/ui_check_failure.py --base http://127.0.0.1:8099
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
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(f"{args.base}/", wait_until="networkidle")
        page.wait_for_selector("text=工作台", timeout=20000)
        page.fill("#tts-text", "验证缺少模型时的错误提示与一键切换云端入口。")
        page.click("button:has-text('开始生成')")
        page.wait_for_selector("text=错误码", timeout=120000)
        time.sleep(1)
        page.screenshot(path=str(out / "11-local-missing-model.png"), full_page=True)
        page.click("button:has-text('一键切换到云端')")
        time.sleep(3)
        page.screenshot(path=str(out / "12-switch-cloud.png"), full_page=True)
        browser.close()
    print(f"失败路径截图已保存到 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
