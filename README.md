# KASSIVO
KASSIVO ist die moderne Lösung zur einfachen und übersichtlichen Verwaltung von Gruppenkassen. Ob Verein, Mannschaft, Freundeskreis oder gemeinsame Projekte – mit KASSIVO lassen sich Einzahlungen, Ausgaben und Kassenbestände zentral verwalten. Transparent, unkompliziert und jederzeit im Blick.

## Überblick

KASSIVO 1.1.1 ist ein lokales Kassenprogramm für eine Person an einem Rechner. Es verwaltet Mitglieder und ihre monatlichen Beiträge, liest Kontoumsätze aus Exportdateien der Bank ein, ordnet sie Mitgliedern und Kategorien zu und zeigt, wer seinen Beitrag bezahlt hat. Die Oberfläche läuft als Webseite im Browser, aber nur auf dem eigenen Rechner (`http://127.0.0.1:8000`). Die Daten liegen in einer SQLite-Datei auf dem eigenen Rechner.

Eine ausführliche Anleitung steckt im Programm: der Menüpunkt "Wiki" (`/wiki`) erklärt jede Funktion mit Schritten und Beispielen. Das Wiki ist nur zum Lesen und braucht kein Internet.

Kontoumsätze kommen per Datei-Import (CAMT V8, Sparkasse). Eine direkte Bankabfrage (FinTS, finAPI) und ein C24-Adapter sind nicht gebaut.

## Funktionen im Detail

- **Mitglieder** (Wiki: "Mitglieder"): Anlegen, Stammdaten ändern, Mitgliedsart Aktiv oder Passiv mit Gültigkeitsmonat wechseln, Eintritt und Austritt, IBAN des Mitglieds. Neue Mitglieder zahlen ab dem Monat nach dem Eintritt, der Austrittsmonat ist noch voll zu zahlen.
- **Beiträge und Prüfungsstart** (Wiki: "Beiträge und Prüfungsstart"): Beitrag je Mitgliedsart mit Gültig-ab-Monat. Eine Änderung wirkt nur ab diesem Monat. Der Prüfungsstart legt fest, ab wann Beiträge geprüft werden.
- **Import** (Wiki: "Kontoexport importieren"): ZIP mit Tagesdateien oder einzelne XML-Datei im Format `camt.052.001.08`. Bereits importierte Umsätze erkennt KASSIVO als Duplikate, ein mehrfacher Import ist sicher.
- **Zuordnung** (Wiki: "Zahlungen zuordnen"): Zahlungseingänge ordnet KASSIVO über die Absender-IBAN automatisch Mitgliedern zu, lernt dabei aus Ihren Zuordnungen ("Absender merken") und sammelt alles Unklare in der Liste "Unklar". Zahlungen, die kein Mitglied brauchen (zum Beispiel Zinsen der Bank), verlassen "Unklar", sobald Sie ihnen eine Kategorie außer "Beiträge" geben ("Kein Mitglied nötig", "Als erledigt kategorisieren"), oder eine Regel kategorisiert sie schon beim Import.
- **Verteilung** (Wiki: "Verteilung auf Beitragsmonate"): Jede Zahlung wird auf Beitragsmonate verteilt. Nur genau passende Zahlungen bestätigt KASSIVO selbst, alles andere ist ein Vorschlag, den Sie prüfen und bestätigen.
- **Kategorien und Regeln** (Wiki: "Kategorien und Regeln"): Regeln nach Zweck, Name oder IBAN der Gegenpartei mit Priorität und Betragsgrenzen ordnen Buchungen Kategorien zu.
- **Beitragsübersicht** (Wiki: "Beitragsübersicht"): Raster Mitglieder mal Monate mit den Status Bezahlt, Teilweise, Offen, Überfällig, dazu die Zahlungshistorie je Mitglied.
- **Prognose** (Wiki: "Prognose"): Offene Mitgliedsbeiträge bis zu einem Stichtag, getrennt in überfällig und noch fällig.
- **Jahresreport** (Wiki: "Jahresreport"): Einnahmen, Ausgaben, Saldo, Diagramme und Tabellen je Kategorie und Monat.
- **Backup** (Wiki: "Sicherung und Wiederherstellung"): Sicherung der Datenbank auf Knopfdruck.
- **Hilfe**: Wiki-Themen "Erste Schritte", "Begriffe", "Datenschutz und Sicherheit", "Fehlerbehebung (FAQ)" und "Bekannte Einschränkungen".

