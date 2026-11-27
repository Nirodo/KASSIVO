# KASSIVO
KASSIVO ist die moderne Lösung zur einfachen und übersichtlichen Verwaltung von Gruppenkassen. Ob Verein, Mannschaft, Freundeskreis oder gemeinsame Projekte – mit KASSIVO lassen sich Einzahlungen, Ausgaben und Kassenbestände zentral verwalten. Transparent, unkompliziert und jederzeit im Blick.

## Überblick

KASSIVO 1.2.0 ist ein lokales Kassenprogramm für eine Person an einem Rechner. Es verwaltet Mitglieder und ihre monatlichen Beiträge, liest Kontoumsätze aus Exportdateien der Bank ein, ordnet sie Mitgliedern und Kategorien zu und zeigt, wer seinen Beitrag bezahlt hat. Die Oberfläche läuft als Webseite im Browser, aber nur auf dem eigenen Rechner (`http://127.0.0.1:8000`). Die Daten liegen in einer SQLite-Datei auf dem eigenen Rechner.

Eine ausführliche Anleitung steckt im Programm: das Fragezeichen oben rechts oder der Bereich "Einstellungen", Punkt "Wiki" (`/wiki`), erklärt jede Funktion mit Schritten und Beispielen. Das Wiki ist nur zum Lesen und braucht kein Internet.

Die Oberfläche hat fünf Bereiche (Übersicht, Mitglieder, Zahlungen, Auswertung, Einstellungen) in der blauen Leiste oben; links steht die Seitenleiste des gewählten Bereichs. Auf dem Handy öffnet der Knopf "Menü" die Bereiche und den Hilfe-Link. Die Seiten nutzen die volle Fensterbreite.

Kontoumsätze kommen per Datei-Import (CAMT V8, Sparkasse). Eine direkte Bankabfrage (FinTS, finAPI) und ein C24-Adapter sind nicht gebaut.

## Funktionen im Detail

- **Mitglieder** (Wiki: "Mitglieder"): Anlegen, Stammdaten ändern, Mitgliedsart Aktiv oder Passiv mit Gültigkeitsmonat wechseln, Eintritt und Austritt, IBAN des Mitglieds. Neue Mitglieder zahlen ab dem Monat nach dem Eintritt, der Austrittsmonat ist noch voll zu zahlen.
- **Beiträge und Prüfungsstart** (Wiki: "Beiträge und Prüfungsstart"): Beitrag je Mitgliedsart mit Gültig-ab-Monat. Eine Änderung wirkt nur ab diesem Monat. Der Prüfungsstart legt fest, ab wann Beiträge geprüft werden.
- **Import** (Wiki: "Kontoexport importieren"): ZIP mit Tagesdateien oder einzelne XML-Datei im Format `camt.052.001.08`. Bereits importierte Umsätze erkennt KASSIVO als Duplikate, ein mehrfacher Import ist sicher.
- **Zuordnung** (Wiki: "Zahlungen zuordnen"): Zahlungseingänge ordnet KASSIVO über die Absender-IBAN automatisch Mitgliedern zu, lernt dabei aus Ihren Zuordnungen ("Absender merken") und sammelt alles Unklare in der Liste "Unklar". Zahlungen, die kein Mitglied brauchen (zum Beispiel Zinsen der Bank), verlassen "Unklar", sobald Sie ihnen eine Kategorie außer "Beiträge" geben ("Kein Mitglied nötig", "Als erledigt kategorisieren"), oder eine Regel kategorisiert sie schon beim Import.
- **Verteilung** (Wiki: "Verteilung auf Beitragsmonate"): Jede Zahlung wird auf Beitragsmonate verteilt. Eindeutige Zahlungen verteilt KASSIVO selbst (Seite "Automatisch verteilt", mit "Rückgängig"), nur echte Ausnahmen bleiben ein Vorschlag, den Sie prüfen und bestätigen.
- **Kategorien und Regeln** (Wiki: "Kategorien und Regeln"): Regeln nach Zweck, Name oder IBAN der Gegenpartei mit Priorität und Betragsgrenzen ordnen Buchungen Kategorien zu.
- **Beitragsübersicht** (Wiki: "Beitragsübersicht"): Raster Mitglieder mal Monate mit den Status Bezahlt, Teilweise, Offen, Überfällig, dazu die Zahlungshistorie je Mitglied.
- **Prognose** (Wiki: "Prognose"): Offene Mitgliedsbeiträge bis zu einem Stichtag, getrennt in überfällig und noch fällig.
- **Jahresreport** (Wiki: "Jahresreport"): Einnahmen, Ausgaben, Saldo, Diagramme und Tabellen je Kategorie und Monat.
- **Backup** (Wiki: "Sicherung und Wiederherstellung"): Sicherung der Datenbank auf Knopfdruck.
- **Hilfe**: Wiki-Themen "Erste Schritte", "Begriffe", "Datenschutz und Sicherheit", "Fehlerbehebung (FAQ)" und "Bekannte Einschränkungen".

