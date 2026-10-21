#!/usr/bin/env bash
# Qwen3-TTS 语音生成系统 · 一键启动（Linux / macOS）
#
# 用法：
#   ./scripts/start.sh                # 启动（自动检查/安装依赖、构建前端）
#   ./scripts/start.sh --selfcheck    # 只跑自检
#   ./scripts/start.sh --no-https     # 本地调试关闭 HTTPS
#   ./scripts/start.sh --cloud        # 以云端推理服务角色启动
#   PORT=9443 ./scripts/start.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$1"; }

ARGS=()
SELFCHECK=0
NOHTTPS=0
CLOUD=0
NOBUILD=0
for arg in "$@"; do
  case "$arg" in
    --selfcheck) SELFCHECK=1 ;;
    --no-https) NOHTTPS=1 ;;
    --cloud) CLOUD=1 ;;
    --no-build) NOBUILD=1 ;;
    *) warn "未知参数 $arg" ;;
  esac
done

# ---------- 1. Python ----------
step "检查 Python 运行环境"
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
  echo "未找到 Python，请安装 Python 3.10+（如 sudo apt install python3 python3-venv）" >&2
  exit 1
fi
VERSION="$($PY -c 'import sys;print("%d.%d" % sys.version_info[:2])')"
if [ "$(printf '%s\n3.10\n' "$VERSION" | sort -V | head -n1)" != "3.10" ]; then
  echo "Python 版本过低（$VERSION），需要 3.10 及以上" >&2
  exit 1
fi
echo "    Python $VERSION ($PY)"

# ---------- 2. 缓存重定向 ----------
CACHE="$ROOT/.cache"
mkdir -p "$CACHE/hf" "$CACHE/modelscope" "$CACHE/torch"
export HF_HOME="$CACHE/hf"
export MODELSCOPE_CACHE="$CACHE/modelscope"
export TORCH_HOME="$CACHE/torch"
export PYTHONIOENCODING=utf-8
export PYTHONPATH="$ROOT/backend"
echo "    缓存目录 $CACHE"

# ---------- 3. 依赖 ----------
step "检查后端依赖"
MISSING="$($PY - <<'PY'
import importlib
req = {
    'fastapi': 'fastapi', 'uvicorn': 'uvicorn', 'pydantic': 'pydantic', 'yaml': 'PyYAML',
    'httpx': 'httpx', 'paramiko': 'paramiko', 'soundfile': 'soundfile', 'numpy': 'numpy',
    'cryptography': 'cryptography', 'multipart': 'python-multipart', 'torch': 'torch',
    'transformers': 'transformers', 'qwen_tts': 'qwen-tts', 'websockets': 'websockets',
}
missing = []
for module, package in req.items():
    try:
        importlib.import_module(module)
    except Exception:
        missing.append(package)
print(' '.join(missing))
PY
)"
if [ -n "${MISSING// /}" ]; then
  warn "缺少依赖：$MISSING，开始安装"
  $PY -m pip install --upgrade pip >/dev/null || true
  $PY -m pip install -r "$ROOT/requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple \
    || $PY -m pip install -r "$ROOT/requirements.txt"
else
  echo "    依赖齐全"
fi

# ---------- 4. 前端 ----------
if [ "$NOBUILD" -eq 0 ] && [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  step "构建前端"
  if command -v npm >/dev/null 2>&1; then
    ( cd "$ROOT/frontend"
      [ -d node_modules ] || npm install --registry https://registry.npmmirror.com --no-audit --no-fund
      npm run build )
  else
    warn "未找到 npm，跳过前端构建（接口仍可用，访问 /docs）"
  fi
fi

# ---------- 5. 配置 ----------
if [ ! -f "$ROOT/config/config.yaml" ]; then
  step "生成配置文件 config/config.yaml"
  cp "$ROOT/config/config.example.yaml" "$ROOT/config/config.yaml"
  warn "已从模板生成配置，请按需修改云端地址/密钥与模型路径"
fi

# ---------- 6. 启动 ----------
[ "$SELFCHECK" -eq 1 ] && ARGS+=(--selfcheck)
[ "$NOHTTPS" -eq 1 ] && ARGS+=(--no-https)
if [ "$CLOUD" -eq 1 ]; then
  export QWENTTS__RUN__MODE=local
  echo "    以云端推理服务角色启动（本地模型 + /v1 接口 + 密钥鉴权）"
fi

step "启动服务"
cd "$ROOT/backend"
exec $PY -m app.main "${ARGS[@]}"
