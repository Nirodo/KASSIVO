# Buchhaltung Schritt 2: Kern (Konten, Kostenstellen, Buchungen, Perioden, Schema 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KASSIVO führt ab Schema 4 ein Journal mit doppelter Buchführung (Konten, Kostenstellen, Buchungen, Storno) mit Buchungsperioden wie bei Webling und einem Eröffnungsbestand je Geldkonto. Alle vorhandenen Daten werden in einem Schritt übernommen und per Summenvergleich geprüft. Seiten "Kontenplan", "Perioden" und "Kontostände" pflegen Konten, Kostenstellen, Perioden und Anfangsbestände und zeigen den Stand je Bank- und Kassenkonto. Die App bleibt nach jedem Task benutzbar. Frist: produktiv nutzbar bis 31.12.2026.

**Architecture:** Neues Modul `ledger.py` (Konten, Kostenstellen, Buchungen, Storno, Prüfung) und `periods.py` auf neuen Tabellen `ledger_account`, `cost_center`, `period`, `posting`, `posting_row`, `posting_rule`. Die Datenbank sichert Soll = Haben (Buchungssatz-Zeilen mit einem Betrag), Unveränderlichkeit, "höchstens eine gültige Buchung je Umsatz", "höchstens ein Eröffnungsbestand je Konto", "Buchung nur in offener Periode" und überschneidungsfreie Perioden (Trigger) ab. Migration 4 baut `"transaction"` ohne `category_id` neu auf, löscht `category` und `category_rule` und verbucht die Altdaten. Ab Schema 4 ist das Journal die einzige Wahrheit; das Wort "Kategorie" bleibt bis Schritt 3 nur als Beschriftung in der Oberfläche und meint das Gegenkonto.