## Voraussetzungen

Python ab 3.10 (`requires-python = ">=3.10"`). Entwickelt wird mit Python 3.15 (siehe `.venv`). Automatisch getestet wird mit Python 3.10, 3.12 und 3.13 unter Windows und Linux sowie mit der jeweils neuesten Python-Version unter Linux. Abhängigkeiten (werden von `pip` installiert): FastAPI, Jinja2, python-multipart, Uvicorn. Ein aktueller Browser.

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

`pip install -e ".[dev]"` installiert KASSIVO bearbeitbar zusammen mit den Entwicklungswerkzeugen (pytest, httpx). Wer nur anwenden will, nimmt `pip install .` (ohne Werkzeuge, nicht bearbeitbar). Die Update-Funktion braucht allerdings die bearbeitbare Installation (`pip install -e .`).

### Update

Ab Version 1.3.0 kann KASSIVO selbst nach einer neuen Version suchen (Seite `/settings/update`, Link auf der Seite Einstellungen, Wiki-Thema „Update“). Die Update-Prüfung ist standardmäßig aus. Eingeschaltet fragt KASSIVO nur auf Knopfdruck die öffentliche Releases-API von GitHub ab, ohne Anmeldung, Token oder Daten aus KASSIVO, mit Zeitlimit und Zertifikatsprüfung. "Update herunterladen" lädt `kassivo-X.Y.Z.zip` und `SHA256SUMS` aus dem Release, prüft Prüfsumme und Inhalt (keine Pfade außerhalb des Versionsordners, keine Links, keine Datenbankdateien, Größengrenzen) und legt das Paket nach `<DB-Ordner>/updates`.

Installiert wird nicht im laufenden Programm: KASSIVO beenden, `update.bat` im Projektordner starten und mit `j` bestätigen (Linux/macOS: `.venv/bin/python -I -m kassivo.update apply`). Das Skript sichert zuerst die Datenbank nach `<DB-Ordner>/backups`, tauscht dann die Programmdateien aus, ruft `pip install -e .` nur bei geänderten Abhängigkeiten auf und prüft, ob die neue Version startet. Bei einem Fehler setzt es den alten Stand zurück. Datenbank, `.venv`, `.git` und eigene Dateien bleiben unberührt; der alte Programmstand bleibt in `.update/` im Projektordner. KASSIVO startet danach nicht von selbst neu. Von 1.2.0 auf 1.3.0 wird noch von Hand aktualisiert. Wer den Projektordner mit Git verwaltet, kann weiterhin mit `git pull` aktualisieren.

Die Pakete baut der Workflow `.github/workflows/release.yml` bei jedem Tag `vX.Y.Z`: Tests, dann Archiv (`git archive`), Wheel und `SHA256SUMS`, angehängt an das GitHub-Release.

## Start

Unter Windows genügt ein Doppelklick auf `start.bat` im Projektordner. Die Datei startet den Server mit der virtuellen Umgebung `.venv` und öffnet den Browser auf http://127.0.0.1:8000. Das Fenster bleibt offen, solange KASSIVO läuft. Zum Beenden schließen Sie das Fenster oder drücken `Strg+C`. Aus einer Eingabeaufforderung oder PowerShell rufen Sie sie mit `.\start.bat` auf; ohne `.\` findet `cmd` den Namen nicht, weil `start` dort ein eingebauter Befehl ist. Mit `.\start.bat nobrowser` startet nur der Server. Fehlt `.venv`, zeigt die Datei die nötigen Installationsschritte.

Von Hand starten Sie so:

```
python -m kassivo
```

Danach ist KASSIVO unter http://127.0.0.1:8000 im Browser erreichbar. Die Adresse ohne Pfad leitet auf das Beitragsraster (Bereich "Auswertung") weiter. Beim Start schreibt KASSIVO in die Konsole:

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

