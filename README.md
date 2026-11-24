# KASSIVO
KASSIVO ist die moderne Lösung zur einfachen und übersichtlichen Verwaltung von Gruppenkassen. Ob Verein, Mannschaft, Freundeskreis oder gemeinsame Projekte – mit KASSIVO lassen sich Einzahlungen, Ausgaben und Kassenbestände zentral verwalten. Transparent, unkompliziert und jederzeit im Blick.

## Was ist KASSIVO?

KASSIVO ist ein lokales Kassenprogramm für eine Person an einem Rechner. Es verwaltet Mitglieder und ihre monatlichen Beiträge, liest Kontoumsätze aus Exportdateien der Bank ein, ordnet sie Mitgliedern und Kategorien zu und zeigt, wer seinen Beitrag bezahlt hat. Die Oberfläche läuft als Webseite im Browser, aber nur auf dem eigenen Rechner.

## Funktionen

- Mitglieder mit Aktiv/Passiv und Beiträgen mit Gültigkeitsmonat
- Import von Kontoumsätzen als `XML CAMT V8` (ZIP oder XML)
- Automatische Zuordnung mit Lernen und die Liste "Unklar" für alles, was nicht eindeutig ist
- Kategorien und Regeln
- Beitragsprüfung pro Monat
- Prognose
- Jahresreport mit Diagrammen
- Backup der Datenbank

## Voraussetzungen

Python ab 3.10. Entwickelt wurde mit Python 3.15 (siehe `.venv`).

## Installation

Im Projektordner:

```
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

Unter Linux oder macOS heißt der Aktivierungsbefehl `source .venv/bin/activate`.

## Start

```
python -m kassivo
```

Danach ist KASSIVO unter http://127.0.0.1:8000 im Browser erreichbar. Beim Start zeigt KASSIVO den vollständigen Pfad der Datenbank und des Sicherungsordners an.

## Speicherort der Daten

Die Datenbank liegt standardmäßig außerhalb des Projektordners in einem lokalen Ordner des Benutzers:

- Windows: `%LOCALAPPDATA%\KASSIVO\kassivo.db` (meist `C:\Users\<Name>\AppData\Local\KASSIVO\kassivo.db`)
- Linux und macOS: `$XDG_DATA_HOME/kassivo/kassivo.db`, sonst `~/.local/share/kassivo/kassivo.db`

Sicherungen liegen im Ordner `backups` neben der Datenbank, also in `<DB-Ordner>/backups`. Der DB-Ordner wird beim Start angelegt, falls er fehlt.

Einen anderen Ort wählt die Umgebungsvariable `KASSIVO_DB` (ein relativer Pfad wird beim Start in einen absoluten umgewandelt):

```
set KASSIVO_DB=D:\Kasse\kassivo.db
python -m kassivo
```

Unter Linux oder macOS: `KASSIVO_DB=/pfad/zur/kassivo.db python -m kassivo`.

**Warnung zu Cloud-Ordnern:** Die Datenbank und die Sicherungen enthalten Namen, Adressen und IBANs. Sie gehören nicht in einen Ordner, der mit OneDrive, Dropbox, Google Drive oder iCloud synchronisiert wird. Liegt der Pfad in einem solchen Ordner, zeigt KASSIVO beim Start eine Warnung (der Start wird nicht verhindert).

**Vorhandene Datenbank umziehen:** Wer schon mit einer älteren Version gearbeitet hat, findet die Datenbank in `data/kassivo.db` im Projektordner. KASSIVO beenden, `data/kassivo.db` (und `data/backups/`, falls vorhanden) an den neuen Ort kopieren, zum Beispiel nach `%LOCALAPPDATA%\KASSIVO\`, KASSIVO starten und prüfen, dass der angezeigte Pfad stimmt und die Daten da sind. Danach die alte Kopie im Projektordner löschen, damit sie nicht weiter synchronisiert wird.

## Ablauf Import

1. Im Online-Banking den Export "XML CAMT V8, gebuchte Umsätze" erzeugen.
2. In KASSIVO die Seite `/import` öffnen und die ZIP- oder XML-Datei hochladen.
3. KASSIVO erkennt bereits importierte Umsätze als Duplikate und legt sie nicht doppelt an. Hinweise und Warnungen zur Datei werden nach dem Import angezeigt.
4. Umsätze, die keinem Mitglied sicher zugeordnet werden können, erscheinen in der Liste "Unklar" und können dort von Hand zugeordnet werden.

## Daten und Sicherheit

- Alles bleibt lokal. KASSIVO hat keine Verbindung zur Bank und schickt nichts ins Internet.
- Der Server lauscht nur auf 127.0.0.1, andere Rechner im Netz erreichen ihn nicht.
- Die Datenbank enthält Namen, Adressen und IBANs. Sie liegt im lokalen Datenordner (siehe "Speicherort der Daten"), Sicherungen in `<DB-Ordner>/backups`.
- Echte Bankdateien und Datenbanken gehören nie ins Git-Repository, in Issues oder Pull Requests. Die `.gitignore` schließt unter anderem `data/`, `backups/`, `*.db`, `*.zip`, `*.xml` und CAMT/MT940-Dateien aus.

## Backup und Wiederherstellung

Sicherung: Seite `/backup` (Menüpunkt "Backup") öffnen und "Sicherung erstellen" wählen. Die Sicherung ist eine vollständige Kopie der Datenbank mit dem Namen `kassivo-JJJJMMTT-HHMMSS.db` in `<DB-Ordner>/backups`. Den genauen Pfad zeigt KASSIVO beim Start an. Die Seite listet die vorhandenen Sicherungen. Alte Sicherungen können von Hand gelöscht werden, KASSIVO löscht nichts selbst.

Wiederherstellung (von Hand):

1. KASSIVO beenden.
2. Die aktuelle `kassivo.db` im DB-Ordner an einen anderen Ort kopieren oder umbenennen, damit sie nicht verloren geht.
3. Eine eventuell vorhandene Datei `kassivo.db-journal` neben der Datenbank löschen, damit kein altes Journal auf die wiederhergestellte Kopie angewendet wird.
4. Die gewünschte Sicherung aus `<DB-Ordner>/backups` als `kassivo.db` in den DB-Ordner kopieren.
5. KASSIVO starten und die Daten prüfen.

## Tests

```
pytest
```

Die Tests verwenden nur erfundene Daten.

## Projektstruktur

```
src/kassivo/
  __main__.py   Startpunkt (python -m kassivo)
  backup.py     Sicherung der Datenbank
  camt.py       Einlesen von CAMT-Dateien
  categories.py Kategorien und Regeln
  db.py         Datenbankschema und Verbindung
  dues.py       Beitragsprüfung pro Monat
  fees.py       Beiträge mit Gültigkeitsmonat
  forecast.py   Prognose
  iban.py       IBAN-Normalisierung
  ingest.py     Import und Duplikaterkennung
  matching.py   Zuordnung von Umsätzen zu Mitgliedern
  members.py    Mitglieder und Mitgliedsarten
  money.py      Geldbeträge (ganze Cent)
  months.py     Monatsrechnung
  reports.py    Jahresreport
  settings.py   Ort der Datenbank (Standardordner, KASSIVO_DB, Cloud-Warnung)
  web/          Weboberfläche (FastAPI, Templates, Routen)
```

## Entwicklung

Der Git-Workflow steht in `CLAUDE.md`. Kurz: Ab Release 1.0.0 gibt es nur noch Branches `feat/<thema>` und `fix/<thema>` von `main`, Änderungen kommen über Pull Requests, jede Änderung hat ein Issue. Versionen folgen SemVer, jedes Release bekommt einen Tag `vX.Y.Z` und ein GitHub-Release.

## Lizenz

Siehe `LICENSE`.
