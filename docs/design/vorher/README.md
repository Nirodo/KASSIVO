# Vorher-Screenshots (aktuelles Design, Version 1.1.1)

Echte App mit erfundenen Demo-Daten (`docs/design/demo/make_demo_db.py`, 16 Mitglieder, 178 Buchungen 2026, Stand November). Je Seite drei Dateien: `<seite>-desktop.png` (1280 breit, hell), `<seite>-dark.png` (1280, dunkel), `<seite>-phone.png` (390 breit, hell). Zuschnitt auf Seitenende; `buchungen-*` ist bei 7000 px abgeschnitten (Seite hat ca. 29000 px).

Erfasst: dues, members, member-history (Tom Test), buchungen, buchungen-vorschlaege, unmatched, forecast, reports, settings-fees, categories, import, backup, wiki-start. Nicht erfasst: `/auto` (Seite existiert auf `main` noch nicht, nur im Branch `feat/auto-verteilung`).

## Beiträge und Historie (dues, member-history)
- `dues-desktop`: Die 12-Monats-Tabelle ist breiter als der 1000-px-Inhaltsbereich und schneidet ab Juli ab; Okt bis Dez nur per Seitwärtsscrollen erreichbar.
- `dues-phone`: Nur Jan und Feb sichtbar, Legende läuft aus dem Bild, Tabelle scrollt seitlich. Mitgliedsname scrollt mit weg.
- `dues-desktop`: Jede Zelle hat drei Zeilen (Status, Soll/Ist, Vorschlag). Zeilen mit Vorschlag sind höher, die Tabelle wirkt unruhig.
- `dues-desktop`: Vorschlag ist nur ein unterstrichener Balken und kursives "Vorschlag"; Felix und Paul (Vorauszahlung) erscheinen rot "Überfällig", obwohl bezahlt und nur unbestätigt.
- `member-history-desktop`: Monatsleiste schneidet bei Nov ab; Verteilung "(vorgeschlagen)" hängt als Textliste, 6 Vorschläge für Tom stapeln sich auf Mai.
- `dues-dark`/`dues-desktop`: Kein Spaltenkopf bleibt beim Scrollen stehen; Navigation ebenfalls nicht.

## Mitglieder (members)
- `members-phone`: Tabelle mit 5 Spalten bricht Namen und Datum um (2024-03-13 auf zwei Zeilen), IBAN abgeschnitten, Spalten rechts nicht erreichbar.
- `members-desktop`: Zwei Bedienelemente (Suche, "Mitglied anlegen") übereinander ohne Gewichtung; Status "Ausgetreten" nur als Text.

## Buchungen (buchungen, buchungen-vorschlaege)
- `buchungen-vorschlaege-desktop`: Eine Zeile ist rund 160 px hoch, weil Kategorie, Mitglied, "Absender merken" und Buttons untereinander stehen; 18 Vorschläge brauchen mehrere Bildschirme.
- `buchungen-desktop`: Je Zeile zwei Buttons ("Setzen", "Zuordnen") statt Direktänderung; viel Wiederholung gleicher Formulare.
- `buchungen-desktop`: Datum bricht in "2026-" / "11-05" um; ISO-Format statt gewohnter Schreibweise.
- `buchungen-desktop`: Filter ist ein Textfeld "Jahr (JJJJ oder all)"; Filter-Button steht in eigener Zeile.
- `buchungen-phone`: Tabelle läuft rechts aus dem Bild.

## Unklar, Kategorien, Einstellungen (unmatched, categories, settings-fees)
- `unmatched-desktop`: Nur zwei Einträge, aber zwei Formulare mit je Auswahl, Haken und Button pro Zeile; "Absender merken" ist vorausgewählt.
- `categories-desktop`: Zwei Formulare (Kategorie, Regel) stehen voll breit untereinander mit Eingabefeldern über 450 px Breite und viel Leerraum rechts; Regelliste und Formulare weit auseinander.
- `categories-desktop`: Gefährliche Aktion "Regeln anwenden" mit zwei Haken am Seitenende, leicht zu übersehen.

## Auswertungen (reports, forecast)
- `reports-desktop`: Kreisdiagramm "Einnahmen" ist fast einfarbig (Beiträge 99 %), Beschriftung der Kleinstwerte nicht lesbar; Legenden sehr klein.
- `reports-desktop`: Diagramme und zwei Tabellen mit denselben Zahlen untereinander; Seite 3 Bildschirme lang.
- `forecast-phone`: Datumsfeld und Button laufen rechts raus; Tabelle zeigt nur 2 von mehreren Spalten.
- `forecast-desktop`: Lange Liste ohne Gruppierung je Mitglied, jedes Mitglied mehrfach untereinander.

## Import, Backup, Wiki, Navigation (import, backup, wiki-start)
- Alle Seiten: Navigation mit 11 Einträgen auf einer Zeile; am Telefon umbrochen und rechts abgeschnitten (`dues-phone`, `forecast-phone`: "Kate..", "Einstellun..."), "Wiki" nicht erreichbar.
- Alle Seiten: "Hilfe zu dieser Seite" ist kleiner Link unter dem Titel; Seiten haben keine sichtbare Hauptaktion.
- `wiki-start-desktop`: Reiner Fließtext, keine Seitenleiste mit Gliederung.
- `dues-dark`: Dunkel folgt der Systemeinstellung; Vorschlagsstrich ist dort gelb, im Hellen dunkelbraun (uneinheitlich).
