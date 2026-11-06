"""MCP Server 回归：Content-Type、语言校验、isError 语义、工具清单。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from app import mcp_server as mcp


def test_request_sets_json_content_type():
    """P0：带 body 时必须显式设置 Content-Type，否则 FastAPI 直接 422。"""
    captured = {}

    class FakeResp:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, context=None, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["data"] = req.data
        return FakeResp()

    with mock.patch.object(mcp.urllib.request, "urlopen", fake_urlopen):
        mcp._request("POST", "/api/tasks", {"type": "tts"})

    assert captured["headers"].get("content-type") == "application/json"
    assert json.loads(captured["data"])["type"] == "tts"


def test_request_keeps_multipart_content_type():
    captured = {}

    class FakeResp:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, context=None, timeout=None):
        captured["ctype"] = {k.lower(): v for k, v in req.header_items()}.get("content-type", "")
        return FakeResp()

    with mock.patch.object(mcp.urllib.request, "urlopen", fake_urlopen):
        mcp._request("POST", "/api/assets", b"xyz", content_type="multipart/form-data; boundary=x")

    assert captured["ctype"].startswith("multipart/form-data")


def test_language_alias_mapping():
    assert mcp._normalize_language("zh") == "chinese"
    assert mcp._normalize_language("EN") == "english"
    assert mcp._normalize_language("中文") == "chinese"
    assert mcp._normalize_language(None) == "Auto"
    assert mcp._normalize_language("chinese") == "chinese"


def test_invalid_language_rejected_before_creating_task():
    """P1：非法语言必须在建任务前拦下，而不是先占队列位。"""
    with mock.patch.object(mcp, "_valid_languages", lambda: {"Auto", "chinese", "english"}):
        with pytest.raises(RuntimeError):
            mcp._check_language("klingon")
        assert mcp._check_language("zh") == "chinese"


def test_failed_task_sets_is_error_true():
    """P1：任务失败要让 Agent 从 isError 就看出来。"""
    fake = {"ok": False, "status": "failed", "error_code": "VRAM_INSUFFICIENT"}
    with mock.patch.object(mcp, "_call_tool", lambda name, args: fake):
        reply = mcp._handle(
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "tts_synthesize", "arguments": {}}}
        )
    assert reply["result"]["isError"] is True
    assert reply["result"]["structuredContent"]["ok"] is False


def test_completed_task_sets_is_error_false():
    fake = {"ok": True, "status": "completed", "task_id": "abc"}
    with mock.patch.object(mcp, "_call_tool", lambda name, args: fake):
        reply = mcp._handle(
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "tts_synthesize", "arguments": {}}}
        )
    assert reply["result"]["isError"] is False


def test_tools_cover_product_capabilities():
    names = {tool["name"] for tool in mcp.TOOLS}
    assert {"tts_synthesize", "tts_submit", "asr_transcribe", "asr_submit"} <= names
    assert {"task_status", "task_cancel", "voice_list", "voice_clone", "voice_design"} <= names
    assert len(names) == 9


def test_voice_list_exposes_language_gender_available():
    payload = {
        "engine": "qwen3-tts-0.6b",
        "languages": [{"id": "chinese"}, {"id": "english"}],
        "builtin": [
            {
                "id": "builtin-vivian",
                "name": "Vivian",
                "kind": "builtin",
                "language": "chinese",
                "gender": "female",
                "available": True,
            }
        ],
        "custom": [],
    }
    with mock.patch.object(mcp, "_request", lambda *a, **k: payload):
        result = mcp._tool_voice_list({})
    voice = result["voices"][0]
    assert voice["language"] == "chinese"
    assert voice["gender"] == "female"
    assert voice["available"] is True


def test_output_path_resolves_task_subdir(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp, "DATA_DIR", tmp_path)
    out_dir = tmp_path / "outputs" / "abc123"
    out_dir.mkdir(parents=True)
    (out_dir / "abc123.wav").write_bytes(b"0")
    path = mcp._output_path({"id": "abc123", "output_file": "abc123.wav"})
    assert path and path.endswith("abc123.wav")
    assert Path(path).is_file()
