"""MCP Server：把合成 / 转写 / 克隆 / 音色设计 / 任务查询暴露给外部 Agent。

传输：stdio，行分隔 JSON-RPC 2.0（MCP 标准）。启动：`python -m app.mcp_server`；
自检：`python -m app.mcp_server --selftest`（initialize → tools/list → 真实合成 → 转写回环）。

设计取舍（与规范的差异）：规范建议「内部直接复用 service 层」，这里改成复用**本机 HTTP 接口**：
- 加载 TTS/ASR 模型要占显存（4GB 卡），在第二个进程里再建一套引擎会直接把显存吃满；
- 走本机 127.0.0.1 只有几百微秒开销，却天然复用排队、取消、日志、进度这些逻辑。
- 认证沿用现有 API（本地 stdio 无需鉴权；需要鉴权时设 QWENTTS_MCP_API_KEY）。

环境变量：
- `QWENTTS_MCP_BASE_URL` 默认 https://127.0.0.1:8443
- `QWENTTS_MCP_API_KEY`  可选，服务端开了鉴权时填
- `QWENTTS_MCP_TIMEOUT`  等待任务完成的总时长，默认 600s
- `QWENTTS_MCP_REQUEST_TIMEOUT` 单次 HTTP 请求超时，默认 300s
- `QWENTTS_DATA_DIR`     输出目录（用于返回 output_path），默认 <repo>/data
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
DATA_DIR = Path(os.environ.get("QWENTTS_DATA_DIR") or (Path(__file__).resolve().parents[2] / "data"))

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

# 语言别名：Agent 常写 zh / en / ja，这里统一映射到后端认的 id
LANG_ALIAS = {
    "zh": "chinese", "zh-cn": "chinese", "cn": "chinese", "mandarin": "chinese", "中文": "chinese",
    "en": "english", "en-us": "english", "en-gb": "english", "英文": "english",
    "ja": "japanese", "jp": "japanese", "日语": "japanese",
    "ko": "korean", "kr": "korean", "韩语": "korean",
    "fr": "french", "de": "german", "es": "spanish", "it": "italian", "pt": "portuguese",
    "ru": "russian", "auto": "Auto", "自动": "Auto",
}


def _headers(extra: dict | None = None) -> dict:
    out = {"Accept": "application/json"}
    if API_KEY:
        out["X-API-Key"] = API_KEY
    out.update(extra or {})
    return out


def _request(method: str, path: str, body: Any = None, content_type: str = "application/json") -> Any:
    """发一个请求。

    注意：**只要带 body 就必须显式设置 Content-Type**。
    历史 bug：JSON 分支没设，urllib 默认发 application/x-www-form-urlencoded，
    FastAPI 直接 422（initialize/tools/list 是 GET/无 body，所以只有真调工具才暴露）。
    """
    data = None
    headers = _headers()
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if content_type == "application/json" else body
        headers["Content-Type"] = content_type
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=_SSL, timeout=REQUEST_TIMEOUT_S) as resp:
            raw = resp.read()
            return {} if not raw else json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            detail = {"detail": str(exc)}
        raise RuntimeError(f"HTTP {exc.code}: {json.dumps(detail, ensure_ascii=False)[:400]}") from None


def _multipart(fields: dict[str, str], file_path: Path | None = None, field_name: str = "file") -> tuple[bytes, str]:
    boundary = f"----qwentts{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    if file_path is not None:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(file_path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------- 辅助
def _normalize_language(value: str | None, default: str = "Auto") -> str:
    raw = (value or "").strip()
    if not raw:
        return default
    key = raw.lower().replace("_", "-")
    return LANG_ALIAS.get(key, raw)


def _valid_languages() -> set[str]:
    try:
        data = _request("GET", "/api/voices")
        return {item.get("id", "") for item in (data.get("languages") or []) if item.get("id")}
    except Exception:  # noqa: BLE001
        return set()


def _check_language(value: str | None) -> str:
    lang = _normalize_language(value)
    valid = _valid_languages()
    if valid and lang not in valid:
        raise RuntimeError(
            f"不支持的语言 {value!r}（已映射为 {lang!r}）。可选：{', '.join(sorted(valid))}"
        )
    return lang


def _output_path(task: dict) -> str | None:
    """输出文件实际在 data/outputs/<task_id>/<file>，这里按真实目录解析。"""
    name = task.get("output_file")
    if not name:
        return None
    task_id = task.get("id") or ""
    candidate = DATA_DIR / "outputs" / task_id / name
    if candidate.is_file():
        return str(candidate.resolve())
    flat = DATA_DIR / "outputs" / name
    if flat.is_file():
        return str(flat.resolve())
    return str(candidate.resolve())


def _task_payload(task: dict) -> dict:
    status = task.get("status") or ""
    payload: dict[str, Any] = {
        "ok": status == "completed",
        "task_id": task.get("id"),
        "status": status,
        "type": task.get("type"),
        "backend": task.get("backend"),
        "duration_sec": task.get("duration_sec"),
        "elapsed_ms": task.get("elapsed_ms"),
        "first_packet_ms": task.get("first_packet_ms"),
        "rtf": task.get("rtf"),
        "progress": task.get("progress"),
        "output_file": task.get("output_file"),
        "output_path": _output_path(task),
        "download_url": f"{BASE_URL}/api/tasks/{task.get('id')}/download" if task.get("output_file") else None,
    }
    if task.get("error_code"):
        payload["error_code"] = task["error_code"]
    if task.get("error_message"):
        payload["error_message"] = task["error_message"]
    if task.get("transcript"):
        payload["transcript"] = task["transcript"]
    if task.get("segments"):
        payload["segments"] = task["segments"]
    return payload


def _wait_task(task_id: str, timeout_s: float = TIMEOUT_S) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        task = _request("GET", f"/api/tasks/{task_id}")
        if task.get("status") in ("completed", "failed", "canceled"):
            return task
        time.sleep(POLL_INTERVAL_S)
    return {
        "id": task_id,
        "status": "timeout",
        "error_message": f"等待超过 {int(timeout_s)}s 仍在运行，可改用 task_status 继续查",
    }


def _create_task(body: dict, wait: bool = True) -> dict:
    created = _request("POST", "/api/tasks", body)
    if not wait:
        return _task_payload(_request("GET", f"/api/tasks/{created['id']}"))
    return _task_payload(_wait_task(created["id"]))


# ---------------------------------------------------------------- 工具实现
def _tool_tts(args: dict, wait: bool = True) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        raise RuntimeError("text 不能为空")
    params: dict[str, Any] = {}
    if args.get("speed") is not None:
        params["speed"] = float(args["speed"])
    if args.get("target_duration_s"):
        params["target_duration_s"] = float(args["target_duration_s"])
    body: dict[str, Any] = {
        "type": "tts",
        "text": text,
        "language": _check_language(args.get("language")),
        "params": params,
    }
    if args.get("voice_id"):
        body["voice_id"] = str(args["voice_id"])
    return _create_task(body, wait=bool(args.get("wait", wait)))


def _tool_asr(args: dict, wait: bool = True) -> dict:
    raw_path = str(args.get("audio_path") or "").strip()
    if not raw_path:
        raise RuntimeError("audio_path 不能为空")
    path = Path(raw_path)
    if not path.is_file():
        raise RuntimeError(f"音频文件不存在：{raw_path}")
    body_bytes, ctype = _multipart({}, path)
    asset = _request("POST", "/api/assets", body_bytes, content_type=ctype)
    asset_id = asset.get("id")
    if not asset_id:
        raise RuntimeError(f"上传音频失败：{json.dumps(asset, ensure_ascii=False)[:200]}")
    params: dict[str, Any] = {}
    if args.get("diarize") is not None:
        params["diarize"] = bool(args["diarize"])
    if args.get("max_speakers") is not None:
        params["max_speakers"] = int(args["max_speakers"])
    body = {
        "type": "asr",
        "asset_id": asset_id,
        "language": _normalize_language(args.get("language"), default="auto"),
        "params": params,
    }
    return _create_task(body, wait=bool(args.get("wait", wait)))


def _tool_task_status(args: dict) -> dict:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError("task_id 不能为空")
    return _task_payload(_request("GET", f"/api/tasks/{task_id}"))


def _tool_task_cancel(args: dict) -> dict:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError("task_id 不能为空")
    _request("POST", f"/api/tasks/{task_id}/cancel", {})
    payload = _task_payload(_request("GET", f"/api/tasks/{task_id}"))
    payload["cancel_requested"] = True
    return payload


def _tool_voice_list(args: dict) -> dict:
    kind = str(args.get("kind") or "all")
    data = _request("GET", f"/api/voices?kind={kind}")
    items = list(data.get("builtin") or []) + list(data.get("custom") or [])
    if args.get("language"):
        want = _normalize_language(str(args["language"])).lower()
        items = [v for v in items if str(v.get("language") or "").lower() == want]
    voices = [
        {
            "id": v.get("id"),
            "name": v.get("name"),
            "kind": v.get("kind"),
            "language": v.get("language"),
            "gender": v.get("gender"),
            "age": v.get("age"),
            "accent": v.get("accent"),
            "available": v.get("available"),
        }
        for v in items
    ]
    return {
        "ok": True,
        "count": len(voices),
        "engine": data.get("engine"),
        "languages": [item.get("id") for item in (data.get("languages") or [])],
        "voices": voices,
    }


def _tool_voice_clone(args: dict) -> dict:
    raw_path = str(args.get("audio_path") or "").strip()
    name = str(args.get("name") or "").strip()
    if not raw_path or not name:
        raise RuntimeError("audio_path 与 name 都是必填")
    path = Path(raw_path)
    if not path.is_file():
        raise RuntimeError(f"参考音频不存在：{raw_path}")
    body, ctype = _multipart({"name": name, "ref_text": str(args.get("ref_text") or "")}, path)
    created = _request("POST", "/api/voices", body, content_type=ctype)
    voice_id = created.get("id")
    return {
        "ok": bool(voice_id),
        "voice_id": voice_id,
        "name": name,
        "hint": "已创建自定义音色，可用 tts_synthesize 指定 voice_id 合成",
    }


def _tool_voice_design(args: dict) -> dict:
    design = str(args.get("design") or "").strip()
    if not design:
        raise RuntimeError("design 不能为空（用自然语言描述想要的声音）")
    body = {
        "type": "design",
        "text": str(args.get("text") or "").strip() or design,
        "design": design,
        "language": _check_language(args.get("language")),
    }
    return _create_task(body, wait=bool(args.get("wait", True)))


def _tool_tts_submit(args: dict) -> dict:
    return _tool_tts(args, wait=False)


def _tool_asr_submit(args: dict) -> dict:
    return _tool_asr(args, wait=False)


TASK_FIELDS = {
    "language": {"type": "string", "description": "语言 id 或常见别名（zh/en/ja…），默认 Auto"},
    "wait": {"type": "boolean", "default": True, "description": "是否等待任务完成（false 时立即返回 task_id）"},
}

TOOLS = [
    {
        "name": "tts_synthesize",
        "description": "文本转语音（等待完成，返回 task_id、耗时与本机输出路径）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要合成的文本"},
                "voice_id": {"type": "string", "description": "音色 id，可先用 voice_list 查"},
                **TASK_FIELDS,
                "speed": {"type": "number", "description": "语速，1.0 为原速"},
                "target_duration_s": {"type": "number", "description": "目标时长（秒），0 表示不控制"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "tts_submit",
        "description": "提交文本转语音任务后立刻返回 task_id（不阻塞），再用 task_status 轮询进度。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "voice_id": {"type": "string"},
                "language": TASK_FIELDS["language"],
                "speed": {"type": "number"},
                "target_duration_s": {"type": "number"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "asr_transcribe",
        "description": "音频转文字（等待完成；可选区分说话人，返回 transcript 与 segments[start_s/end_s]）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "本机音频绝对路径"},
                **TASK_FIELDS,
                "diarize": {"type": "boolean", "default": False, "description": "是否区分说话人"},
                "max_speakers": {"type": "integer", "default": 4},
            },
            "required": ["audio_path"],
        },
    },
    {
        "name": "asr_submit",
        "description": "提交转写任务后立刻返回 task_id（不阻塞），再用 task_status 轮询。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string"},
                "language": TASK_FIELDS["language"],
                "diarize": {"type": "boolean"},
                "max_speakers": {"type": "integer"},
            },
            "required": ["audio_path"],
        },
    },
    {
        "name": "task_status",
        "description": "查询任务状态、进度、错误码、输出文件与本机路径。",
        "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    },
    {
        "name": "task_cancel",
        "description": "取消排队中或运行中的任务（已生成片段会保留并标注已截断）。",
        "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    },
    {
        "name": "voice_list",
        "description": "列出音色（含 language / gender / available，可按语种过滤）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["all", "builtin", "custom"], "default": "all"},
                "language": {"type": "string", "description": "按语种过滤，如 chinese / english"},
            },
        },
    },
    {
        "name": "voice_clone",
        "description": "用参考音频克隆自定义音色（返回 voice_id，之后可用 tts_synthesize 指定）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "参考音频绝对路径（3–10 秒清晰人声）"},
                "name": {"type": "string", "description": "音色名称"},
                "ref_text": {"type": "string", "description": "参考音频里说的那句话（可选）"},
            },
            "required": ["audio_path", "name"],
        },
    },
    {
        "name": "voice_design",
        "description": "用自然语言描述设计音色并合成（如「低沉磁性的中年男声，语速偏慢」）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "design": {"type": "string", "description": "音色描述"},
                "text": {"type": "string", "description": "要合成的文本，留空则用描述本身"},
                "language": TASK_FIELDS["language"],
                "wait": TASK_FIELDS["wait"],
            },
            "required": ["design"],
        },
    },
]

_TOOL_MAP = {
    "tts_synthesize": _tool_tts,
    "tts_submit": _tool_tts_submit,
    "asr_transcribe": _tool_asr,
    "asr_submit": _tool_asr_submit,
    "task_status": _tool_task_status,
    "task_cancel": _tool_task_cancel,
    "voice_list": _tool_voice_list,
    "voice_clone": _tool_voice_clone,
    "voice_design": _tool_voice_design,
}


def _call_tool(name: str, args: dict) -> dict:
    handler = _TOOL_MAP.get(name)
    if handler is None:
        raise RuntimeError(f"未知工具：{name}")
    return handler(args or {})


def _handle(message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None and method and method.startswith("notifications/"):
        return None

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code: int, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        params = message.get("params") or {}
        return ok(
            {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "qwentts-mcp", "version": __version__},
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        try:
            payload = _call_tool(name, args)
        except Exception as exc:  # noqa: BLE001
            return ok({"content": [{"type": "text", "text": f"工具执行失败：{exc}"}], "isError": True})
        # 任务失败/超时/取消都要让 Agent 立刻看出失败（历史 bug：一律 isError=false）
        return ok(
            {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                "structuredContent": payload,
                "isError": payload.get("ok") is False,
            }
        )
    return err(-32601, f"Method not found: {method}")


# ---------------------------------------------------------------- 自检
def selftest() -> int:
    """端到端自检：initialize → tools/list → 真实合成 → 转写回环。"""
    print(f"qwentts-mcp selftest  (server v{__version__}, base={BASE_URL})")
    failures: list[str] = []

    def step(name: str, fn):
        try:
            value = fn()
            print(f"  PASS  {name}")
            return value
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")
            print(f"  FAIL  {name}  →  {exc}")
            return None

    init = step("initialize", lambda: _handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
    if init and "serverInfo" in init.get("result", {}):
        print(f"        serverInfo = {init['result']['serverInfo']}")
    tools = step("tools/list", lambda: _handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) or {})
    if tools:
        names = [t["name"] for t in tools["result"]["tools"]]
        print(f"        工具 {len(names)} 个：{', '.join(names)}")
        if len(names) < 9:
            failures.append(f"tools/list 数量异常：{len(names)}")

    step("health", lambda: _request("GET", "/api/health"))
    voices = step("voice_list", lambda: _tool_voice_list({}))
    voice_id = None
    if voices and voices.get("voices"):
        voice_id = next((v["id"] for v in voices["voices"] if v.get("available")), voices["voices"][0]["id"])
        print(f"        选用音色 {voice_id}")

    tts = step("tts_synthesize (真实合成)", lambda: _tool_tts({"text": "声匠自检：你好。", "voice_id": voice_id}))
    if tts:
        print(f"        {tts.get('status')}  output_path={tts.get('output_path')}")
        if not tts.get("ok"):
            failures.append(f"合成未成功：{tts.get('error_code') or tts.get('status')}")

    if tts and tts.get("output_path"):
        asr = step("asr_transcribe (回环)", lambda: _tool_asr({"audio_path": tts["output_path"]}))
        if asr:
            print(f"        transcript={str(asr.get('transcript'))[:40]!r}")
            if not asr.get("ok"):
                failures.append("转写未成功")

    print()
    if failures:
        print(f"selftest 失败 {len(failures)} 项：")
        for item in failures:
            print("  -", item)
        return 1
    print("selftest 全部通过")
    return 0


def main() -> None:
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
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
