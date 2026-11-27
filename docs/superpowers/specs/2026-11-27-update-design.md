# Update-Funktion (Issue #31): Design

Stand: 2026-11-27. Gilt ab Version 1.3.0.

## Ziel

KASSIVO erkennt ein neues GitHub-Release, lädt das Update-Paket herunter, prüft es und
installiert es mit einem lokalen Skript. Die Datenbank und lokale Dateien bleiben unberührt.

## Rulings

### R1 Bereitstellung: Release-Archiv aus GitHub Actions

- Workflow `.github/workflows/release.yml` läuft bei jedem Tag `vX.Y.Z`.
- Er prüft: Tag-Format `vX.Y.Z`, Tag gleich `version` in `pyproject.toml` und gleich
  `kassivo.__version__`. Danach laufen die Tests.
- Er baut `kassivo-X.Y.Z.zip` mit `git archive` aus dem getaggten Commit (Präfix
  `kassivo-X.Y.Z/`), dazu das Wheel und `SHA256SUMS` (Format von `sha256sum`).
- Er hängt die Dateien an das Release. Gibt es das Release schon (von Hand angelegt), lädt er
  nur hoch und überschreibt nichts (`gh release upload` ohne `--clobber`). Sonst legt er es an,
  mit `docs/releases/vX.Y.Z.md` als Notizen.
- Actions mit gepinnten SHAs wie in `tests.yml`, keine Fremd-Actions, `gh` ist auf dem Runner.
  Rechte: global `contents: read`, nur der Job zum Hochladen bekommt `contents: write`.
  Werte aus dem Tag kommen über `env` in die Skripte, nie direkt per `${{ }}` (Script-Injection).
- Verworfen: Wheel als einziges Paket (enthält kein `start.bat`, `update.bat`, Wiki-Quellen der
  Doku und Tests); GitHubs automatisches "Source code (zip)" (keine Prüfsumme, nicht
  reproduzierbar benannt); signierte Attestierung (`actions/attest-build-provenance`), weil die
  Prüfung `gh` auf dem PC bräuchte. Kann später ergänzt werden.

Ehrliche Grenze: `SHA256SUMS` liegt im selben Release wie das Archiv. Die Prüfsumme schützt
gegen beschädigte und abgebrochene Downloads und gegen ausgetauschte Dateien auf dem Weg, nicht
gegen jemanden, der das GitHub-Konto selbst übernommen hat. Die Echtheit kommt aus TLS zu
`github.com`. Liefert die API zusätzlich ein `digest` (`sha256:...`) je Datei, muss es ebenfalls
passen.

### R2 Prüfen: nur auf Knopfdruck, nur nach Opt-in

- Einstellung `update_check` in der Tabelle `settings`, Standard aus. Kein neues Schema.
- Ist sie aus, baut KASSIVO keine einzige Verbindung auf, auch nicht beim Druck auf einen Knopf
  (die Knöpfe antworten dann mit einer Meldung). Ist sie an, prüft KASSIVO nur, wenn der Nutzer
  "Jetzt nach Updates suchen" drückt.
- Verworfen für diese Version: automatische Prüfung beim Start (einmal am Tag). Sie wäre nur mit
  einem Hinweis im Seitenlayout sinnvoll, und `base.html` wird parallel umgebaut. Offener Punkt.
- Anfrage: `GET https://api.github.com/repos/Nirodo/KASSIVO/releases/latest`, fest im Code.
  Kopfzeilen nur `Accept`, `User-Agent: KASSIVO-Update` (GitHub verlangt einen User-Agent; ohne
  Versionsnummer) und `X-GitHub-Api-Version`. Keine Tokens, keine Cookies, keine Nutzerdaten,
  keine Telemetrie.
- Zeitlimit 10 s je Verbindung, TLS mit Zertifikats- und Hostnamenprüfung
  (`ssl.create_default_context`, unter Windows der Zertifikatsspeicher des Systems, damit ein
  Firmen-Proxy mit eigener CA funktioniert). Proxy aus `HTTPS_PROXY` wird beachtet.
- Weiterleitungen nur auf `https`. Antworten haben Größengrenzen (API 1 MB, `SHA256SUMS` 64 KB,
  Archiv 50 MB).
- Versionsvergleich streng nach SemVer `X.Y.Z` (ohne führende Nullen, mit optionalem `v`).
  Vorabversionen und Entwürfe werden ignoriert. Nur eine größere Version gilt als Update.
- Fehler (offline, Zeitlimit, HTTP-Fehler, kaputtes JSON) ergeben eine kurze deutsche Meldung,
  nie einen Absturz und keinen Stacktrace auf der Seite.

### R3 Herunterladen und prüfen: im Programm

- Knopf "Update herunterladen" fragt die API erneut (keine URL aus dem Formular). Nur Dateien
  aus `https://github.com/Nirodo/KASSIVO/releases/download/vX.Y.Z/` werden geladen.
- Ohne `kassivo-X.Y.Z.zip` und `SHA256SUMS` gibt es kein Update-Paket, nur den Hinweis auf
  die Release-Seite.
- Prüfsumme wird mit `hmac.compare_digest` verglichen. Passt sie nicht, wird nichts gespeichert.
- Das Archiv wird vor dem Speichern vollständig geprüft (R5) und atomar nach
  `<DB-Ordner>/updates/kassivo-X.Y.Z.zip` geschrieben, daneben `kassivo-X.Y.Z.zip.sha256`.
  Der Ordner liegt außerhalb des Projektordners, also nicht in OneDrive.

### R4 Installieren: lokales Skript `update.bat`, nicht im laufenden Programm

