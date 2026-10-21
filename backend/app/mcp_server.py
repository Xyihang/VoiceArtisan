"""MCP Server（PHASE-3 3.7）：把合成 / 转写 / 任务查询暴露给外部 Agent。

传输：stdio，行分隔 JSON-RPC 2.0（MCP 标准）。启动：`python -m app.mcp_server`。

设计取舍（与规格的差异）：规格建议「内部直接复用 service 层」，这里改成复用**本机 HTTP 接口**：
- 加载 TTS/ASR 模型要占显存（4GB 卡），在第二个进程里再建一套引擎会直接把显存吃满；
- 走本机 127.0.0.1 只有几百微秒开销，却天然复用排队、取消、日志、进度这些逻辑。
- 认证沿用现有 API（本地 stdio 无需鉴权；需要鉴权时设 QWENTTS_MCP_API_KEY）。
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from . import __version__

PROTOCOL_VERSION = "2024-11-05"
BASE_URL = os.environ.get("QWENTTS_MCP_BASE_URL", "https://127.0.0.1:8443").rstrip("/")
API_KEY = os.environ.get("QWENTTS_MCP_API_KEY", "")
TIMEOUT_S = float(os.environ.get("QWENTTS_MCP_TIMEOUT", "600"))
REQUEST_TIMEOUT_S = float(os.environ.get("QWENTTS_MCP_REQUEST_TIMEOUT", "300"))
POLL_INTERVAL_S = 1.0

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


def _headers(extra: dict | None = None) -> dict:
    out = {"Accept": "application/json"}
    if API_KEY:
        out["X-API-Key"] = API_KEY
    out.update(extra or {})
    return out


def _request(method: str, path: str, body: Any = None, content_type: str = "application/json") -> Any:
    data = None
    headers = _headers()
    if body is not None:
        if content_type == "application/json":
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        else:
            data = body
            headers["Content-Type"] = content_type
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=_SSL, timeout=REQUEST_TIMEOUT_S) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            detail = {"detail": str(exc)}
        raise RuntimeError(f"HTTP {exc.code}: {json.dumps(detail, ensure_ascii=False)[:400]}") from None


def _multipart(file_path: Path) -> tuple[bytes, str]:
    boundary = f"----qwentts{uuid.uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + file_path.read_bytes() + tail, f"multipart/form-data; boundary={boundary}"


def _wait_task(task_id: str, timeout_s: float = TIMEOUT_S) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        task = _request("GET", f"/api/tasks/{task_id}")
        if task.get("status") in ("completed", "failed", "canceled"):
            return task
        time.sleep(POLL_INTERVAL_S)
    return {"id": task_id, "status": "timeout", "error_message": f"等待超过 {int(timeout_s)}s 仍在运行"}


TOOLS = [
    {
        "name": "tts_synthesize",
        "description": "把文本合成为语音（走本机引擎，返回任务 id、状态与下载地址）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要合成的文本"},
                "voice_id": {"type": "string", "description": "音色 id；留空用默认音色"},
                "language": {"type": "string", "default": "Auto"},
                "speed": {"type": "number", "description": "语速，1.0 为原速"},
                "target_duration_s": {"type": "number", "description": "目标时长（秒），0 表示不控制"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "asr_transcribe",
        "description": "把本地音频文件转写成文本，可选按说话人分段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "本机音频绝对路径"},
                "language": {"type": "string", "default": "auto"},
                "diarize": {"type": "boolean", "default": False, "description": "是否区分说话人"},
                "max_speakers": {"type": "integer", "default": 4},
            },
            "required": ["audio_path"],
        },
    },
    {
        "name": "task_status",
        "description": "查询任务状态、进度与结果文件。",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "voice_list",
        "description": "列出可用音色（内置 + 自定义）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _tool_tts(args: dict) -> dict:
    params: dict = {}
    if args.get("speed") is not None:
        params["speed"] = float(args["speed"])
    if args.get("target_duration_s"):
        params["target_duration_s"] = float(args["target_duration_s"])
    body = {
        "type": "tts",
        "text": str(args.get("text") or ""),
        "language": args.get("language") or "Auto",
        "params": params,
    }
    if args.get("voice_id"):
        body["voice_id"] = str(args["voice_id"])
    created = _request("POST", "/api/tasks", body)
    task = _wait_task(created["id"])
    return {
        "task_id": task.get("id"),
        "status": task.get("status"),
        "duration_sec": task.get("duration_sec"),
        "backend": task.get("backend"),
        "download_url": f"{BASE_URL}/api/tasks/{task.get('id')}/download",
        "error": task.get("error_message") if task.get("status") == "failed" else None,
    }


def _tool_asr(args: dict) -> dict:
    path = Path(str(args.get("audio_path") or ""))
    if not path.is_file():
        raise RuntimeError(f"文件不存在：{path}")
    blob, ctype = _multipart(path)
    asset = _request("POST", "/api/assets", blob, content_type=ctype)
    params = {"diarize": bool(args.get("diarize", False))}
    if args.get("max_speakers"):
        params["max_speakers"] = int(args["max_speakers"])
    created = _request(
        "POST",
        "/api/tasks",
        {
            "type": "asr",
            "text": "",
            "asset_id": asset["id"],
            "language": args.get("language") or "auto",
            "params": params,
        },
    )
    task = _wait_task(created["id"])
    segments = task.get("segments") or []
    return {
        "task_id": task.get("id"),
        "status": task.get("status"),
        "transcript": task.get("transcript"),
        "segments": segments,
        "error": task.get("error_message") if task.get("status") == "failed" else None,
    }


def _call_tool(name: str, args: dict) -> dict:
    if name == "tts_synthesize":
        return _tool_tts(args)
    if name == "asr_transcribe":
        return _tool_asr(args)
    if name == "task_status":
        task = _request("GET", f"/api/tasks/{args.get('task_id', '')}")
        return {
            "task_id": task.get("id"),
            "status": task.get("status"),
            "progress": task.get("progress"),
            "error_code": task.get("error_code"),
            "error_message": task.get("error_message"),
            "output_file": task.get("output_file"),
        }
    if name == "voice_list":
        data = _request("GET", "/api/voices")
        return {
            "builtin": [{"id": v.get("id"), "name": v.get("name")} for v in data.get("builtin", [])],
            "custom": [{"id": v.get("id"), "name": v.get("name")} for v in data.get("custom", [])],
        }
    raise RuntimeError(f"未知工具：{name}")


def _handle(message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "qwentts", "version": __version__},
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        try:
            payload = _call_tool(name, args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                    "isError": False,
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True},
            }
    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    # Windows 控制台默认 cp936，强制 UTF-8 避免中文乱码/UnicodeDecodeError
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            # MCP 规范：解析失败必须回 -32700 Parse error，不能静默跳过
            sys.stdout.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                    ensure_ascii=False,
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        reply = _handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
