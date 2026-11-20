@echo off
setlocal
cd /d "%~dp0"

rem Prefer the PySide6 launcher (new UI); fall back to the tkinter build.
rem Old QWENTTS-* names stay as fallbacks so already-shipped packages keep working.
if exist "launcher\dist-qt\VoiceArtisan-Setup-Qt\VoiceArtisan-Setup-Qt.exe" (
    start "" "launcher\dist-qt\VoiceArtisan-Setup-Qt\VoiceArtisan-Setup-Qt.exe"
    exit /b 0
)
if exist "launcher\dist-qt\QWENTTS-Setup-Qt\QWENTTS-Setup-Qt.exe" (
    start "" "launcher\dist-qt\QWENTTS-Setup-Qt\QWENTTS-Setup-Qt.exe"
    exit /b 0
)
if exist "launcher\dist\VoiceArtisan-Setup.exe" (
    start "" "launcher\dist\VoiceArtisan-Setup.exe"
    exit /b 0
)
if exist "launcher\dist\QWENTTS-Setup.exe" (
    start "" "launcher\dist\QWENTTS-Setup.exe"
    exit /b 0
)

echo ============================================================
echo  VoiceArtisan launcher not found
echo  Expected one of:
echo    launcher\dist-qt\VoiceArtisan-Setup-Qt\VoiceArtisan-Setup-Qt.exe
echo    launcher\dist\VoiceArtisan-Setup.exe
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
