@echo off
REM ============================================================
REM  Qwen3-TTS model one-click download (double-click to run)
REM  Downloads Qwen3-TTS 1.7B x3 (TTS / voice clone / voice design)
REM  about 12.6 GB. Already-downloaded files are skipped,
REM  interrupted downloads resume automatically.
REM ============================================================
setlocal
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set HF_HOME=%~dp0.cache\hf
set MODELSCOPE_CACHE=%~dp0.cache\modelscope
set TORCH_HOME=%~dp0.cache\torch

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found. Install Python 3.10+ and add it to PATH.
  pause
  exit /b 1
)

echo ============================================================
echo  Downloading Qwen3-TTS models from ModelScope (China direct)
echo  Target: %~dp0models
echo ============================================================
echo.

python "%~dp0scripts\download_models.py" --size 1.7B --out "%~dp0models" --jobs 4
set RC=%ERRORLEVEL%

echo.
if %RC% neq 0 (
  echo [INFO] Some files failed. Run this file again to resume.
) else (
  echo [DONE] Models ready. Next steps:
  echo        .\scripts\start.ps1                    ^(start service^)
  echo        cd backend ^&^& python -m app.main --selfcheck   ^(verify^)
)
pause
