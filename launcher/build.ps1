# Qwen3-TTS 一键安装器 · 打包脚本（PyInstaller → QWENTTS-Setup.exe）
#
# 用法（在项目根或任意目录执行均可）：
#   powershell -NoProfile -File .\launcher\build.ps1
#   powershell -NoProfile -Command "& { . 'launcher\build.ps1' }"
#
# 产物：launcher\dist\QWENTTS-Setup.exe（单文件、无控制台窗口）
# 说明：仅打包 launcher 本体（纯标准库 + tkinter），不含模型与后端代码；
#       运行时仍需要系统已安装 Python 3.10+/Node（后端服务由源码目录提供）。

[CmdletBinding()]
param(
    [switch]$SkipInstallMirror,   # 不用国内镜像装 pyinstaller
    [switch]$KeepBuild            # 保留中间目录 launcher\build
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$entry = Join-Path $projectRoot 'launcher\qwentts_launcher.py'
$distDir = Join-Path $scriptDir 'dist'
$workDir = Join-Path $scriptDir 'build'
$exePath = Join-Path $distDir 'QWENTTS-Setup.exe'
$mirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'

# 调用原生命令时必须临时放宽 ErrorActionPreference，
# 否则 pyinstaller 写到 stderr 的普通 INFO 会被当成终止性错误抛出。
function Invoke-Native([scriptblock]$block) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $block } finally { $ErrorActionPreference = $previous }
}

function Write-Step([string]$message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Ok([string]$message) { Write-Host "    $message" -ForegroundColor Green }
function Write-Warn2([string]$message) { Write-Host "!!  $message" -ForegroundColor Yellow }
function Fail([string]$message) { Write-Host "[失败] $message" -ForegroundColor Red; exit 1 }

try {
    Set-Location $projectRoot

    # ---------- 0. 前置检查 ----------
    Write-Step '检查入口文件与 Python'
    if (-not (Test-Path $entry)) { Fail "找不到入口文件：$entry" }
    Write-Ok "入口：$entry"

    $python = $null
    foreach ($candidate in @('python', 'python3', 'py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { $python = $cmd.Source; break }
    }
    if (-not $python) { Fail '未找到 Python，请先安装 Python 3.10+ 并加入 PATH：https://www.python.org/downloads/' }
    $pyVersion = & $python -c "import sys;print('%d.%d.%d' % sys.version_info[:3])"
    if ([version]$pyVersion -lt [version]'3.10') { Fail "Python 版本过低（$pyVersion），打包需要 3.10 及以上" }
    Write-Ok "Python $pyVersion（$python）"

    # ---------- 1. 检查/安装 PyInstaller ----------
    Write-Step '检查 PyInstaller'
    $pyiArgs = @('-m', 'PyInstaller', '--version')
    $pyiVersion = $null
    try { $pyiVersion = (Invoke-Native { & $python @pyiArgs 2>&1 | Out-String }).Trim() } catch { $pyiVersion = $null }
    if (-not $pyiVersion -or $LASTEXITCODE -ne 0) {
        Write-Warn2 '未检测到 PyInstaller，开始安装（仅装在当前 Python 环境，不改系统 PATH）'
        if ($SkipInstallMirror) {
            Invoke-Native { & $python -m pip install pyinstaller }
        } else {
            Invoke-Native { & $python -m pip install pyinstaller -i $mirror }
            if ($LASTEXITCODE -ne 0) {
                Write-Warn2 '国内镜像安装失败，改用官方源重试'
                Invoke-Native { & $python -m pip install pyinstaller }
            }
        }
        if ($LASTEXITCODE -ne 0) { Fail 'PyInstaller 安装失败：请检查网络/代理，或手动执行 python -m pip install pyinstaller' }
        $pyiVersion = (Invoke-Native { & $python -m PyInstaller --version 2>&1 | Out-String }).Trim()
    }
    Write-Ok "PyInstaller $pyiVersion"

    # ---------- 2. 打包 ----------
    Write-Step '开始打包（--onefile --windowed，约 1-3 分钟）'
    New-Item -ItemType Directory -Force -Path $distDir | Out-Null
    if (Test-Path $exePath) { Remove-Item $exePath -Force }

    $pyiCmd = @(
        '-m', 'PyInstaller',
        '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'QWENTTS-Setup',
        '--distpath', $distDir,
        '--workpath', $workDir,
        '--specpath', $scriptDir,
        $entry
    )
    Write-Host ("    命令：{0} {1}" -f $python, ($pyiCmd -join ' ')) -ForegroundColor DarkGray

    $buildLog = Join-Path $env:TEMP 'qwentts-pyinstaller.log'
    Invoke-Native { & $python @pyiCmd *>&1 | Tee-Object -FilePath $buildLog }
    $buildExit = $LASTEXITCODE

    if ($buildExit -ne 0) {
        Write-Warn2 "PyInstaller 退出码 $buildExit，错误摘要："
        if (Test-Path $buildLog) { Get-Content $buildLog -Tail 25 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkYellow } }
        Fail "打包失败：常见原因是磁盘空间不足、杀软拦截 PyInstaller、或 tkinter 缺失（完整日志：$buildLog）"
    }
    if (-not (Test-Path $exePath)) {
        Fail "打包命令成功但未生成产物：$exePath（请检查 --distpath 权限或杀软隔离）"
    }

    # ---------- 3. 结果 ----------
    $sizeMb = [math]::Round((Get-Item $exePath).Length / 1MB, 2)
    Write-Step '打包完成'
    Write-Host "    产物路径：$exePath" -ForegroundColor Green
    Write-Host "    产物大小：$sizeMb MB" -ForegroundColor Green
    Write-Host '    分发方式：把 QWENTTS-Setup.exe 与源码压缩包一起发给用户；双击即可运行。' -ForegroundColor Gray
    Write-Host '    注意：首次运行可能被杀软/Windows Defender 拦一下，选择「仍要运行」并加入信任。' -ForegroundColor Yellow
    Write-Host '    打包日志：' -NoNewline; Write-Host $buildLog -ForegroundColor DarkGray

    if (-not $KeepBuild) {
        if (Test-Path $workDir) { Remove-Item $workDir -Recurse -Force -ErrorAction SilentlyContinue }
        $specFile = Join-Path $scriptDir 'QWENTTS-Setup.spec'
        if (Test-Path $specFile) { Remove-Item $specFile -Force -ErrorAction SilentlyContinue }
    }
    exit 0
}
catch {
    Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    exit 1
}
