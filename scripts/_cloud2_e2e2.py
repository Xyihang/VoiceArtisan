"""修正字段路径后重测云端任务。"""
from __future__ import annotations

import json
import time
import warnings

import httpx

warnings.filterwarnings("ignore")

c = httpx.Client(base_url="https://127.0.0.1:8443", verify=False, timeout=90)

print("① health")
print(json.dumps(c.get("/api/health").json(), ensure_ascii=False)[:400])

print("\n② 创建任务")
resp = c.post("/api/tasks", json={"type": "tts", "text": "云端显卡测试，第二台主机。",
                                  "voice_id": "vivian", "mode": "cloud"})
print("status", resp.status_code)
data = resp.json()
print("keys:", list(data)[:8])
task = data.get("task") or data
tid = task.get("id")
print("task id:", tid, "| backend:", task.get("backend"), "| mode:", task.get("mode"))

print("\n③ 轮询")
for i in range(30):
    time.sleep(3)
    d = c.get(f"/api/tasks/{tid}").json()
    t = d.get("task") or d
    print(f"[{i * 3 + 3}s] status={t.get('status')} backend={t.get('backend')} dur={t.get('duration_ms')} err={str(t.get('error'))[:60]}")
    if t.get("status") in ("succeeded", "failed", "cancelled"):
        print(json.dumps({k: t.get(k) for k in ("status", "backend", "duration_ms", "error", "wav_path")},
                         ensure_ascii=False))
        break
