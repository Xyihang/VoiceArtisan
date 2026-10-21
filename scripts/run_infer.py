"""单独运行推理流程（D4：用于定位问题，不依赖 HTTP 服务）。

示例：
    # 内置音色（指令控制）
    python scripts/run_infer.py --kind custom_voice --text "你好，这是一次离线推理测试。" \
        --speaker vivian --instruct "语气自然一点" --out E:/tmp/test.wav

    # 声音克隆
    python scripts/run_infer.py --kind clone --text "克隆测试" --ref-audio ref.wav --ref-text "参考文本"

    # 音色设计
    python scripts/run_infer.py --kind voice_design --text "设计测试" \
        --design "年轻女性，温柔，语速稍慢"

    # 只检查模型能否加载（不合成）
    python scripts/run_infer.py --kind custom_voice --text "" --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.bootstrap import bootstrap  # noqa: E402

bootstrap()

from app.audio import write_wav  # noqa: E402
from app.config import config_manager  # noqa: E402
from app.engine import vram  # noqa: E402


def main() -> int:
    cfg = config_manager.app
    parser = argparse.ArgumentParser(description="Qwen3-TTS 单次推理（离线定位）")
    parser.add_argument("--kind", default="custom_voice", choices=["custom_voice", "clone", "voice_design"])
    parser.add_argument("--text", default="你好，这是一次离线推理测试。")
    parser.add_argument("--language", default="Auto")
    parser.add_argument("--speaker", default="vivian")
    parser.add_argument("--instruct", default="")
    parser.add_argument("--design", default="")
    parser.add_argument("--ref-audio", default="")
    parser.add_argument("--ref-text", default="")
    parser.add_argument("--model", default="", help="模型目录（默认取配置中的对应模型）")
    parser.add_argument("--device", default=cfg.local.device)
    parser.add_argument("--dtype", default=cfg.local.dtype)
    parser.add_argument("--max-new-tokens", type=int, default=cfg.local.max_new_tokens)
    parser.add_argument("--out", default="out/infer.wav")
    parser.add_argument("--offload", action="store_true",
                        help="显存不足时用 CPU/磁盘 offload（低显存机器可用，速度慢）")
    parser.add_argument("--cpu-memory-gb", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    model_path = args.model or getattr(cfg.local.models, args.kind)
    print(f"模型：{model_path}")
    print(f"设备：{args.device}  精度：{args.dtype}")
    if not Path(model_path).exists():
        print(f"[FAIL] 模型目录不存在：{model_path}")
        print("提示：运行 python scripts/download_models.py 下载模型")
        return 2
    print(f"显存：{vram.device_info()}")

    import torch
    from qwen_tts import Qwen3TTSModel

    dtype = getattr(torch, args.dtype, torch.bfloat16)
    started = time.perf_counter()
    model = None
    try:
        if args.offload:
            gpu_gb = max(1.0, round(vram.device_info()["free_gb"] - 0.9, 1))
            offload_dir = Path(cfg.data_dir) / "offload"
            offload_dir.mkdir(parents=True, exist_ok=True)
            print(f"offload 模式：GPU {gpu_gb}GiB + CPU {args.cpu_memory_gb}GiB，目录 {offload_dir}")
            model = Qwen3TTSModel.from_pretrained(
                model_path,
                device_map="auto",
                max_memory={0: f"{gpu_gb}GiB", "cpu": f"{args.cpu_memory_gb}GiB"},
                offload_folder=str(offload_dir),
                dtype=dtype,
            )
        else:
            # 优先用 device_map 交给 accelerate 摆放（与主程序一致，避免子模块落在 CPU）
            try:
                model = Qwen3TTSModel.from_pretrained(
                    model_path, device_map=args.device, dtype=dtype
                )
            except (TypeError, ValueError) as exc:
                print(f"device_map 加载失败，回退手工搬运：{exc}")
                model = Qwen3TTSModel.from_pretrained(model_path, torch_dtype=dtype)
                if hasattr(model, "model"):
                    model.model.to(args.device)
                    tok = getattr(model.model, "speech_tokenizer", None)
                    if tok is not None and hasattr(tok, "to"):
                        tok.to(args.device)
        if hasattr(model, "model"):
            model.model.eval()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 模型加载失败：{type(exc).__name__}: {exc}")
        if "out of memory" in str(exc).lower():
            print("提示：显存不足，可加 --offload 或改用 0.6B 回退模型 / 切换到云端模式")
        return 3
    print(f"[ OK ] 模型加载完成：{time.perf_counter() - started:.1f}s，显存 {vram.allocated_mb()}MB")

    if args.dry_run:
        print("dry-run 结束，不执行合成")
        return 0

    gen_kwargs = {"do_sample": True, "temperature": 0.9, "top_p": 0.8, "max_new_tokens": args.max_new_tokens}
    started = time.perf_counter()
    with torch.inference_mode():
        try:
            if args.kind == "clone":
                if not args.ref_audio:
                    print("[FAIL] 克隆需要 --ref-audio")
                    return 2
                x_only = not (args.ref_text or "").strip()
                wavs, sr = model.generate_voice_clone(
                    text=args.text,
                    language=args.language,
                    ref_audio=args.ref_audio,
                    ref_text=(None if x_only else args.ref_text),
                    x_vector_only_mode=x_only,
                    **gen_kwargs,
                )
            elif args.kind == "voice_design":
                wavs, sr = model.generate_voice_design(
                    text=args.text, instruct=args.design or args.instruct or "自然清晰的播音音色",
                    language=args.language, **gen_kwargs,
                )
            else:
                wavs, sr = model.generate_custom_voice(
                    text=args.text, speaker=args.speaker, language=args.language,
                    instruct=args.instruct or None, **gen_kwargs,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] 推理失败：{type(exc).__name__}: {exc}")
            return 4

    elapsed = time.perf_counter() - started
    pcm = wavs[0]
    duration = len(pcm) / sr
    out = Path(args.out)
    write_wav(pcm, sr, out)
    print(f"[ OK ] 合成完成：{duration:.2f}s 音频 / {elapsed:.2f}s 耗时 / RTF {duration / elapsed:.2f}")
    print(f"[ OK ] 输出：{out.resolve()}  采样率 {sr}Hz  显存 {vram.allocated_mb()}MB")
    vram.hard_release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