**Tech Stack:** Python, FastAPI, SQLite, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-buchhaltung-design.md` (Begriffe und Daten, Kontenplan beim Start, Korrigieren, Umstellung vorhandener Daten). Issue #57. Verbuchen-Oberfläche, Aufteilen in der Oberfläche, Buchen von Hand, Merken für Konto und Kostenstelle: Schritt 3. Abschluss mit Übertrag, Bilanz, Erfolgsrechnung, Belegnummern: Schritt 5 oder eigener Folge-Task (Datenmodell schon in Schema 4). Perioden und Eröffnungsbestand sind auf Wunsch des Nutzers (2026-11-28) aus Schritt 5 nach vorn gezogen.

## Global Constraints

- Geld ist Integer in Cent, nie Float. Bankumsätze (`"transaction"`) werden nie gelöscht oder inhaltlich geändert; ihre `id` bleibt bei der Migration erhalten.
- Buchungen (`posting`, `posting_row`) werden nie geändert oder gelöscht. Korrektur = Storno (Gegenbuchung) plus neue Buchung.
- Vom Nutzer bestätigte Verteilungen, `allocation` insgesamt, `no_member`, `member_iban` und `member_id` bleiben bei der Migration bitgleich.
- Migrationen führen nur `conn.execute()` aus, nie `commit()` oder `executescript()` (Regel in `db.py`). `SCHEMA` (Schema 1) bleibt eingefroren.
- Keine IBAN, kein Name, kein Verwendungszweck in Buchungstexten, Logs, Fehlermeldungen oder URLs. Buchungen zu Bankumsätzen haben `text` NULL; die Anzeige holt Name und Zweck aus dem Umsatz.
- Kein Inline-Skript, kein Inline-Style. Oberfläche und Texte deutsch. Jede schreibende Route wie bisher mit Origin-Prüfung (Middleware) und 303 nach Erfolg.
- Nie die echte Datenbank öffnen. Tests nur mit `tmp_path` oder `:memory:` und erfundenen Daten.
- Zwischenstände dieses Branches nie gegen eine echte Datenbank starten: Migration 4 ändert sich bis Task 5 noch.
- Jede Geldfunktion hat Tests für 0, negative Beträge, doppelte Buchung; Rundung entfällt in diesem Schritt (es wird nichts geteilt, Teilbeträge gibt der Aufrufer exakt vor).

## Entscheidungen

### E1: Eine Wahrheit in der Zwischenzeit (Kategorien und Journal)

**Entscheidung:** Schema 4 entfernt die Kategorien aus den Daten (`"transaction".category_id`, `category`, `category_rule`). Ab dann ist die gültige Buchung eines Umsatzes die einzige Auskunft darüber, "wofür" er war. Die Oberfläche von Schritt 2 sagt weiter "Kategorie", zeigt und setzt aber das Gegenkonto (Ertrags- und Aufwandskonten). Jeder Schreibpfad läuft über `ledger.set_counter_account()` oder `ledger.book_tx()`; jeder Lesepfad über `ledger`-Abfragen. Schritt 3 tauscht nur noch die Oberfläche.

Begründung: Es gibt nichts, was auseinanderlaufen kann, weil es nur einen Speicherort gibt. Die Spec verlangt Ansatz 2a ("Das Journal ersetzt die Kategorien", 2b verworfen); Schritt 3 lässt laut Spec die Kategorien nur "aus der Oberfläche" verschwinden. Es bleibt bei einer Datenmigration mit einem Summenvergleich.

Verworfen:
- `category_id` behalten und jede Schreibstelle (Kategorie setzen, Regeln, Beitragskategorie bei Zuordnung, Import) über eine Funktion führen, die die Buchung nachführt. Das sind zwei Stände. Jeder vergessene Pfad, jedes direkte SQL in Tests und jedes eingespielte Backup kann sie trennen. Schritt 3 bräuchte dann Schema 5 mit einem zweiten Summenvergleich.
- Trigger, der bei `update "transaction" set category_id` die Buchung erzeugt: Geldlogik in SQL, Storno und Kontenwahl ("für beides") nicht sauber abbildbar, schwer zu testen.
- `category_id` als Spalte behalten und nur nicht mehr schreiben: veraltete Werte bleiben lesbar und laden zu Fehlern ein.

### E2: Buchungssatz-Zeilen statt Soll- und Haben-Zeilen

**Entscheidung:** Eine Buchung (`posting`) hat eine oder mehrere Zeilen (`posting_row`) der Form Soll-Konto, Haben-Konto, Betrag > 0, optional Kostenstelle. Jede Zeile ist für sich ausgeglichen; damit ist Soll = Haben für jede Buchung durch `CHECK` erzwungen, unabhängig davon, wie viele Zeilen es gibt. Eine Zeile ergibt zwei Kontenzeilen; die Spec-Forderung "mindestens zwei Zeilen" (Soll und Haben) ist damit erfüllt. Webling arbeitet ebenso mit Soll, Haben, Betrag und Splitbuchungen. Eine Sammelzahlung "Bank 100 an Beiträge 60 und Spenden 40" sind zwei Zeilen.

Verworfen: Vorzeichenbehaftete Kontenzeilen mit Summe 0. Die Summe lässt sich in SQLite erst nach der letzten Zeile prüfen. Dafür bräuchte es aufgeschobene Fremdschlüssel und einen Abschluss-Trigger, und das hängt an `PRAGMA foreign_keys`. Zwei Spalten Soll/Haben je Kontenzeile: dasselbe Problem, mehr Prüfungen.

### E3: Gültigkeit ohne veränderliches Statusfeld

**Entscheidung:** Ein Storno ist eine eigene Buchung mit `reversal_of` = Original (eindeutig) und gespiegelten Zeilen. Gültig ist eine Buchung, die kein Storno ist und kein Storno hat. Ein `BEFORE INSERT`-Trigger auf `posting` lehnt eine zweite gültige Buchung für denselben Umsatz ab. Ein Storno eines Stornos, ein doppeltes Storno und ein Storno zu einem anderen Umsatz lehnt die Datenbank ebenfalls ab. `posting` und `posting_row` haben Trigger gegen `UPDATE` und `DELETE`. Zeilen dürfen nur zur neuesten Buchung hinzukommen (Siegel: ältere Buchungen sind geschlossen).

Verworfen: Statusspalte `valid/reversed` (Original würde beim Storno geändert). Verknüpfungstabelle "aktuelle Buchung je Umsatz" (ein Cache, den direktes SQL verfälschen kann). Partieller eindeutiger Index (braucht eine veränderliche Spalte).

### E4: Storno-Datum

Das Storno trägt das Datum der stornierten Buchung. So bleiben Periodensummen stabil. Liegt das Original in einer abgeschlossenen Periode, ist kein Storno möglich (Trigger `posting_in_open_period`). Korrekturen gehören dann in die offene Periode; diese Regel kommt mit dem Abschluss und betrifft nur `ledger.reverse()`.

### E5: Konten mit Rolle

`ledger_account.role` (eindeutig, NULL erlaubt) markiert die Konten, die KASSIVO selbst braucht: `fees` (Beiträge, Ziel von "Mitglied zugeordnet"), `equity` (Vereinsvermögen, Schritt 5), `bank_default` (das Startkonto "Bank", das die erste IBAN übernimmt). KASSIVO erkennt diese Konten nie am Namen. Konten mit Rolle lassen sich umbenennen, aber nicht ausblenden oder löschen. Jedes Bankkonto (`account`, IBAN) hat genau ein Aktivkonto über `ledger_account.bank_account_id` (eindeutig).

### E6: Regeln zeigen auf Konten

`posting_rule` ersetzt `category_rule` mit `account_id` statt `category_id`. Ziel ist ein Ertrags- oder Aufwandskonto; die Richtung folgt aus der Art (Ertrag nur Eingänge, Aufwand nur Ausgänge) wie bisher `category.kind`. Eine Regel einer Kategorie "für beides" wird zu zwei Regeln (Eingang auf das Ertragskonto, Ausgang auf das Aufwandskonto) mit gleicher Priorität; das Verhalten bleibt gleich. Regeln auf ausgeblendete Konten ruhen. Gemerktes vor Regeln kommt in Schritt 3.

### E7: Fremdschlüssel aus während Migration 4

Das Entfernen von `category_id` erfordert den Neuaufbau von `"transaction"` (SQLite kann eine Spalte mit Fremdschlüssel nicht löschen), auf den `allocation` verweist. Das ist das offizielle SQLite-Verfahren: Fremdschlüssel aus, neu anlegen, kopieren, alte Tabelle löschen, umbenennen, `PRAGMA foreign_key_check`, Fremdschlüssel an. `PRAGMA foreign_keys` wirkt nicht innerhalb einer Transaktion. `db._migrate` bekommt deshalb Schritte mit `fk_off = True`, die vor `begin immediate` umschalten.

### E8: Backup einspielen

`restore._quick_check` prüft gegen die Referenz der Schema-Version der Datei, nicht mehr gegen eine feste Tabellenliste. Trigger sind erlaubt, wenn Name, Tabelle und normalisierter Text genau denen der Referenz dieser Version entsprechen (v1 bis v3: keine, v4: die Ledger-Trigger). Fremde Trigger, Views und virtuelle Tabellen bleiben abgelehnt. Nach der Umstellung prüft `_strict_check` zusätzlich `ledger.check()`.

### E10: Perioden wie bei Webling

**Entscheidung:** Tabelle `period` (Name frei, Von, Bis, `open`/`closed`, `closed_at`). Perioden überschneiden sich nie (Trigger). Jede Buchung, auch ein Storno oder Eröffnungsbestand, liegt in einer offenen Periode (Trigger). Bankumsätze selbst brauchen keine Periode; der Import legt aber für jedes neue Datum per `periods.ensure_period` das passende Kalenderjahr an, beschnitten auf Lücken zwischen vorhandenen Perioden. So scheitert kein Import an einer fehlenden Periode. Umsätze in einer abgeschlossenen Periode werden importiert, aber nicht verbucht (Warnung). Die aktive Periode ist eine Ansichtswahl in `settings.active_period`.

Verworfen: Spalte `period_id` an `posting`. Das wäre redundant zum Datum und könnte widersprechen; die Periode folgt eindeutig aus dem Datum. Ebenfalls verworfen: Perioden nur als Kalenderjahr ohne Tabelle. Webling erlaubt freie Namen und Grenzen, und der Abschluss braucht einen gespeicherten Status.

### E11: Eröffnungsbestand je Geldkonto

**Entscheidung:** Der Eröffnungsbestand ist eine normale Buchung mit `source='opening'` und `opening_account_id`: Soll Geldkonto, Haben Vereinsvermögen (negativ umgekehrt). Ändern heißt Storno und neue Buchung wie überall. Höchstens einer gilt je Konto (Trigger). Der Kontostand ist immer die Summe der Buchungen (Projektregel: kein gespeicherter Saldo). Bei Bankkonten zeigt KASSIVO daneben den Stand "laut Umsätzen" (Eröffnungsbestand plus Summe aller Umsätze) und die Zahl der unverbuchten Umsätze. Die Spec-Forderung "Bankkontoseite vergleicht den gebuchten Stand mit der Summe der Umsätze" ist damit schon erfüllt.

Verworfen: Spalte `opening_cents` am Konto (ein zweiter, nicht gebuchter Geldwert; die Bilanz stimmte nicht). Eröffnungsbestand je Periode: Das ist der Übertrag und kommt mit dem Abschluss.

### E9: Update-Rückweg

Migration 4 ist ein einziger Schritt in einer Transaktion. Scheitert sie (auch am Summenvergleich), bleibt die Datenbank auf Schema 3, und die Sicherung vor der Umstellung liegt in `backups/`. Ein Zurück auf 1.4.0 nach erfolgreicher Umstellung geht nur über die Sicherung vor der Umstellung (1.4.0 lehnt Schema 4 ab); Änderungen danach gehen verloren. Wiki "Update" und die Release-Notizen sagen das.

### E12: Kontogruppen als eigene Tabelle (Vorgabe 2026-11-28)

**Entscheidung:** Tabelle `account_group(id, kind, name)`; `ledger_account.group_id` ist optional. Eine Gruppe gehört zu genau einer Art (wie bei Webling: Gruppen stehen unter Aktiven, Passiven, Ertrag oder Aufwand). Der Name ist je Art eindeutig (Groß/Klein egal), "Veranstaltungen" darf es also als Ertrags- und als Aufwandsgruppe geben. Ein Trigger verlangt, dass Konto und Gruppe dieselbe Art haben. Der Kontenplan wird später nach Art, Gruppe, Nummer und Name gegliedert. Startgruppen (generisch): "Umlaufvermögen" (Aktiv), "Eigenkapital" (Passiv), "Ertrag" (Ertrag), "Ausgaben" (Aufwand).

Begründung: Umbenennen einer Gruppe ist eine Zeile statt vieler; Tippfehler erzeugen keine zweite Gruppe; eine Gruppe kann leer existieren; der spätere CSV-Import findet Gruppen eindeutig über (Art, Name).

Verworfen: Freitext `group_name` am Konto (Gruppe "Ertrag " und "ertrag" wären zwei Gruppen; Umbenennen ändert viele Konten; leere Gruppen gibt es nicht). Gruppen ohne Art (eine Gruppe könnte Ertrags- und Aufwandskonten mischen; Bilanz und Erfolgsrechnung bräuchten dann Sonderregeln).

### E13: Kontonummer optional, Namen je Gruppe eindeutig (Vorgabe 2026-11-28)

**Entscheidung:** `ledger_account.number` darf NULL sein; ist sie gesetzt, ist sie eindeutig (SQLite erlaubt mehrere NULL in `unique`). Der Name ist nur innerhalb von (Art, Gruppe) eindeutig, Konten ohne Gruppe bilden je Art einen eigenen Bereich: eindeutiger Ausdrucksindex `(kind, ifnull(group_id, 0), name collate nocase)`, im Code zusätzlich per `casefold`. Damit gehen "Spenden" als Ertrag und als Aufwand sowie "Essen" in zwei Gruppen. Der generische Startplan behält Nummern und bekommt Gruppen. Vereinsspezifische Namen kommen nicht ins Repo. Meldungen nennen ein Konto über seine Nummer, ohne Nummer über "Konto-ID n".

Verworfen: Name global eindeutig (verbietet die verlangten Fälle). Name je Art eindeutig (verbietet "Essen" in zwei Aufwandsgruppen). Nummer Pflicht (der Nutzer arbeitet ohne Nummern).

### E14: Kontenplan-Import aus CSV vorbereitet (Vorgabe 2026-11-28, Import selbst später)

Spalten `nummer, konto, typ, gruppe, eroeffnung`. Das Modell trägt jede Zeile ohne Schemaänderung: `typ` `assets|liabilitys|income|expense` wird `asset|liability|income|expense`; `gruppe` wird über (Art, Name) gefunden oder angelegt (`ledger.find_group`, `create_group`); `nummer` leer bleibt NULL; `eroeffnung` wird `ledger.set_opening` (Aktiv- und Passivkonten, nicht das Vereinsvermögen selbst; Ertrag und Aufwand haben keinen Eröffnungsbestand). Gleiche Namen in verschiedenen Gruppen sind erlaubt (E13). Abweichend vom ursprünglichen Plan erlaubt `set_opening` deshalb auch Passivkonten: positiv heißt Haben Konto, Soll Vereinsvermögen.

### E15: Geschäftsjahr = Kalenderjahr als Standard (Vorgabe 2026-11-28)

Seed, Migration und `periods.ensure_period` legen Kalenderjahre "Jahr JJJJ" an. Andere Perioden (Vereinsjahr) legt der Nutzer selbst an (Task 7).

## Review Focus

- Summenvergleich: unabhängig berechnet (erwartet aus Altdaten in Python, tatsächlich per SQL aus `posting_row`), bricht die Migration mit Rollback ab, Meldung ohne Personendaten.
- Kategorie "für beides", Eingang mit Ausgabe-Kategorie, Ausgang mit Einnahme-Kategorie, Umsatz 0 Cent, Mitglied ohne Kategorie: richtig verbucht oder bewusst "Neu".
- Kein Schreibpfad schreibt noch Kategorien; kein Lesepfad liest sie (`grep -rn "category" src/kassivo` nur in den im Task 5 genannten Stellen).
- Import bleibt atomar: Fehler in Regeln, Zuordnung oder Buchung hinterlassen keine Buchung.
- Doppelt gesendetes Formular erzeugt keine zweite Buchung; erneutes Setzen desselben Kontos erzeugt kein Storno.
- Trigger halten auch unter `restore.harden` (`trusted_schema=OFF`), und ein Backup mit verändertem oder zusätzlichem Trigger wird abgelehnt.
- Perioden: kein Import scheitert an fehlender Periode; abgeschlossene Periode sperrt Buchung und Storno; Überschneidung und herausfallende Buchungen sind DB-seitig ausgeschlossen.
- Eröffnungsbestand: negativ, 0, doppelt, Änderung; Kontostand ist Summe der Buchungen, kein gespeicherter Wert.
- Performance: 20 000 Umsätze migrieren und `/transactions`, `/unmatched`, `/reports` bleiben schnell (Indizes, keine Abfrage je Zeile ohne Index).

---
### Task 1: Migrationsschritte mit ausgeschalteten Fremdschlüsseln

**Files:** Modify `src/kassivo/db.py`; Test `tests/test_schema_version.py`. Kein Schema-Wechsel, kein neues Modul.

**Interfaces:**
- Produces: Eine Migrationsfunktion darf das Attribut `fk_off = True` tragen. `_migrate` führt sie so aus:
  1. Offene Transaktion (nach Sicherung unter Sperre) mit `rollback()` beenden (es wurde nichts geschrieben).
  2. `PRAGMA foreign_keys = OFF`, danach `PRAGMA foreign_keys` lesen; nicht 0: `MigrationError`.
  3. `_lock()` wie bisher (Version unter Sperre neu lesen, ggf. überspringen).
  4. Migration ausführen, dann `PRAGMA foreign_key_check`; liefert es Zeilen: Exception, Rollback, `MigrationError` mit "Fremdschlüssel verletzt" (nur Tabellennamen, keine Werte).
  5. `_set_version`, `commit()`.
  6. In `finally` immer `PRAGMA foreign_keys = ON` (auch nach Fehler und Rollback).
- Produces: Hilfsfunktion `_fk_check(conn) -> list[str]` (Tabellennamen mit Verstößen).
- Kommentarblock "Neue Migration hinzufügen" um Punkt 5 ergänzen: "Tabellen neu aufbauen nur in einem Schritt mit `fk_off = True`."

- [ ] Tests zuerst (injizierte Migrationen, Datei-Datenbank in `tmp_path`):
  - `test_fk_off_step_can_rebuild_referenced_table`: Schritt 2 baut eine Tabelle neu, auf die eine andere verweist (`create new`, `insert select`, `drop`, `rename`). Danach `schema_version == 2`, Daten gleich, `pragma foreign_keys` der Verbindung ist 1.
  - `test_fk_off_step_with_violation_rolls_back`: Schritt hinterlässt eine verwaiste Zeile. `MigrationError`, Text enthält "Schema 2" und "Fremdschlüssel", Version bleibt 1, Tabelle unverändert, `pragma foreign_keys` ist 1.
  - `test_fk_off_step_exception_restores_fk_pragma`: Schritt wirft `RuntimeError`. Danach Version 1 und `pragma foreign_keys == 1`.
  - `test_fk_off_step_after_backup_takes_one_backup`: Altbestand mit Version 1 und Schritt 2 (`fk_off`): genau eine Sicherung in `backup_dir`.
  - `test_fk_off_parallel_instance_skips`: Wie `test_parallel_instance_migrates_once`, mit `fk_off`-Schritt: zweite Instanz führt ihn nicht erneut aus.
  - `test_normal_step_keeps_fk_on`: Schritt ohne Attribut sieht `pragma foreign_keys == 1` (Regression).
- [ ] Rot sehen, umsetzen, grün. Volle Suite grün. Commit `feat: Migrationsschritte ohne Fremdschlüsselprüfung` (Refs #57).

### Task 2: Backup einspielen prüft je Schema-Version, Trigger nur wie in der Referenz

**Files:** Modify `src/kassivo/restore.py`; Test `tests/test_restore_hardening.py`, `tests/test_restore.py` (bestehender Test `test_triggers_and_views_rejected` bleibt und muss grün bleiben).

**Interfaces:**
- Produces: `_reference(version: int | None = None) -> tuple[list, list]`. `None` = aktuelle Version (wie bisher). Sonst: frische Datenbank im Speicher, migriert mit `{k: db.MIGRATIONS[k] for k in range(2, version + 1)}` (für Version 0 und 1: keine Migration), mit `on_open=harden`. Kein Zwischenspeicher: eine Speicher-Datenbank ist in Millisekunden angelegt, und Tests mit ausgetauschten `db.MIGRATIONS` sehen so nie einen alten Stand.
- Produces: `_quick_check(c) -> int` prüft statt `REQUIRED_TABLES`:
  - Tabellen der Referenz dieser Version (Version 0 wie 1) sind vorhanden.
  - Keine Views, keine virtuellen Tabellen (wie bisher).
  - Trigger: Menge `(name, tbl_name, _norm(sql))` der Datei ist gleich der Menge der Referenz dieser Version. Version 0 bis 3: leer, also abgelehnt wie bisher.
- `REQUIRED_TABLES` entfällt (oder bleibt nur noch als Konstante für Version 0, falls Tests sie nutzen). `MONEY_COLUMNS` bleibt in diesem Task unverändert.

Begründung: Die bisherige Ablehnung aller Trigger macht Sicherungen ab Schema 4 unbrauchbar. Ein fremder Trigger würde bei der Umstellung oder später beim Schreiben laufen; deshalb sind nur genau die eigenen erlaubt. Verworfen: Trigger vor der Umstellung löschen und danach neu anlegen (eine Datei mit fehlenden oder veränderten Triggern sähe danach "richtig" aus, die Ursache bliebe unbemerkt).

- [ ] Tests zuerst. Für Trigger-Fälle `monkeypatch` von `db.MIGRATIONS` und `db.SCHEMA_VERSION` auf eine Testmigration 4, die eine Tabelle und einen Trigger `t_demo` anlegt (die echte Migration 4 kommt erst in Task 5):
  - `test_reference_per_version_differs`: `_reference(1)` enthält keine Spalte `source` in `allocation`, `_reference(3)` enthält `no_member`.
  - `test_v1_v2_v3_backups_still_accepted`: je eine mit `db.connect` und Teilmenge der Migrationen erzeugte Datei: `prepare()` liefert die ursprüngliche Version und eine Zusammenfassung.
  - `test_trigger_from_reference_accepted` (Testmigration 4): Datei mit Version 4 und `t_demo` wird bereitgestellt.
  - `test_trigger_changed_text_rejected`: `t_demo` mit anderem Rumpf: `RestoreError(E_SCHEMA)`.
  - `test_extra_trigger_rejected`: zusätzlicher Trigger in Version 4: `E_SCHEMA`.
  - `test_missing_trigger_rejected`: `t_demo` fehlt: `E_SCHEMA`.
  - `test_trigger_in_old_version_rejected`: Datei mit Version 3 und dem Trigger `t_demo`: `E_SCHEMA` vor der Umstellung (der Trigger darf nie laufen).
  - `test_reference_follows_migrations`: Nach `monkeypatch` von `db.MIGRATIONS` liefert `_reference(4)` die neue Struktur.
- [ ] Rot sehen, umsetzen, grün. Volle Suite grün. Commit `feat: Backup einspielen prüft je Schema-Version und erlaubt nur eigene Trigger` (Refs #57).

### Task 3: Buchungskern `ledger.py` mit Tabellen, Perioden und Triggern (noch ohne Migration)

**Files:** Modify `src/kassivo/db.py` (nur neue Konstanten `_LEDGER_DDL_4`, `START_PLAN_4`, Funktion `_seed_ledger_4`; nicht in `MIGRATIONS` eingetragen), `src/kassivo/iban.py` (`mask_iban` hierher verschieben), `src/kassivo/ingest.py` (`from kassivo.iban import mask_iban`, Name bleibt importierbar); Create `src/kassivo/ledger.py`, `src/kassivo/periods.py`; Test `tests/test_ledger.py` (Gruppen, Konten, Kostenstellen), `tests/test_ledger_booking.py` (Buchen, Storno, Beiträge), `tests/test_ledger_balance.py` (Eröffnungsbestand, Kontostände, Abfragen, Prüfung), `tests/test_ledger_db.py`, `tests/test_periods.py`.

Die App läuft in diesem Task weiter auf Schema 3. Die Tests legen die Tabellen selbst an: Fixture `ledger_conn` = `db.connect(":memory:")`, danach jede Anweisung aus `db._statements(db._LEDGER_DDL_4)` und `db._seed_ledger_4(conn, today)`.

**SQL-DDL (`_LEDGER_DDL_4`, ab Freigabe eingefroren wie `SCHEMA`):**

```sql
-- Kontogruppe wie bei Webling (E12): gehört zu einer Art, Name je Art eindeutig
create table account_group(
  id integer primary key,
  kind text not null check(kind in ('asset','liability','income','expense')),
  name text not null check(length(trim(name)) between 1 and 100));
