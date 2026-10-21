"""接口冒烟测试：覆盖健康、音色、任务全生命周期、下载与设置。

用法：
    python scripts/smoke_test.py --base http://127.0.0.1:8099 --mock
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import wave

import httpx

PASS, FAIL = "[PASS]", "[FAIL]"
results: list[tuple[str, bool, str]] = []


CREATED_VOICES: list[str] = []


def _cleanup_created_voices(client) -> None:
    """删除本次冒烟测试创建的音色（脚本自己造的"冒烟克隆"不该留在用户音色库里）。"""
    for voice_id in CREATED_VOICES:
        try:
            resp = client.delete(f"/api/voices/{voice_id}")
            if resp.status_code < 400:
                print(f"  已清理测试音色 {voice_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"  清理测试音色失败 {voice_id}: {exc}")
    CREATED_VOICES.clear()


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"{PASS if cond else FAIL} {name} {detail}")


def make_wav(seconds: float = 1.0, sr: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(int(sr * seconds)):
            v = int(6000 * ((i // 120) % 2 * 2 - 1))
            frames += int(v).to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def wait_task(client: httpx.Client, task_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = client.get(f"/api/tasks/{task_id}").json()
        if row["status"] in ("completed", "failed", "canceled"):
            return row
        time.sleep(0.4)
    return client.get(f"/api/tasks/{task_id}").json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8099")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base, timeout=60.0, verify=False)

    health = client.get("/api/health").json()
    check("健康检查", health.get("status") == "ok", f"mode={health.get('mode')} active={health.get('active_backend')}")

    meta = client.get("/api/meta").json()
    check("元数据", len(meta.get("languages", [])) > 5, f"语言 {len(meta.get('languages', []))} 种")

    voices = client.get("/api/voices").json()
    check("内置音色", voices["counts"]["builtin"] >= 5, f"内置 {voices['counts']['builtin']} 个")
    voice_id = voices["builtin"][0]["id"]

    # 1) 文本转语音
    task = client.post("/api/tasks", json={
        "type": "tts", "text": "这是接口冒烟测试，用于验证语音生成链路是否完整。",
        "voice_id": voice_id, "instruct": "语气自然一点", "params": {"speed": 1.05},
    }).json()
    check("创建 TTS 任务", bool(task.get("id")), task.get("id", ""))
    done = wait_task(client, task["id"])
    check("TTS 完成", done["status"] == "completed", f"{done['status']} {done.get('error_message') or ''}")
    check("首包延迟记录", done.get("first_packet_ms") is not None, f"{done.get('first_packet_ms')}ms")
    check("音频文件", bool(done.get("output_file")), done.get("output_file") or "")

    if done.get("output_file"):
        r = client.get(f"/api/tasks/{task['id']}/download")
        check("下载结果", r.status_code == 200 and len(r.content) > 1000, f"{len(r.content)} bytes")
        bad = client.get(f"/api/files/{task['id']}/../app.db")
        check("下载白名单防穿越", bad.status_code in (400, 404), f"HTTP {bad.status_code}")

    # 2) 声音克隆（上传参考音频）
    files = {"file": ("ref.wav", make_wav(2.0), "audio/wav")}
    data = {"text": "克隆链路冒烟测试。", "voice_name": "冒烟克隆"}
    clone = client.post("/api/clone", files=files, data=data).json()
    check("创建克隆任务", bool(clone.get("id")), clone.get("id", ""))
    # 记下 /api/clone 顺带创建的音色，测试结束要删掉（否则会堆一堆"冒烟克隆"）
    if clone.get("voice_id"):
        CREATED_VOICES.append(clone["voice_id"])
    clone_done = wait_task(client, clone["id"])
    check("克隆完成", clone_done["status"] == "completed", clone_done["status"])

    # 3) 音色设计
    design = client.post("/api/design", data={
        "text": "音色设计冒烟测试。",
        "design": "年轻女性，温柔，语速稍慢，普通话标准",
    }).json()
    design_done = wait_task(client, design["id"])
    check("音色设计完成", design_done["status"] == "completed", design_done["status"])

    # 4) 音频转文字（上传音频创建任务）
    asset = client.post("/api/assets", files={"file": ("asr.wav", make_wav(1.5), "audio/wav")}).json()
    check("上传音频资产", bool(asset.get("id")), f"{asset.get('duration_sec')}s")
    asr = client.post("/api/tasks", json={"type": "asr", "asset_id": asset["id"], "language": "chinese"}).json()
    asr_done = wait_task(client, asr["id"])
    check("转写任务创建", bool(asr_done.get("id")), asr_done["status"])

    # 5) 任务列表 / 详情 / 取消 / 重试 / 批量删除
    listing = client.get("/api/tasks", params={"page_size": 50}).json()
    check("任务列表", listing["total"] >= 3, f"共 {listing['total']} 条")
    check("任务详情日志", isinstance(client.get(f"/api/tasks/{task['id']}").json().get("logs"), list))

    retry = client.post(f"/api/tasks/{task['id']}/retry", json={"force_backend": None}).json()
    check("失败重试", retry.get("retry_of") == task["id"], retry.get("id", ""))
    wait_task(client, retry["id"])

    # 清理本次测试创建的音色（失败也照删，避免残留）
    _cleanup_created_voices(client)

    ids = [task["id"], clone["id"]]
    batch = client.post("/api/tasks/batch-delete", json={"ids": ids}).json()
    check("批量删除", batch.get("deleted") == len(ids), str(batch))

    # 6) 设置
    st = client.get("/api/settings").json()
    check("读取设置", st["run"]["mode"] in ("local", "cloud", "hybrid"), st["run"]["mode"])
    switched = client.post("/api/settings/mode", json={"mode": "cloud"}).json()
    check("切换模式", switched.get("mode") == "cloud")
    client.post("/api/settings/mode", json={"mode": st["run"]["mode"]})

    # 7) 自检与日志
    sc = client.get("/api/system/selfcheck").json()
    check("运行自检", "items" in sc, f"errors={sc.get('errors')}")
    logs = client.get("/api/system/logs", params={"limit": 10}).json()
    check("日志读取", len(logs["items"]) > 0, f"{len(logs['items'])} 条")

    failed = [r for r in results if not r[1]]
    print(f"\n合计 {len(results)} 项，失败 {len(failed)} 项")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