## Voraussetzungen

Python ab 3.10 (`requires-python = ">=3.10"`). Entwickelt und getestet wird nur mit Python 3.15 (siehe `.venv`); ältere Versionen sind erlaubt, aber nicht getestet. Abhängigkeiten (werden von `pip` installiert): FastAPI, Jinja2, python-multipart, Uvicorn. Ein aktueller Browser.

## Installation

Im Projektordner, Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Blockiert PowerShell das Aktivieren, rufen Sie Python direkt aus der virtuellen Umgebung auf: `.venv\Scripts\python -m pip install -e ".[dev]"` und später `.venv\Scripts\python -m kassivo`.

Linux, macOS oder eine andere Shell:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -e ".[dev]"` installiert KASSIVO bearbeitbar zusammen mit den Entwicklungswerkzeugen (pytest, httpx). Wer nur anwenden will, nimmt `pip install .` (ohne Werkzeuge, nicht bearbeitbar).

## Start

Unter Windows genügt ein Doppelklick auf `start.bat` im Projektordner. Die Datei startet den Server mit der virtuellen Umgebung `.venv` und öffnet den Browser auf http://127.0.0.1:8000. Das Fenster bleibt offen, solange KASSIVO läuft. Zum Beenden schließen Sie das Fenster oder drücken `Strg+C`. Aus einer Eingabeaufforderung oder PowerShell rufen Sie sie mit `.\start.bat` auf; ohne `.\` findet `cmd` den Namen nicht, weil `start` dort ein eingebauter Befehl ist. Mit `.\start.bat nobrowser` startet nur der Server. Fehlt `.venv`, zeigt die Datei die nötigen Installationsschritte.

Von Hand starten Sie so:

```
python -m kassivo
```

Danach ist KASSIVO unter http://127.0.0.1:8000 im Browser erreichbar. Die Startseite zeigt die Beitragsübersicht. Beim Start schreibt KASSIVO in die Konsole:

```
Datenbank: <vollständiger Pfad zur kassivo.db>
Sicherungen: <vollständiger Pfad zum Ordner backups>
```

Liegt die Datenbank in einem Cloud-Ordner (OneDrive, Dropbox, Google Drive, iCloud), folgt eine Warnung. Der Start wird dadurch nicht verhindert. Beim ersten Start ist KASSIVO leer, die Datenbank legt es selbst an, mit den Mitgliedsarten Aktiv (17,00 €) und Passiv (10,00 €) und den Kategorien Beiträge, Hauptverein und Sonstiges.

Ist der Port 8000 schon belegt (zum Beispiel weil KASSIVO bereits läuft), meldet Uvicorn einen Fehler beim Binden der Adresse und KASSIVO beendet sich. Einen anderen Port wählt es nicht.

## Konfiguration

| Einstellung | Wirkung |
|---|---|
| `KASSIVO_DB` | Umgebungsvariable mit dem Pfad der Datenbankdatei. Ein relativer Pfad wird beim Start in einen absoluten umgewandelt. |
| Ohne `KASSIVO_DB` unter Windows | `%LOCALAPPDATA%\KASSIVO\kassivo.db` (meist `C:\Users\<Name>\AppData\Local\KASSIVO\kassivo.db`) |
| Ohne `KASSIVO_DB` unter Linux und macOS | `$XDG_DATA_HOME/kassivo/kassivo.db` (nur wenn der Pfad absolut ist), sonst `~/.local/share/kassivo/kassivo.db` |
| Sicherungen | immer `<DB-Ordner>/backups`, also neben der Datenbank |
| Adresse und Port | fest `127.0.0.1` und `8000`, nicht einstellbar |

Der DB-Ordner wird beim Start angelegt, falls er fehlt. Einen anderen Ort wählen Sie so, je nach Fenster:

Eingabeaufforderung (cmd):

```
set KASSIVO_DB=D:\Kasse\kassivo.db
python -m kassivo
```

PowerShell:

```powershell
$env:KASSIVO_DB = "D:\Kasse\kassivo.db"
python -m kassivo
```

Der cmd-Befehl `set` setzt in PowerShell keine Umgebungsvariable. KASSIVO startet dann ohne Fehlermeldung mit dem Standardpfad. Die Variable gilt nur für das Fenster, in dem Sie sie gesetzt haben. Dauerhaft setzen Sie sie in cmd mit `setx KASSIVO_DB "D:\Kasse\kassivo.db"`, das wirkt erst in neuen Fenstern. Sonst startet ein späterer Start mit einer anderen Datenbank (siehe Wiki, Fehlerbehebung, Frage 3). Unter Linux oder macOS: `KASSIVO_DB=/pfad/zur/kassivo.db python -m kassivo`. Mit verschiedenen Pfaden führen Sie mehrere getrennte Kassen.

**Warnung zu Cloud-Ordnern:** Die Datenbank und die Sicherungen enthalten Namen, Adressen und IBANs. Sie gehören nicht in einen Ordner, der mit OneDrive, Dropbox, Google Drive oder iCloud synchronisiert wird.

**Vorhandene Datenbank umziehen:** Wer schon mit dem Entwicklungsstand vor 1.0.0 gearbeitet hat, findet die Datenbank in `data/kassivo.db` im Projektordner. KASSIVO beenden, `data/kassivo.db` (und `data/backups/`, falls vorhanden) an den neuen Ort kopieren, zum Beispiel nach `%LOCALAPPDATA%\KASSIVO\`, KASSIVO starten und prüfen, dass der angezeigte Pfad stimmt und die Daten da sind. Danach die alte Kopie im Projektordner löschen, damit sie nicht weiter synchronisiert wird.

## Empfohlener Ablauf

Ausführlicher im Wiki unter "Erste Schritte" (`/wiki/start`).

1. **Prüfungsstart festlegen** (Einstellungen): ab welchem Monat Beiträge geprüft werden. Solange er fehlt, zeigen Beitragsübersicht und Import einen Hinweis. Am besten den ersten vollen Monat wählen, den der Kontoexport abdeckt: Beginnt der Export mitten im Monat, fehlen frühere Zahlungen dieses Monats und er erschiene als überfällig. Zahlungen, die vor dem Prüfungsstart-Monat gebucht sind (zum Beispiel am 20.03. bei Prüfungsstart 2026-04), bekommen einen Vorschlag für den Prüfungsstart-Monat. Bestätigen Sie ihn nicht blind: Sonst gilt der April als bezahlt, und die echte Aprilzahlung wird für den Mai vorgeschlagen. Ändern Sie den Monat auf der Seite "Verteilung" oder heben Sie die Verteilung auf. Wird der Prüfungsstart später geändert, berechnet KASSIVO offene Vorschläge neu; bestätigte Verteilungen bleiben unverändert.
2. **Mitglieder und Beiträge anlegen** (Mitglieder, Einstellungen).
3. **Kontoexport importieren** (Import).
4. **"Unklar" zuordnen**: jede Zahlung einem Mitglied zuordnen, mit "Absender merken". Weitere unklare Zahlungen derselben IBAN werden dann gleich mit zugeordnet. Der Knopf "Unklare automatisch zuordnen" wendet Gelerntes erneut auf die ganze Liste an, auch auf Zahlungen, die Sie mit "— keins —" auf Unklar zurückgesetzt haben. Zahlt eine IBAN für mehrere Mitglieder (z. B. ein Elternteil für zwei Kinder), wird sie nicht automatisch zugeordnet.
5. **"Offene Vorschläge" prüfen und bestätigen** (Link in Buchungen und Unklar): Verteilungen, die KASSIVO nur vorschlägt, zählen erst nach der Bestätigung als bezahlt.
6. **Beitragsübersicht, Prognose und Report ansehen.**

Eine Zahlung bestätigt KASSIVO nur dann selbst, wenn sie genau den Beitrag des frühesten offenen Monats deckt und in diesem Monat gebucht ist. Zahlungen, die außerhalb des Fälligkeitsmonats gebucht sind (Rückstände, Vorauszahlungen, Zahlung schon im Vormonat), und Mehrmonatszahlungen brauchen immer eine Bestätigung von Hand.

## Import-Format und Ablauf

Unterstützt ist genau ein Format: `camt.052.001.08`, im Online-Banking "XML CAMT V8, gebuchte Umsätze" (Sparkasse). Eine ZIP-Datei enthält dabei eine XML-Datei je Buchungstag. Andere Formate (CAMT V2, MT940, CSV) lehnt KASSIVO ab.

1. Im Online-Banking den Export "XML CAMT V8, gebuchte Umsätze" erzeugen.
2. In KASSIVO die Seite `/import` öffnen und die ZIP- oder XML-Datei hochladen. Die ZIP-Datei wird nicht entpackt, KASSIVO liest sie direkt.
3. KASSIVO erkennt bereits importierte Umsätze als Duplikate (Bankreferenz, sonst ein Fingerabdruck aus Datum, Betrag, Gegenpartei-IBAN und Zweck) und legt sie nicht doppelt an. Hinweise und Warnungen zur Datei (zum Beispiel "Saldo stimmt nicht") erscheinen nach dem Import.
4. Auf neue Buchungen wendet KASSIVO die Kategorien-Regeln und die automatische Zuordnung an. Die Ergebnisseite zählt Neu, Duplikate, kategorisiert, zugeordnet und die offenen Vorschläge.
5. Umsätze, die keinem Mitglied sicher zugeordnet werden können, erscheinen in der Liste "Unklar" und können dort von Hand zugeordnet werden.

Grenzen: Eine Datei darf höchstens 100 MB groß sein. In einer ZIP-Datei liest KASSIVO höchstens 5000 XML-Dateien, jede höchstens 20 MB, zusammen höchstens 200 MB. Eine XML-Datei darf genau einen Bericht enthalten. Findet KASSIVO in irgendeiner Datei einen Fehler, bricht der ganze Import ab und legt nichts an (zum Beispiel bei falschem Format oder bei einem Betrag von 0,00). Ein Eintrag mit 0,00 € lässt sich nicht überspringen: Lässt man den Tag im Export weg, fehlen alle Zahlungen dieses Tages für immer, weil es keine Buchung von Hand gibt. Klären Sie den Tag mit der Bank. Die Duplikaterkennung hat bekannte Grenzen (siehe "Bekannte Einschränkungen"). Importiert werden nur Einträge mit dem Status "gebucht"; Sammelbuchungen kommen ohne Gegenpartei herein und werden nie automatisch zugeordnet.

## Regeln im Überblick

**Beitragsregeln**

- Der Beitrag ist zu Monatsbeginn fällig und darf den ganzen Monat über eingehen. Mit dem Monatsende gilt ein nicht bezahlter Beitrag als überfällig.
- Ein neues Mitglied zahlt ab dem 1. des Monats nach dem Eintritt (Feld "Zahlt ab Monat", leer lassen für diese Regel).
- Tritt ein Mitglied mitten im Monat aus, ist der Austrittsmonat noch voll zu zahlen.
- Zu wenig gezahlt: Der Monat gilt als offen oder teilbezahlt.
- Zu viel gezahlt: Der Mehrbetrag kann auf Folgemonate angerechnet werden oder versäumte Monate ausgleichen. Das Verteilen bestätigen Sie.
- Jedes Mitglied zahlt für sich. Zahlt eine andere Person, ordnen Sie die Überweisung dem Mitglied von Hand zu.
- Beträge ändern sich nur ab ihrem Gültigkeitsmonat. Frühere Monate bleiben, wie sie waren.

**Zuordnung**

KASSIVO ordnet einen neuen Zahlungseingang automatisch zu, wenn die Absender-IBAN genau einem Mitglied gehört, entweder gelernt ("Absender merken") oder im Mitgliedsdatensatz. Mehrdeutige IBANs (mehrere Mitglieder) und Zahlungen nach dem Austrittsdatum des Mitglieds bleiben in "Unklar". Eine gelernte IBAN lässt sich in dieser Version nicht vergessen. Beim Umhängen auf ein anderes Mitglied sperren bestätigte Verteilungen die Änderung, bis Sie die Verteilung aufheben.

**Verteilung**

KASSIVO füllt ab dem frühesten Monat mit Fehlbetrag die Monate der Reihe nach. Nur bestätigte Verteilungen zählen als bezahlt. Selbst bestätigt wird nur, wenn der Betrag genau den Fehlbetrag des frühesten offenen Monats deckt und der Buchungsmonat dieser Beitragsmonat ist. Beispiele bei 17,00 € Monatsbeitrag und Prüfungsstart 2026-01:

| Zahlung | Ergebnis |
|---|---|
| 17,00 € am 15.01.2026 | Januar, bestätigt (automatisch) |
| 34,00 € am 15.01.2026 | Januar und Februar je 17,00 €, Vorschlag |
| 10,00 € am 15.01.2026 | Januar 10,00 €, Vorschlag; nach Bestätigung "Teilweise" |
| 20,00 € am 15.01.2026 | Januar 17,00 € und Februar 3,00 €, Vorschlag |
| 17,00 € am 30.12.2026, Dezember schon bezahlt | Januar 2027, Vorschlag (Buchungsmonat ist nicht der Beitragsmonat) |

**Kategorien**

Eine Buchung hat höchstens eine Kategorie. Regeln prüfen, ob ein Feld (Verwendungszweck, Name oder IBAN der Gegenpartei) einen Text enthält, optional mit Betragsgrenzen. Die kleinere Prioritätszahl gewinnt. Beim Import wirken Regeln auf neue Buchungen; "Regeln anwenden" wirkt auf vorhandene und überschreibt gesetzte Kategorien nur mit ausdrücklicher Bestätigung. Der Name "Ohne Kategorie" ist reserviert.

## Berichte

- **Beitragsübersicht** (`/dues`): Raster Mitglieder mal zwölf Monate mit Bezahlt, Teilweise, Offen, Überfällig und "—" (kein Beitrag fällig). Monate mit unbestätigtem Vorschlag tragen die Markierung "Vorschlag". Ein Klick auf den Namen öffnet die Zahlungshistorie.
- **Prognose** (`/forecast`): offene Beiträge vom Prüfungsstart bis zu einem gewählten Datum (es zählt der ganze Monat des Datums: mit 10.03. rechnet die Prognose den gesamten März mit), höchstens 50 Jahre in der Zukunft. Nur Mitgliedsbeiträge zählen, keine wiederkehrenden Ausgaben.
- **Jahresreport** (`/reports`): Einnahmen, Ausgaben, Saldo, Diagramme (Chart.js) und Tabellen. Die Beiträge je Mitglied rechnet der Report nach Beitragsmonat, nicht nach Buchungsdatum.
- Die Liste "Buchungen" zeigt 100 Buchungen je Seite und lässt sich nach Jahr, Kategorie, Art und Suchtext filtern.

## Backup und Wiederherstellung

Sicherung: Seite `/backup` (Menüpunkt "Backup") öffnen und "Sicherung erstellen" wählen. Die Sicherung ist eine vollständige Kopie der Datenbank mit dem Namen `kassivo-JJJJMMTT-HHMMSS.db` in `<DB-Ordner>/backups`. Den genauen Pfad zeigt KASSIVO beim Start an. Die Seite listet die vorhandenen Sicherungen. Alte Sicherungen können von Hand gelöscht werden, KASSIVO löscht nichts selbst und sichert nicht automatisch.

Wiederherstellung (von Hand):

1. KASSIVO beenden.
2. Die aktuelle `kassivo.db` im DB-Ordner an einen anderen Ort kopieren oder umbenennen, damit sie nicht verloren geht.
3. Eine eventuell vorhandene Datei `kassivo.db-journal` neben der Datenbank löschen, damit kein altes Journal auf die wiederhergestellte Kopie angewendet wird.
4. Die gewünschte Sicherung aus `<DB-Ordner>/backups` als `kassivo.db` in den DB-Ordner kopieren.
5. KASSIVO starten und die Daten prüfen.

Sicherungen enthalten personenbezogene Daten. Sie gehören nicht in einen Cloud-Ordner.

## Datenschutz und Sicherheit

KASSIVO ist für den Betrieb durch eine Person auf dem eigenen Rechner gebaut.

- Alles bleibt lokal. KASSIVO hat keine Verbindung zur Bank, kennt keine Zugangsdaten oder PINs und schickt nichts ins Internet. Es liest nur Dateien, die Sie selbst hochladen. Auch Chart.js liegt lokal im Programm.
- Der Server lauscht nur auf 127.0.0.1, andere Rechner im Netz erreichen ihn nicht.
- **Host-Prüfung:** KASSIVO beantwortet nur Anfragen an `127.0.0.1` oder `localhost` und weist andere Host-Namen mit "Ungültiger Host." ab. Das schützt davor, dass eine fremde Webseite den Browser dazu bringt, KASSIVO unter einem anderen Namen anzusprechen.
- **Origin-Prüfung:** Anfragen, die etwas ändern (POST und ähnliche), lehnt KASSIVO ab, wenn der Origin-Header auf eine andere Herkunft zeigt. Andere offene Webseiten können so nichts in KASSIVO ändern.
- **Content-Security-Policy:** Die Seiten laden nur eigene Dateien (`default-src 'self'`) und lassen sich nicht einbetten (`frame-ancestors 'none'`). Dazu kommen `X-Content-Type-Options: nosniff` und `Referrer-Policy: same-origin`. Es gibt kein Inline-JavaScript.
- **Uploads:** Größengrenzen (siehe Import), Dateityp-Prüfung am Inhalt, ZIP-Dateien werden nie entpackt, XML mit DOCTYPE oder ENTITY wird abgelehnt. Der Dateiname des Browsers wird nie verwendet.
- IBANs erscheinen in Listen und Meldungen gekürzt.
- **Konsolenausgabe:** Das Konsolenfenster protokolliert jede Anfrage mit ihrer Adresse (Uvicorn-Zugriffsprotokoll), auch Suchtexte wie `/transactions?q=Name`. Konsolenausgaben nie ungeprüft in Issues oder Nachrichten einfügen, solche Zeilen vorher entfernen.

**Nicht geschützt:** Es gibt keine Anmeldung. Wer an Ihrem Rechner angemeldet ist und `http://127.0.0.1:8000` öffnet, sieht und ändert alle Daten, solange KASSIVO läuft. Die Datenbank ist nicht verschlüsselt. Schützen Sie den Rechner (Benutzerkonto, Bildschirmsperre, Festplattenverschlüsselung), betreiben Sie KASSIVO nicht auf einem gemeinsam genutzten Rechner oder Server und legen Sie Datenbank und Sicherungen nicht in einen Cloud-Ordner.

