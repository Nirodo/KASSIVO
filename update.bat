@echo off
rem KASSIVO aktualisieren: ein heruntergeladenes und geprueftes Update installieren.
rem Vorher in KASSIVO unter Einstellungen, Update das Paket herunterladen und KASSIVO beenden.
rem Das Skript sichert zuerst die Datenbank, tauscht dann die Programmdateien aus und setzt
rem bei einem Fehler den alten Stand zurueck. Die Datenbank selbst wird nicht veraendert.
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
rem Der Ablauf steht in einem Block: cmd liest ihn vollstaendig ein, bevor er startet.
rem So stoert es nicht, dass das Update diese Datei selbst austauscht.
(
    if not exist ".venv\Scripts\python.exe" (
        echo Die virtuelle Umgebung .venv fehlt. Ein Update ist so nicht moeglich.
        echo Bitte KASSIVO wie im README beschrieben installieren.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -I -m kassivo.update apply
    set "rc=!errorlevel!"
    echo.
    pause
    exit /b !rc!
)
