# Build the PySide6 launcher into a standalone exe folder.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File launcher\build-qt.ps1
# Output: launcher\dist-qt\VoiceArtisan-Setup-Qt\VoiceArtisan-Setup-Qt.exe
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 as ANSI
# unless it has a BOM, and CJK text then breaks the parser.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

Write-Host "== check PySide6 ==" -ForegroundColor Cyan
python -c "import PySide6" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PySide6 not found, installing (about 150MB)..." -ForegroundColor Yellow
    python -m pip install pyside6 -i https://pypi.tuna.tsinghua.edu.cn/simple
}

Write-Host "== build with PyInstaller (onedir) ==" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean --onedir --name VoiceArtisan-Setup-Qt --distpath "$here\dist-qt" --workpath "$root\.cache\pyi-launcher-qt" --specpath "$root\.cache\pyi-launcher-qt" --paths "$here" --exclude-module tkinter --exclude-module torch --exclude-module transformers "$here\voiceartisan_launcher_qt.py"

if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller failed" -ForegroundColor Red
    exit 1
}

$exe = Join-Path $here "dist-qt\VoiceArtisan-Setup-Qt\VoiceArtisan-Setup-Qt.exe"
if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    $folderMb = [math]::Round(((Get-ChildItem (Split-Path $exe) -Recurse -File | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
    Write-Host "== done ==" -ForegroundColor Green
    Write-Host "exe:    $exe ($mb MB)"
    Write-Host "folder: $(Split-Path $exe) ($folderMb MB total)"
} else {
    Write-Host "exe not found: $exe" -ForegroundColor Red
    exit 1
}
