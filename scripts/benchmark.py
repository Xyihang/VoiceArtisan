"""实测脚本：首包延迟、实时率、显存占用、流式连续性。

对运行中的服务发起真实任务（TTS / Clone / Design / ASR），通过 WebSocket 统计首包到达时间，
完成后汇总为 JSON 与 Markdown 表格。

用法：
    python scripts/benchmark.py --base http://127.0.0.1:8099 --rounds 3 --out docs/BENCHMARK.md
    python scripts/benchmark.py --base https://localhost:8443 --verify-tls false
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import statistics
import sys
import time
import wave
from pathlib import Path

import httpx
from websockets.sync.client import connect as ws_connect

TEXTS = {
    "short": "你好，这是首包延迟实测。",
    "sentence": "欢迎使用 Qwen3-TTS 语音生成系统，它支持文本转语音、声音克隆、音色设计与音频转文字。",
    "long": (
        "长文本流式合成测试。第一句用于观察首包到达时间。第二句用于检查分块调度是否连续、有没有断续。"
        "第三句包含中英混排：Qwen3-TTS supports cross-lingual cloning and instruction control. "
        "第四句继续拉长文本，用来评估实时率在高负载下的表现。最后一句结束整段测试。"
    ),
}


def make_wav(seconds: float = 4.0, sr: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(int(sr * seconds)):
            v = int(3000 * ((i // 100) % 2 * 2 - 1))
            frames += int(v).to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def run_task(client: httpx.Client, base: str, body: dict, timeout: float = 300.0) -> dict:
    """创建任务并通过 WebSocket 计时首包，返回统计结果。"""
    created = client.post("/api/tasks", json=body).json()
    task_id = created["id"]
    if not task_id:
        raise RuntimeError(f"创建任务失败：{created}")

    ws_url = base.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + f"/ws/tasks/{task_id}"
    first_chunk_ms = None
    chunk_times: list[float] = []
    chunk_durations: list[float] = []
    started = time.perf_counter()

    try:
        with ws_connect(ws_url, open_timeout=15, max_size=None) as ws:
            while True:
                raw = ws.recv(timeout=timeout)
                event = json.loads(raw)
                name = event.get("event")
                if name == "task.chunk":
                    data = event["data"]
                    payload = base64.b64decode(data["wav_b64"])
                    if first_chunk_ms is None:
                        first_chunk_ms = int((time.perf_counter() - started) * 1000)
                    chunk_times.append(time.perf_counter() - started)
                    chunk_durations.append(float(data["duration"]))
                    _ = payload
                elif name == "task.final":
                    break
                elif name == "task.update" and event.get("data", {}).get("status") in (
                    "failed",
                    "canceled",
                ):
                    break
    except Exception as exc:  # noqa: BLE001
        print(f"  ! WebSocket 统计失败：{exc}")

    task = client.get(f"/api/tasks/{task_id}").json()
    gaps = [round((chunk_times[i] - chunk_times[i - 1]) * 1000) for i in range(1, len(chunk_times))]
    return {
        "id": task_id,
        "status": task["status"],
        "first_packet_ms_ws": first_chunk_ms,
        "first_packet_ms_server": task.get("first_packet_ms"),
        "elapsed_ms": task.get("elapsed_ms"),
        "duration_sec": task.get("duration_sec"),
        "rtf": task.get("rtf"),
        "vram_mb": task.get("vram_mb"),
        "backend": task.get("backend"),
        "model": task.get("model"),
        "degraded": task.get("degraded"),
        "chunks": len(chunk_times),
        "max_gap_ms": max(gaps) if gaps else None,
        "error": task.get("error_message"),
    }


def summarize(rows: list[dict]) -> str:
    ok = [r for r in rows if r["status"] == "completed"]
    if not ok:
        return "| 场景 | 结果 |\n| --- | --- |\n| 全部场景 | 失败：" + str(rows[0].get("error")) + " |\n"
    def avg(key):
        values = [r[key] for r in ok if r.get(key) is not None]
        return round(statistics.mean(values), 1) if values else None
    lines = [
        "| 场景 | 首次首包(客户端) | 首包(服务端) | 单次耗时 | 音频时长 | RTF | 分块数 | 最大分块间隔 | 显存 | 模型 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in ok:
        lines.append(
            "| {scene} | {fp1} ms | {fp2} ms | {el} ms | {dur} s | {rtf} | {n} | {gap} ms | {vram} MB | {model} |".format(
                scene=row["scene"],
                fp1=row["first_packet_ms_ws"],
                fp2=row["first_packet_ms_server"],
                el=row["elapsed_ms"],
                dur=row["duration_sec"],
                rtf=row["rtf"],
                n=row["chunks"],
                gap=row["max_gap_ms"],
                vram=row["vram_mb"],
                model=(row["model"] or "")[:38],
            )
        )
    lines.append("")
    lines.append(
        f"平均首包（客户端）{avg('first_packet_ms_ws')} ms / 平均耗时 {avg('elapsed_ms')} ms / 平均 RTF {avg('rtf')}"
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8099")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--out", default="")
    parser.add_argument("--scenes", default="tts_short,tts_sentence,tts_long,clone,design")
    parser.add_argument("--verify-tls", default="false")
    parser.add_argument("--voice-id", default="")
    parser.add_argument("--skip-asr", action="store_true")
    args = parser.parse_args()

    scenes = [s.strip() for s in args.scenes.split(",") if s.strip()]
    client = httpx.Client(
        base_url=args.base, timeout=600.0, verify=args.verify_tls.lower() == "true"
    )
    health = client.get("/api/health").json()
    print(f"后端：mode={health['mode']} active={health['active_backend']} device={health['local'].get('device')}")

    voices = client.get("/api/voices").json()
    voice_id = args.voice_id or (voices["builtin"][0]["id"] if voices["builtin"] else "builtin-vivian")

    rows: list[dict] = []
    for round_index in range(args.rounds):
        for scene in scenes:
            body: dict
            if scene.startswith("tts"):
                key = scene.split("_", 1)[1]
                body = {"type": "tts", "text": TEXTS[key], "voice_id": voice_id, "params": {"speed": 1.0}}
            elif scene == "clone":
                asset = client.post(
                    "/api/assets", files={"file": ("ref.wav", make_wav(4.0), "audio/wav")}
                ).json()
                body = {"type": "clone", "text": TEXTS["sentence"], "asset_id": asset["id"]}
            elif scene == "design":
                body = {
                    "type": "design",
                    "text": TEXTS["short"],
                    "design": "二十五岁左右的女性，温柔，普通话标准，语速稍慢",
                }
            elif scene == "asr":
                asset = client.post(
                    "/api/assets", files={"file": ("asr.wav", make_wav(3.0), "audio/wav")}
                ).json()
                body = {"type": "asr", "asset_id": asset["id"], "language": "chinese"}
            else:
                continue
            print(f"[{round_index + 1}/{args.rounds}] {scene} …", flush=True)
            try:
                result = run_task(client, args.base, body)
            except Exception as exc:  # noqa: BLE001
                result = {"id": "-", "status": "error", "error": str(exc)}
            result["scene"] = scene if args.rounds == 1 else f"{scene}#{round_index + 1}"
            rows.append(result)
            print("   ", json.dumps({k: result.get(k) for k in ("status", "first_packet_ms_ws", "elapsed_ms", "rtf", "model")}, ensure_ascii=False))

    table = summarize(rows)
    print("\n" + table)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "\n## 实测数据（自动生成）\n\n"
            f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- 服务：{args.base}\n"
            f"- 后端：mode={health['mode']} active={health['active_backend']}"
            f" device={health['local'].get('device')} vram={health['local'].get('vram_total_gb')}GB\n\n"
        )
        with out.open("a", encoding="utf-8") as fh:
            fh.write(header + table + "\n")
        print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