Echte Bankdateien und Datenbanken gehören nie ins Git-Repository, in Issues oder Pull Requests. Die `.gitignore` schließt unter anderem `data/`, `backups/`, `*.db`, `*.zip`, `*.xml` und CAMT/MT940-Dateien aus.

## Tests

```
pytest
```

Die Tests verwenden nur erfundene Daten. Dazu gehören Prüfungen, dass die Hilfeseiten des Wikis und dieses README zu Beschriftungen, Meldungen und Grenzen im Code passen.

## Projektstruktur

```
src/kassivo/
  __main__.py          Startpunkt (python -m kassivo)
  backup.py            Sicherung der Datenbank
  camt.py              Einlesen von CAMT-Dateien (camt.052.001.08)
  categories.py        Kategorien und Regeln
  db.py                Datenbankschema und Verbindung
  dues.py              Beitragsprüfung, Verteilung und Vorschläge
  fees.py              Beiträge mit Gültigkeitsmonat
  forecast.py          Prognose
  iban.py              IBAN-Normalisierung
  ingest.py            Import und Duplikaterkennung
  matching.py          Zuordnung von Umsätzen zu Mitgliedern
  members.py           Mitglieder und Mitgliedsarten
  money.py             Geldbeträge (ganze Cent)
  months.py            Monatsrechnung
  reports.py           Jahresreport
  settings.py          Ort der Datenbank (Standardordner, KASSIVO_DB, Cloud-Warnung)
  web/                 Weboberfläche
    app.py             App-Fabrik, Host- und Origin-Prüfung, Sicherheits-Header, Fehlerseiten
    common.py          Gemeinsame Helfer: Vorlagen, DB je Anfrage, Eingabeprüfung
    backup_routes.py   Backup-Seite
    categories_routes.py  Kategorien und Regeln
    dues_routes.py     Beitragsübersicht, Zahlungshistorie, Prognose
    import_routes.py   Upload und Import
    members_routes.py  Mitglieder
    reports_routes.py  Jahresreport und Diagrammdaten
    settings_routes.py Beiträge und Prüfungsstart
    transactions_routes.py  Buchungen, Unklar, Verteilung
    wiki_routes.py     Wiki im Programm (feste Themenliste, nur lesend)
    templates/         HTML-Vorlagen (Jinja2)
      wiki/            Vorlagen der Wiki-Themen
    static/            style.css, reports.js
      vendor/          chart.umd.min.js (Chart.js, lokal), LICENSE.chartjs.txt, README.md
tests/                 pytest, nur erfundene Daten
docs/                  Entwurf und Plan
```

