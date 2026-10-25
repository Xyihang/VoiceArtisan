"""ASR / 声纹（diarize）云端实测的守护测试。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

from app import capabilities as caps_mod
from app import cloud_models


def _cfg(mode="cloud"):
    cloud = SimpleNamespace(
        base_url="https://1.2.3.4:8443",
        api_key="k",
        models=SimpleNamespace(
            base="Qwen3-TTS-12Hz-1.7B-Base",
            custom_voice="Qwen3-TTS-12Hz-1.7B-CustomVoice",
            voice_design="Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        ),
    )
    return SimpleNamespace(run=SimpleNamespace(mode=mode), cloud=cloud)


def _snapshot(caps, ready=None, asr_ok=True, diarize_ok=False):
    return {"caps": caps, "ready_models": ready or {}, "asr_ok": asr_ok,
            "diarize_ok": diarize_ok, "error": None}


def test_cloud_asr_missing_marks_asr_unavailable():
    """云端没装 ASR → caps 不含 asr（以前写死始终为真）。"""
    info = caps_mod.effective_capabilities_with(
        _cfg(), {"tts", "asr"}, "qwen3-tts-0.6b",
        cloud_snapshot=_snapshot(["tts", "clone", "design"], asr_ok=False),
    )
    assert "asr" not in info["caps"]
    assert "asr" in info["missing"]


def test_cloud_diarize_available_only_when_verified_true():
    snap = _snapshot(["tts", "clone", "design", "asr"], {"asr": "asr-whisper-small"}, diarize_ok=True)
    info = caps_mod.effective_capabilities_with(_cfg(), {"tts", "asr"}, "", cloud_snapshot=snap)
    assert info["diarize_available"] is True

    snap2 = _snapshot(["tts", "asr"], diarize_ok=False)
    info2 = caps_mod.effective_capabilities_with(_cfg(), {"tts", "asr"}, "", cloud_snapshot=snap2)
    assert info2["diarize_available"] is False


def test_diarize_unknown_when_not_verified():
    """探测失败（cloud_verified=False）时 diarize_available 必须是 None（未知），不能猜成 True。"""
    snap = {"caps": None, "ready_models": {}, "asr_ok": False, "diarize_ok": False,
            "error": "URLError: refused"}
    info = caps_mod.effective_capabilities_with(_cfg(), {"tts", "asr"}, "", cloud_snapshot=snap)
    assert info["diarize_available"] is None


class _Handler(BaseHTTPRequestHandler):
    models_payload: dict = {}
    health_payload: dict = {}

    def log_message(self, *args):  # 静默
        pass

    def do_GET(self):
        if self.path.endswith("/v1/models"):
            body = json.dumps(self.models_payload).encode()
        elif self.path.endswith("/v1/health"):
            body = json.dumps(self.health_payload).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_probe_parses_asr_and_diarize_from_health():
    _Handler.models_payload = {"data": [
        {"id": "qwen3-tts-design", "kind": "voice_design", "ready": True},
        {"id": "qwen3-asr", "kind": "asr", "ready": True},
    ]}
    _Handler.health_payload = {"asr": {"engine_id": "asr-whisper-small", "diarize": True}}

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = cloud_models._probe(f"http://127.0.0.1:{port}", "")
    finally:
        server.shutdown()

    assert "asr" in result["caps"]
    assert "diarize" in result["caps"]
    assert result["asr_ok"] is True
    assert result["diarize_ok"] is True
    assert result["ready_models"]["asr"] == "asr-whisper-small"
