import sys, pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent
HTML = (ROOT / "poster.html").as_uri()
OUT = pathlib.Path(r"E:\trae_projects\QWENTTS\docs\overview")
OUT.mkdir(parents=True, exist_ok=True)

names = {1: "01-overview", 2: "02-workbench-voices", 3: "03-tasks",
         4: "04-settings", 5: "05-ecosystem"}

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2.5)
    page.goto(HTML)
    page.wait_for_timeout(600)
    # 溢出体检：任何元素超出 sheet 边界或自身滚动溢出都报出来
    report = page.evaluate("""() => {
      const out = [];
      for (const sheet of document.querySelectorAll('.sheet')) {
        const sb = sheet.getBoundingClientRect();
        if (sheet.scrollHeight > sheet.clientHeight + 1 || sheet.scrollWidth > sheet.clientWidth + 1)
          out.push(`${sheet.id}: sheet overflow ${sheet.scrollWidth}x${sheet.scrollHeight} vs ${sheet.clientWidth}x${sheet.clientHeight}`);
        for (const el of sheet.querySelectorAll('*')) {
          const r = el.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) continue;
          if (r.right > sb.right + 0.6 || r.bottom > sb.bottom + 0.6 || r.left < sb.left - 0.6 || r.top < sb.top - 0.6)
            out.push(`${sheet.id}: OOB ${el.className || el.tagName} ${Math.round(r.left)},${Math.round(r.top)},${Math.round(r.right)},${Math.round(r.bottom)}`);
          if (el.scrollHeight > el.clientHeight + 2 && getComputedStyle(el).overflow !== 'visible' && el.clientHeight > 0)
            out.push(`${sheet.id}: clipped ${el.className || el.tagName} ${el.clientHeight}->${el.scrollHeight}`);
        }
      }
      return out.slice(0, 40);
    }""")
    for line in report:
        print("WARN", line)
    for i, sheet in enumerate(page.query_selector_all(".sheet"), start=1):
        path = OUT / f"{names[i]}.png"
        sheet.screenshot(path=str(path))
        print(f"ok {path.name} {path.stat().st_size/1024:.0f} KB")
    b.close()