## Architektur in Kürze

- **Schichten:** Fachmodule (`dues.py`, `matching.py`, `ingest.py` und andere) enthalten die Regeln und kennen kein Web. Die Routen in `web/` prüfen Eingaben, rufen die Fachmodule auf und reichen das Ergebnis an die Vorlagen. Die Vorlagen stellen nur dar.
- **Datenbank:** SQLite in einer Datei. Jede Anfrage bekommt ihre eigene Verbindung, die am Ende geschlossen wird.
- **Geld** sind ganze Cent (Integer), nie Fließkommazahlen. Beträge werden nur zur Anzeige als Euro formatiert.
- **Buchungen** ändert KASSIVO nie. Es setzt nur Mitglied und Kategorie und legt Verteilungen an.
- **Verteilungen und Vorschläge:** Eine Zahlung hat null oder mehr Verteilungszeilen (Monat und Betrag), jede im Zustand `proposed` oder `confirmed`. Bezahlt ist ein Monat nur durch bestätigte Zeilen.
- **Beiträge und Mitgliedsarten** haben einen Gültigkeitsmonat. Das Soll je Monat wird beim Anzeigen aus ihnen berechnet und nicht gespeichert, deshalb bleiben alte Monate stabil, solange der Gültigkeitsmonat einer Änderung in der Zukunft liegt.
- **Wiki:** Eine feste Tabelle in `wiki_routes.py` ordnet jedem Thema eine Vorlage zu. Aus der Anfrage wird nie ein Dateipfad gebaut.