1. **Prüfungsstart festlegen** (Einstellungen): ab welchem Monat Beiträge geprüft werden. Solange er fehlt, zeigen Beitragsübersicht und Import einen Hinweis. Am besten den ersten vollen Monat wählen, den der Kontoexport abdeckt: Beginnt der Export mitten im Monat, fehlen frühere Zahlungen dieses Monats und er erschiene als überfällig. Zahlungen, die vor dem Prüfungsstart-Monat gebucht sind (zum Beispiel am 20.03. bei Prüfungsstart 2026-04), bleiben dem Mitglied zugeordnet, werden aber nicht auf Monate verteilt und erscheinen nicht als Vorschlag. Wird der Prüfungsstart später geändert, berechnet KASSIVO alle automatischen Verteilungen und Vorschläge neu; von Ihnen bestätigte Verteilungen bleiben unverändert.
2. **Mitglieder und Beiträge anlegen** (Mitglieder, Einstellungen).
3. **Kontoexport importieren** (Import).
4. **"Unklar" zuordnen**: jede Zahlung einem Mitglied zuordnen, mit "Absender merken". Weitere unklare Zahlungen derselben IBAN werden dann gleich mit zugeordnet. Der Knopf "Unklare automatisch zuordnen" wendet Gelerntes erneut auf die ganze Liste an, auch auf Zahlungen, die Sie mit "— keins —" auf Unklar zurückgesetzt haben. Zahlt eine IBAN für mehrere Mitglieder (z. B. ein Elternteil für zwei Kinder), wird sie nicht automatisch zugeordnet.
5. **Ausnahmen prüfen** ("Offene Vorschläge", Link in Buchungen, Unklar und Beitragsübersicht): Eindeutige Zahlungen verteilt KASSIVO selbst ("Automatisch verteilt"). Nur Ausnahmen, zum Beispiel 20,00 € bei 17,00 € Beitrag, warten mit einem Grund auf Ihre Bestätigung. Ein Knopf dort wendet die Regeln erneut auf alle offenen Vorschläge an (etwa aus einer älteren Version).
6. **Beitragsübersicht, Prognose und Report ansehen.**

Jede automatische Verteilung steht auf der Seite "Automatisch verteilt" (`/auto`) und lässt sich dort mit "Rückgängig" in einen Vorschlag zurückverwandeln, den Sie auf der Seite "Verteilung" ändern und bestätigen.

## Import-Format und Ablauf

Unterstützt ist genau ein Format: `camt.052.001.08`, im Online-Banking "XML CAMT V8, gebuchte Umsätze" (Sparkasse). Eine ZIP-Datei enthält dabei eine XML-Datei je Buchungstag. Andere Formate (CAMT V2, MT940, CSV) lehnt KASSIVO ab.

1. Im Online-Banking den Export "XML CAMT V8, gebuchte Umsätze" erzeugen.
2. In KASSIVO die Seite `/import` öffnen und die ZIP- oder XML-Datei hochladen. Die ZIP-Datei wird nicht entpackt, KASSIVO liest sie direkt.
3. KASSIVO erkennt bereits importierte Umsätze als Duplikate (Bankreferenz, sonst ein Fingerabdruck aus Datum, Betrag, Gegenpartei-IBAN und Zweck) und legt sie nicht doppelt an. Hinweise und Warnungen zur Datei (zum Beispiel "Saldo stimmt nicht") erscheinen nach dem Import.
4. Auf neue Buchungen wendet KASSIVO die Kategorien-Regeln und die automatische Zuordnung an. Die Ergebnisseite zählt Neu, Duplikate, kategorisiert, zugeordnet und für die neuen Zahlungen "automatisch verteilt" und "zur Prüfung".
5. Umsätze, die keinem Mitglied sicher zugeordnet werden können, erscheinen in der Liste "Unklar" und können dort von Hand zugeordnet werden.

