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

Danach ist KASSIVO unter http://127.0.0.1:8000 im Browser erreichbar. Standardmäßig liegt die Datenbank in `data/kassivo.db`. Mit der Umgebungsvariable `KASSIVO_DB` lässt sich ein anderer Pfad wählen, zum Beispiel `KASSIVO_DB=D:\Kasse\kassivo.db`.

## Ablauf Import

1. Im Online-Banking den Export "XML CAMT V8, gebuchte Umsätze" erzeugen.
2. In KASSIVO die Seite `/import` öffnen und die ZIP- oder XML-Datei hochladen.
3. KASSIVO erkennt bereits importierte Umsätze als Duplikate und legt sie nicht doppelt an. Hinweise und Warnungen zur Datei werden nach dem Import angezeigt.
4. Umsätze, die keinem Mitglied sicher zugeordnet werden können, erscheinen in der Liste "Unklar" und können dort von Hand zugeordnet werden.

## Daten und Sicherheit

- Alles bleibt lokal. KASSIVO hat keine Verbindung zur Bank und schickt nichts ins Internet.
- Der Server lauscht nur auf 127.0.0.1, andere Rechner im Netz erreichen ihn nicht.
- Die Datenbank enthält Namen, Adressen und IBANs. Sie liegt in `data/kassivo.db`, Sicherungen in `data/backups/`.
- Echte Bankdateien und Datenbanken gehören nie ins Git-Repository, in Issues oder Pull Requests. Die `.gitignore` schließt unter anderem `data/`, `backups/`, `*.db`, `*.zip`, `*.xml` und CAMT/MT940-Dateien aus.

## Backup und Wiederherstellung

Sicherung: Seite `/backup` (Menüpunkt "Backup") öffnen und "Sicherung erstellen" wählen. Die Sicherung ist eine vollständige Kopie der Datenbank mit dem Namen `kassivo-JJJJMMTT-HHMMSS.db` in `data/backups/`. Die Seite listet die vorhandenen Sicherungen. Alte Sicherungen können von Hand gelöscht werden, KASSIVO löscht nichts selbst.

Wiederherstellung (von Hand):

1. KASSIVO beenden.
2. Die gewünschte Sicherung aus `data/backups/` über `data/kassivo.db` kopieren.
3. KASSIVO wieder starten.

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
  settings.py   Pfad der Datenbank (KASSIVO_DB)
  web/          Weboberfläche (FastAPI, Templates, Routen)
```

## Entwicklung

Der Git-Workflow steht in `CLAUDE.md`. Kurz: Ab Release 1.0.0 gibt es nur noch Branches `feat/<thema>` und `fix/<thema>` von `main`, Änderungen kommen über Pull Requests, jede Änderung hat ein Issue. Versionen folgen SemVer, jedes Release bekommt einen Tag `vX.Y.Z` und ein GitHub-Release.

## Lizenz

Siehe `LICENSE`.
