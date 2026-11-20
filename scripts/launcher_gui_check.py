"""GUI 自动化验证：体检完成后按钮必须可点（Tk 无头运行，真实实例化安装器）。"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = str(ROOT / "launcher" / "voiceartisan_launcher.py")

spec = importlib.util.spec_from_file_location("voiceartisan_launcher", LAUNCHER)
mod = importlib.util.module_from_spec(spec)
sys.modules["voiceartisan_launcher"] = mod
spec.loader.exec_module(mod)

try:
    app = mod.LauncherApp(initial_root=ROOT)
except TypeError:
    # 构造签名不同则退回无参
    app = mod.LauncherApp()

win = app.win
win.withdraw()  # 不弹窗

# 等自动体检跑完（最多 60s）
deadline = time.time() + 60
while time.time() < deadline and app.busy:
    win.update()
    time.sleep(0.2)

print("自动体检结束：busy =", app.busy)

# 手动点一次「环境体检」
app.run_checks()
deadline = time.time() + 90
while time.time() < deadline and app.busy:
    win.update()
    time.sleep(0.2)
print("手动体检结束：busy =", app.busy)

# 再给后台刷新线程一点时间（异步 refresh_checks_quietly）
end = time.time() + 8
while time.time() < end:
    win.update()
    time.sleep(0.2)

states = {}
for text, button in app.button_map.items():
    states[text] = str(button.cget("state"))
print("按钮状态：")
for text, state in states.items():
    print(f"  {text:<10} {state}")

blocked = [t for t, st in states.items() if st == "disabled"]
print("\n被禁用的按钮：", blocked or "无 ✅")
print("提示文字：", app.tip_var.get())
win.destroy()
sys.exit(0 if not blocked else 1)