Grenzen: Eine Datei darf höchstens 100 MB groß sein. In einer ZIP-Datei liest KASSIVO höchstens 5000 XML-Dateien, jede höchstens 20 MB, zusammen höchstens 200 MB. Eine XML-Datei darf genau einen Bericht enthalten. Findet KASSIVO in irgendeiner Datei einen Fehler, bricht der ganze Import ab und legt nichts an (zum Beispiel bei falschem Format oder bei einem ungültigen Betrag). Einträge mit 0,00 € sind kein Fehler: KASSIVO überspringt sie, speichert sie nicht und meldet sie als Warnung auf der Ergebnisseite (eine Warnung je Datei, mit maskierter IBAN und Datum). Einträge ohne Status überspringt KASSIVO und meldet sie als Warnung; als Zeichenkodierung sind nur UTF-8, US-ASCII und ISO-8859-1 erlaubt (UTF-16 wird abgelehnt). Eine Buchung mit bereits bekannter Bankreferenz, aber anderem Betrag oder Datum wird nicht angelegt und als Warnung gemeldet. Die Duplikaterkennung hat bekannte Grenzen (siehe "Bekannte Einschränkungen"). Importiert werden nur Einträge mit dem Status "gebucht"; Sammelbuchungen kommen ohne Gegenpartei herein und werden nie automatisch zugeordnet.

## Regeln im Überblick

**Beitragsregeln**

- Der Beitrag ist zu Monatsbeginn fällig und darf den ganzen Monat über eingehen. Mit dem Monatsende gilt ein nicht bezahlter Beitrag als überfällig.
- Ein neues Mitglied zahlt ab dem 1. des Monats nach dem Eintritt (Feld "Zahlt ab Monat", leer lassen für diese Regel).
- Tritt ein Mitglied mitten im Monat aus, ist der Austrittsmonat noch voll zu zahlen.
- Zu wenig gezahlt: Der Monat gilt als offen oder teilbezahlt.
- Zu viel gezahlt: Der Mehrbetrag gleicht versäumte Monate aus und wird dann auf Folgemonate angerechnet. Passt er zu ganzen Monatsbeiträgen, verteilt KASSIVO ihn selbst, sonst bestätigen Sie die Verteilung.
- Jedes Mitglied zahlt für sich. Zahlt eine andere Person, ordnen Sie die Überweisung dem Mitglied von Hand zu.
- Beträge ändern sich nur ab ihrem Gültigkeitsmonat. Frühere Monate bleiben, wie sie waren.

**Zuordnung**

KASSIVO ordnet einen neuen Zahlungseingang automatisch zu, wenn die Absender-IBAN genau einem Mitglied gehört, entweder gelernt ("Absender merken") oder im Mitgliedsdatensatz. Mehrdeutige IBANs (mehrere Mitglieder) und Zahlungen nach dem Austrittsdatum des Mitglieds bleiben in "Unklar". Eine gelernte IBAN lässt sich in dieser Version nicht vergessen. Beim Umhängen auf ein anderes Mitglied sperren Verteilungen, die Sie selbst bestätigt haben, die Änderung, bis Sie die Verteilung aufheben. Automatische Verteilungen rechnet KASSIVO für das neue Mitglied neu.

**Verteilung**

Nur bestätigte Verteilungen zählen als bezahlt. Grundsatz: Was KASSIVO selbst berechnet (automatisch verteilt oder vorgeschlagen), ist abgeleitet und wird neu berechnet, wenn sich Prüfungsstart, Zuordnung, Beitrag, Mitgliedsart oder Austritt ändern oder wenn Sie bei einer anderen Zahlung desselben Mitglieds entscheiden. Automatische Verteilungen bezahlen keinen Monat doppelt. Was Sie entschieden haben ("Verteilung bestätigen", "Rückgängig", "Verteilung aufheben", "Neu vorschlagen"), ist manuell und wird nie automatisch geändert. KASSIVO verteilt so:

1. Zahlungen vor dem Prüfungsstart werden nicht verteilt (sie bleiben dem Mitglied zugeordnet).
2. Ist ein früherer Monat angefangen (teilweise bezahlt) und übersteigt die Zahlung den Rest nicht, vervollständigt sie den ältesten solchen Monat.
3. Sonst zuerst der Buchungsmonat, falls dort etwas fehlt. Ein Rest geht an die ältesten offenen Monate (Rückstand), danach an folgende Monate (Vorauszahlung, höchstens 36 Monate voraus). Ist der Buchungsmonat schon bezahlt, beginnt KASSIVO beim ältesten offenen Monat.
4. Ist alles verteilt, sind es höchstens 12 Monate und bekommt jeder Monat seinen vollen Fehlbetrag, oder ist es eine einzelne Teilzahlung, bestätigt KASSIVO selbst (automatisch verteilt). Sonst bleibt ein Vorschlag mit Grund zur Prüfung (zum Beispiel "Mehr als 12 Monate im Voraus").