create unique index account_group_name on account_group(kind, name collate nocase);
-- Nummer optional, wenn gesetzt eindeutig; Name je (Art, Gruppe) eindeutig (E13)
create table ledger_account(
  id integer primary key,
  number integer unique
    check(number is null or (typeof(number) = 'integer' and number between 1 and 99999)),
  name text not null check(length(trim(name)) between 1 and 100),
  kind text not null check(kind in ('asset','liability','income','expense')),
  group_id integer references account_group(id),
  hidden integer not null default 0 check(hidden in (0,1)),
  role text unique check(role in ('bank_default','fees','equity')),
  bank_account_id integer unique references account(id),
  check(bank_account_id is null or kind = 'asset'),
  check(role is null or hidden = 0));
create table cost_center(
  id integer primary key,
  name text not null unique check(length(trim(name)) between 1 and 100),
  hidden integer not null default 0 check(hidden in (0,1)));
-- Buchungsperiode wie bei Webling: frei benennbar, Von-Bis, offen oder abgeschlossen
create table period(
  id integer primary key,
  name text not null unique check(length(trim(name)) between 1 and 60),
  start_date text not null check(start_date glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  end_date text not null check(end_date glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  status text not null default 'open' check(status in ('open','closed')),
  closed_at text,
  check(start_date <= end_date),
  check((status = 'closed') = (closed_at is not null)));
create table posting(
  id integer primary key,
  booking_date text not null
    check(booking_date glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  text text check(text is null or length(text) <= 200),
  voucher text check(voucher is null or length(voucher) <= 40),
  tx_id integer references "transaction"(id),
  -- Eröffnungsbestand eines Geldkontos (höchstens einer gültig je Konto)
  opening_account_id integer references ledger_account(id),
  reversal_of integer unique references posting(id),
  source text not null check(source in
    ('migration','import','rule','fee','manual','opening','closing','carryover')),
  created_at text not null,
  check(tx_id is null or opening_account_id is null),
  check((source = 'opening') = (opening_account_id is not null)));
create table posting_row(
  id integer primary key,
  posting_id integer not null references posting(id),
  debit_account_id integer not null references ledger_account(id),
  credit_account_id integer not null references ledger_account(id),
  cents integer not null
    check(typeof(cents) = 'integer' and cents between 1 and 100000000000),
  cost_center_id integer references cost_center(id),
  check(debit_account_id <> credit_account_id));
create table posting_rule(
  id integer primary key,
  account_id integer not null references ledger_account(id),
  field text not null check(field in ('purpose','cp_name','cp_iban')),
  contains text not null check(length(contains) between 1 and 200),
  priority integer not null default 100,
  min_cents integer check(min_cents is null or (typeof(min_cents) = 'integer' and min_cents >= 0)),
  max_cents integer check(max_cents is null or (typeof(max_cents) = 'integer' and max_cents >= 0)));
create index posting_tx on posting(tx_id);
create index posting_opening on posting(opening_account_id);
create index posting_date on posting(booking_date);
create index posting_row_posting on posting_row(posting_id);
create index posting_row_debit on posting_row(debit_account_id);
create index posting_row_credit on posting_row(credit_account_id);
create index posting_rule_account on posting_rule(account_id);
create index period_start on period(start_date);
create unique index ledger_account_name
  on ledger_account(kind, ifnull(group_id, 0), name collate nocase);
create index ledger_account_group on ledger_account(group_id);

create trigger ledger_account_group_kind before insert on ledger_account
when new.group_id is not null
 and (select kind from account_group where id = new.group_id) is not new.kind
begin select raise(abort, 'Die Kontogruppe gehört zu einer anderen Kontoart.'); end;
create trigger ledger_account_group_kind_upd before update of kind, group_id on ledger_account
when new.group_id is not null
 and (select kind from account_group where id = new.group_id) is not new.kind
begin select raise(abort, 'Die Kontogruppe gehört zu einer anderen Kontoart.'); end;
create trigger account_group_kind_fixed before update of kind on account_group
when new.kind <> old.kind and exists (select 1 from ledger_account where group_id = old.id)
begin select raise(abort, 'Die Art einer benutzten Kontogruppe lässt sich nicht ändern.'); end;

create trigger posting_no_update before update on posting
begin select raise(abort, 'Buchungen sind unveränderlich (Storno statt Ändern).'); end;
create trigger posting_no_delete before delete on posting
begin select raise(abort, 'Buchungen werden nicht gelöscht (Storno statt Löschen).'); end;
create trigger posting_row_no_update before update on posting_row
begin select raise(abort, 'Buchungen sind unveränderlich (Storno statt Ändern).'); end;
create trigger posting_row_no_delete before delete on posting_row
begin select raise(abort, 'Buchungen werden nicht gelöscht (Storno statt Löschen).'); end;
-- Zeilen nur zur neuesten Buchung: ältere Buchungen sind geschlossen
create trigger posting_row_sealed before insert on posting_row
when new.posting_id is not (select max(id) from posting)
begin select raise(abort, 'Die Buchung ist abgeschlossen.'); end;
create trigger posting_row_cost_center before insert on posting_row
when new.cost_center_id is not null and not exists (
  select 1 from ledger_account a
  where a.id in (new.debit_account_id, new.credit_account_id)
    and a.kind in ('income','expense'))
begin select raise(abort, 'Kostenstellen gibt es nur bei Ertrags- und Aufwandskonten.'); end;
-- jede Buchung liegt in einer offenen Periode (auch Storno)
create trigger posting_in_open_period before insert on posting
when not exists (select 1 from period
                 where new.booking_date between start_date and end_date and status = 'open')
begin select raise(abort, 'Für dieses Datum gibt es keine offene Buchungsperiode.'); end;
-- höchstens eine gültige Buchung je Bankumsatz
create trigger posting_one_valid before insert on posting
when new.tx_id is not null and new.reversal_of is null and exists (
  select 1 from posting p
  where p.tx_id = new.tx_id and +p.reversal_of is null
    and not exists (select 1 from posting r where r.reversal_of = p.id))
begin select raise(abort, 'Der Umsatz hat schon eine gültige Buchung.'); end;
-- höchstens ein gültiger Eröffnungsbestand je Konto
create trigger posting_one_opening before insert on posting
when new.opening_account_id is not null and new.reversal_of is null and exists (
  select 1 from posting p
  where p.opening_account_id = new.opening_account_id and +p.reversal_of is null
    and not exists (select 1 from posting r where r.reversal_of = p.id))
begin select raise(abort, 'Das Konto hat schon einen Eröffnungsbestand.'); end;
create trigger posting_reversal_ok before insert on posting
when new.reversal_of is not null
begin
  select raise(abort, 'Storno ohne stornierte Buchung.')
    where not exists (select 1 from posting where id = new.reversal_of);
  select raise(abort, 'Ein Storno kann nicht storniert werden.')
    where (select reversal_of from posting where id = new.reversal_of) is not null;
  select raise(abort, 'Storno gehört zu einem anderen Umsatz oder Konto.')
    where new.tx_id is not (select tx_id from posting where id = new.reversal_of)
       or new.opening_account_id is not (select opening_account_id from posting
                                          where id = new.reversal_of);
end;
create trigger period_no_overlap before insert on period
when exists (select 1 from period
             where new.start_date <= end_date and new.end_date >= start_date)
begin select raise(abort, 'Buchungsperioden dürfen sich nicht überschneiden.'); end;
create trigger period_no_overlap_upd before update of start_date, end_date on period
when exists (select 1 from period where id <> new.id
             and new.start_date <= end_date and new.end_date >= start_date)
begin select raise(abort, 'Buchungsperioden dürfen sich nicht überschneiden.'); end;
-- Grenzen nur ändern, wenn keine Buchung dadurch aus der Periode fällt
create trigger period_keep_postings before update of start_date, end_date on period
when exists (select 1 from posting
             where booking_date between old.start_date and old.end_date
               and booking_date not between new.start_date and new.end_date)
begin select raise(abort, 'Die Periode enthält Buchungen außerhalb der neuen Grenzen.'); end;
create trigger period_closed_fixed before update of start_date, end_date on period
when old.status = 'closed'
begin select raise(abort, 'Eine abgeschlossene Periode lässt sich nicht ändern.'); end;
create trigger period_keep before delete on period
when exists (select 1 from posting
             where booking_date between old.start_date and old.end_date)
begin select raise(abort, 'Die Periode enthält Buchungen.'); end;
create trigger ledger_account_kind_fixed before update of kind on ledger_account
when new.kind <> old.kind and (
  exists (select 1 from posting_row
          where debit_account_id = old.id or credit_account_id = old.id)
  or exists (select 1 from posting_rule where account_id = old.id))
begin select raise(abort, 'Die Art eines benutzten Kontos lässt sich nicht ändern.'); end;
create trigger ledger_account_bank_fixed before update of bank_account_id on ledger_account
when old.bank_account_id is not null and new.bank_account_id is not old.bank_account_id
begin select raise(abort, 'Die Zuordnung zum Bankkonto lässt sich nicht ändern.'); end;
create trigger ledger_account_keep before delete on ledger_account
when old.role is not null or old.bank_account_id is not null
begin select raise(abort, 'Dieses Konto braucht KASSIVO.'); end;
create trigger posting_rule_target before insert on posting_rule
when (select kind from ledger_account where id = new.account_id) not in ('income','expense')
begin select raise(abort, 'Regeln buchen nur auf Ertrags- oder Aufwandskonten.'); end;
create trigger posting_rule_target_upd before update of account_id on posting_rule
when (select kind from ledger_account where id = new.account_id) not in ('income','expense')
begin select raise(abort, 'Regeln buchen nur auf Ertrags- oder Aufwandskonten.'); end;
```

Hinweise:
- `+p.reversal_of is null` (unäres Plus) in `posting_one_valid`, `posting_one_opening` und `ledger.VALID`: Ohne das wählt SQLite für `is null` den Index auf `reversal_of` und durchsucht alle Buchungen; jede Einfügung würde linear langsamer (gemessen: 20 000 Umsätze 8,7 s statt unter 1 s). Test `test_many_postings_stay_fast`.
- Ausgeblendete Konten prüft nur der Code (ein Storno muss auch auf ein inzwischen ausgeblendetes Konto möglich sein).
- Alle Funktionen in Triggern und `CHECK` (`typeof`, `length`, `trim`, `glob`, `raise`) laufen auch unter `trusted_schema=OFF`. Ein Test sichert das ab.
- Abschluss mit Übertrag (spätere Task oder Schritt 5) braucht kein neues Schema. `period.status`/`closed_at`, die Quellen `closing` und `carryover` sowie die Sperre `posting_in_open_period` sind schon da. Ablauf dann: Sicherung, Ergebnis per `closing`-Buchung ins Vereinsvermögen, Periode schließen, `carryover`-Buchungen der Bestandskonten zum Start der Folgeperiode.
- Kontostand eines Bestandskontos am Tag X = Summe aller Zeilen bis X, über Periodengrenzen hinweg. Er braucht deshalb vor dem ersten Abschluss keinen Übertrag.
- Die aktive Periode steht in `settings` (Schlüssel `active_period`, Wert = `period.id`). Es gibt keine eigene Spalte, weil "aktiv" eine Ansichtswahl ist und keine Eigenschaft der Daten.

**Startplan (`START_PLAN_4`, Tupel Nummer, Name, Art, Rolle, Gruppe; Gruppen in `START_GROUPS_4`):**

| Nummer | Name | Art | Rolle | Gruppe |
|---|---|---|---|---|
| 1000 | Barkasse | asset | | Umlaufvermögen |
| 1200 | Bank | asset | `bank_default` | Umlaufvermögen |
| 2000 | Vereinsvermögen | liability | `equity` | Eigenkapital |
| 3000 | Beiträge | income | `fees` | Ertrag |
| 3100 | Spenden | income | | Ertrag |
| 3200 | Zinsen | income | | Ertrag |
| 3900 | Sonstige Einnahmen | income | | Ertrag |
| 4000 | Hauptverein | expense | | Ausgaben |
| 4100 | Bankgebühren | expense | | Ausgaben |
| 4900 | Sonstige Ausgaben | expense | | Ausgaben |

`_seed_ledger_4(conn, today: date) -> None` legt Startgruppen und Startplan an und verknüpft jedes vorhandene Bankkonto (`account`, nach `id`): das erste mit "Bank" (1200), jedes weitere mit einem neuen Aktivkonto "Bank DE12…1234" in der Gruppe von "Bank" (maskiert über `iban.mask_iban`, bei Namensgleichheit in der Gruppe " (2)", " (3)") und der nächsten freien Nummer ab 1201. Dazu kommen Perioden "Jahr JJJJ" (Kalenderjahr, offen) vom frühesten Buchungsjahr der Umsätze (ohne Umsätze: `today.year`) bis `today.year`. `settings.active_period` zeigt auf das Jahr von `today`.

**Interfaces `periods.py` (kein `commit()`):**
- `create_period(conn, name: str, start: date, end: date) -> int`; `update_period(conn, period_id: int, *, name: str, start: date, end: date) -> None`; `delete_period(conn, period_id: int) -> None` (nur ohne Buchungen); `list_periods(conn) -> list[sqlite3.Row]` (`id, name, start_date, end_date, status, postings`, nach `start_date`).
- `period_for(conn, day: str) -> sqlite3.Row | None`.
- `ensure_period(conn, day: str) -> int`: Periode, die `day` enthält. Fehlt sie, wird das Kalenderjahr "Jahr JJJJ" angelegt, beschnitten auf die Lücke zwischen den Nachbarperioden (Start = max(1.1., Ende Vorgänger + 1 Tag), Ende = min(31.12., Start Nachfolger - 1 Tag)). Ist der Name vergeben, kommt " (2)" dazu. Deterministisch, ohne Überschneidung.
- `get_active(conn, today: date) -> sqlite3.Row`: gespeicherte aktive Periode; fehlt sie oder ist sie gelöscht, die Periode mit `today`, sonst die letzte. `set_active(conn, period_id: int) -> None` (ruft `db.set_setting`, das committet, deshalb nur aus Routen).
- `is_open(conn, day: str) -> bool`.
- `PeriodError(ValueError)`: deutsche Meldungen (Überschneidung, Grenzen, Name, Buchungen außerhalb).
- Schließen und Wiedereröffnen sind in diesem Schritt nicht in der Oberfläche. `close_period` kommt mit dem Übertrag.

**Interfaces `ledger.py` (keine Funktion ruft `commit()`, Aufrufer committen wie bei `categories`):**
- `class LedgerError(ValueError)`: deutsche Meldung ohne IBAN, Namen, Zweck.
- `KINDS = ("asset", "liability", "income", "expense")`, `KIND_TEXT = {"asset": "Aktiv", "liability": "Passiv", "income": "Ertrag", "expense": "Aufwand"}`, `SOURCES` wie im `CHECK`.
- `@dataclass(frozen=True) class Part: account_id: int; cents: int; cost_center_id: int | None = None`. `cents` hat das Vorzeichen des Umsatzes aus Sicht der Bank (Eingang positiv).
- Kontogruppen (E12): `create_group(conn, kind: str, name: str) -> int`; `rename_group(conn, group_id: int, name: str) -> None`; `delete_group(conn, group_id: int) -> None` (nur ohne Konten); `find_group(conn, kind: str, name: str) -> int | None` (casefold, für den späteren CSV-Import); `list_groups(conn, kind: str | None = None) -> list[sqlite3.Row]` (`id, kind, name, accounts`, nach Art und Name). Name nach `strip()` 1 bis 100 Zeichen, je Art eindeutig ohne Groß/Klein.
- Konten: `create_account(conn, name: str, kind: str, *, number: int | None = None, group_id: int | None = None) -> int`; `update_account(conn, account_id: int, *, name: str, number: int | None, group_id: int | None) -> None`; `set_account_hidden(conn, account_id: int, hidden: bool) -> None`; `delete_account(conn, account_id: int) -> None`; `list_accounts(conn, *, visible_only: bool = False, kinds: tuple[str, ...] | None = None) -> list[sqlite3.Row]` (Spalten `id, number, name, kind, group_id, group_name, hidden, role, bank_account_id, used`; `used` = Zeilen plus Regeln; nach Art in `KINDS`-Reihenfolge, Gruppe (ohne Gruppe zuletzt), Nummer (ohne Nummer zuletzt), Name); `account_label(row) -> str` ("1200 Bank" oder "Bank"); `next_free_number(conn, start: int) -> int`; `fees_account_id(conn) -> int`; `equity_account_id(conn) -> int`; `bank_ledger_account(conn, bank_account_id: int) -> int` (vorhandenes, sonst `bank_default` verknüpfen, sonst neu wie im Seed).
- Prüfregeln Konten: Nummer ist leer (`None`) oder eine ganze Zahl von 1 bis 99 999 und noch frei. Name nach `strip()` 1 bis 100 Zeichen, eindeutig ohne Groß/Klein (`casefold`) nur innerhalb von (Art, Gruppe), nicht "Ohne Kategorie" (bis Schritt 3 reserviert). Art in `KINDS`. Gruppe vorhanden und von derselben Art. Ausblenden geht nicht bei Rolle oder Bankkonto. Löschen geht nur ohne Zeilen, Regeln, Rolle und Bankkonto.
- Kostenstellen: `create_cost_center(conn, name: str) -> int`; `rename_cost_center(conn, cc_id: int, name: str) -> None`; `set_cost_center_hidden(conn, cc_id: int, hidden: bool) -> None`; `delete_cost_center(conn, cc_id: int) -> None` (nur unbenutzt); `list_cost_centers(conn, *, visible_only: bool = False) -> list[sqlite3.Row]` (`id, name, hidden, used`).
- Buchen: `post(conn, booking_date: str, rows: list[tuple[int, int, int, int | None]], *, source: str, tx_id: int | None = None, opening_account_id: int | None = None, text: str | None = None, voucher: str | None = None) -> int`. Zeilen sind `(debit_id, credit_id, cents, cost_center_id)`. Prüft vorab mit verständlicher Meldung: Konten vorhanden und sichtbar, Kostenstelle sichtbar, mindestens eine Zeile, Periode offen (`periods.is_open`). `created_at` = jetzt, auf Sekunden.
- `reverse(conn, posting_id: int, *, source: str) -> int`: gespiegelte Zeilen (Soll und Haben getauscht), Datum, `tx_id` und `opening_account_id` des Originals. Ist die Buchung schon storniert oder selbst ein Storno: `LedgerError`. Liegt sie in einer abgeschlossenen Periode: `LedgerError` "Periode abgeschlossen".
- `valid_posting_id(conn, tx_id: int) -> int | None`.
- `book_tx(conn, tx_id: int, parts: list[Part], *, source: str) -> int`. Bankseite ist `bank_ledger_account(tx.account_id)`. Vorher `periods.ensure_period(tx.booking_date)`. Prüft: Umsatz vorhanden; Betrag nicht 0; `parts` nicht leer; jeder Teil ungleich 0; Summe gleich `amount_cents`; kein Konto doppelt; kein Teil auf die eigene Bankseite; Kostenstelle nur bei Ertrag oder Aufwand. Ist die gültige Buchung schon genau so (gleiche Zeilen als Menge), wird nichts geschrieben und ihre `id` zurückgegeben. Sonst wird die gültige Buchung storniert und neu gebucht. Teil mit `cents > 0`: Soll Bank, Haben Gegenkonto; `cents < 0`: Soll Gegenkonto, Haben Bank, Betrag `abs`.
- `set_counter_account(conn, tx_id: int, account_id: int | None, *, cost_center_id: int | None = None, source: str) -> int | None`: `None` heißt "Ohne Kategorie" (stornieren, falls gebucht).
- `unbook_tx(conn, tx_id: int, *, source: str) -> bool` (True, wenn storniert wurde).
- `book_fees_if_unbooked(conn, tx_id: int) -> bool`: nur bei Eingang ohne gültige Buchung und mit offener Periode. Bucht auf `fees_account_id` mit `source='fee'`. Bei abgeschlossener Periode: False, kein Fehler.
- Eröffnungsbestand: `set_opening(conn, account_id: int, cents: int, day: str) -> int | None`. Aktiv- und Passivkonten (E14), nicht das Konto mit Rolle `equity`. `cents` 0 heißt kein Eröffnungsbestand (stornieren). Aktivkonto positiv: Soll Konto, Haben Vereinsvermögen; Passivkonto positiv: Soll Vereinsvermögen, Haben Konto. Negativ (überzogenes Konto): jeweils umgekehrt mit `abs`. Gleicher Betrag und Tag: nichts schreiben. Sonst stornieren und neu buchen, `source='opening'`. `get_opening(conn, account_id: int) -> tuple[int, str] | None`.
- Kontostand: `balance(conn, account_id: int, until: str | None = None) -> int` (Soll minus Haben bis einschließlich `until`, für Aktivkonten positiv = Guthaben); `money_accounts(conn, until: str) -> list[dict]` je sichtbarem Aktivkonto mit `id, number, name, opening_cents, opening_day, booked_cents` (`balance`), bei Bankkonten zusätzlich `statement_cents` (Eröffnungsbestand plus Summe aller Umsätze bis `until`), `unbooked` (Anzahl Umsätze bis `until` ohne gültige Buchung) und `diff_cents` (`statement_cents - booked_cents`).
- `counter_accounts(conn, tx_ids) -> dict[int, list[int]]` (gültige Buchung, Gegenseite, aufsteigend; in Blöcken zu 500 IDs wie `dues.distribution_counts`).
- Feste SQL-Bausteine für Abfragen mit Alias `t` für `"transaction"` (nie aus Nutzereingaben gebaut): `VALID` (Alias `p`: kein Storno, nicht storniert), `BOOKED` (Umsatz hat gültige Buchung), `COUNTER` (Gegenkonto-ID, wenn genau eines, sonst NULL), `ON_FEES` (gültige Buchung hat eine Zeile auf dem Konto mit Rolle `fees`).
- `check(conn) -> list[str]`: Befunde, leer heißt in Ordnung. Prüft mengenbasiert: Buchung ohne Zeilen; Storno spiegelt das Original nicht genau; Bankseite einer gültigen Umsatz-Buchung ungleich `amount_cents`; mehr als eine gültige Buchung je Umsatz oder Eröffnungsbestand je Konto; gültige Buchung auf einem 0-Cent-Umsatz; Bankkonto ohne Aktivkonto; Buchung außerhalb jeder Periode. Texte nennen nur Buchungs- und Kontonummern (ohne Nummer "Konto-ID n").

- [ ] Tests zuerst, `tests/test_ledger_db.py` (rohes SQL gegen die Trigger, ohne `ledger.py`):
  - `test_row_amount_zero_negative_float_rejected`: `cents` 0, -1, 1.5, '1.5', '100x', NULL ergeben `IntegrityError`; '100' wird durch Integer-Affinität exakt die Zahl 100 (`test_row_amount_text_digits_become_integer`).
  - `test_same_account_both_sides_rejected`.
  - `test_update_and_delete_of_posting_and_row_rejected`: alle vier Trigger, Meldung enthält "Storno".
  - `test_row_for_older_posting_rejected`: Zeile zu Buchung 1, nachdem Buchung 2 existiert, ergibt "abgeschlossen".
  - `test_second_valid_posting_for_tx_rejected`; `test_after_reversal_new_posting_allowed`; `test_reversal_twice_rejected` (eindeutiges `reversal_of`); `test_reversal_of_reversal_rejected`; `test_reversal_other_tx_rejected`; `test_reversal_of_missing_rejected`.
  - `test_second_opening_for_account_rejected`; `test_opening_requires_source_opening` (CHECK); `test_opening_and_tx_together_rejected`.
  - `test_posting_outside_period_rejected`; `test_posting_in_closed_period_rejected` (auch Storno); `test_period_overlap_rejected` (insert und update); `test_period_shrink_with_postings_rejected`; `test_closed_period_bounds_fixed`; `test_period_with_postings_not_deletable`; `test_closed_requires_closed_at`.
  - `test_cost_center_on_asset_only_rejected`; `test_cost_center_on_income_ok`.
  - `test_kind_change_of_used_account_rejected`; `test_kind_change_of_unused_account_ok`; `test_bank_link_change_rejected`; `test_role_and_bank_accounts_not_deletable`; `test_used_account_not_deletable_by_fk`.
  - `test_rule_on_asset_rejected` (insert und update).
  - `test_triggers_work_under_hardened_connection`: Datei-Datenbank, mit `restore.harden` geöffnet. Gültige Buchung, Storno und Neubuchung gehen; eine zweite gültige Buchung wird abgelehnt.
  - `test_seed_links_first_bank_and_creates_more`: Zwei Bankkonten: das erste an 1200 "Bank", das zweite an 1201 "Bank DE11…1111". Startplan vollständig, Rollen eindeutig.
  - Gruppen und Namen (E12, E13): `test_number_optional_and_unique` (zwei Konten ohne Nummer gehen, doppelte Nummer nicht); `test_same_name_other_kind_or_group_ok` ("Spenden" Ertrag und Aufwand; "Essen" in zwei Aufwandsgruppen); `test_same_name_same_group_rejected` (auch Groß/Klein, auch ohne Gruppe); `test_group_kind_must_match` (insert und update); `test_group_name_unique_per_kind`; `test_used_group_kind_fixed`; `test_seed_groups`.
  - `test_seed_periods_from_first_tx_year`: Umsätze ab 2023, `today` 2026-11-28: Perioden "Jahr 2023" bis "Jahr 2026", alle offen, aktiv ist 2026. Ohne Umsätze nur "Jahr 2026".
- [ ] Tests zuerst, `tests/test_periods.py`: `test_create_validates` (Ende vor Start, Name leer oder doppelt, Überschneidung: `PeriodError`); `test_vereinsjahr_july_to_june`; `test_ensure_period_creates_calendar_year`; `test_ensure_period_fills_gap_only` (Nachbarn 2026-07-01..2027-06-30 und keine davor: 2026-03-05 ergibt 2026-01-01..2026-06-30); `test_ensure_period_name_taken_gets_suffix`; `test_get_active_falls_back_to_today_then_last`; `test_delete_only_without_postings`; `test_update_cannot_drop_postings`.
- [ ] Tests zuerst, `tests/test_ledger.py`:
  - Gruppen: `test_group_create_rename_delete`; `test_group_validates` (leer, doppelt je Art, Art "x"); `test_find_group_casefold`; `test_delete_used_group_rejected`.
  - Konten: `test_create_account_validates` (Nummer 0, 100000, "12" oder doppelt; Name leer, 101 Zeichen, "beiträge" gegen "Beiträge" in derselben Gruppe, "Ohne Kategorie"; Art "x"; Gruppe anderer Art oder unbekannt: jeweils `LedgerError`, DB unverändert); `test_create_account_without_number`; `test_same_name_in_other_group_or_kind_ok`; `test_update_account_group_and_clear_number`; `test_list_accounts_order_kind_group_number`; `test_update_account_renames_and_renumbers`; `test_hide_role_or_bank_account_rejected`; `test_delete_only_unused`; `test_list_accounts_used_counts_rows_and_rules`; `test_next_free_number_skips_taken`; `test_bank_ledger_account_links_default_then_creates`.
  - Kostenstellen: `test_cost_center_create_rename_hide_delete`; `test_delete_used_cost_center_rejected`; `test_hidden_cost_center_not_bookable`.
  - Buchen: `test_book_income_debits_bank` (+1700: Soll Bank, Haben Beiträge, 1700); `test_book_expense_credits_bank` (-500: Soll Hauptverein, Haben Bank, 500); `test_book_zero_amount_rejected`; `test_parts_sum_must_equal_amount` (1700 gegen 1000 + 699: Fehler; 1000 + 700: zwei Zeilen); `test_split_with_mixed_signs` (+1000 = +1200 Beiträge, -200 Bankgebühren: Bankseite netto 1000); `test_duplicate_account_in_parts_rejected`; `test_own_bank_as_counter_rejected`; `test_hidden_account_rejected_for_new_but_reversal_ok`; `test_book_tx_creates_missing_period`.
  - Doppelt und Storno: `test_same_booking_twice_is_noop` (zweiter Aufruf: gleiche `id`, keine neue Zeile, kein Storno); `test_change_account_reverses_and_rebooks` (danach drei Buchungen: Original, Storno, neu; Saldo je Konto stimmt; genau eine gültige); `test_set_none_reverses`; `test_set_none_on_unbooked_is_noop`; `test_reverse_twice_rejected`; `test_reversal_keeps_original_date`; `test_reverse_in_closed_period_rejected`.
  - Beiträge: `test_book_fees_if_unbooked_only_once`; `test_book_fees_skips_booked_outgoing_and_closed`.
  - Eröffnungsbestand und Kontostand: `test_opening_positive_and_negative` (+50 000 und -1 234: Soll und Haben richtig, Gegenkonto Vereinsvermögen); `test_opening_zero_reverses`; `test_opening_same_twice_is_noop`; `test_opening_change_reverses_and_rebooks`; `test_opening_only_for_asset_and_liability` (Ertrag, Aufwand und Vereinsvermögen abgelehnt; Passivkonto positiv: Haben Konto); `test_balance_until_date`; `test_money_accounts_shows_unbooked_difference` (Eröffnung 10 000, Umsätze +1 700 gebucht und -300 nicht gebucht: `booked_cents` 11 700, `statement_cents` 11 400, `unbooked` 1, `diff_cents` -300); `test_money_accounts_skips_hidden`.
  - Abfragen: `test_counter_accounts_for_split_and_reversed`; `test_sql_fragments_match_python` (30 zufällige, festgesäte Umsätze mit Buchungen, Storni und Teilungen: `BOOKED`, `COUNTER`, `ON_FEES` per SQL gleich `counter_accounts` und `valid_posting_id`).
  - Prüfung: `test_check_clean_db_empty`; `test_check_finds_bank_side_mismatch` (Zeilen per rohem SQL mit falschem Betrag); `test_check_finds_posting_without_rows`; `test_check_finds_bad_reversal`; `test_check_messages_without_personal_data` (Name und IBAN des Umsatzes kommen in keinem Befund vor).
- [ ] Rot sehen, umsetzen, grün. Volle Suite grün (App unverändert auf Schema 3). Commit `feat: Buchungskern mit Konten, Perioden, Eröffnungsbestand und Storno` (Refs #57).

### Task 4: Migration 4 mit Summenvergleich (noch nicht eingetragen)

**Files:** Modify `src/kassivo/db.py` (`_migrate_to_4`, `_TX_DDL_4`, Hilfsfunktionen; **nicht** in `MIGRATIONS`); Test `tests/test_migration_4.py`, `tests/test_migration_4_perf.py`. Die Tests nutzen `db.connect(path, migrations={**db.MIGRATIONS, 4: db._migrate_to_4})`.

**Interfaces:**
- `_migrate_to_4(conn) -> None`, Attribut `_migrate_to_4.fk_off = True` (Task 1). `today` kommt aus `date.today()`; für Tests über das Modulattribut `db._today` austauschbar (Standard `date.today`).
- `class LedgerMigrationError(Exception)`: Meldung "Summenvergleich: Konto {number}, Jahr {jahr}: vorher {a} Cent, nachher {b} Cent." (keine Namen, keine IBAN; ohne Nummer "Konto-ID {id}"). `_migrate` macht daraus wie bei jedem Fehler `MigrationError` mit Rollback und Hinweis auf die Sicherung.
- `_category_map(conn, categories) -> dict[int, tuple[int, int]]`: Kategorie-ID auf (Konto für Eingänge, Konto für Ausgänge); `categories` sind die in Schritt 1 gelesenen Zeilen `(id, name, kind)`, weil `category` in Schritt 3 schon gelöscht ist.
- `_book_migrated_4`, `_migrate_rules_4`, `_sum_check_4`: eingefrorene Hilfsfunktionen. Regeln, die nie greifen konnten (unbekanntes Feld, leerer Text, Obergrenze < 0), entfallen; eine negative Untergrenze wird NULL (gleiches Verhalten, `CHECK` verlangt >= 0).

**Ablauf in einer Transaktion (Fremdschlüssel aus):**
1. Altbestand lesen (Python): Kategorien, Regeln, je Umsatz `id, account_id, booking_date, amount_cents, category_id, member_id`.
2. `"transaction"` ohne `category_id` neu aufbauen. Spalten, Reihenfolge der übrigen Spalten, `unique(account_id, dedupe_key)` und Fremdschlüssel bleiben. `id` wird mitkopiert (`insert into transaction_new(id, ...) select id, ...`), damit `allocation.tx_id` gültig bleibt. Danach `drop table "transaction"`, `alter table transaction_new rename to "transaction"`. `_TX_DDL_4` ist der neue CREATE-Text, eingefroren.
3. `drop table category_rule`, `drop table category`.
4. `_LEDGER_DDL_4` ausführen, `_seed_ledger_4(conn, today)`.
5. Kategorien zu Konten (Ergebnis `_category_map`), je Kategorie in der Reihenfolge `id`:
   - `in`: Ertragskonto. `out`: Aufwandskonto. `both`: je ein Ertrags- und ein Aufwandskonto.
   - Wiederverwendung eines Startkontos, wenn Name (casefold) und Art passen. Fest: "Beiträge" auf 3000 (Rolle `fees`), "Hauptverein" auf 4000, "Sonstiges" (`both`) auf 3900 "Sonstige Einnahmen" und 4900 "Sonstige Ausgaben".
   - Sonst neues Konto in der Startgruppe der Art ("Ertrag" bzw. "Ausgaben"). Name = Kategoriename, bei `both` "Name (Einnahmen)" und "Name (Ausgaben)" (eindeutig in der Auswahl „Kategorie“, die bis Schritt 3 Ertrag und Aufwand gemeinsam zeigt). Ist der Name in dieser Art und Gruppe schon vergeben (E13), kommt " (2)" dazu; gleiche Namen in anderer Art bleiben ohne Zusatz. Nummer = `next_free_number` ab 3500 (Ertrag) oder 4500 (Aufwand). Sind die Bereiche voll, die nächste freie Nummer ab 5000.
6. Regeln: jede `category_rule` wird zu `posting_rule` mit gleichem `field`, `contains`, `priority`, `min_cents`, `max_cents`. `in` zeigt auf das Ertragskonto, `out` auf das Aufwandskonto, `both` wird zu zwei Regeln. Die Reihenfolge `priority, id` bleibt erhalten (neue IDs in alter Reihenfolge).
7. Verbuchen, je Umsatz mit `amount_cents != 0`, in der Reihenfolge `booking_date, id`, `source='migration'`:
   - Mit `category_id`: Gegenkonto = Eingangs- oder Ausgangskonto der Kategorie nach Vorzeichen. Ein Ausgang mit Einnahme-Kategorie (z. B. Rückbuchung "Beiträge") wird damit Soll Beiträge, Haben Bank. Das ist richtig: es mindert den Ertrag.
   - Ohne `category_id`, aber mit `member_id`: Konto mit Rolle `fees` (Spec: "Umsätze mit Mitglied auf Beiträge").
   - Sonst keine Buchung (Status "Neu").
   - Je Umsatz erst `posting`, dann seine eine Zeile. Wegen des Siegels `posting_row_sealed` müssen sich die beiden abwechseln; "erst alle Buchungen, dann alle Zeilen" schlägt fehl. Zwei vorbereitete Anweisungen im Wechsel reichen für die Leistung (Test unten).
   - Die Migration nutzt eigene, eingefrorene Hilfsfunktionen in `db.py` und nicht `ledger.py`, weil sich das Modul später ändern darf. Einzige Ausnahme ist `ledger.check` als reine Prüfung am Ende.
8. Summenvergleich (unabhängig):
   - Erwartet (Python, nur aus Schritt 1 und `_category_map`): je (Konto, Jahr) die Summe der `amount_cents` der verbuchten Umsätze. Je (Bank-Aktivkonto, Jahr) dasselbe über `account_id`. Dazu Anzahl der verbuchten Umsätze.
   - Tatsächlich (SQL über `posting_row` und `posting`, nur gültige): je (Gegenkonto, Jahr) Haben minus Soll, je (Bankkonto, Jahr) Soll minus Haben, Anzahl gültiger Buchungen mit `tx_id`.
   - Jede Abweichung: `LedgerMigrationError` mit der ersten Abweichung (nach Konto, Jahr sortiert).
   - Zusätzlich je Kategorie und Jahr: Summe über ihre Konten gleich der früheren Kategoriesumme (das ist die Prüfung der Spec: "je Konto und Jahr dieselben Summen wie vorher die Kategorien"). Umsätze ohne Kategorie mit Mitglied zählen bei "Beiträge".
9. `ledger.check(conn)` muss leer sein, sonst `LedgerMigrationError("Prüfung: ...")`.
10. `PRAGMA foreign_key_check` übernimmt `_migrate` (Task 1).

Leistung: Ziel unter 10 s für 20 000 Umsätze auf dem CI-Rechner. Messung mit `time.perf_counter` wie `tests/test_auto_perf.py`.

- [ ] Tests zuerst, `tests/test_migration_4.py` (Hilfsfunktion `_v3(tmp_path, rows)` baut eine Schema-3-Datei mit `db.connect`, erfundenen Mitgliedern, Kategorien, Regeln, Umsätzen, Verteilungen, `no_member`, `member_iban`):
  - `test_fresh_db_gets_start_plan_and_period`: Neue Datenbank: Startplan (10 Konten), Perioden, keine Buchung, keine Tabelle `category`, keine Spalte `category_id`.
  - `test_categories_become_accounts`: "Beiträge" ist 3000 mit Rolle `fees`, "Hauptverein" ist 4000, "Sonstiges" ist 3900 und 4900, eigene "Miete" (`out`) ist 4500, eigene "Müller" (`both`) ist 3500 "Müller (Einnahmen)" und 4501 "Müller (Ausgaben)".
  - `test_same_name_other_kind_no_suffix`: Kategorie "Spenden" mit `out` ergibt Aufwandskonto "Spenden" (Ertragskonto 3100 "Spenden" bleibt, E13).
  - `test_name_collision_gets_suffix`: Kategorien "Miete (Ausgaben)" (`out`) und "Miete" (`both`) ergeben "Miete (Ausgaben)" und "Miete (Ausgaben) (2)".
  - `test_migrated_accounts_in_start_groups`.
  - `test_many_categories_overflow_numbers`: 600 Kategorien `in`: alle bekommen eindeutige Nummern ≤ 99 999.
  - `test_rules_keep_order_and_split_both`: Regel auf `both` wird zu zwei Regeln. `apply_rules` (neu, Task 5) wird hier nicht gebraucht; geprüft werden Felder und Reihenfolge.
  - `test_tx_with_category_booked_by_sign`: Eingang mit "Sonstiges" auf 3900, Ausgang mit "Sonstiges" auf 4900, Ausgang mit "Beiträge" Soll 3000.
  - `test_member_without_category_booked_on_fees`.
  - `test_no_category_no_member_stays_unbooked`; `test_zero_amount_with_category_stays_unbooked`.
  - `test_ids_allocations_no_member_member_iban_unchanged`: Schnappschuss vorher und nachher (alle Spalten außer `category_id`) gleich, `allocation` bitgleich.
  - `test_sums_per_account_and_year`: drei Jahre, beide Vorzeichen. `year_summary` vorher (Kategorien) und nachher (Task 5 ist noch nicht da, deshalb direkte SQL-Summe) gleich je Kategorie.
  - `test_two_bank_accounts_booked_on_own_bank_side`.
  - `test_sum_mismatch_rolls_back` (`monkeypatch` einer Hilfsfunktion, die eine Zeile mit falschem Betrag bucht): `MigrationError`, Text enthält "Summenvergleich" und keinen Namen oder IBAN, Version bleibt 3, `category_id` noch da, eine Sicherung in `backups/`.
  - `test_check_failure_rolls_back` (`ledger.check` per `monkeypatch` mit Befund): Version bleibt 3.
  - `test_migration_from_v1_and_v2`: Altbestand Version 1 (1.1.1) und 2 läuft über 2, 3, 4 durch.
  - `test_migration_under_hardened_connection`: `db.connect(path, on_open=restore.harden, backup_before_migration=False)` stellt eine Schema-3-Datei um.
  - `test_second_start_is_noop`: zweites `connect` ändert nichts (gleiche Zahl Buchungen).
  - `test_periods_cover_all_tx_years` (Umsätze 2021 und 2026, Lücke 2022 bis 2025: alle Jahre angelegt).
  - `test_messages_without_personal_data`: bei erzwungenem Fehler enthält die Meldung weder `cp_name` noch IBAN noch Zweck.
- [ ] `tests/test_migration_4_perf.py`: `test_migrate_20000_tx_fast` (20 000 Umsätze, 300 Mitglieder, 5 Kategorien, unter 10 s, danach `ledger.check` leer).
- [ ] Rot sehen, umsetzen, grün. Volle Suite grün (App weiter auf Schema 3). Commit `feat: Umstellung auf Schema 4 mit Summenvergleich` (Refs #57).

### Task 5: Umschalten auf Schema 4 (alle Schreib- und Lesepfade über das Journal)

**Files:** Modify `src/kassivo/db.py` (`MIGRATIONS[4]`, `SCHEMA_VERSION = 4`), `categories.py` (wird Regelmodul auf Konten), `matching.py`, `ingest.py`, `reports.py`, `restore.py`, `web/transactions_routes.py`, `web/categories_routes.py`, `web/templates/_macros.html`, `transactions.html`, `unmatched.html`, `categories.html`, `docs/design/demo/make_demo_db.py`; Tests anpassen: `test_categories.py`, `test_matching.py`, `test_import_atomar.py`, `test_ingest.py`, `test_dues.py`, `test_reports.py`, `test_web_transactions.py`, `test_web_reports.py`, `test_web_start.py`, `test_db.py`, `test_schema_version.py`, `test_restore*.py`, `test_security_polish.py`, `tests/js/reports-page.test.mjs` (nur falls Feldnamen sich ändern; Ziel: sie ändern sich nicht).

Das ist der eine große Schritt, in dem `category` verschwindet. Kleiner schneiden geht nicht, ohne dass Tests zwischendurch rot sind oder zwei Stände entstehen (E1). Reihenfolge innerhalb des Tasks: Backend, Restore, Web, Tests. Erst am Ende die volle Suite.

**Interfaces:**
- `categories.py` (Name bleibt bis Schritt 3; Docstring: "Regeln auf Konten"):
  - `add_rule(conn, account_id: int, field: str, contains: str, *, priority: int = 100, min_cents: int | None = None, max_cents: int | None = None) -> int`. Konto unbekannt, ausgeblendet oder nicht Ertrag/Aufwand: `ValueError`.
  - `apply_rules(conn, tx_ids: list[int] | None = None, overwrite: bool = False) -> int`: Erste passende Regel (Reihenfolge `priority, id`). Ertragskonto nur bei Eingang, Aufwandskonto nur bei Ausgang. Ruhende Regeln (Konto ausgeblendet) zählen nicht. Ohne `overwrite` nur ungebuchte Umsätze, sonst alle. Bucht über `ledger.set_counter_account(..., source='rule')`. Umsätze in abgeschlossener Periode werden übersprungen. Rückgabe = Zahl neuer Buchungen (gleiches Konto zählt nicht).
  - `create_category`, `set_category`, `KINDS`, `RESERVED_NAME` entfallen. `FIELD_COLUMNS` bleibt.
- `matching.py`: `_set_fee_category_if_empty` wird zu `ledger.book_fees_if_unbooked(conn, tx_id)` (in `assign_member` und `_auto_assign`). `_NEEDS_MEMBER` wird `"(not " + ledger.BOOKED + " or " + ledger.ON_FEES + ")"`. `FEE_CATEGORY` entfällt. Docstring: "Eine Buchung wird nie geändert, nur member_id und no_member; die Beitragsbuchung legt ledger an."
- `ingest.py`: `_account_id` ruft nach dem Anlegen eines neuen Bankkontos `ledger.bank_ledger_account(conn, acc)` (gleiche Transaktion). `_write` ruft für jeden neuen Umsatz `periods.ensure_period(conn, booking_date)`, damit es zu jedem Datum eine Periode gibt (auch für Umsätze, die "Neu" bleiben). `ImportOutcome.categorized` = neue Regel-Buchungen. Liegt ein neuer Umsatz in einer abgeschlossenen Periode, wird er importiert, aber nicht verbucht; die Warnung "{n} Umsätze liegen in einer abgeschlossenen Periode und wurden nicht verbucht" kommt in `warnings`.
- `reports.year_summary`: `by_category` aus dem Journal. Je Umsatz des Jahres und Gegenkonto der gültigen Buchung der vorzeichenbehaftete Teilbetrag aus Sicht der Bank. Ist er positiv, zählt er zu `income_cents` der Gruppe, sonst zu `expense_cents`. Ungebuchte Umsätze bilden die Gruppe "Ohne Kategorie". Gruppenname = Kontoname. Schlüssel und Sortierung bleiben (Reports-JS unverändert). `income_cents`/`expense_cents` gesamt bleiben aus den Umsätzen.
- `restore.py`: `MONEY_COLUMNS` = `("transaction", "amount_cents", False)`, `("allocation", "cents", False)`, `("fee_rate", "cents", False)`, `("posting_row", "cents", False)`, `("posting_rule", "min_cents", True)`, `("posting_rule", "max_cents", True)`. `_strict_check` ruft danach `ledger.check(c)`; Befunde ergeben `RestoreError(E_LEDGER)`, `E_LEDGER = "Die Buchungen in der Sicherung sind nicht stimmig (Soll und Haben, Storno oder Bankseite)."`. `summary()` bekommt `postings` (Zahl gültiger Buchungen) für Manifest und Vorschau; das Manifest-Feld ist optional, ältere Manifeste bleiben gültig.
- Web, Wort "Kategorie" bleibt bis Schritt 3:
  - `_cats(conn)` = sichtbare Ertrags- und Aufwandskonten (`ledger.list_accounts(visible_only=True, kinds=("income","expense"))`), Anzeige "3000 Beiträge". Auf `/unmatched` nur Ertragskonten.
  - `/transactions`: Je Zeile `counter` aus `ledger.COUNTER` (in der Hauptabfrage als Spalte `counter_id`) und `booked` aus `ledger.BOOKED`. Steht `counter_id` auf einem ausgeblendeten Konto, wird es als zusätzliche Option angezeigt (sonst wäre die Auswahl falsch). Aufgeteilte Buchung (gebucht, `counter_id` NULL): Text "aufgeteilt", kein Formular.
  - Filter `category=none` heißt `not BOOKED`. `category=<id>` heißt gültige Buchung mit Zeile auf diesem Konto (Unterabfrage, Parameter gebunden).
  - `POST /transactions/{tx_id}/category` mit `category_id` (Konto-ID, leer = Ohne Kategorie): `ledger.set_counter_account(..., source='manual')`, Commit, 303. Fehler (`LedgerError`, `PeriodError`): 422 mit der deutschen Meldung, Rollback.
  - `/categories` heißt in der Seite "Regeln". Das Formular "Kategorie anlegen" entfällt; dort steht der Hinweis "Konten legen Sie im Kontenplan an." (ohne Link; Task 6 macht daraus einen Link auf `/accounts`). Bis Task 6 lassen sich keine neuen Konten anlegen, die vorhandenen und der Startplan reichen für den Betrieb. Die Regel-Auswahl zeigt sichtbare Ertrags- und Aufwandskonten. Ruhende Regeln sind markiert: "ruht, Konto ausgeblendet".
- `docs/design/demo/make_demo_db.py`: Konten über `ledger.create_account`, Buchungen über `ledger.set_counter_account`.

Abschluss-Kontrolle: `grep -rn "category" src/kassivo` trifft nur noch `categories.py` (Modulname), `categories_routes.py` und Vorlagen mit dem Wort "Kategorie" in Texten sowie Formularfeldern `category`/`category_id`, außerdem `db.py` (eingefrorenes `SCHEMA` und Migration 4).

- [ ] Tests zuerst:
  - `test_schema_version.py`: `test_migrations_registered` auf 2, 3, 4; `test_fresh_db_schema_4`.
  - `test_categories.py` neu (gleiche Fälle wie bisher, Konten statt Kategorien): `test_rule_books_matching_tx`; `test_income_rule_ignores_outgoing`; `test_priority_order`; `test_overwrite_reverses_and_rebooks` (Storno sichtbar, genau eine gültige); `test_same_account_counts_zero`; `test_hidden_account_rule_sleeps`; `test_closed_period_skipped`; `test_add_rule_rejects_asset_and_hidden`.
  - `test_matching.py`: Erwartungen "category_id == Beiträge" werden zu "gültige Buchung auf dem `fees`-Konto". Neu: `test_assign_member_books_fees_once` (zweimal zuordnen: eine Buchung); `test_assign_keeps_other_booking` (Umsatz auf "Zinsen" gebucht, Mitglied zugeordnet: Buchung bleibt "Zinsen"); `test_unmatched_excludes_non_fee_bookings`; `test_unmatched_includes_unbooked_and_fees`; `test_no_member_still_skipped`.
  - `test_import_atomar.py`: Fehler in `ledger.post` (`monkeypatch`, als weiteres `where`) hinterlässt keinen Umsatz, keine Buchung, kein Bankkonto, keine Periode. Neu: `test_import_new_iban_links_bank_account`; `test_import_creates_period_for_new_year`; `test_import_into_closed_period_warns_not_books`; `test_reimport_creates_no_second_posting`.
  - `test_reports.py`: `by_category` nach Konten; `test_split_groups_by_sign`; `test_unbooked_is_ohne_kategorie`; `test_report_equal_before_and_after_migration` (gleiche Daten über Schema 3 mit Kategorien und über Schema 4: Summen je Gruppe gleich, "Sonstiges" gleich der Summe aus "Sonstige Einnahmen" und "Sonstige Ausgaben").
  - `test_web_transactions.py`: Kategorie setzen bucht, gleiche Auswahl noch einmal erzeugt keine Buchung (doppelt gesendetes Formular), "Ohne Kategorie" storniert, unbekanntes oder ausgeblendetes Konto ergibt 422 mit deutschem Text, Filter `none` und `<id>`, ausgeblendetes Gegenkonto erscheint als Option, aufgeteilt zeigt Text, fremde Origin 403.
  - `test_restore*.py`: Rundreise Export und Einspielen mit Schema 4 inklusive Triggern; Sicherung mit Schema 1, 2 und 3 wird eingespielt und umgestellt (`test_restore_old_schema_is_migrated_without_extra_backup` deckt 3 ab, neu 1 und 2); `test_ledger_inconsistent_backup_rejected` (Zeile per rohem SQL mit Triggern aus der Datei entfernt, Trigger neu angelegt: `E_LEDGER`); `test_posting_row_real_cents_rejected` (`E_MONEY`); `MONEY_COLUMNS`-Tests auf neue Tabellen.
  - `test_security_polish.py`, `test_web_start.py`, `test_db.py`, `test_ingest.py`, `test_dues.py`: Stellen mit `category_id` auf `ledger`-Abfragen umstellen, Aussage unverändert.
- [ ] Rot sehen, umsetzen, grün. Abschluss-Kontrolle per `grep`. Volle Suite grün, auch `node --test tests/js`. Commit `feat: Schema 4, das Journal ersetzt die Kategorien` (Refs #57).
- [ ] `kassivo-reviewer` (opus) über Task 1 bis 5 und `/security-review`, Befunde beheben, bevor Task 6 beginnt.

### Task 6: Seite Kontenplan (Konten anlegen, ändern, ausblenden, löschen)

**Files:** Create `src/kassivo/web/accounts_routes.py`, `web/templates/accounts.html`; Modify `web/app.py` (Router), `web/templates/_nav.html`, `web/templates/categories.html` (Hinweis wird Link auf `/accounts`); Test `tests/test_web_accounts.py`, `tests/test_web_layout.py`.

**Interfaces:** Consumes Task 3 (`ledger.*account*`).
- Vorgabe 2026-11-28: gegliedert nach Art und darin nach Kontogruppe (E12); Nummer im Formular optional (E13); Gruppen anlegen, umbenennen, löschen (nur leer).
- `GET /accounts`: Tabelle je Art (Aktiv, Passiv, Ertrag, Aufwand) mit Nummer, Name, Status ("ausgeblendet", "von KASSIVO benötigt", "Bankkonto …1234" maskiert) und Zahl der Verwendungen. Ausgeblendete Konten stehen ausgegraut am Ende ihrer Gruppe. Formular "Konto anlegen" (Nummer, Name, Art) mit Vorschlag `next_free_number` je Art (3500, 4500, 1001, 2001 als Startwerte).
- `POST /accounts`: Felder `number`, `name`, `kind`. Fehler ergeben 422 mit Formularwerten und deutscher Meldung. Erfolg: 303 auf `/accounts?ok=created`.
- `POST /accounts/{account_id:int}`: Felder `number`, `name`. Gleiche Prüfung.
- `POST /accounts/{account_id:int}/hidden`: Feld `hidden` = `1` oder `0`.
- `POST /accounts/{account_id:int}/delete`: Feld `confirm=yes` Pflicht. Ohne Bestätigung oder bei benutztem Konto: 422 "Das Konto ist benutzt und lässt sich nur ausblenden."
- Unbekannte oder veraltete `account_id` (doppelt gesendet nach Löschen): 404 mit deutschem Text, kein 500.
- Navigation: Bereich "Buchhaltung" bekommt die Gruppe "Konten" mit "Kontenplan" (`/accounts`). Pfadanfänge des Bereichs um `/accounts` ergänzen.
- Texte: Ein Satz oben: "Die Konten ersetzen ab dieser Version die Kategorien. Ertragskonten erscheinen in der Auswahl „Kategorie“ für Eingänge, Aufwandskonten für Ausgänge." Und: "KASSIVO folgt keinem amtlichen Kontenrahmen."

- [ ] Tests zuerst: Seite listet Startplan nach Nummer; Anlegen ergibt neues Konto in der Kategorie-Auswahl auf `/transactions`; doppelte Nummer und doppelter Name (Groß/Klein) ergeben 422; Umbenennen ändert die Anzeige in Buchungsliste und Report; Ausblenden entfernt es aus der Auswahl, eine bestehende Buchung bleibt sichtbar; Konto mit Rolle oder Bankkonto lässt sich nicht ausblenden oder löschen (422); Löschen eines unbenutzten Kontos geht nur mit `confirm=yes`; veraltete ID ergibt 404 mit deutschem Text; volle IBAN steht nie im HTML; fremde Origin ergibt 403; Navigationstest um "Kontenplan" ergänzt.
- [ ] Umsetzen, grün, volle Suite. Commit `feat: Seite Kontenplan` (Refs #57).

### Task 7: Buchungsperioden und aktive Periode

**Files:** Create `src/kassivo/web/periods_routes.py`, `web/templates/periods.html`; Modify `web/app.py`, `_nav.html`, `web/templates/base.html` (Anzeige der aktiven Periode im Bereich Buchhaltung), `web/common.py` (Kontext `active_period`); Test `tests/test_web_periods.py`.

**Interfaces:** Consumes Task 3 (`periods.*`).
- `GET /periods`: Liste mit Name, Von, Bis, Status ("offen", "abgeschlossen"), Zahl der Buchungen. Die aktive Periode ist markiert ("aktiv", Marke mit Wort). Formular "Periode anlegen" mit Vorschlag: nächster Zeitraum gleicher Länge nach der letzten Periode, Name "Jahr JJJJ" oder "JJJJ/JJ" bei Vereinsjahr.
- `POST /periods`: Felder `name`, `start`, `end` (TT.MM.JJJJ oder JJJJ-MM-TT, wie `common.parse_date`).
- `POST /periods/{period_id:int}`: Name und Grenzen ändern (offen und ohne herausfallende Buchungen).
- `POST /periods/{period_id:int}/delete`: `confirm=yes`, nur ohne Buchungen.
- `POST /periods/active`: Feld `period_id`. Speichert `settings.active_period`. 303 zurück auf `next` (`safe_next`).
- Auf den Seiten des Bereichs Buchhaltung steht oben "Periode: Jahr 2026" mit Link auf `/periods`.
- Wirkung der aktiven Periode in diesem Schritt: Kontostände (Task 8) und der Vorschlag für den Eröffnungsbestand. `/transactions` bekommt den zusätzlichen Filter `period=<id>` (Buchungsdatum in den Grenzen); ohne Filter bleibt das laufende Jahr wie bisher. Die Umstellung aller Listen auf die aktive Periode ist eine offene Frage (unten).
- Abschließen und Übertrag sind hier **nicht** enthalten (Folgearbeit, siehe Verschiebbar). Die Seite zeigt bei jeder offenen Periode den Hinweis "Abschließen kommt mit einer späteren Version."

- [ ] Tests zuerst: Startzustand nach Migration listet die Jahre; Anlegen Vereinsjahr 01.07.2026 bis 30.06.2027 neben "Jahr 2026" ergibt 422 "überschneiden"; Ende vor Start ergibt 422; Grenzen ändern, sodass Buchungen herausfallen, ergibt 422; Löschen mit Buchungen ergibt 422; aktiv setzen wird gespeichert und erscheint in der Kopfzeile; gelöschte aktive Periode fällt auf die Periode mit heute zurück; Filter `period=<id>` auf `/transactions` zeigt nur Umsätze in den Grenzen, ungültige ID ergibt 422; fremde Origin ergibt 403; veraltete ID ergibt 404.
- [ ] Umsetzen, grün, volle Suite. Commit `feat: Buchungsperioden mit aktiver Periode` (Refs #57).

### Task 8: Eröffnungsbestand und Kontostände der Geldkonten

**Files:** Create `src/kassivo/web/balances_routes.py`, `web/templates/balances.html`; Modify `web/app.py`, `_nav.html`, `src/kassivo/overview.py`, `web/templates/overview.html`; Test `tests/test_web_balances.py`, `tests/test_overview.py` (bzw. bestehende Start-Tests `tests/test_web_start.py`).

**Interfaces:** Consumes Task 3 (`ledger.set_opening`, `get_opening`, `money_accounts`), Task 7 (`periods.get_active`).
- `GET /balances` (Navigation "Kontostände" in der Gruppe "Konten"): je sichtbarem Aktivkonto (Bank, Barkasse, weitere) Eröffnungsbestand mit Datum, gebuchter Stand am Ende der aktiven Periode (bei laufender Periode zusätzlich "heute"). Bei Bankkonten außerdem "laut Umsätzen" und der Hinweis "{n} Umsätze noch nicht verbucht (Unterschied 12,34 €)" mit Link auf `/transactions?category=none`. Unterschied 0: "stimmt".
- `POST /balances/{account_id:int}/opening`: Felder `amount` (deutsches Format, darf negativ sein: `-12,50`; `money.parse_cents` mit Vorzeichen, Grenze ±100 000 000,00) und `day` (Vorschlag: Start der ersten Periode). Leerer Betrag oder 0 entfernt den Eröffnungsbestand (Storno). Ruft `ledger.set_opening`, Commit, 303. Fehler (nicht Aktiv, ausgeblendet, Periode abgeschlossen, Format): 422.
- `overview.overview()` bekommt den Schlüssel `money`: Summe der gebuchten Stände und Summe "laut Umsätzen" aller Geldkonten zum heutigen Tag sowie die Zahl unverbuchter Umsätze, aus `ledger.money_accounts` (gleiche Funktion wie die Zielseite, Regel aus `overview.py`). Die Startseite zeigt eine Kachel "Kontostand" mit Link auf `/balances`.
- Beträge nur als Cent-Integer. Anzeige über den vorhandenen Filter `eur`. Negative Stände mit Minus und Wort ("überzogen"), nicht nur farbig.

- [ ] Tests zuerst: ohne Eröffnungsbestand zeigt die Seite 0,00 € und einen Hinweis "Eröffnungsbestand eintragen"; Eingabe 1.234,56 ergibt Buchung Soll Bank, Haben Vereinsvermögen über 123 456; Eingabe -50,00 ergibt Haben Bank; zweimal gleicher Wert erzeugt keine zweite Buchung; Änderung ergibt Storno und neue Buchung, gebuchter Stand stimmt; leer entfernt; Barkasse ohne Umsätze zeigt nur den gebuchten Stand; Bankkonto mit unverbuchtem Umsatz zeigt Unterschied und Anzahl; Format "12.5x" ergibt 422; Datum außerhalb jeder Periode legt keine Periode an und ergibt 422 "keine offene Buchungsperiode" (Eröffnung nur in vorhandenen Perioden); Startseite zeigt Kachel mit derselben Summe wie `/balances`; fremde Origin ergibt 403.
- [ ] Umsetzen, grün, volle Suite. Commit `feat: Eröffnungsbestand und Kontostände` (Refs #57).

### Task 9: Kostenstellen auf der Seite Kontenplan (verschiebbar)

**Files:** Modify `web/accounts_routes.py`, `web/templates/accounts.html`; Test `tests/test_web_accounts.py`.

**Interfaces:** Consumes Task 3 (`ledger.*cost_center*`).
- Abschnitt "Kostenstellen" auf `/accounts`: Liste mit Name, Status, Verwendungen. Routen `POST /cost-centers`, `POST /cost-centers/{cc_id:int}` (umbenennen), `POST /cost-centers/{cc_id:int}/hidden`, `POST /cost-centers/{cc_id:int}/delete` (`confirm=yes`, nur unbenutzt).
- Hinweis: "Kostenstellen wählen Sie beim Verbuchen (ab der nächsten Version)." Bis Schritt 3 lassen sie sich anlegen, aber noch nicht an Buchungen setzen. Deshalb ist dieser Task verschiebbar: Das Datenmodell ist mit Task 3 fertig, die Oberfläche kann mit Schritt 3 kommen.

- [ ] Tests zuerst: anlegen, doppelter Name (Groß/Klein) ergibt 422, umbenennen, ausblenden, löschen nur unbenutzt und mit Bestätigung, veraltete ID 404, fremde Origin 403.
- [ ] Umsetzen, grün, volle Suite. Commit `feat: Kostenstellen pflegen` (Refs #57).

### Task 10: Doku, Wiki, Update-Rückweg, Abschluss

**Files:** Modify `README.md`, Wiki-Vorlagen `kategorien.html` (wird "Konten und Regeln"), `begriffe.html` (Konto, Kostenstelle, Buchung, Storno, Periode, Eröffnungsbestand, Soll und Haben in einem Satz), `start.html`, `update.html` (Abschnitt "Zurück auf eine ältere Version nach der Umstellung auf Schema 4"), `backup.html` (Sicherungen aus älteren Versionen werden umgestellt), `reports.html`, `grenzen.html` (kein amtlicher Kontenrahmen, keine steuerliche Beratung, nicht GoBD-geprüft); neue Wiki-Seiten `kontenplan.html`, `perioden.html`, `kontostand.html` mit Einträgen in `wiki_routes.py`; Tests `tests/test_wiki_content.py`, `tests/test_readme.py`, `tests/test_web_wiki.py`.

- Wiki "Update", Rückweg: "Beim ersten Start legt KASSIVO eine Sicherung an und stellt auf Schema 4 um. Wollen Sie zurück auf 1.4.0: alte Version wiederherstellen (wie im Abschnitt Wiederherstellung von Hand), dann unter Backup die Sicherung von vor der Umstellung einspielen. Was Sie danach erfasst haben, ist dann nicht mehr da."
- Release-Notizen werden erst beim Release geschrieben (Entwurf der Punkte im PR-Text).
- [ ] Tests zuerst (Wiki nennt Kontenplan, Perioden, Eröffnungsbestand, Rückweg; README nennt Schema 4), umsetzen, grün.
- [ ] `superpowers:verification-before-completion`: volle Suite (`pytest`, `node --test tests/js`), Migration einer erfundenen großen Datenbank von Hand per Testskript in `tmp_path` (nie die echte), App mit frischer Datenbank starten und alle neuen Seiten aufrufen.
- [ ] Abschluss-Review `kassivo-reviewer` (opus) über den ganzen Branch und `/security-review`. Befunde beheben.
- [ ] Commit `docs: Kontenplan, Perioden und Kontostand im Wiki` (Refs #57), Push, PR auf `main` mit Verweis auf #57.

---

## Reihenfolge, Frist und Verschiebbares

Frist: KASSIVO muss bis 31.12.2026 produktiv nutzbar sein (Webling gekündigt). Die Reihenfolge der Tasks liefert früh Benutzbares:

| Nach Task | Stand für den Nutzer |
|---|---|
| 1 bis 4 | Nichts Sichtbares; App unverändert auf Schema 3, alles abgesichert. |
| 5 | App auf Schema 4, benutzbar wie heute. Kategorie-Auswahl bucht ins Journal. Eingespielte Sicherungen aus 1.x werden umgestellt. |
| 6 | Eigene Konten anlegen (ersetzt "Kategorie anlegen"). **Mindeststand für ein Release.** |
| 7 | Perioden wie bei Webling, aktive Periode. |
| 8 | Eröffnungsbestand, Kontostand je Bank- und Kassenkonto, Kachel auf der Startseite. **Ziel-Release von Schritt 2.** |
| 9 | Kostenstellen pflegen. **Verschiebbar** in Schritt 3. |
| 10 | Doku und Abschluss (nicht verschiebbar, muss vor Release). |

Verschiebbar ohne Schemaänderung (Datenmodell ist in Schema 4 vorgesehen):
- Abschluss einer Periode mit Übertrag ins Vereinsvermögen und Anfangsständen der Folgeperiode (`period.status`, `closed_at`, Quellen `closing`/`carryover`, Sperre `posting_in_open_period`). Muss vor dem ersten Jahresabschluss kommen, also spätestens Anfang 2027 für das Jahr 2026.
- Kostenstellen-Oberfläche (Task 9), Belegnummern, Bilanz und Erfolgsrechnung (Schritt 5).

Nicht verschiebbar für "produktiv bis 31.12.": Task 1 bis 8 und 10. Danach zählt Schritt 3 (Verbuchen, Buchen von Hand für Barkasse). Ohne Schritt 3 lassen sich Barein- und -ausgaben nicht erfassen; das ist bei der Planung von Schritt 3 zu berücksichtigen.

## Offene Fragen an den Nutzer

1. **Release-Zuschnitt.** Schritt 2 allein als 1.5.0 (nach Task 8 und 10) oder zusammen mit Schritt 3? Empfehlung: 1.5.0 nach Schritt 2. Dann läuft die Umstellung auf Schema 4 früh und mit Puffer vor dem 31.12., und Kontostände sind sichtbar. Schritt 3 folgt als 1.6.0.
2. **Kontonummern des Startplans** (Tabelle in Task 3). Empfehlung: so lassen. Sie sind frei änderbar, und Webling-Nummern lassen sich nach der Demo-Analyse (`docs/design/referenz/webling-demo/`) übernehmen, bevor Task 3 eingefroren wird. **Bitte vor Task 3 entscheiden**, weil der Startplan Teil der Migration ist.
3. **Namen für Kategorien "für beides".** Vorschlag "Name (Einnahmen)" und "Name (Ausgaben)", für "Sonstiges" fest "Sonstige Einnahmen" und "Sonstige Ausgaben". Empfehlung: so.
4. **Aktive Periode für alle Listen.** Soll `/transactions` und `/reports` ohne Filter die aktive Periode statt des Kalenderjahres zeigen (wie Webling)? Empfehlung: ja, aber in Schritt 3 zusammen mit der neuen Umsatzliste; in Schritt 2 nur Kontostände und Filter.
5. **Perioden für alte Jahre.** Die Migration legt "Jahr JJJJ" ab dem ersten Umsatzjahr an. Wer ein Vereinsjahr (z. B. 1.7. bis 30.6.) hat, kann alte Perioden nicht mehr verschieben, sobald Buchungen darin liegen. Empfehlung: Kalenderjahre lassen; neue Perioden ab 2027 frei wählen. Falls Vereinsjahr gewünscht ist: vor der Migration sagen, dann wird die Periodenlänge eine Einstellung.
6. **Eröffnungsbestand vorbelegen.** CAMT-Dateien enthalten einen Anfangssaldo. Soll KASSIVO beim ersten Import den Anfangssaldo der ältesten Datei als Eröffnungsbestand vorschlagen? Empfehlung: ja, als Vorschlag auf `/balances`, nicht automatisch gebucht (Folgearbeit; heute wird der Saldo nicht gespeichert).
7. **Storno-Datum.** Das Storno trägt das Datum der Originalbuchung (E4). Webling ändert Buchungen direkt. Empfehlung: so lassen; mit dem Abschluss ist eine abgeschlossene Periode ohnehin gesperrt.
8. **Mitgliedszahlung ohne Kategorie.** Laut Spec wird sie auf "Beiträge" verbucht. Im Report wandert sie damit von "Ohne Kategorie" zu "Beiträge". Empfehlung: so (Spec), in den Release-Notizen erwähnen.
