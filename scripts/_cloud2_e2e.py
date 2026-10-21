"""切换运行模式到 hybrid，并用一个短任务验证「云端 GPU 真的在干活」。"""
from __future__ import annotations

import json
import time
import warnings

import httpx

warnings.filterwarnings("ignore")

c = httpx.Client(base_url="https://127.0.0.1:8443", verify=False, timeout=90)

print("① 当前状态")
st = c.get("/api/system/status").json()
print(json.dumps({k: st.get(k) for k in ("mode", "active_backend", "compute_device", "cloud_health", "tunnel")},
                 ensure_ascii=False)[:600])

print("\n② 切到 hybrid（本地优先，显存不够自动走云端）")
r = c.post("/api/settings/mode", json={"mode": "hybrid"})
print("->", r.status_code, json.dumps(r.json(), ensure_ascii=False)[:200])

print("\n③ 创建一个短任务（走云端）")
task = c.post("/api/tasks", json={
    "type": "tts",
    "text": "云端显卡测试，这是第二台主机。",
    "voice_id": "vivian",
    "mode": "cloud",
}).json()
print("task:", json.dumps(task, ensure_ascii=False)[:300])
tid = task.get("id") or task.get("task", {}).get("id")

for i in range(40):
    time.sleep(3)
    d = c.get(f"/api/tasks/{tid}").json()
    status = d.get("status")
    print(f"[{i * 3 + 3}s] {status} backend={d.get('backend')} dur={d.get('duration_ms')}")
    if status in ("succeeded", "failed", "cancelled"):
        print(json.dumps({k: d.get(k) for k in ("status", "backend", "duration_ms", "error", "wav_path")},
                         ensure_ascii=False))
        break

print("\n④ 云端侧确认（GPU 显存有波动即证明在跑）")
