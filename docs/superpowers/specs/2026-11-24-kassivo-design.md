# KASSIVO: Design

Stand: 2026-11-24. Status: Entwurf, wartet auf Freigabe.

## Zweck

KASSIVO ist ein lokales Kassensystem für eine Gruppenkasse (Verein, Mannschaft). Der Kassenwart prüft damit monatlich, ob alle Mitglieder ihren Beitrag gezahlt haben, ordnet alle Ein- und Ausgänge Kategorien zu und wertet das Jahr in Diagrammen aus. Vorbild ist Webling (https://www.webling.eu/), aber zugeschnitten auf einen einzelnen Nutzer und eine Kasse.

Warum: Der Nutzer will auf einen Blick sehen, wer bezahlt hat, wer offen ist, was noch kommt und wohin das Geld geht, ohne Tabellen von Hand zu pflegen.

## Nutzer und Rahmen

- Ein Nutzer (der Kassenwart), ein Konto, ein PC. Kein Login, keine Rollen, keine Mandanten.
- Läuft nur lokal. Eine EXE ist kein Ziel, die Technik soll sie aber nicht ausschließen.
- Das Gruppenkonto ist ein Treuhandkonto bei der Sparkasse. Es wird voraussichtlich Ende 2026 auf die C24 Bank umgestellt.

## Ziele

1. **Mitglieder verwalten:** Anlegen, ändern, Name, Adresse und weitere Stammdaten, Mitgliedsart Aktiv oder Passiv.
2. **Beitragsprüfung:** Pro Mitglied und Monat sehen, ob der Beitrag eingegangen ist (bezahlt, offen, überfällig).
3. **Zahlungshistorie:** Pro Mitglied sehen, wann wer wie viel gezahlt hat.
4. **Zuordnung:** Eingänge werden automatisch einem Mitglied zugeordnet. Was nicht passt, landet in einer Liste "Unklar" zur manuellen Zuordnung.
5. **Kategorien:** Alle Buchungen (Ein- und Ausgänge) werden Kategorien zugeordnet, auch über feste Regeln (Filter).
6. **Prognose:** Anzeigen, was bis Monatsende oder bis zu einem gewählten Datum noch kommen sollte.
7. **Reports:** Jahresübersicht Einnahmen und Ausgaben mit Torten- und Balkendiagrammen.
8. **Kontoumsätze importieren** aus einer Exportdatei des Online-Bankings.

## Nicht-Ziele

- Keine direkte Bankabfrage (FinTS, finAPI) in der ersten Version.
- Keine Zahlungsauslösung, kein Schreibzugriff auf das Konto.
- Keine Mehrbenutzer-Funktion, kein Hosting im Internet.
- Keine EXE-Paketierung in der ersten Version.

## Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- Falsche oder doppelte Beträge. Geldzahlen müssen exakt stimmen.
- Alte Reports ändern sich, wenn später ein Beitrag angepasst wird.
- Bankzugangsdaten oder Kontodaten liegen offen im Repo oder in Logs.

## Constraints

- Beträge sind ganze Zahlen in Cent, nie Fließkomma.
- Buchungen werden nie gelöscht oder überschrieben. Korrekturen sind Gegenbuchungen.
- Beiträge und Mitgliedsarten gelten mit Gültigkeitsdatum. Vergangene Monate rechnen mit dem damals gültigen Wert.
- Personenbezogene Daten (Name, Adresse, IBAN) bleiben lokal. Die Datenbankdatei steht in `.gitignore`.
- Die Bank wechselt Ende 2026. Der Import muss pro Bank austauschbar sein.

## Ansätze für die Bankdaten (geprüft)

| Ansatz | Vorteil | Nachteil | Entscheidung |
|---|---|---|---|
| **Datei-Import mit Bank-Adaptern** | Bankunabhängig, stabil, ohne Freigaben | Monatlicher Export und Upload von Hand | **Gewählt** |
| FinTS-Direktabfrage | Bequem | Nur Sparkasse, bei C24 laut Hersteller nicht vorgesehen, 90-Tage-TAN, Registrierung offen | Später prüfen |
| PSD2-Anbieter (finAPI) | Bankübergreifend, bei C24 der einzige Direktweg | Zugang für Privatnutzer, Kosten und Datenweitergabe ungeklärt | Später prüfen |

Der Nutzer hat den Datei-Import gewählt. Die Adapter-Schicht bleibt so offen, dass FinTS oder finAPI später als weiterer Adapter hinzukommen können.

## Entscheidungen des Nutzers

- Zwei Mitgliedsarten: Aktiv (aktuell 17 €/Monat) und Passiv (aktuell 10 €/Monat).
- Beträge müssen später anpassbar sein.
- Speicherung lokal.
- Start mit Datei-Import.

## Meine Annahmen (vom Nutzer nicht bestätigt, bitte prüfen)

- **Technik:** Python, SQLite als Datei, lokale Web-Oberfläche im Browser (FastAPI mit Jinja-Seiten), Diagramme mit Chart.js. Gründe: ein Prozess ohne Server, leicht zu sichern, später als EXE paketierbar. Das ist mein Vorschlag, nicht vom Nutzer entschieden.
- **Zahlweise:** Alle Mitglieder zahlen monatlich. Mehrmonatszahlungen und Rückstände verteilt der Nutzer von Hand auf Monate.
- **Mitgliederwechsel:** Wechsel zwischen Aktiv und Passiv gilt ab einem gewählten Monat.
- **Zuordnung Mitglied:** Regeln nach Name im Verwendungszweck, IBAN des Absenders oder Referenz. Nichts erkannt: Liste "Unklar".
- **Kategorien:** Eigene, frei anlegbare Kategorien. Feste Regeln nach Text, Absender oder Betrag.
- **Format des Imports:** Offen, bis eine Beispieldatei der Sparkasse vorliegt (CSV oder CAMT).

## Datenmodell (grob)

- `member`: Name, Adresse, Kontakt, IBAN (optional), Eintritts- und Austrittsdatum.
- `membership_type`: Aktiv, Passiv.
- `fee_rate`: Mitgliedsart, Betrag in Cent, gültig ab (Monat).
- `member_type_history`: Mitglied, Mitgliedsart, gültig ab (Monat).
- `transaction`: Datum, Betrag in Cent, Absender/Empfänger, IBAN, Verwendungszweck, Bank-Referenz, Hash, Kategorie, Mitglied (optional), Quelle (Import-Datei).
- `category` und `category_rule`: Name, Regel (Text, IBAN, Betrag), Priorität.
- `allocation`: Verteilung einer Buchung auf Monate eines Mitglieds (für Sammelzahlungen und Rückstände).
- `import_batch`: Datei, Bank, Zeitpunkt, Anzahl neu und doppelt.

## Bausteine

1. **Import:** Bank-Adapter (Sparkasse zuerst, C24 danach) wandelt Datei in einheitliche Buchungen. Doppelte werden über Bank-Referenz oder Hash erkannt.
2. **Zuordnung:** Regeln für Mitglied und Kategorie. Ergebnisse sind änderbar, manuelle Zuordnung hat Vorrang.
3. **Mitglieder:** Verwaltung mit Beitragsstufen und Verlauf.
4. **Beitragsprüfung und Prognose:** Erwartet (Beitrag × Monate) gegen eingegangen (Zuordnung). Daraus Bezahlt, Offen, Überfällig und die Vorschau bis zum gewählten Datum.
5. **Reports:** Jahresauswertung nach Kategorie, Monat und Mitglied mit Torten- und Balkendiagrammen.

## Beitragsregeln (vom Nutzer bestätigt, 2026-11-24)

- Der Beitrag ist zu Monatsbeginn fällig und darf im Lauf des ganzen Monats eingehen. Mit Monatsende gilt ein nicht bezahlter Beitrag als überfällig.
- Ein neues Mitglied zahlt ab dem 1. des folgenden Monats nach dem Eintritt.
- Tritt ein Mitglied mitten im Monat aus, ist der Austrittsmonat noch voll zu zahlen.
- Zu wenig gezahlt: Der Monat gilt als offen bzw. teilbezahlt.
- Zu viel gezahlt: Der Mehrbetrag kann auf Folgemonate angerechnet werden oder versäumte Monate ausgleichen. Das Verteilen bestätigt der Nutzer.
- Jedes Mitglied zahlt für sich. Zahlt eine andere Person (z. B. die Mutter), hängt der Nutzer die Überweisung von Hand an das Mitglied.
- KASSIVO lernt: Ordnet der Nutzer eine Zahlung einmal zu, wird dieselbe Absender-IBAN künftig automatisch diesem Mitglied zugeordnet.
- Die Mitgliederliste legt der Nutzer neu an. Ein Import von Altdaten ist nicht nötig.
- Die Beitragsprüfung kann rückwirkend laufen. Der Nutzer wählt den Startmonat.
- Der jährliche Beitrag an den Hauptverein hat kein festes Datum und wird zunächst von Hand als Ausgabe gebucht.
- Technik (Python, lokale Browser-Oberfläche oder Desktop-Fenster) überlässt der Nutzer dem Builder nach Best Practice.

## Importformat

Das Sparkasse-Online-Banking bietet u. a. CSV-CAMT V2/V8, CSV-MT940, XML CAMT V2/V8 (gebuchte Umsätze) und Text MT940. **Gewählt (mein Vorschlag): XML CAMT V8, gebuchte Umsätze.** Gründe: ein genormtes Format mit eindeutiger Bank-Referenz, Absender-IBAN und Verwendungszweck in getrennten Feldern, kein Raten bei Spalten. Das hilft auch beim späteren C24-Adapter, falls C24 CAMT anbietet (ungeprüft). Fallback: CSV-CAMT V8.

**Befund an der echten Sparkasse-Exportdatei** (ein ZIP, nur lokal, nicht im Repo; Zeitraum 2024-12-30 bis 2026-11-04):
- Das ZIP enthält 140 XML-Dateien, **eine pro Buchungstag** (z. B. `2025.01.07.xml`). Der Import muss ein ZIP oder einen Ordner mit mehreren Dateien annehmen.
- Format ist `camt.052.001.08`. Pro Datei steht ein Konto (`Acct`) mit IBAN und Währung EUR.
- Jede Datei hat Anfangs- und Schlusssaldo (`OPBD`, `CLBD`). Damit kann KASSIVO beim Import prüfen, ob die Summe der Buchungen zum Saldo passt.
- Jede Buchung (`Ntry`) hat Betrag, Soll/Haben (`CdtDbtInd`: `DBIT` oder `CRDT`), Buchungs- und Wertstellungsdatum, Bankreferenz (`AcctSvcrRef`) und Gegenpartei (Name und IBAN als Zahler `Dbtr` oder Empfänger `Cdtr`).
- Die Datei enthält echte Namen und IBANs. Sie darf nie ins Repo. Die `.gitignore` sperrt `*.zip`, `*.xml`, `*camt052*`, `*camt053*`, `*camt52*`, `*camt53*`, `*CAMT52*`, `*CAMT53*`, `*MT940*`, `*.csv`, `*.sta` (jeweils auch in Großschreibung), `samples/` und `exports/`.

## Weitere Annahmen nach dem Builder-Check (meine Entscheidungen, bitte prüfen)

- **Reports werden berechnet, nicht gespeichert.** Stabil bleiben alte Jahre durch Beiträge und Mitgliedsarten mit Gültigkeitsdatum. Ändert der Nutzer eine Kategorie-Regel, wirkt sie nur auf neue Importe. Bestehende Buchungen ändern sich nur, wenn der Nutzer ausdrücklich "Regeln neu anwenden" auslöst.
- **Beiträge sind eine feste Kategorie "Beiträge".** Eine Buchung hat in Version 1 genau eine Kategorie, keine Aufteilung auf mehrere Kategorien.
- **Konto:** `transaction` bekommt ein Feld `account` (z. B. Sparkasse, C24). Doppelte werden pro Konto erkannt. Alte und neue Buchungen laufen in einer gemeinsamen Kasse.
- **Prognose in Version 1:** nur offene Mitgliedsbeiträge. Wiederkehrende Ausgaben (Miete, Versicherung) sind ein späterer Ausbau.
- **Technik** (Python, FastAPI, SQLite, Chart.js) bleibt mein Vorschlag, bis der Nutzer ihn bestätigt.
- Backup-Funktion, Oberfläche, Währung (nur EUR) und Sprache (Deutsch) entscheidet der Builder.

## Offen (liegt beim Builder)

- Aufbau und Aussehen der Oberfläche und des Dashboards.
- Genaues Format des Sparkasse-Exports, sobald eine Beispieldatei vorliegt.
- Aufteilung in Meilensteine (Vorschlag: Mitglieder, Import, Zuordnung, Beitragsprüfung, Reports).
- Sicherung der Datenbankdatei (Backup-Funktion).

## Sicherheit

- Der Import ist lesend. KASSIVO speichert keine Bankzugangsdaten.
- Die Datenbankdatei und Beispieldateien mit echten Daten sind in `.gitignore`.
- Logs enthalten keine vollständigen IBANs.