## Entwicklung

Bis zum ersten Release (1.0.0) wurde auf `feature/kassivo-v1` gearbeitet. Danach gilt:

- Nur noch Branches `feat/<thema>` (neue Funktion) und `fix/<thema>` (Fehlerbehebung), jeweils von `main`. Keine Commits direkt auf `main`.
- Änderungen kommen über Pull Requests in `main`. Jede Änderung hat ein GitHub-Issue, auf das der Pull Request verweist.
- Versionierung nach SemVer: `X.0.0` Major-Release, `0.X.0` Minor-Release (neue Funktion), `0.0.X` Bugfix. Jedes Release bekommt einen Tag `vX.Y.Z` und ein GitHub-Release mit Notizen. Packages (GitHub Packages) sind vorerst nicht vorgesehen.
- Commit-Nachrichten sind deutsch und beginnen mit einer Art: `feat:`, `fix:`, `docs:` oder `chore:`.
- Tests zuerst: Erst der Test, dann der Code. Vor einem Pull Request muss `pytest` grün sein.
- Echte Bankdaten kommen in keinen Commit, keinen Pull Request, kein Issue und kein Release. Beispiele nutzen erfundene Namen und offensichtlich falsche IBANs wie `XX00 FAKE 0000 0000 01`.

Der Entwurf des Programms steht in `docs/superpowers/specs/2026-11-24-kassivo-design.md`, der Umsetzungsplan in `docs/superpowers/plans/2026-11-24-kassivo-v1.md`.

