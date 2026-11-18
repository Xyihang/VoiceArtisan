@echo off
setlocal
cd /d "%~dp0"

if exist "launcher\dist\QWENTTS-Setup.exe" (
    start "" "launcher\dist\QWENTTS-Setup.exe"
    exit /b 0
)

echo ============================================================
echo  VoiceSmith launcher not found
echo  Expected: launcher\dist\QWENTTS-Setup.exe
echo ============================================================
echo.
echo Manual start:
echo   1^) python -m venv .venv
echo   2^) .venv\Scripts\activate
echo   3^) pip install -r requirements.txt
echo   4^) cd backend ^&^& python -m app.main
echo.
pause
exit /b 1