Beispiele bei 17,00 € Monatsbeitrag und Prüfungsstart 2026-01:

| Zahlung | Ergebnis |
|---|---|
| 17,00 € am 15.01.2026 | Januar, automatisch verteilt |
| 2 × 17,00 € im Januar 2026 | Januar und Februar (Vorauszahlung), automatisch verteilt |
| 34,00 € am 15.01.2026 | Januar und Februar je 17,00 €, automatisch verteilt |
| 204,00 € am 15.01.2026 | Januar bis Dezember je 17,00 €, automatisch verteilt |
| 34,00 € am 03.03.2026, Januar bezahlt, Februar offen | März und Februar (Rückstand), automatisch verteilt |
| 10,00 € am 15.01.2026 | Januar 10,00 €, automatisch verteilt; "Teilweise" |
| danach 7,00 € am 03.02.2026 | Januar 7,00 € (vervollständigt den Januar), automatisch verteilt |
| 221,00 € am 15.01.2026 (13 Monate) | Vorschlag zur Prüfung ("Mehr als 12 Monate im Voraus") |
| 20,00 € am 15.01.2026 | Januar 17,00 € und Februar 3,00 €, Vorschlag zur Prüfung |
| 17,00 € am 30.12.2025 | nicht verteilt (vor dem Prüfungsstart) |
| 17,00 € am 30.12.2026, Dezember schon bezahlt | Januar 2027 (Vorauszahlung), automatisch verteilt |

**Kategorien**

Eine Buchung hat höchstens eine Kategorie. Regeln prüfen, ob ein Feld (Verwendungszweck, Name oder IBAN der Gegenpartei) einen Text enthält, optional mit Betragsgrenzen. Die kleinere Prioritätszahl gewinnt. Beim Import wirken Regeln auf neue Buchungen; "Regeln anwenden" wirkt auf vorhandene und überschreibt gesetzte Kategorien nur mit ausdrücklicher Bestätigung. Der Name "Ohne Kategorie" ist reserviert.

## Berichte

- **Beitragsübersicht** (`/dues`): Raster Mitglieder mal zwölf Monate mit Bezahlt, Teilweise, Offen, Überfällig und "—" (kein Beitrag fällig). Monate mit unbestätigtem Vorschlag tragen die Markierung "Vorschlag"; darüber steht, wie viele Zahlungen auf Ihre Prüfung warten. Ein Klick auf den Namen öffnet die Zahlungshistorie.
- **Prognose** (`/forecast`): offene Beiträge vom Prüfungsstart bis zu einem gewählten Datum (es zählt der ganze Monat des Datums: mit 10.03. rechnet die Prognose den gesamten März mit), höchstens 50 Jahre in der Zukunft. Nur Mitgliedsbeiträge zählen, keine wiederkehrenden Ausgaben.
- **Jahresreport** (`/reports`): Einnahmen, Ausgaben, Saldo, Diagramme (Chart.js) und Tabellen. Die Beiträge je Mitglied rechnet der Report nach Beitragsmonat, nicht nach Buchungsdatum.
- Die Liste "Buchungen" zeigt 100 Buchungen je Seite und lässt sich nach Jahr, Kategorie, Art und Suchtext filtern.

## Backup und Wiederherstellung

Sicherung: Seite `/backup` (Bereich "Einstellungen", Punkt "Backup") öffnen und "Sicherung erstellen" wählen. Die Sicherung ist eine vollständige Kopie der Datenbank mit dem Namen `kassivo-JJJJMMTT-HHMMSS.db` in `<DB-Ordner>/backups`. Den genauen Pfad zeigt KASSIVO beim Start an. Die Seite listet die vorhandenen Sicherungen. KASSIVO löscht nie von selbst. Auf derselben Seite räumt "Alte Sicherungen aufräumen" auf: Sie behalten die neuesten 1 bis 1000 Sicherungen (voreingestellt 10) und bestätigen per Haken, dass ältere gelöscht werden. Die neueste bleibt immer, andere Dateien im Ordner bleiben unberührt. Das Löschen ist nicht rückgängig zu machen. Die Reihenfolge richtet sich nach dem Zeitstempel im Dateinamen (Ortszeit); bei der Umstellung auf die Winterzeit kann ein älter wirkender Name der neuere sein, dann einige Sicherungen mehr behalten. Eine Sicherung wird vor der Freigabe vollständig auf den Datenträger geschrieben; nach dem Erstellen lädt die Seite neu, sodass ein Neuladen keine zweite Sicherung anlegt.

