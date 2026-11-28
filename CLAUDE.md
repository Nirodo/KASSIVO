# KASSIVO

Verwaltung von Gruppenkassen (Verein, Mannschaft, Freundeskreis): Einzahlungen, Ausgaben, Kassenstände. Ziel: Zahlungseingänge automatisch aus einem Bankkonto abrufen.

## Stand

Version 1.5.0 ist umgesetzt: lokale Web-App (Python, FastAPI, SQLite, Chart.js) für eine Gruppenkasse. Design: `docs/superpowers/specs/2026-11-24-kassivo-design.md`, Plan: `docs/superpowers/plans/2026-11-24-kassivo-v1.md`. Kontoumsätze kommen per Datei-Import (CAMT V8, Sparkasse); eine direkte Bankabfrage (FinTS, finAPI) und ein C24-Adapter sind noch nicht gebaut. Neue Arbeit läuft nach dem Git-Workflow unten.

## Arbeitsweise

- Sprache: Deutsch.
- Vor neuen Features: `superpowers:brainstorming`, danach `superpowers:writing-plans`.
- Tests zuerst (`superpowers:test-driven-development`).
- Vor "fertig": `superpowers:verification-before-completion`.
- Änderungen an Geld-, Bank- oder Auth-Code: `kassivo-reviewer` und `/security-review`.

## Projekt-Skills (`.claude/skills/`)

- `kassivo-geldlogik`: Beträge in Cent, Buchungen unveränderlich, Deduplizierung.
- `kassivo-bank-sicherheit`: Secrets, Verschlüsselung, Logging, DSGVO.
- `kassivo-modellwahl`: Welches Modell für welche Aufgabe.

## Agents und Modelle (`.claude/agents/`)

Das Modell richtet sich nach Komplexität und Risiko der Aufgabe: einfache Aufgaben günstig (Haiku oder Sonnet), schwere oder sehr umfangreiche Aufgaben Opus. Obergrenze ist Opus 5.5 (`claude-opus-5-5`). Fable wird nicht verwendet. Bei Sonnet und Haiku ist die Version egal.

| Aufgabe | Modell |
|---|---|
| Suchen, Auflisten, einfache Fragen, kleine mechanische Änderungen | haiku (`kassivo-explorer`) |
| Normale Features ohne Geldentscheidung: Oberfläche, Aggregation, Backup, Doku | sonnet (`kassivo-builder`) |
| Geldlogik mit Entscheidungen, Bankimport, Auth, Sicherheit, Architektur, sehr umfangreiche Aufgaben | opus (`kassivo-architect`) |
| Reviews: kleine oder mechanische Diffs und kleine Re-Reviews sonnet, Geld/Bank/Auth/Upload und der Abschluss-Review opus | `kassivo-reviewer` |

Beim Start eines Agents wird `model` ausdrücklich nach dieser Tabelle gesetzt (es überschreibt das Frontmatter), auch beim `kassivo-reviewer`. Details im Skill `kassivo-modellwahl`.

## Git-Workflow ab Release 1.0.0

Bis zum ersten Release (1.0.0) wird auf `feature/kassivo-v1` gearbeitet. Danach gilt:

- Nur noch Branches `feat/<thema>` (neue Funktion) und `fix/<thema>` (Fehlerbehebung), jeweils von `main`. Keine Commits direkt auf `main`.
- Änderungen kommen über Pull Requests in `main`. Jede Änderung hat ein GitHub-Issue, auf das der Pull Request verweist.
- Versionierung (SemVer): `X.0.0` Major-Release, `0.X.0` Minor-Release (neue Funktion), `0.0.X` Bugfix. Jedes Release bekommt einen Tag `vX.Y.Z` und ein GitHub-Release mit Notizen.
- Packages (GitHub Packages) sind vorerst nicht vorgesehen. Releases bestehen aus Tag und Release-Notizen.
- Echte Bankdaten kommen in keinen Commit, keinen Pull Request, kein Issue und kein Release.

## Harte Regeln

- Geldbeträge sind Integer in Cent, nie Float.
- Keine Zugangsdaten, Tokens oder PINs im Repo oder in Logs.
- Bankzugriff nur lesend, solange nichts anderes beschlossen ist.
