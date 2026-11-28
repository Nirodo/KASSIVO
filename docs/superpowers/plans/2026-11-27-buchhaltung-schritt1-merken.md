# Buchhaltung Schritt 1: Merken reparieren und umbenennen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die letzte Zuordnung des Nutzers wird gemerkt, Gemerktes ist sichtbar und änderbar, und der Bereich "Zahlungen" heißt "Buchhaltung".

**Architecture:** Die Regeln liegen in `matching.py` auf der vorhandenen Tabelle `member_iban` (`member_id` NULL = zahlt für mehrere Mitglieder). Eine neue Seite `/zuordnungen` pflegt die Einträge. Kein Schema-Wechsel.

**Tech Stack:** Python, FastAPI, SQLite, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-buchhaltung-design.md` (Abschnitt Merken). Merken für Konto und Kostenstelle sowie der Schlüssel "Name ohne IBAN" kommen in Schritt 3.

## Global Constraints

- Geld ist Integer in Cent. Bankumsätze werden nie gelöscht oder inhaltlich geändert.
- Vom Nutzer bestätigte Verteilungen und "kein Mitglied" (`no_member`) bleiben unangetastet.
- IBANs erscheinen nie in Logs, Fehlertexten oder URLs; Anzeige nur maskiert (`mask_iban`).
- Kein Inline-Skript, kein Inline-Style. Oberfläche und Texte deutsch.
- Kein Schema-Wechsel in diesem Schritt.

## Review Focus

- Eltern-IBAN für zwei Kinder: Nach dem Wechsel von Kind A zu Kind B darf KASSIVO nicht still weitere Zahlungen auf B umbuchen.
- Alt-Daten mit NULL durch Fehlkorrektur: muss sich auf `/zuordnungen` beheben lassen.
- Zuordnen mit abgewähltem Haken ändert das Gemerkte nicht.
- Doppelt gesendetes oder veraltetes Formular auf `/zuordnungen` (Eintrag schon gelöscht): verständliche Meldung, kein 500.

---

### Task 1: Merkregeln

**Files:** Modify `src/kassivo/matching.py`; Test `tests/test_merken.py` (bestehende Matching-Tests, die das alte "wird mehrdeutig" festschreiben, anpassen).

**Interfaces:**
- Produces: `assign_member(conn, tx_id: int, member_id: int | None, learn: bool = True) -> str | None` mit Rückgabe `"new"`, `"same"`, `"changed"`, `"multiple"` oder `None` (nichts gelernt).
- Produces: `remembered(conn) -> list[sqlite3.Row]` (Spalten `id` = rowid, `iban`, `member_id`, `member_name`), `set_remembered(conn, entry_id: int, member_id: int | None) -> None` (None = mehrere), `forget(conn, entry_id: int) -> None`; unbekannte `entry_id` oder Mitglied: `ValueError`.

Regeln beim Lernen (nur `learn=True`, Mitglied gewählt, IBAN vorhanden):
1. Eintrag in `member_iban` mit NULL: bleibt NULL, Rückgabe `"multiple"`.
2. Kein Eintrag und zwei oder mehr Mitglieder tragen die IBAN in den Stammdaten: nichts speichern, `"multiple"`.
3. Sonst: Eintrag = gewähltes Mitglied (**letzte Entscheidung gilt**). Zeigte die wirksame Zuordnung (Eintrag oder Stammdaten) auf ein anderes Mitglied: `"changed"`; auf dasselbe: `"same"`; auf keines: `"new"`.

- [ ] Tests zuerst: A dann B ergibt Eintrag B und `"changed"`; Korrektur zurück auf A ergibt A; NULL bleibt NULL; zwei Stammdaten-Treffer ergeben `"multiple"` ohne Eintrag; `learn=False` und "keins" ändern `member_iban` nicht; `set_remembered`, `forget` inklusive unbekannter ID.
- [ ] Rot sehen, umsetzen, grün. Commit `fix: letzte Zuordnung wird gemerkt` (Refs #55).

### Task 2: Oberfläche zum Merken

**Files:** Modify `web/transactions_routes.py`, `web/common.py`, `web/templates/_macros.html`, `unmatched.html`, `_nav.html`, `web/app.py`; Create `web/zuordnungen_routes.py`, `web/templates/zuordnungen.html`; Test `tests/test_web_zuordnungen.py`.

**Interfaces:** Consumes Task 1.
- `GET /zuordnungen`; `POST /zuordnungen/{entry_id}` mit `member_id` ("" ist ungültig, `multi` = zahlt für mehrere); `POST /zuordnungen/{entry_id}/forget`.

- "Absender merken" ist überall vorausgewählt (Parameter `learn_checked` entfällt).
- Nach `"changed"`: **keine** automatische Zuordnung weiterer Zahlungen. Hinweis: "Gemerkt: Dieser Absender gehört jetzt zum gewählten Mitglied (vorher zu einem anderen). Zahlt er für mehrere Mitglieder, legen Sie das unter „Gemerkte Zuordnungen“ fest. Weitere offene Zahlungen ordnet „Unklare automatisch zuordnen“ zu."
- Nach `"new"`/`"same"`: wie bisher weitere Zahlungen derselben IBAN zuordnen.
- Nach `"multiple"`: bisheriger Mehrdeutig-Hinweis, ergänzt um den Verweis auf die neue Seite.
- `/zuordnungen`: Tabelle mit maskierter IBAN, Mitglied oder "zahlt für mehrere", Auswahl mit "Speichern", Knopf "Vergessen". Ändern ordnet keine Zahlungen um; die Seite sagt das.

- [ ] Tests zuerst: Haken vorausgewählt auf `/transactions` und `/unmatched`; `"changed"` kaskadiert nicht; Seite listet maskiert (volle IBAN nicht im HTML); Speichern, "mehrere", Vergessen; veraltete ID ergibt 4xx mit deutschem Text; fremde Origin 403; Navigationstest ergänzt.
- [ ] Umsetzen, grün, Commit `feat: Seite Gemerkte Zuordnungen` (Refs #55).

### Task 3: "Zahlungen" heißt "Buchhaltung", Doku

**Files:** Modify `_nav.html`, `tests/test_web_layout.py`, Wiki-Vorlagen (`zuordnung`, `start`, weitere mit dem Bereichsnamen), `README.md`.

- Nur der Bereichsname ändert sich; Routen bleiben. "Zahlungen" als Wort für einzelne Geldeingänge bleibt.
- Seitenleiste: "Gemerkte Zuordnungen" (`/zuordnungen`).
- [ ] Tests anpassen, Wiki und README beschreiben Merken und die neue Seite. Volle Suite grün. Commit `feat: Bereich Buchhaltung` (Refs #54).
