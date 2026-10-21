"""引擎子进程推理入口：把「在引擎自己的 venv 里跑一次合成」变成一个进程。

调用形式（由适配器 _synth_in_venv() 生成，用户一般不需要手敲）：

    <venv_python> runner.py --engine cosyvoice3-0.5b --request req.json --out out.wav

约定：
- **不 import app.engine.registry**：registry 会把所有适配器都 import 一遍（含 qwen →
  app.engine.local → torch），在这个只装了引擎依赖的 venv 里必然炸；这里按 id 直接
  import 对应的适配器模块并实例化。
- 适配器实例被强制设为 isolation="inprocess"：否则它会再 spawn 一个 runner，无限递归。
- 合成期间 stdout 全部重定向到 stderr，最后只往**真 stdout** 打一行 JSON，方便父进程解析：
  成功 {"ok":true,"duration_s":..,"sample_rate":..,"chunks":..}；
  失败 {"ok":false,"error":{"code":..,"message":..,"detail":..}} 且退出码非 0。
- wav 只在**全部成功之后**才落盘（先写临时文件再原子改名），失败绝不留半截文件。
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import sys
import traceback
from pathlib import Path

# runner.py 在 backend/app/engine/adapters/ 下 → parents[3] 就是 backend/
BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 关键：脚本方式运行时 sys.path[0] 是 adapters 目录，里面的 `voxcpm.py`（适配器）
# 会遮蔽 venv 里真正的 `voxcpm` 包 → 子进程里 `from voxcpm import VoxCPM` 报
# "attempted relative import with no known parent package"。这里把自己的目录移出 sys.path。
_ADAPTERS_DIR = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != _ADAPTERS_DIR]

# 引擎 id → 适配器模块。**刻意不写 registry**：那个模块会连带 import 全部适配器。
ADAPTER_MODULES: dict[str, str] = {
    "qwen3-tts-1.7b": "app.engine.adapters.qwen",
    "cosyvoice3-0.5b": "app.engine.adapters.cosyvoice3",
    "cosyvoice2-0.5b": "app.engine.adapters.cosyvoice2",
    "cosyvoice-300m-sft": "app.engine.adapters.cosyvoice300m",
    "voxcpm1.5": "app.engine.adapters.voxcpm",
    "indextts2": "app.engine.adapters.indextts2",
}

# 关键：先切掉 torchaudio 的 sox_io 后端，避免 Windows 上「SoX could not be found」
try:
    from app import soxfree as _soxfree

    _soxfree.apply()
except Exception:  # noqa: BLE001 - 补丁失败不影响引擎运行
    pass

EXIT_OK = 0
EXIT_APP_ERROR = 2
EXIT_INTERNAL = 3


class RunnerError(Exception):
    """runner 内部的可读错误（带错误码，父进程据此映射 AppError）。"""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def _emit(payload: dict) -> None:
    """往真 stdout 打一行 JSON（先 flush，避免被重定向的缓冲吞掉）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def build_adapter(engine_id: str):
    """按 id import 模块并实例化适配器（绝不经 registry）。"""
    module_name = ADAPTER_MODULES.get(engine_id)
    if not module_name:
        raise RunnerError(
            "ENGINE_NOT_FOUND",
            f"runner 不认识引擎 {engine_id}；可用：{'、'.join(sorted(ADAPTER_MODULES))}",
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RunnerError(
            "PACKAGE_MISSING",
            f"无法 import {module_name}（{type(exc).__name__}: {exc}）；"
            f"该引擎的依赖还没装进它自己的 venv：{sys.executable}",
            detail=str(exc),
        ) from exc
    for value in vars(module).values():
        if isinstance(value, type) and getattr(value, "id", None) == engine_id:
            adapter = value()
            # 关键：在 venv 里必须就地推理，否则会再 spawn 一次 runner
            adapter.isolation = "inprocess"
            return adapter
    raise RunnerError("ENGINE_NOT_FOUND", f"{module_name} 里找不到 id={engine_id} 的适配器类")


def build_request(payload: dict):
    """把 JSON 请求转成 SynthRequest（字段缺失都有默认值）。"""
    from app.engine.base import SynthRequest, TTSParams

    return SynthRequest(
        task_id=str(payload.get("task_id") or "engine-runner"),
        kind=str(payload.get("kind") or "tts"),
        text=str(payload.get("text") or ""),
        params=TTSParams.from_dict(payload.get("params")),
        speaker=payload.get("speaker") or None,
        ref_audio=payload.get("ref_audio") or None,
        ref_text=payload.get("ref_text") or None,
        instruct=str(payload.get("instruct") or ""),
        design=str(payload.get("design") or ""),
    )


def write_wav(path: Path, pcm, sample_rate: int) -> None:
    """先写同名 .tmp 再原子改名，失败不留半截 wav。"""
    import numpy as np
    import soundfile as sf

    data = np.asarray(pcm, dtype="float32")
    if data.ndim > 1:
        data = data.reshape(-1)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 关键：临时文件后缀不是 .wav，soundfile 无法从扩展名推断格式 → 必须显式指定 format
    temp = path.with_name(path.name + ".tmp")
    try:
        sf.write(str(temp), data, int(sample_rate or 24000), subtype="PCM_16", format="WAV")
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    os.replace(temp, path)


def synthesize(engine_id: str, payload: dict, out_path: Path) -> dict:
    """跑一次完整合成，返回统计信息；任何失败都抛 RunnerError。"""
    import numpy as np

    from app.errors import AppError

    adapter = build_adapter(engine_id)
    req = build_request(payload)
    if not req.text.strip():
        raise RunnerError("TEXT_EMPTY", "合成文本为空")

    pieces: list = []
    sample_rate = int(getattr(adapter, "sample_rate", 0) or 24000)
    chunks = 0
    try:
        for chunk in adapter.synthesize(req):
            pcm = np.asarray(chunk.pcm, dtype="float32").reshape(-1)
            if len(pcm) == 0:
                continue
            pieces.append(pcm)
            sample_rate = int(chunk.sample_rate or sample_rate)
            chunks += 1
    except AppError as exc:
        raise RunnerError(exc.code, exc.message, exc.detail or "") from exc
    if not pieces:
        raise RunnerError("MODEL_LOAD_FAILED", "引擎没有产出任何音频")
    audio_data = pieces[0] if len(pieces) == 1 else np.concatenate(pieces)
    write_wav(out_path, audio_data, sample_rate)
    return {
        "ok": True,
        "duration_s": round(len(audio_data) / float(sample_rate or 1), 3),
        "sample_rate": sample_rate,
        "chunks": chunks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engine-runner", description="引擎 venv 子进程推理入口")
    parser.add_argument("--engine", required=True, help="引擎 id，例如 cosyvoice3-0.5b")
    parser.add_argument("--request", required=True, help="请求 JSON 文件路径")
    parser.add_argument("--out", required=True, help="输出 wav 路径")
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    try:
        # utf-8-sig：容忍 Windows 记事本/Set-Content 写出的 BOM
        payload = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        _emit({"ok": False, "error": {"code": "INVALID_INPUT", "message": f"请求文件无法读取（{exc}）"}})
        return EXIT_APP_ERROR

    # 引擎自己的 print/warning 一律走 stderr；结果 JSON 在 with 块之外发到真 stdout
    result: dict
    code = EXIT_OK
    with contextlib.redirect_stdout(sys.stderr):
        try:
            result = synthesize(args.engine, payload, out_path)
        except RunnerError as exc:
            out_path.unlink(missing_ok=True)
            Path(str(out_path) + ".tmp").unlink(missing_ok=True)
            result = {
                "ok": False,
                "error": {"code": exc.code, "message": exc.message, "detail": exc.detail[:800]},
            }
            code = EXIT_APP_ERROR
        except FileNotFoundError as exc:
            out_path.unlink(missing_ok=True)
            result = {
                "ok": False,
                "error": {
                    "code": "PACKAGE_MISSING",
                    "message": f"引擎依赖或模型文件缺失（{exc}）；请先给它安装依赖",
                },
            }
            code = EXIT_APP_ERROR
        except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都要变成可读 JSON
            out_path.unlink(missing_ok=True)
            result = {
                "ok": False,
                "error": {
                    "code": "INTERNAL",
                    "message": f"{type(exc).__name__}: {exc}",
                    "detail": traceback.format_exc()[-1200:],
                },
            }
            code = EXIT_INTERNAL
    _emit(result)
    return code


if __name__ == "__main__":
    sys.exit(main())
