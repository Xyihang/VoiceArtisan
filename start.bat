@echo off
REM ============================================================
REM  Qwen3-TTS 语音生成系统 · 一键启动（Windows 双击运行）
REM  自动：检查 Python/依赖 -> 构建前端 -> 生成配置 -> 自检 -> 起服务
REM  Made by xiyihang360@163.com - All rights reserved.
REM ============================================================
setlocal
cd /d "%~dp0"

echo ============================================================
echo  Qwen3-TTS 语音生成系统
echo  项目目录: %~dp0
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 未找到 python。请先安装 Python 3.10+ 并勾选 "Add python.exe to PATH"
  echo         下载: https://www.python.org/downloads/
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
set RC=%ERRORLEVEL%

echo.
if %RC% neq 0 (
  echo [INFO] 服务已退出（退出码 %RC%）。常见原因：
  echo        - 端口 8443 被占用：改 config\config.yaml 的 server.port
  echo        - 缺少模型：运行 python scripts\download_models.py --size 1.7B
  echo        - 依赖缺失：运行 pip install -r requirements.lock.txt
  echo        详细排查见 docs\TROUBLESHOOTING.md
)
pause