Datenbankformat: Die Datenbank trägt eine Schema-Version (angezeigt auf der Seite `/backup`). Muss eine neue KASSIVO-Version das Format ändern, legt sie vorher automatisch eine Sicherung `kassivo-JJJJMMTT-HHMMSS.db` in `<DB-Ordner>/backups` an. Sonst sichert KASSIVO nicht automatisch. Eine Datenbank aus einer neueren KASSIVO-Version öffnet KASSIVO nicht: Es meldet das beim Start und beendet sich, die Datei bleibt unverändert. Versionen bis 1.1.1 prüfen die Schema-Version nicht. Kehren Sie deshalb nach einem Update nicht mit derselben Datenbank zu einer älteren KASSIVO-Version zurück, und legen Sie vor jedem Update eine Sicherung an.

Wiederherstellung (von Hand):

1. KASSIVO beenden.
2. Die aktuelle `kassivo.db` im DB-Ordner an einen anderen Ort kopieren oder umbenennen, damit sie nicht verloren geht.
3. Eine eventuell vorhandene Datei `kassivo.db-journal` neben der Datenbank löschen, damit kein altes Journal auf die wiederhergestellte Kopie angewendet wird.
4. Die gewünschte Sicherung aus `<DB-Ordner>/backups` als `kassivo.db` in den DB-Ordner kopieren.
5. KASSIVO starten und die Daten prüfen.

Sicherungen enthalten personenbezogene Daten. Sie gehören nicht in einen Cloud-Ordner.

## Datenschutz und Sicherheit

KASSIVO ist für den Betrieb durch eine Person auf dem eigenen Rechner gebaut.

- Alles bleibt lokal. KASSIVO hat keine Verbindung zur Bank, kennt keine Zugangsdaten oder PINs und schickt nichts ins Internet. Einzige Ausnahme ist die Update-Prüfung: standardmäßig aus, eingeschaltet nur auf Knopfdruck und ohne Daten aus KASSIVO (siehe Update). Es liest nur Dateien, die Sie selbst hochladen. Auch Chart.js liegt lokal im Programm.
- Der Server lauscht nur auf 127.0.0.1, andere Rechner im Netz erreichen ihn nicht.
- **Host-Prüfung:** KASSIVO beantwortet nur Anfragen an `127.0.0.1` oder `localhost` und weist andere Host-Namen mit "Ungültiger Host." ab. Das schützt davor, dass eine fremde Webseite den Browser dazu bringt, KASSIVO unter einem anderen Namen anzusprechen.
- **Origin-Prüfung:** Anfragen, die etwas ändern (POST und ähnliche), lehnt KASSIVO ab, wenn der Origin-Header auf eine andere Herkunft zeigt. Andere offene Webseiten können so nichts in KASSIVO ändern.
- **Content-Security-Policy:** Die Seiten laden nur eigene Dateien (`default-src 'self'`) und lassen sich nicht einbetten (`frame-ancestors 'none'`). Dazu kommen `X-Content-Type-Options: nosniff` und `Referrer-Policy: same-origin`. Es gibt kein Inline-JavaScript.
- **Uploads:** Größengrenzen (siehe Import), Dateityp-Prüfung am Inhalt, ZIP-Dateien werden nie entpackt, XML mit DOCTYPE oder ENTITY wird abgelehnt. Der Dateiname des Browsers wird nie verwendet.
- IBANs erscheinen in Listen und Meldungen gekürzt.
- **Sec-Fetch-Site:** Zusätzlich zur Origin-Prüfung lehnt KASSIVO ändernde Anfragen ab, wenn der Browser mit `Sec-Fetch-Site` meldet, dass sie von einer anderen Seite stammen (`cross-site`, `same-site`). Jeder andere Wert außer `same-origin` und `none` wird ebenfalls abgelehnt, einschließlich unbekannter Werte, falls ein künftiger Browser neue Werte sendet.
- **Konsolenausgabe:** Das Konsolenfenster protokolliert Anfragen (Uvicorn-Zugriffsprotokoll) ohne Suchtexte: aus `/transactions?q=Name` wird `/transactions`. Fehlermeldungen können trotzdem Namen oder Beträge enthalten, Konsolenausgaben deshalb nie ungeprüft in Issues oder Nachrichten einfügen.
- Besteht eine Regel nur aus einer vollständigen IBAN, zeigt die Regelliste sie gekürzt an. Gespeichert bleibt der volle Text.

