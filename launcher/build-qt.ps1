# 用它把 PySide6 版启动器打成 exe（launcher/dist/QWENTTS-Setup-Qt.exe）
# 用法：powershell -ExecutionPolicy Bypass -File launcher\build-qt.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

Write-Host "== 检查 PySide6 ==" -ForegroundColor Cyan
python -c "import PySide6" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "未安装 PySide6，正在安装（约 150MB）…" -ForegroundColor Yellow
    python -m pip install pyside6 -i https://pypi.tuna.tsinghua.edu.cn/simple
}

Write-Host "== 打包（onedir，便于快速启动）==" -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm --clean --onedir `
    --name QWENTTS-Setup-Qt `
    --distpath "$here\dist-qt" `
    --workpath "$root\.cache\pyi-launcher-qt" `
    --specpath "$root\.cache\pyi-launcher-qt" `
    --paths "$here" `
    --exclude-module tkinter `
    --exclude-module torch `
    --exclude-module transformers `
    "$here\qwentts_launcher_qt.py"

Write-Host "== 完成 ==" -ForegroundColor Green
Write-Host "产物：$here\dist-qt\QWENTTS-Setup-Qt\QWENTTS-Setup-Qt.exe"
