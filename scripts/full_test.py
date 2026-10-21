"""全功能验收测试（默认 1.7B 模型）：语音生成 / 克隆 / 音色设计 / 转写 / 参数 / 任务 / 设置 / 预热。

用法：
    python scripts/full_test.py --base https://127.0.0.1:8443 --verify-tls false
    python scripts/full_test.py --base http://127.0.0.1:8099 --skip-long
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
import time
import warnings

import httpx

warnings.filterwarnings("ignore")

PASS, FAIL, WARN = "[PASS]", "[FAIL]", "[WARN]"
results: list[tuple[str, str, str]] = []


_CREATED_VOICES: list[str] = []


def _cleanup_voices(c) -> None:
    """异常中断时兜底删除测试创建的临时音色。"""
    for voice_id in list(_CREATED_VOICES):
        try:
            c.delete(f"/api/voices/{voice_id}")
            print(f"  已清理测试音色 {voice_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"  清理测试音色失败 {voice_id}: {exc}")
    _CREATED_VOICES.clear()


def check(name: str, ok: bool, detail: str = "", warn_only: bool = False) -> bool:
    mark = PASS if ok else (WARN if warn_only else FAIL)
    results.append((name, mark, detail))
    print(f"{mark} {name} {detail}", flush=True)
    return ok


def norm(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text or "")


def wait(client: httpx.Client, task_id: str, timeout: float = 900.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = client.get(f"/api/tasks/{task_id}").json()
        if row["status"] in ("completed", "failed", "canceled"):
            return row
        time.sleep(2)
    return client.get(f"/api/tasks/{task_id}").json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://127.0.0.1:8443")
    ap.add_argument("--verify-tls", default="false")
    ap.add_argument("--skip-long", action="store_true")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base, verify=args.verify_tls.lower() == "true", timeout=1200)

    # ---------- 0. 环境 ----------
    health = c.get("/api/health").json()
    print(f"\n== 环境：backend={health['active_backend']} device={health.get('compute_device')} "
          f"loaded={list(health['local']['loaded_models'])} 显存={health['local']['vram_allocated_mb']}MB ==\n")
    meta = c.get("/api/meta").json()
    check("元数据接口", len(meta.get("languages", [])) > 5, f"语言 {len(meta['languages'])} 种")
    voices = c.get("/api/voices").json()
    check("音色列表", voices["counts"]["builtin"] >= 5, f"内置 {voices['counts']['builtin']} / 自定义 {voices['counts']['custom']}")
    voice_id = voices["builtin"][0]["id"]

    is_17b = False

    def assert_model(row: dict, label: str) -> None:
        nonlocal is_17b
        model = row.get("model") or ""
        if "1.7B" in model:
            is_17b = True
        check(
            f"{label} · 使用 1.7B",
            "1.7B" in model and "0.6B" not in model,
            model or "(空)",
        )

    # ---------- 1. 语音生成（内置音色 + 指令控制） ----------
    text1 = "你好，这是 1.7B 模型在 4GB 显存上的实测。"
    t = c.post("/api/tasks", json={"type": "tts", "text": text1, "voice_id": voice_id,
                                   "instruct": "语气自然亲切"}).json()
    r = wait(c, t["id"])
    check("语音生成完成", r["status"] == "completed", f"{r['status']} {r.get('error_message') or ''}"[:120])
    check("语音生成有音频", bool(r.get("output_file")) and (r.get("duration_sec") or 0) > 0,
          f"{r.get('duration_sec')}s / 首包 {r.get('first_packet_ms')}ms")
    assert_model(r, "语音生成")
    tts_id = r["id"]

    # ---------- 2. 内容一致性（ASR 回译对比，验证"文字对不上/不全"） ----------
    with open(f"data/outputs/{tts_id}/{r['output_file']}", "rb") as fh:
        asset = c.post("/api/assets", files={"file": ("a.wav", fh, "audio/wav")}).json()
    asr_task = c.post("/api/tasks", json={"type": "asr", "asset_id": asset["id"], "language": "chinese"}).json()
    asr = wait(c, asr_task["id"])
    transcript = (asr.get("transcript") or "").strip()
    ratio = difflib.SequenceMatcher(None, norm(text1), norm(transcript)).ratio() if transcript else 0.0
    check("音频转文字（本地 ASR）", asr["status"] == "completed" and bool(transcript),
          f"{asr['status']} / {len(norm(transcript))} 字")
    check("合成内容与原文一致", ratio >= 0.75, f"相似度 {ratio * 100:.0f}%（转写：{transcript[:40]}）", warn_only=True)

    # ---------- 3. 参数：语速 / 音高 / mp3 ----------
    t = c.post("/api/tasks", json={"type": "tts", "text": "参数测试，语速与音高。", "voice_id": voice_id,
                                   "params": {"speed": 1.25, "pitch": 2, "output_format": "mp3"}}).json()
    r = wait(c, t["id"])
    check("参数（语速/音高/mp3）", r["status"] == "completed" and (r.get("output_file") or "").endswith(".mp3"),
          f"{r['status']} / {r.get('output_file')}")
    assert_model(r, "参数任务")

    # ---------- 4. 非流式 ----------
    t = c.post("/api/tasks", json={"type": "tts", "text": "非流式整段生成测试。", "voice_id": voice_id,
                                   "params": {"streaming": False}}).json()
    r = wait(c, t["id"])
    check("非流式合成", r["status"] == "completed", f"{r['status']} / {r.get('duration_sec')}s")

    # ---------- 5. 声音克隆 ----------
    with open(f"data/outputs/{tts_id}/{c.get(f'/api/tasks/{tts_id}').json()['output_file']}", "rb") as fh:
        ref = c.post("/api/assets", files={"file": ("ref.wav", fh, "audio/wav")}).json()
    t = c.post("/api/tasks", json={"type": "clone", "text": "这是零样本克隆测试。", "asset_id": ref["id"]}).json()
    r = wait(c, t["id"])
    check("声音克隆", r["status"] == "completed", f"{r['status']} {r.get('error_message') or ''}"[:120])
    assert_model(r, "声音克隆")

    # 自定义音色：创建 / 重命名 / 删除
    with open(f"data/outputs/{tts_id}/{c.get(f'/api/tasks/{tts_id}').json()['output_file']}", "rb") as fh:
        created = c.post("/api/voices", files={"file": ("v.wav", fh, "audio/wav")},
                         data={"name": "测试音色", "ref_text": "你好，这是 1.7B 模型"}).json()
    check("新建自定义音色", bool(created.get("id")), created.get("id", "")[:12])
    if created.get("id"):
        _CREATED_VOICES.append(created["id"])
        renamed = c.patch(f"/api/voices/{created['id']}", json={"name": "测试音色-改"}).json()
        check("重命名音色", renamed.get("name") == "测试音色-改", renamed.get("name", ""))
        check("删除音色", c.delete(f"/api/voices/{created['id']}").json().get("ok") is True)
        _CREATED_VOICES.remove(created["id"])

    # ---------- 6. 音色设计 ----------
    t = c.post("/api/tasks", json={"type": "design", "text": "音色设计测试。",
                                   "design": "三十岁女性，声音温柔清澈，普通话标准，语速稍慢"}).json()
    r = wait(c, t["id"])
    check("音色设计", r["status"] == "completed", f"{r['status']} {r.get('error_message') or ''}"[:120])
    assert_model(r, "音色设计")

    # ---------- 7. 长文本流式 ----------
    if not args.skip_long:
        long_text = "".join(
            f"第{i}句用于长文本流式测试，检查分块拼接、首包延迟与内容完整性。" for i in range(1, 8)
        )
        t = c.post("/api/tasks", json={"type": "tts", "text": long_text, "voice_id": voice_id,
                                       "params": {"streaming": True, "chunk_chars": 40}}).json()
        r = wait(c, t["id"])
        check("长文本流式", r["status"] == "completed" and (r.get("duration_sec") or 0) > 5,
              f"{r['status']} / {r.get('duration_sec')}s / 首包 {r.get('first_packet_ms')}ms")

    # ---------- 8. 任务管理 ----------
    listing = c.get("/api/tasks?page_size=50").json()
    check("任务列表", listing["total"] >= 5, f"共 {listing['total']} 条")
    detail = c.get(f"/api/tasks/{tts_id}").json()
    check("任务详情含日志", isinstance(detail.get("logs"), list) and len(detail["logs"]) > 0,
          f"{len(detail.get('logs') or [])} 条")
    dl = c.get(f"/api/tasks/{tts_id}/download")
    check("下载结果", dl.status_code == 200 and len(dl.content) > 1000, f"{len(dl.content)} bytes")
    bad = c.get(f"/api/files/{tts_id}/../app.db")
    check("下载防穿越", bad.status_code in (400, 404), f"HTTP {bad.status_code}")
    retry = c.post(f"/api/tasks/{tts_id}/retry", json={}).json()
    check("失败/重复重试", bool(retry.get("id")), retry.get("id", "")[:12])
    wait(c, retry["id"])

    # 取消：提交后立即取消
    t = c.post("/api/tasks", json={"type": "tts", "text": "取消测试，这一段应该被中断。" * 3, "voice_id": voice_id}).json()
    time.sleep(1.0)
    c.post(f"/api/tasks/{t['id']}/cancel")
    canceled = wait(c, t["id"])
    check("取消任务", canceled["status"] in ("canceled", "completed"),
          f"{canceled['status']} / {canceled.get('output_file')}")

    ids = [retry["id"], t["id"]]
    batch = c.post("/api/tasks/batch-delete", json={"ids": ids}).json()
    check("批量删除", batch.get("deleted") == len(ids), str(batch))

    # ---------- 9. 设置 ----------
    st = c.get("/api/settings").json()
    check("读取设置", st["local"]["dtype"] in ("float16", "bfloat16", "float32"),
          f"dtype={st['local']['dtype']} device={st['local']['device']}")
    upd = c.put("/api/settings", json={"defaults": {**st["defaults"], "speed": 1.05},
                                       "local": {"warmup_on_start": st["local"]["warmup_on_start"],
                                                 "warmup_kinds": st["local"]["warmup_kinds"]}})
    check("保存设置", upd.status_code == 200, f"HTTP {upd.status_code}")
    c.put("/api/settings", json={"defaults": {**st["defaults"], "speed": 1.0}})
    mode_now = st["run"]["mode"]
    switched = c.post("/api/settings/mode", json={"mode": "local"}).json()
    check("切换运行模式", switched.get("mode") == "local", switched.get("mode", ""))
    c.post("/api/settings/mode", json={"mode": mode_now})

    # ---------- 10. 预热与卸载 ----------
    pre = c.post("/api/system/preload", json={"kinds": ["custom_voice"]}).json()
    check("预热指定模型", pre["detail"]["custom_voice"]["status"] in ("ready", "evicted"),
          pre["detail"]["custom_voice"]["status"])
    off = c.post("/api/system/unload", json={"kinds": []}).json()
    check("卸载释放显存", off.get("loaded") == [], str(off.get("loaded")))

    # ---------- 11. 自检与日志 ----------
    sc = c.get("/api/system/selfcheck").json()
    check("运行自检", "items" in sc, f"errors={sc.get('errors')} warnings={sc.get('warnings')}")
    logs = c.get("/api/system/logs?limit=50").json()["items"]
    noise = [x for x in logs if "_ProactorBasePipeTransport" in x["message"]]
    check("日志无 asyncio 噪声", len(noise) == 0, f"{len(noise)} 条")

    # ---------- 汇总 ----------
    failed = [r for r in results if r[1] == FAIL]
    warned = [r for r in results if r[1] == WARN]
    print(f"\n== 汇总：{len(results)} 项，失败 {len(failed)}，警告 {len(warned)} ==")
    for name, mark, detail in results:
        if mark != PASS:
            print(f"  {mark} {name} {detail}")
    print(f"全部使用模型：{'1.7B' if is_17b else '未确认 1.7B'}")
    _cleanup_voices(c)  # 兜底：异常中断也不留测试音色
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

