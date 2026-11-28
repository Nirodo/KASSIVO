@echo off
rem KASSIVO aktualisieren: ein heruntergeladenes und geprueftes Update installieren.
rem Vorher in KASSIVO unter Einstellungen, Update das Paket herunterladen und KASSIVO beenden.
rem Das Skript sichert zuerst die Datenbank, tauscht dann die Programmdateien aus und setzt
rem bei einem Fehler den alten Stand zurueck. Die Datenbank selbst wird nicht veraendert.
chcp 65001 >nul
rem cd vor der verzoegerten Erweiterung: sonst gingen Ausrufezeichen im Pfad verloren.
cd /d "%~dp0"
setlocal EnableDelayedExpansion
rem Der Ablauf steht in einem Block: cmd liest ihn vollstaendig ein, bevor er startet.
rem So stoert es nicht, dass das Update diese Datei selbst austauscht.
(
    if not exist ".venv\Scripts\python.exe" (
        echo Die virtuelle Umgebung .venv fehlt. Ein Update ist so nicht moeglich.
        echo Bitte KASSIVO wie im README beschrieben installieren.
        pause
        exit /b 1
    )
    rem Unvollstaendiges frueheres Update: ohne kassivo zuruecksetzen, src kann halb fehlen.
    if exist ".update\journal.json" (
        ".venv\Scripts\python.exe" -I ".update\wiederherstellen.py" .
        echo.
        echo Bitte update.bat danach erneut starten.
        pause
        exit /b 2
    )
    ".venv\Scripts\python.exe" -I -m kassivo.update apply
    set "rc=!errorlevel!"
    echo.
    pause
    exit /b !rc!
)