**Nicht geschützt:** Es gibt keine Anmeldung. Wer an Ihrem Rechner angemeldet ist und `http://127.0.0.1:8000` öffnet, sieht und ändert alle Daten, solange KASSIVO läuft. Die Datenbank ist nicht verschlüsselt. Schützen Sie den Rechner (Benutzerkonto, Bildschirmsperre, Festplattenverschlüsselung), betreiben Sie KASSIVO nicht auf einem gemeinsam genutzten Rechner oder Server und legen Sie Datenbank und Sicherungen nicht in einen Cloud-Ordner.

Echte Bankdateien und Datenbanken gehören nie ins Git-Repository, in Issues oder Pull Requests. Die `.gitignore` schließt unter anderem `data/`, `backups/`, `*.db`, `*.zip`, `*.xml` und CAMT/MT940-Dateien aus.

## Tests

```
pytest
```

Die Tests laufen zusätzlich bei jedem Pull Request automatisch auf GitHub. Die Tests verwenden nur erfundene Daten. Dazu gehören Prüfungen, dass die Hilfeseiten des Wikis und dieses README zu Beschriftungen, Meldungen und Grenzen im Code passen.

## Projektstruktur

```
src/kassivo/
  __main__.py          Startpunkt (python -m kassivo)
  backup.py            Sicherung der Datenbank
  camt.py              Einlesen von CAMT-Dateien (camt.052.001.08)
  categories.py        Kategorien und Regeln
  db.py                Datenbankschema und Verbindung
  dues.py              Beitragsprüfung, automatische Verteilung und Vorschläge
  fees.py              Beiträge mit Gültigkeitsmonat
  forecast.py          Prognose
  iban.py              IBAN-Normalisierung
  ingest.py            Import und Duplikaterkennung
  logconfig.py         Konsolenprotokoll ohne Suchtexte (Filter für das Uvicorn-Zugriffsprotokoll)
  matching.py          Zuordnung von Umsätzen zu Mitgliedern
  members.py           Mitglieder und Mitgliedsarten
  money.py             Geldbeträge (ganze Cent)
  months.py            Monatsrechnung
  reports.py           Jahresreport
  settings.py          Ort der Datenbank (Standardordner, KASSIVO_DB, Cloud-Warnung)
  update.py            Update: Release prüfen, Paket laden und prüfen, installieren (update.bat)
  web/                 Weboberfläche
    app.py             App-Fabrik, Host-, Origin- und Sec-Fetch-Site-Prüfung, Sicherheits-Header, Fehlerseiten
    auto_routes.py     Automatisch verteilt, Rückgängig, offene Vorschläge neu verteilen
    common.py          Gemeinsame Helfer: Vorlagen, DB je Anfrage, Eingabeprüfung
    backup_routes.py   Backup-Seite
    categories_routes.py  Kategorien und Regeln
    dues_routes.py     Beitragsübersicht, Zahlungshistorie, Prognose
    import_routes.py   Upload und Import
    members_routes.py  Mitglieder
    reports_routes.py  Jahresreport und Diagrammdaten
    settings_routes.py Beiträge und Prüfungsstart
    update_routes.py   Update-Seite: Opt-in, nach Updates suchen, Paket herunterladen
    transactions_routes.py  Buchungen, Unklar, Verteilung
    wiki_routes.py     Wiki im Programm (feste Themenliste, nur lesend)
    templates/         HTML-Vorlagen (Jinja2)
      wiki/            Vorlagen der Wiki-Themen
    static/            style.css, reports.js, reports-lib.js
      vendor/          chart.umd.min.js (Chart.js, lokal), LICENSE.chartjs.txt, README.md
tests/                 pytest, nur erfundene Daten
docs/                  Entwurf und Plan
```

## Architektur in Kürze

