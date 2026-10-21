"""UI-POLISH-1 验收脚本：对齐 / 弹窗居中 / 留白填充 / 窄屏。"""
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
        ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # A. 设置页：同行字段基线 + 控件等高
        page.goto(f"{BASE}/#/settings", wait_until="networkidle")
        page.wait_for_selector("text=运行模式", timeout=25000)
        time.sleep(2.0)
        res["field_rows"] = page.evaluate(
            """() => [...document.querySelectorAll('.field-row')].slice(0,10).map(r =>
                 [...r.children].map(c => { const b = c.getBoundingClientRect();
                   return {y: Math.round(b.y), h: Math.round(b.height)}; }))"""
        )
        res["input_heights"] = page.evaluate(
            """() => [...new Set([...document.querySelectorAll('.page .input, .page .select')]
                 .map(e => Math.round(e.getBoundingClientRect().height)))]"""
        )

        # B. 弹窗：滚动前后 top 是否恒定 + body 是否被锁
        page.evaluate(
            """() => { const b = [...document.querySelectorAll('button')]
                 .find(x => (x.textContent || '').includes('全部卸载')); if (b) b.click(); }"""
        )
        time.sleep(0.8)
        res["modal_scroll0"] = page.evaluate(
            """() => { const m = document.querySelector('.modal-layer .modal'); if (!m) return null;
                 const r = m.getBoundingClientRect();
                 return {top: Math.round(r.top), expect: Math.round((window.innerHeight - r.height) / 2)}; }"""
        )
        res["body_overflow_open"] = page.evaluate("() => getComputedStyle(document.body).overflow")
        page.evaluate("window.scrollTo(0, 2400)")
        time.sleep(0.5)
        res["modal_scroll2400"] = page.evaluate(
            """() => { const m = document.querySelector('.modal-layer .modal');
                 const r = m.getBoundingClientRect();
                 return {top: Math.round(r.top), expect: Math.round((window.innerHeight - r.height) / 2)}; }"""
        )
        res["scrollY_while_modal"] = page.evaluate("() => Math.round(window.scrollY)")
        page.keyboard.press("Escape")
        time.sleep(0.5)
        res["body_overflow_closed"] = page.evaluate("() => getComputedStyle(document.body).overflow")
        res["modal_exists_closed"] = page.evaluate("() => document.querySelectorAll('.modal-layer').length")

        # C. 工作台：右栏填充与音色卡等高
        page.goto(f"{BASE}/#/workbench", wait_until="networkidle")
        time.sleep(2.5)
        res["wb_col_heights"] = page.evaluate(
            "() => [...document.querySelectorAll('.workbench__column')].map(c => Math.round(c.getBoundingClientRect().height))"
        )
        res["wb_right_cards"] = page.evaluate(
            """() => { const col = document.querySelectorAll('.workbench__column')[1];
                 if (!col) return [];
                 return [...col.children].map(e => ({title: (e.textContent || '').trim().slice(0, 8),
                   h: Math.round(e.getBoundingClientRect().height)})); }"""
        )
        res["recent_rows"] = page.evaluate("() => document.querySelectorAll('.recent-list__row').length")
        res["shortcut_rows"] = page.evaluate("() => document.querySelectorAll('.shortcut-list > div').length")
        res["voice_card_heights"] = page.evaluate(
            "() => [...new Set([...document.querySelectorAll('.voice-card')].map(e => Math.round(e.getBoundingClientRect().height)))]"
        )

        # D. 窄屏横向溢出
        for width in (760, 333):
            page.set_viewport_size({"width": width, "height": 1000})
            time.sleep(0.6)
            for route in ("settings", "workbench", "tasks", "voices", "status"):
                page.goto(f"{BASE}/#/{route}", wait_until="networkidle")
                time.sleep(1.0)
                over = page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                res[f"overflow_{width}_{route}"] = over

        browser.close()
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
