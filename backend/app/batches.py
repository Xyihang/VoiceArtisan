"""批量队列：把「一批线索」变成一组普通任务 + 一份批次清单。

设计取舍（与 PHASE-P1 规格的差异）：不引入父任务行。
父任务会被 worker 当普通任务执行（`_restore` 会把 queued 重新入队），还要改 worker；
改成「批次清单文件 + N 个普通任务」后，worker 完全不用动，进度由接口按清单聚合。
"""
from __future__ import annotations

import json
import time
import uuid
import zipfile
from pathlib import Path

from .errors import AppError

MAX_ITEMS = 50
MAX_CHARS = 50000


def batch_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "batches"


def _path(data_dir: Path, batch_id: str) -> Path:
    return batch_dir(data_dir) / f"{batch_id}.json"


def save(data_dir: Path, batch: dict) -> None:
    batch_dir(data_dir).mkdir(parents=True, exist_ok=True)
    _path(data_dir, batch["batch_id"]).write_text(
        json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load(data_dir: Path, batch_id: str) -> dict:
    p = _path(data_dir, batch_id)
    if not p.exists():
        raise AppError("INVALID_INPUT", f"批次不存在：{batch_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def new_batch(items: list[dict], defaults: dict, kind: str) -> dict:
    if not items:
        raise AppError("INVALID_INPUT", "批次至少要有 1 条线索")
    if len(items) > MAX_ITEMS:
        raise AppError("INVALID_INPUT", f"单批最多 {MAX_ITEMS} 条（当前 {len(items)} 条）")
    total = sum(len(str(it.get("text") or "")) for it in items)
    if total > MAX_CHARS:
        raise AppError("INVALID_INPUT", f"单批文本总长最多 {MAX_CHARS} 字（当前 {total} 字）")
    return {
        "batch_id": uuid.uuid4().hex[:12],
        "created_at": time.time(),
        "kind": kind,
        "defaults": defaults,
        "items": [
            {
                "index": i,
                "text": str(it.get("text") or ""),
                # 逐项参数必须落盘，否则「只重试失败项」会丢 voice_id/asset_id/instruct/design/language
                "voice_id": it.get("voice_id"),
                "asset_id": it.get("asset_id"),
                "instruct": it.get("instruct") or "",
                "design": it.get("design") or "",
                "language": it.get("language") or "Auto",
                "params": dict(it.get("params") or {}),
                "task_id": "",
                "status": "queued",
            }
            for i, it in enumerate(items)
        ],
    }


def summarize(batch: dict, task_rows: dict[str, dict | None]) -> dict:
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "canceled": 0}
    items = []
    for item in batch.get("items", []):
        row = task_rows.get(item.get("task_id") or "")
        status = (row or {}).get("status") or item.get("status") or "queued"
        status = "failed" if status == "failed" else status
        counts[status] = counts.get(status, 0) + 1
        items.append(
            {
                "index": item.get("index"),
                "text": item.get("text", "")[:80],
                "task_id": item.get("task_id", ""),
                "status": status,
                "output_file": (row or {}).get("output_file"),
                "duration_sec": (row or {}).get("duration_sec"),
                "error_code": (row or {}).get("error_code"),
                "error_message": ((row or {}).get("error_message") or "")[:200],
            }
        )
    total = len(items)
    done = counts.get("completed", 0) + counts.get("failed", 0) + counts.get("canceled", 0)
    return {
        "batch_id": batch["batch_id"],
        "created_at": batch.get("created_at"),
        "kind": batch.get("kind"),
        "total": total,
        "counts": counts,
        "progress": round(done / total, 3) if total else 0.0,
        "items": items,
    }


def build_zip(batch: dict, task_rows: dict[str, dict | None], outputs_dir: Path, target: Path) -> dict:
    """把已完成项打包，附 manifest.json（记录失败项与原因）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    ok = 0
    failed: list[dict] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in batch.get("items", []):
            tid = item.get("task_id") or ""
            row = task_rows.get(tid)
            name = (row or {}).get("output_file")
            if row and row.get("status") == "completed" and name:
                src = Path(outputs_dir) / tid / name
                if src.exists():
                    zf.write(src, arcname=f"{item.get('index'):03d}_{tid}_{name}")
                    ok += 1
                    continue
            failed.append(
                {
                    "index": item.get("index"),
                    "text": item.get("text", "")[:80],
                    "status": (row or {}).get("status") or item.get("status"),
                    "error_code": (row or {}).get("error_code"),
                    "error_message": ((row or {}).get("error_message") or "")[:200],
                }
            )
        zf.writestr(
            "manifest.json",
            json.dumps(
                {"batch_id": batch["batch_id"], "ok": ok, "failed": failed},
                ensure_ascii=False,
                indent=2,
            ),
        )
    return {"ok": ok, "failed": len(failed)}
