@echo off
rem KASSIVO starten: Server hochfahren und den Browser oeffnen.
rem Aufruf: start.bat           (mit Browser)
rem         start.bat nobrowser (nur Server, fuer Tests)
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Die virtuelle Umgebung .venv fehlt.
    echo Einmalig im Projektordner ausfuehren:
    echo     python -m venv .venv
    echo     .venv\Scripts\python -m pip install -e ".[dev]"
    echo Danach start.bat erneut starten.
    pause
    exit /b 1
)

if /i not "%~1"=="nobrowser" (
    rem Browser nach kurzer Wartezeit oeffnen, damit der Server schon laeuft.
    start "" /b cmd /c "ping -n 4 127.0.0.1 >nul & start "" http://127.0.0.1:8000"
)

echo KASSIVO laeuft. Zum Beenden dieses Fenster schliessen oder Strg+C druecken.
".venv\Scripts\python.exe" -m kassivo
echo.
echo KASSIVO wurde beendet.
pause
