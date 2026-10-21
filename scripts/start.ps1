# Qwen3-TTS 语音生成系统 · 一键启动（Windows PowerShell）
#
# 用法：
#   .\scripts\start.ps1              # 启动（自动检查/安装依赖、构建前端、打开浏览器）
#   .\scripts\start.ps1 -SelfCheck   # 只跑自检
#   .\scripts\start.ps1 -NoHttps     # 本地调试关闭 HTTPS
#   .\scripts\start.ps1 -Cloud       # 以云端推理服务角色启动（对外提供 /v1 接口）

param(
    [switch]$SelfCheck,
    [switch]$NoHttps,
    [switch]$NoBuild,
    [switch]$Cloud,
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "!!  $message" -ForegroundColor Yellow }

# ---------- 1. 运行时检查 ----------
Write-Step '检查 Python 运行环境'
$python = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source; break }
}
if (-not $python) {
    Write-Error '未找到 Python，请先安装 Python 3.10+ 并加入 PATH：https://www.python.org/downloads/'
    exit 1
}
$version = & $python -c "import sys;print('%d.%d' % sys.version_info[:2])"
if ([version]$version -lt [version]'3.10') {
    Write-Error "Python 版本过低（$version），需要 3.10 及以上"
    exit 1
}
Write-Host "    Python $version ($python)"

# ---------- 2. 缓存目录重定向（避免写入 C 盘） ----------
$cacheRoot = Join-Path $root '.cache'
New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null
$env:HF_HOME = Join-Path $cacheRoot 'hf'
$env:MODELSCOPE_CACHE = Join-Path $cacheRoot 'modelscope'
$env:TORCH_HOME = Join-Path $cacheRoot 'torch'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = Join-Path $root 'backend'
Write-Host "    缓存目录 $cacheRoot"

# ---------- 3. 依赖检查与安装 ----------
Write-Step '检查后端依赖'
$depCheck = @'
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
'@
$missing = ($depCheck | & $python -) 
if ($missing -and $missing.Trim()) {
    Write-Warn "缺少依赖：$missing，开始安装（使用国内镜像）"
    & $python -m pip install --upgrade pip --quiet
    # 两段式：普通依赖走清华源；只有 torch 三件套走 pytorch 源（全局 --extra-index-url 会让每个包都去 pytorch.org 找一遍）
    & $python -m pip install -r (Join-Path $root 'requirements.txt') -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Write-Warn '国内镜像安装失败，尝试官方源'
        & $python -m pip install -r (Join-Path $root 'requirements.txt')
    }
    $torchReq = Join-Path $root 'requirements-torch.txt'
    if (Test-Path $torchReq) {
        & $python -m pip install -r $torchReq --index-url https://download.pytorch.org/whl/cu121
        if ($LASTEXITCODE -ne 0) {
            Write-Warn 'pytorch 官方源不通，改用上交镜像'
            & $python -m pip install -r $torchReq --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cu121
        }
    }
} else {
    Write-Host '    依赖齐全'
}

# ---------- 4. 前端构建 ----------
$dist = Join-Path $root 'frontend\dist\index.html'
if (-not $NoBuild -and -not (Test-Path $dist)) {
    Write-Step '构建前端'
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        Push-Location (Join-Path $root 'frontend')
        if (-not (Test-Path 'node_modules')) {
            & npm install --registry https://registry.npmmirror.com --no-audit --no-fund
        }
        & npm run build
        Pop-Location
    } else {
        Write-Warn '未找到 npm，跳过前端构建（接口仍可用，访问 /docs 查看接口文档）'
    }
} elseif (Test-Path $dist) {
    Write-Host '    前端构建产物已存在'
}

# ---------- 5. 配置初始化 ----------
$config = Join-Path $root 'config\config.yaml'
if (-not (Test-Path $config)) {
    Write-Step '生成配置文件 config/config.yaml'
    Copy-Item (Join-Path $root 'config\config.example.yaml') $config
    Write-Warn '已从模板生成配置，请按需修改云端地址/密钥与模型路径'
}

# ---------- 6. 启动 ----------
$args = @('-m', 'app.main')
if ($SelfCheck) { $args += '--selfcheck' }
if ($NoHttps) { $args += '--no-https' }
if ($Port -gt 0) { $args += @('--port', $Port) }
if ($Cloud) {
    $env:QWENTTS__RUN__MODE = 'local'
    Write-Host '    以云端推理服务角色启动（本地模型 + /v1 接口 + 密钥鉴权）'
}

Write-Step '启动服务'
Push-Location (Join-Path $root 'backend')
& $python @args
Pop-Location