## Bekannte Einschränkungen

- Nur Datei-Import im Format camt.052.001.08. Keine Direktabfrage der Bank (FinTS, finAPI), kein C24-Adapter.
- Ein Nutzer, ein Rechner, keine Anmeldung.
- Einträge mit 0,00 € brechen den Import der ganzen Datei ab. Mehr als ein Bericht pro Datei geht nicht.
- Eine gelernte IBAN lässt sich nicht vergessen. Eine einmal mehrdeutig gewordene IBAN bleibt es, und eine falsch gemerkte IBAN wird nie für das richtige Mitglied neu gelernt. "— keins —" setzt eine Zahlung nur vorübergehend auf Unklar: "Unklare automatisch zuordnen" ordnet sie über die gemerkte IBAN wieder zu.
- Duplikaterkennung: Eine Buchung mit derselben Bankreferenz, aber anderem Betrag oder Datum, wird still als Duplikat verworfen. Eine zweite gleiche Zahlung ohne Bankreferenz am selben Tag wird verworfen, wenn sie in einer späteren Datei kommt (die laufende Nummer beginnt je Datei neu). Der Import speichert die Buchungen vor Regeln und automatischer Zuordnung: Scheitern diese, bleiben die Buchungen unkategorisiert und unzugeordnet, ein erneuter Import zählt sie als Duplikate. Abhilfe: "Regeln anwenden" und "Unklare automatisch zuordnen". Bei einem Betrag von 0,00 gilt: Lässt man den Tag im Export weg, fehlen seine Zahlungen für immer.
- Barzahlungen und eigene Ausgaben lassen sich nicht von Hand buchen. Der Monat bleibt überfällig, es gibt in 1.0.0 keine Abhilfe.
- Zwei offene Vorschläge desselben Mitglieds können denselben Monat treffen (nur der Hinweis "Monat bereits bezahlt"). Ein Austritt vor dem Eintritt wird akzeptiert. Die Textfelder der Mitglieder haben keine Längenbegrenzung.
- Die Prognose betrachtet nur Mitgliedsbeiträge, keine wiederkehrenden Ausgaben.
- Eine Datenbank ist eine Kasse (keine Mehrmandanten).
- Buchungen, Mitglieder, Kategorien und Regeln lassen sich nicht löschen. Buchungen lassen sich nicht von Hand anlegen. Korrekturen laufen über Zuordnung und Verteilung.
- Kein Knopf zum Wiederherstellen einer Sicherung, keine automatische Sicherung.
- Die Diagramme im Report brauchen JavaScript im Browser. Die Tabellen gehen immer.
- Adresse und Port sind fest (`127.0.0.1:8000`).
- Python ab 3.10 erlaubt, getestet wird nur mit 3.15.

Die ausführliche Fassung steht im Wiki unter "Bekannte Einschränkungen" (`/wiki/grenzen`).

## Drittanbieter

Chart.js 4.4.1 (MIT-Lizenz), unverändert und lokal eingebunden unter `src/kassivo/web/static/vendor/`. Der Lizenztext liegt in `src/kassivo/web/static/vendor/LICENSE.chartjs.txt`, Herkunft und Prüfsumme stehen in `src/kassivo/web/static/vendor/README.md`. Die Seite lädt nur diese lokale Kopie.

## Lizenz

Siehe [LICENSE](LICENSE).
