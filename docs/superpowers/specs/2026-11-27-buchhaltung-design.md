# Design: Buchhaltung wie bei Webling (#54 bis #57)

Stand: 2026-11-27. Status: **freigegeben am 2026-11-27.**

## Ziel

KASSIVO bekommt eine doppelte Buchführung wie Webling, behält die eigenen Funktionen (CAMT-Import, automatische Beitragsverteilung, Prognose, Reports, Backup, Update, Wiki) und wird in der täglichen Arbeit einfacher:

- Zuordnungen werden zuverlässig gemerkt (#55).
- Beiträge lassen sich direkt in der Tabelle bestätigen (#56).
- Konten und Kostenstellen lassen sich selbst anlegen (#57).
- Der Bereich "Zahlungen" heißt "Buchhaltung" und ist wie bei Webling aufgebaut (#54).

Misslungen wäre es, wenn der Kassenwart für den normalen Beitragseingang Soll und Haben kennen müsste.

## Nicht-Ziele (vorerst)

Mehrwertsteuer, Budget, Sphären der Gemeinnützigkeit, Rechnungen als PDF oder E-Mail, SEPA-Lastschrift, mehrere Mandanten, Benutzer und Rechte.

## Ansätze

1. Konto und Kostenstelle als Einnahmen-Ausgaben-Rechnung. Wenig Aufwand, aber keine Bilanz. Verworfen: der Nutzer will es wie bei Webling.
2. **Doppelte Buchführung (gewählt).** Kontenplan, Buchungen mit Soll und Haben, Bilanz, Erfolgsrechnung, Perioden. Deutlich mehr Aufwand.

Innerhalb von 2:
- **2a (gewählt, mein Vorschlag):** Das Journal ersetzt die Kategorien. Es gibt nur eine Wahrheit.
- 2b: Das Journal wird zusätzlich aus den Kategorien abgeleitet. Weniger Umbau, aber zwei Stände, die auseinanderlaufen können. Verworfen.

## Begriffe und Daten

| Begriff | Bedeutung |
|---|---|
| Konto | Nummer, Name, Art: Aktiv, Passiv, Ertrag, Aufwand. Anlegen, umbenennen, ausblenden. Löschen nur ohne Buchungen. |
| Kostenstelle | Zweck oder Bereich (z. B. Sommerfest, Jugend). Freiwillig, je Buchungszeile auf Ertrags- und Aufwandskonten. |
| Bankumsatz | Importierte Zeile aus dem Kontoauszug. Wird nie geändert oder gelöscht. |
| Buchung | Datum, Text, Belegnummer, mindestens zwei Zeilen. Summe Soll = Summe Haben, in Cent als ganze Zahl. |
| Periode | Kalenderjahr, offen oder abgeschlossen. |

- Jedes Bankkonto (IBAN) hat ein Aktivkonto. Eine Barkasse ist ein weiteres Aktivkonto.
- "Verbuchen" erzeugt aus einem Bankumsatz eine Buchung: eine Seite ist das Bankkonto, die andere das gewählte Gegenkonto. Ein Umsatz hat höchstens eine gültige Buchung.
- Die Datenbank sichert Soll = Haben und "höchstens eine gültige Buchung je Umsatz" ab; die Prüfung liegt nicht nur in der Oberfläche.
- Aufteilen auf mehrere Gegenkonten ist möglich (Sammelzahlung).
- Von Hand buchen: Bareinnahmen, Barausgaben, Umbuchungen (enthält #1).

### Kontenplan beim Start

Ein kleiner Vereinsplan, den man frei ändern kann: Bank, Barkasse, Vereinsvermögen, Beiträge, Zinsen, Spenden, Sonstige Einnahmen, Hauptverein, Bankgebühren, Sonstige Ausgaben. Eigene Kontonummern sind möglich. KASSIVO behauptet nicht, einem amtlichen Kontenrahmen (SKR) zu entsprechen.

## Tägliche Bedienung

- Der Import verbucht selbst, was eindeutig ist: Beiträge bekannter Absender und alles, wofür etwas gemerkt ist.
- Der Rest steht als **Neu** in der Umsatzliste mit dem Knopf **Verbuchen**. Dort wählt man in Alltagssprache "Wofür?" (Gegenkonto), optional Kostenstelle und Mitglied. Soll und Haben ergeben sich selbst und stehen in der Buchungsliste.
- Status je Umsatz: Neu, Prüfen, Verbucht (farbige Marke mit Wort).

## Merken (#55)

- Gemerkt werden Gegenkonto, Kostenstelle und Mitglied.
- Schlüssel ist die IBAN der Gegenseite, ohne IBAN der Name, jeweils getrennt nach Eingang und Ausgang.
- **Die letzte Entscheidung gilt.** Eine Korrektur überschreibt das Gemerkte.
- "Zahlt für mehrere Mitglieder" ist eine bewusste Auswahl. Dann ordnet KASSIVO das Mitglied nicht selbst zu.
- "Merken" ist überall gleich vorausgewählt. Es gibt eine Seite "Gemerkte Zuordnungen" zum Ansehen, Ändern und Löschen (enthält #4).
- Die heutigen Kategorie-Regeln bleiben als Regeln mit Gegenkonto bestehen. Gemerktes geht vor Regeln.

## Beiträge

- Die Verteilung auf Beitragsmonate bleibt, wie sie ist. Sie hängt weiter am Bankumsatz und am Mitglied, auch an Bargeldzahlungen.
- **Mein Vorschlag:** Beiträge werden beim Geldeingang gebucht (Bank an Beiträge). Es gibt keine Rechnungen und keine Forderungsbuchungen. Offene Beiträge zeigt weiter das Beitragsraster. Webling arbeitet hier mit Rechnungen; das wäre ein späterer, eigener Ausbau.
- Raster (#56): Klick auf eine Zelle zeigt die Zahlung mit "Bestätigen" und "Ändern". "Alle Vorschläge bestätigen" zeigt vorher die Liste.

## Korrigieren

- **Mein Vorschlag:** In einer offenen Periode klickt man auf "Ändern" oder "Löschen". KASSIVO löscht dabei nichts, sondern storniert die alte Buchung und legt bei "Ändern" eine neue an. Stornos sind ausgeblendet und lassen sich einblenden. Webling ändert Buchungen direkt; die Abweichung hält die Projektregel "Buchungen unveränderlich" ein und macht die Kasse prüfbar.
- Eine abgeschlossene Periode ist gesperrt.

## Perioden und Berichte

- Eröffnungsbilanz: Anfangsstände von Bank und Barkasse für das erste Jahr; Gegenkonto ist Vereinsvermögen.
- Abschließen überträgt das Ergebnis ins Vereinsvermögen und legt die Anfangsstände des Folgejahres an. Vorher wird die Datenbank gesichert.
- Berichte: Bilanz, Erfolgsrechnung, Kontoblatt je Konto, Auswertung je Kostenstelle, Buchungsjournal. Ausgabe am Bildschirm, zum Drucken und als CSV.
- Jede Bankkontoseite vergleicht den gebuchten Stand mit der Summe der Umsätze und zeigt Abweichungen.

## Umstellung vorhandener Daten

- Schema 4, mit automatischer Sicherung davor und in einem Schritt.
- Kategorien werden zu Konten. "Beiträge" wird ein Ertragskonto. Eine Kategorie "für beides" wird zu je einem Ertrags- und Aufwandskonto.
- Umsätze mit Kategorie werden verbucht, ebenso Umsätze mit Mitglied (auf Beiträge). Alle anderen stehen als "Neu" da.
- Beitragsverteilungen, Bestätigungen und "kein Mitglied" bleiben unverändert.
- Gelernte IBANs werden übernommen. Als mehrdeutig gespeicherte stehen auf der Seite "Gemerkte Zuordnungen" und lassen sich neu festlegen.
- Prüfung: je Konto und Jahr ergeben die neuen Buchungen dieselben Summen wie vorher die Kategorien.

## Schritte

Jeder Schritt hat eigenes Issue, Branch, Tests und Review; die App bleibt benutzbar. Geld- und Migrationscode wird mit Opus geprüft.

1. **Merken reparieren und umbenennen** (#55 für Mitglieder, "Buchhaltung" aus #54).
2. **Kern:** Konten, Kostenstellen, Buchungen, Umstellung auf Schema 4, Kontenplan-Seite (#57).
3. **Verbuchen:** Umsatzliste mit Status, Verbuchen, Aufteilen, von Hand buchen, Merken für Konto und Kostenstelle. Die Kategorien verschwinden aus der Oberfläche.
4. **Beitragstabelle** zum Bestätigen (#56).
5. **Berichte, Eröffnungsbilanz, Perioden.**
6. **Webling-Aufbau:** Übersicht in Kacheln, Aktionen bei Zeilenauswahl, Mitglied mit Reitern (#54).

Davor erscheint **1.3.0** mit dem bereits Gemergten (neue Oberfläche Teil 1 und 2, Backup einspielen, Import, Update). Die Buchhaltung kommt in 1.4.0 bis 2.0.0; 2.0.0 ist der Stand mit Schritt 6.

## Dem Bauenden überlassen

Tabellennamen, Seitenaufbau im Einzelnen, Reihenfolge innerhalb eines Schritts.

## Risiken

- Großer Umbau an Geldcode. Gegenmittel: kleine Schritte, Summenvergleich bei der Umstellung, Sicherung davor.
- KASSIVO ersetzt keine steuerliche Beratung und ist nicht als GoBD-konform geprüft.