- **Schichten:** Fachmodule (`dues.py`, `matching.py`, `ingest.py` und andere) enthalten die Regeln und kennen kein Web. Die Routen in `web/` prüfen Eingaben, rufen die Fachmodule auf und reichen das Ergebnis an die Vorlagen. Die Vorlagen stellen nur dar.
- **Datenbank:** SQLite in einer Datei. Jede Anfrage bekommt ihre eigene Verbindung, die am Ende geschlossen wird.
- **Geld** sind ganze Cent (Integer), nie Fließkommazahlen. Beträge werden nur zur Anzeige als Euro formatiert.
- **Buchungen** ändert KASSIVO nie. Es setzt nur Mitglied und Kategorie und legt Verteilungen an.
- **Verteilungen und Vorschläge:** Eine Zahlung hat null oder mehr Verteilungszeilen (Monat und Betrag), jede im Zustand `proposed` oder `confirmed` und mit der Herkunft `auto` (KASSIVO) oder `manual` (Ihre Entscheidung). Bezahlt ist ein Monat nur durch bestätigte Zeilen. Automatische Läufe fassen nur Zahlungen ohne Zeilen oder mit reinen `auto`-Vorschlägen an.
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
- Einträge mit 0,00 € werden beim Import übersprungen und als Warnung gemeldet, nicht gespeichert. Mehr als ein Bericht pro Datei geht nicht.
- Eine gelernte IBAN lässt sich nicht vergessen. Eine einmal mehrdeutig gewordene IBAN bleibt es, und eine falsch gemerkte IBAN wird nie für das richtige Mitglied neu gelernt. "— keins —" setzt eine Zahlung nur vorübergehend auf Unklar: "Unklare automatisch zuordnen" ordnet sie über die gemerkte IBAN wieder zu.
- Duplikaterkennung: Eine Buchung mit derselben Bankreferenz, aber anderem Betrag oder Datum, wird als Duplikat gezählt und mit einer Warnung gemeldet. Eine zweite gleiche Zahlung ohne Bankreferenz am selben Tag wird verworfen, wenn sie in einer späteren Datei kommt (die laufende Nummer beginnt je Datei neu, gewollt; betrifft nur Zwischenstände desselben Tages, Sparkasse-Tagesdateien tragen Bankreferenzen). Der Import speichert die Buchungen vor Regeln und automatischer Zuordnung: Scheitern diese, bleiben die Buchungen unkategorisiert und unzugeordnet, ein erneuter Import zählt sie als Duplikate. Abhilfe: "Regeln anwenden" und "Unklare automatisch zuordnen".
- Barzahlungen und eigene Ausgaben lassen sich nicht von Hand buchen. Der Monat bleibt überfällig, es gibt in 1.0.0 keine Abhilfe.
- Die automatische Verteilung kennt den gemeinten Monat nicht: Sie nimmt zuerst den Buchungsmonat. Eine Nachzahlung für einen versäumten Monat ohne Mehrbetrag zählt deshalb für den Buchungsmonat (korrigierbar mit "Rückgängig"). Verteilungen, die vor der Umstellung auf Datenbankschema 2 schon bestätigt waren, gelten als von Ihnen bestätigt.
- Zwei offene Vorschläge desselben Mitglieds können denselben Monat treffen (nur der Hinweis "Monat bereits bezahlt"). Ein Austritt vor dem Eintritt wird akzeptiert. Die Textfelder der Mitglieder haben keine Längenbegrenzung.
- Die Prognose betrachtet nur Mitgliedsbeiträge, keine wiederkehrenden Ausgaben.
- Eine Datenbank ist eine Kasse (keine Mehrmandanten).
- Buchungen, Mitglieder, Kategorien und Regeln lassen sich nicht löschen. Buchungen lassen sich nicht von Hand anlegen. Korrekturen laufen über Zuordnung und Verteilung.
- Kein Knopf zum Wiederherstellen einer Sicherung. Automatisch gesichert wird nur vor einer Änderung am Datenbankformat.
- Die Diagramme im Report brauchen JavaScript im Browser. Die Tabellen gehen immer.
- Adresse und Port sind fest (`127.0.0.1:8000`).
- Python ab 3.10 erlaubt, entwickelt wird mit 3.15, automatisch getestet wird mit 3.10, 3.12 und 3.13 unter Windows und Linux.

Die ausführliche Fassung steht im Wiki unter "Bekannte Einschränkungen" (`/wiki/grenzen`).

## Drittanbieter

Chart.js 4.4.1 (MIT-Lizenz), unverändert und lokal eingebunden unter `src/kassivo/web/static/vendor/`. Der Lizenztext liegt in `src/kassivo/web/static/vendor/LICENSE.chartjs.txt`, Herkunft und Prüfsumme stehen in `src/kassivo/web/static/vendor/README.md`. Die Seite lädt nur diese lokale Kopie.

## Lizenz

Siehe [LICENSE](LICENSE).
