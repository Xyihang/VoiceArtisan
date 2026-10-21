"""定位 /api/engines 卡住的原因：逐个引擎计时 status()。"""
from __future__ import annotations

import sys
import time
import traceback

sys.path.insert(0, r"E:\trae_projects\QWENTTS\backend")

from app.config import AppConfig, load  # noqa: E402
from app.engine.adapters import registry  # noqa: E402

cfg = load()
registry.setup(cfg) if hasattr(registry, "setup") else None

ids = getattr(registry, "ids", None)
engine_ids = ids() if callable(ids) else []
print("引擎:", engine_ids)

for eid in engine_ids:
    t0 = time.time()
    try:
        adapter = registry.get(eid)
        status = adapter.status()
        print(f"{eid:<20} {time.time() - t0:6.2f}s  {status[0]}")
    except Exception:  # noqa: BLE001
        print(f"{eid:<20} {time.time() - t0:6.2f}s  异常")
        traceback.print_exc(limit=2)