Entscheidung: Das laufende Programm überschreibt sich nicht selbst. Der Nutzer beendet KASSIVO
und startet `update.bat` im Projektordner. Begründung:

- Unter Windows sind Dateien eines laufenden Programms teils gesperrt; ein halber Austausch
  während einer Anfrage (z. B. mitten in einem Import) wäre schlimmer als gar keiner.
- Das Skript, das den Austausch macht, stammt aus der installierten Version, nicht aus dem Netz.
  Heruntergeladener Code läuft erst, wenn der Nutzer `update.bat` startet und mit `j` bestätigt.
- Kein automatischer Neustart: das Skript endet mit dem Hinweis, `start.bat` neu zu starten.

Ablauf von `python -I -m kassivo.update apply` (aus `update.bat`):

1. Neuestes geprüftes Paket in `<DB-Ordner>/updates` suchen, das neuer ist als die laufende
   Version. Prüfsumme erneut berechnen und mit der `.sha256`-Datei vergleichen, Archiv erneut
   prüfen (R5).
2. Abbrechen, wenn Port 8000 belegt ist (KASSIVO läuft noch), wenn KASSIVO nicht als
   editierbare Installation aus `<Projekt>/src` läuft, oder wenn die Datenbank innerhalb eines
   auszutauschenden Ordners liegt.
3. Bestätigung `j` abfragen.
4. Datenbank sichern (SQLite-Backup-API, Datei nur lesend geöffnet) nach `<DB-Ordner>/backups`.
   Scheitert das, bricht das Update ab. Die Datenbank selbst wird nie geschrieben.
5. Archiv nach `<Projekt>/.update/neu-X.Y.Z-<Zeit>` entpacken (gleiches Laufwerk, damit das
   Verschieben atomar ist).
6. Für jeden Eintrag der obersten Ebene des Archivs: alten Eintrag nach
   `<Projekt>/.update/alt-<alte Version>-<Zeit>` verschieben, neuen an seine Stelle. Einträge,
   die nicht im Archiv sind (`.venv`, `.git`, eigene Dateien), bleiben unberührt.
7. Haben sich die Abhängigkeiten in `pyproject.toml` geändert, `pip install -e .` ausführen
   (braucht Zugang zu PyPI). Sonst nicht.
8. Rauchtest: neuer Python-Prozess importiert `kassivo.web.app` und meldet die neue Version.
9. Scheitert 6, 7 oder 8: alles zurück aus `alt-...`, Meldung mit Grund. Der alte Stand bleibt
   nach Erfolg in `.update/alt-...` liegen und kann von Hand gelöscht werden.
10. Protokoll in `<DB-Ordner>/updates/update.log` (Zeit, Versionen, Schritte, keine
    personenbezogenen Daten).

Die Datenbank-Migration passiert beim nächsten Start wie bisher (mit eigener Sicherung, #30).

Verworfen:

- Selbstüberschreiben im laufenden Programm (siehe oben).
- Git als Update-Weg (`git fetch` und `git checkout vX.Y.Z`): braucht Git auf jedem PC und
  scheitert an lokalen Änderungen. Wer ein Git-Checkout hat, kann weiterhin so aktualisieren;
  nach `update.bat` zeigt `git status` geänderte Dateien, bis man auf das neue Tag wechselt.
- Download direkt durch `update.bat` (PowerShell `Invoke-WebRequest`): zweite Implementierung
  von TLS-, Größen- und Prüfsummenregeln, schwerer zu testen.

Ehrliche Grenzen:

- Die Update-Funktion gibt es ab 1.3.0. Von 1.2.0 auf 1.3.0 wird noch von Hand aktualisiert.
- Nur Installationen mit `pip install -e .` (wie in README und `start.bat` beschrieben). Bei
  `pip install .` bricht `update.bat` mit Hinweis ab.
- Nur Windows hat ein Skript; unter Linux/macOS läuft derselbe Befehl
  `.venv/bin/python -I -m kassivo.update apply` von Hand.
- Neue Abhängigkeiten brauchen PyPI. Ist das gesperrt, wird zurückgerollt.

### R5 Archiv-Prüfung

Vor dem Speichern und vor dem Entpacken:

- höchstens 5000 Einträge, je Datei höchstens 50 MB, zusammen höchstens 200 MB entpackt;
  beim Entpacken wird mitgezählt, nicht nur den Angaben im Archiv vertraut.
- jeder Name beginnt mit `kassivo-X.Y.Z/`; keine absoluten Pfade, kein `..`, kein `\`, kein
  Laufwerk, keine Steuerzeichen, keine symbolischen Links, keine doppelten Namen (auch nicht
  bei anderer Groß-/Kleinschreibung).
- verboten auf oberster Ebene: `.git`, `.venv`, `.update`, `.env`; verboten überall:
  Dateien mit Endung `.db`, `.sqlite`, `-journal`, `-wal`.
- Pflicht: `pyproject.toml`, `src/kassivo/__init__.py` mit `__version__ = "X.Y.Z"` passend zum
  Dateinamen und zum Release.

### R6 Oberfläche

- Neue Seite `/settings/update` mit eigener Vorlage `update.html`; ein Link darauf auf der Seite
  Einstellungen. `base.html` und `style.css` bleiben unverändert.
- Ändernde Anfragen sind `POST` und laufen durch den bestehenden Host- und Origin-Schutz.
- Release-Notizen werden als Text angezeigt (escaped, höchstens 5000 Zeichen), nie als HTML.
- Der HTTP-Client ist injizierbar (`app.state.update_fetch`); Tests machen keine echten
  Netzaufrufe (Autouse-Fixture sperrt den echten Client).
