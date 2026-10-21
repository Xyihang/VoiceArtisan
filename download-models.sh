#!/usr/bin/env bash
# Qwen3-TTS 模型一键下载（Linux / macOS）
# 下载 1.7B 三件套（约 12.6GB），已下载文件自动跳过，中断可续传
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

export PYTHONIOENCODING=utf-8
export HF_HOME="$ROOT/.cache/hf"
export MODELSCOPE_CACHE="$ROOT/.cache/modelscope"
export TORCH_HOME="$ROOT/.cache/torch"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "未找到 python，请先安装 Python 3.10+" >&2
  exit 1
fi

echo "==> 下载 Qwen3-TTS 1.7B 三件套到 $ROOT/models"
exec $PY "$ROOT/scripts/download_models.py" --size 1.7B --out "$ROOT/models" --jobs 4 "$@"
