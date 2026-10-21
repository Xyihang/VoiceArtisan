"""UI-POLISH-2 验收：设置页左导航等高、任务中心整页占满窗口。"""
from __future__ import annotations

import json
import time
import warnings

from playwright.sync_api import sync_playwright

warnings.filterwarnings("ignore")
BASE = "https://127.0.0.1:8443"


def main() -> None:
    res: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width, height in ((1440, 900), (1920, 1000)):
            ctx = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
            page = ctx.new_page()

            # 1) 设置页：导航高度 vs 内容高度
            page.goto(f"{BASE}/#/settings", wait_until="networkidle")
            page.wait_for_selector("text=运行模式", timeout=25000)
            time.sleep(2.0)
            res[f"settings_{width}"] = page.evaluate(
                """() => {
                  const nav = document.querySelector('.settings-nav');
                  const body = document.querySelector('.settings-body');
                  const col = document.querySelectorAll('.settings-body > *')[1];
                  return {
                    nav: Math.round(nav.getBoundingClientRect().height),
                    body: Math.round(body.getBoundingClientRect().height),
                    content: col ? Math.round(col.getBoundingClientRect().height) : null,
                  };
                }"""
            )

            # 2) 任务中心：整页是否正好占满窗口
            page.goto(f"{BASE}/#/tasks", wait_until="networkidle")
            time.sleep(2.0)
            res[f"tasks_{width}"] = page.evaluate(
                """() => {
                  const doc = document.documentElement;
                  const card = document.querySelector('.card--fill');
                  const pag = document.querySelector('.pagination');
                  const wrap = document.querySelector('.card--fill .table-wrap');
                  return {
                    viewport: window.innerHeight,
                    scrollHeight: doc.scrollHeight,
                    pageScroll: doc.scrollHeight - window.innerHeight,
                    cardBottom: card ? Math.round(card.getBoundingClientRect().bottom) : null,
                    paginationBottom: pag ? Math.round(pag.getBoundingClientRect().bottom) : null,
                    tableScrollable: wrap ? wrap.scrollHeight > wrap.clientHeight : null,
                  };
                }"""
            )
            ctx.close()
        browser.close()
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
