# KASSIVO: Entwurf für eine übersichtlichere Oberfläche (Issue #26)

Das sind statische Entwürfe zum Anschauen und Auswählen. Es gibt keinen Anwendungscode, kein JavaScript und keine externen Ressourcen. Alle Namen, IBANs und Beträge sind erfunden.

**Einstieg:** `docs/design/index.html` im Browser öffnen (Doppelklick). Die Seite zeigt drei Richtungen mit Vorschaubildern und Links auf die lebendigen Seiten. Verkleinere das Browserfenster, um die Handy-Ansicht zu sehen. Hell und Dunkel folgen dem System.

Ziel laut Rückmeldung: Die alte Oberfläche ist unüberschaubar und zu voll. Der Alltag der Kassenwartin soll einfacher werden: wenige Bereiche, ein klarer Startpunkt, weniger Klicks, seltene Funktionen aus dem Weg, ruhig und lesbar, auch auf dem Handy.

## Gemeinsame Informationsarchitektur (alle Richtungen)

Fünf Bereiche statt elf Menüpunkte:

1. **Übersicht**: Startseite „Heute zu tun“ mit der Hauptaktion „Kontoauszug importieren“, kleinen Aufgaben mit Zahl und Direktlink (unklar, zu prüfen, überfällig), Monatsstatus, Kennzahlen, letztem Import.
2. **Mitglieder**: Liste mit Suche und Status-Wort (bezahlt, offen, teilweise, überfällig), Klick auf ein Mitglied öffnet das Profil.
3. **Zahlungen**: eine Liste mit Filtern (Alle, Eingänge, Ausgänge, Unklar, Prüfen, Automatisch verteilt), eine Zuordnung direkt in der Zeile, Sammelleiste für markierte Zeilen, Import-Knopf.
4. **Auswertung**: Beitragsraster, Prognose, Report.
5. **Einstellungen**: Beiträge und Prüfungsstart, Kategorien und Regeln, Backup, Update, Hilfe/Wiki.

## Die drei Richtungen im Vergleich

| | 1 „Ruhig“ | 2 „Kompakt“ | 3 „Aufgaben“ |
|---|---|---|---|
| Gefühl | weit, weich, Karten, große Schrift | Vereinssoftware mit Modulleiste, Seitenleiste, Kacheln, dichte Tabellen | geführter Ablauf, große Status-Kacheln, Stepper |
| Navigation | schlanke obere Leiste, auf dem Handy „Menü“ | blaue Modulleiste oben plus Seitenleiste mit Gruppen; auf dem Handy „Menü“ und aufklappbares Bereichsmenü | schmale Icon-Leiste links (Text beim Hover/Fokus), auf dem Handy untere Leiste |
| Startseite | Hauptaktion, Aufgabenkarte, Monatskarte, Zahlen | Kachelraster: Heute zu tun, Einnahmen/Ausgaben, Beiträge, Kassenstand, zwei Kuchen, Monatsbalken | Stepper „1 Import → 2 Unklar → 3 Prüfen → 4 Auswertung“ mit „Weiter mit …“-Balken |
| Liste | Zeilen mit Auswahlfeld, wenig Spalten | Tabelle mit Kopfzeile in Kapitälchen, Felder/Ansicht/Sortieren/Suchen | Zeilen wie 1, dicke Rahmen, große Ziele |
| Geeignet für | seltene Nutzung (ein paar Mal im Monat), Handy | tägliche Arbeit, viele Buchungen, Überblick über Zahlen | wer nicht nachdenken will, was als Nächstes dran ist |
| Nachteile | wenige Daten pro Bildschirm, mehr Scrollen | viel Oberfläche, mehr Pflege (Diagramme, Seitenleiste je Bereich) | Ablauf ist starr; wer „nur schnell nachschauen“ will, muss am Stepper vorbei |
| Aufwand im echten Programm | niedrig bis mittel (Layout und CSS, keine neue Logik) | hoch (Seitenleiste je Bereich, Diagramme als SVG vom Server, Spalten/Ansichten) | mittel (Stepper braucht die Zähler, sonst nur Layout) |

### Empfehlung

**Richtung 2 als Grundlage, mit der Startseite-Idee aus Richtung 3.**

- Richtung 2 passt am besten zu deinem Vorbild und bietet den schnellsten Zugriff, wenn du jeden Tag arbeitest. Die Seitenleiste gruppiert, was früher als elf Menüpunkte verstreut war. Offene Dinge stehen als Zahl in der Seitenleiste (Unklar 3, Prüfen 3).
- Der Aufwand ist höher, lässt sich aber in kleinen Schritten liefern (siehe Plan). Die Diagramme sind reines SVG und brauchen keine Bibliothek, das passt zur strengen CSP der echten App.
- Der „Heute zu tun“-Block steht in Richtung 2 bereits oben links. Den Stepper aus Richtung 3 kann man später als optionalen Streifen oben auf der Übersicht ergänzen, ohne die Navigation zu ändern.
- Richtung 1 ist die Wahl, wenn die App meistens auf dem Handy und nur selten genutzt wird. Dann wäre sie auch die einfachste Umsetzung.

## Gestaltungsprinzipien

- **Höchstens fünf Hauptpunkte** in der Navigation, alles Seltene wandert in Einstellungen.
- **Eine Hauptaktion pro Seite**: Übersicht „Kontoauszug importieren“, Zahlungen „Erfassen“ beziehungsweise Import, Prüfen „Bestätigen“.
- **Schrittweise Offenlegung**: Details zum Import, weitere Aktionen und seltene Einstellungen sind eingeklappt (`<details>`).
- **Wörter, nicht nur Farbe**: Jeder Status steht als Wort (bezahlt, offen, teilweise, überfällig, unklar, prüfen, automatisch). Im Beitragsraster steht in jeder Zelle ein Kürzel (bez., teil, offen, über, –) und die Legende nennt die Wörter. Beträge tragen ein Vorzeichen (+ und −), nicht nur Grün und Rot.
- **Touch-Ziele mindestens 44 px** in 1 und 3 überall. Richtung 2 nutzt am Desktop 36 px hohe Steuerelemente und wechselt unter 1024 px auf 44 px.
- **Kontrast mindestens AA** (4,5:1 für Text): Farben stammen aus der bestehenden `style.css` (Akzent `#0b5cad` auf Weiß 6,7:1) und haben in Richtung 2 gedämpfte Zusatzfarben (Blau der Leiste `#1a5ea8` mit weißer Schrift 6,5:1, Grün `#1d7a3a` mit Weiß 5,4:1). Im dunklen Modus werden hellere Töne mit dunkler Schrift genutzt.
- **Sichtbarer Tastaturfokus**: Alle interaktiven Elemente haben `:focus-visible` mit 3 px Rahmen. In der blauen Leiste ist der Rahmen weiß.
- **Ruhige Typografie**: Systemschrift, vier Größen, 4-Pixel-Raster für Abstände, Zahlen mit festen Ziffernbreiten.

## Angelehnte und bewusst nicht übernommene Muster (Webling)

Das Webling-Layout diente nur als Struktur-Vorbild (nach öffentlichem Text und deinen Screenshots). Logo, Maskottchen, Name, genaue Farben und Texte sind **nicht** übernommen. Die Screenshots liegen lokal in `docs/design/referenz/` und sind per `.gitignore` vom Commit ausgeschlossen.

Übernommen (hauptsächlich in Richtung 2, einzelne Ideen in 1 und 3):

- Modulleiste oben mit aktivem Bereich dunkler, Suche, Hilfe und Benutzer rechts.
- Seitenleiste mit Kontextblock („Verein“, „Beitragsjahr 2026“) und fett gruppierter Unternavigation; aktiver Punkt als gefüllte blaue Leiste.
- Seitenhintergrund hellgrau-blau mit weißen Karten und weichem Schatten.
- Aktionszeile: ein grüner Hauptknopf mit Pfeil („Erfassen“) und eine verbundene Gruppe weißer Knöpfe (Exportieren, Drucken, Versenden, Extras).
- Listenkarte mit „Felder“, „Ansicht“, „Sortieren nach“ und Suche; Tabelle mit kleinen Kapitälchen-Köpfen, Avatar mit Initialen, weichen Status-Pillen und Summenzeile.
- Dashboard-Karten: Einnahmen und Ausgaben als Balken (grün/rot mit Vorzeichen), bezahlt gegen offen, zwei Kuchen mit Legende, Monatsübersicht als Doppelbalken.
- Gespeicherte Ansichten und Sammelaktionen auf Listen, Schutz abgeschlossener Jahre (Beitragsjahr 2025 „abgeschlossen, schreibgeschützt“), Änderungsprotokoll im Profil, „Vorschlag gelernt aus früheren Buchungen“ bei der Prüfung.
- Das Prüfen-Panel mit drei Schritten in Richtung 3 erinnert an einen Buchungsassistenten.

Bewusst nicht übernommen:

- Sechs Module (Kalender, Material, Dokumente) und Mehrmandantenfähigkeit: KASSIVO hat einen Verein und fünf Bereiche.
- Farbige Symbole vor jedem Menüpunkt: ohne Symbole bleibt die Seitenleiste ruhiger. Richtung 3 nutzt dafür schlichte Linien-Icons.
- Die sehr breite Tabelle mit zehn Spalten: Zahlungen und Mitglieder zeigen bis zu sechs Spalten, der Rest steht im Profil.
- Der Probier-Knopf in der Leiste und mehrere gleichwertige grüne Knöpfe: pro Seite bleibt eine Hauptaktion.
- Budget-Ringe und Kontenplan: das ist Buchhaltung, die KASSIVO nicht macht.

## Alte Seite → Neuer Ort

| Alte Seite | Neuer Ort | Was passiert |
|---|---|---|
| `/dues` Beiträge | Auswertung → Beitragsraster (und Monatsleiste im Mitgliedsprofil) | umgezogen, als kompakte Jahresmatrix mit Wörtern |
| `/members` Mitglieder | Mitglieder | bleibt, schlanker (Name, Status, Beitrag, offen) |
| `/members/{id}/history` | Mitglieder → Profil (Zahlungshistorie) | zusammengelegt in die Profilseite |
| `/transactions` Buchungen | Zahlungen, Filter „Alle“ | umbenannt, eine Liste |
| `/transactions?proposed=1` | Zahlungen, Filter „Prüfen“ | wird Filter, Vorschlag mit „Bestätigen“ und „Ändern“ |
| `/unmatched` Unklar | Zahlungen, Filter „Unklar“ | wird Filter, Zuordnung direkt in der Zeile |
| `/auto` Automatisch verteilt | Zahlungen, Filter „Automatisch verteilt“ | wird Filter, Rückgängig bleibt in der Zeile |
| `/allocate` Zuordnung | Zahlungen (Zeile oder Prüfen-Karte) | entfällt als eigene Seite |
| `/import` und Import-Ergebnis | Übersicht (große Aktion) und Zahlungen (Knopf); Ergebnis mit Direktlinks | bleibt als Ablauf, aber ohne Umweg über mehrere Seiten |
| `/categories` Kategorien | Einstellungen → Kategorien und Regeln | versteckt, wird selten gebraucht |
| `/forecast` Prognose | Auswertung → Prognose | Tab |
| `/reports` Reports | Auswertung → Report | Tab |
| `/settings/fees` Einstellungen | Einstellungen → Beiträge und Prüfungsstart | bleibt |
| `/backup` Backup | Einstellungen → Backup | versteckt |
| `/wiki` Wiki | Einstellungen → Hilfe und Wiki (zusätzlich „?“ in der Leiste) | versteckt, aber überall erreichbar |

Entfernt oder zusammengelegt: das Menü schrumpft von elf auf fünf Punkte. Die Seiten „Unklar“, „Prüfen“ und „Automatisch verteilt“ werden zu Filtern derselben Liste. Versteckt: Kategorien, Backup, Update, Wiki.

## Umsetzungsplan (kleine `feat/`-Schritte, die App bleibt dabei lauffähig)

1. `feat/neues-layout`: neues `base.html` und neue Navigation mit fünf Punkten. Alte Routen bleiben unverändert und werden den neuen Bereichen zugeordnet; die alten Menüpunkte verschwinden erst hier.
2. `feat/startseite`: neue Übersicht mit „Heute zu tun“, Zählern, Monatsstatus, Kennzahlen. Der Import-Knopf führt auf die bestehende Importseite.
3. `feat/zahlungen-filter`: `/transactions` bekommt die Filter (Alle, Eingänge, Ausgänge, Unklar, Prüfen, Automatisch verteilt). Die alten Routen `/unmatched`, `/auto` und `?proposed=1` leiten dorthin um. Zuordnung in der Zeile und Sammelleiste.
4. `feat/import-ergebnis`: Ergebnisseite mit Direktlinks („3 unklar, 3 zu prüfen“).
5. `feat/mitglieder-profil`: Mitgliederliste schlanker, Profilseite mit Status, Monatsleiste und Zahlungshistorie (löst `/members/{id}/history` ab).
6. `feat/auswertung-tabs`: Beitragsraster, Prognose und Report als Tabs unter „Auswertung“.
7. `feat/einstellungen-bereiche`: Einstellungen mit Abschnitten (Beiträge, Kategorien, Backup, Update, Hilfe).
8. (nur Richtung 2) `feat/uebersicht-diagramme`: Kuchen und Monatsbalken als Inline-SVG, serverseitig gerendert.

Jeder Schritt hat eigene Tests für Routen und Umleitungen. Die Geldlogik wird nirgends angefasst.

## Barrierefreiheit (Selbstprüfung)

- Jede Seite hat `lang="de"`, einen Sprunglink „Zum Inhalt springen“, `<main>`, benannte `<nav>`-Bereiche und Überschriften in sinnvoller Reihenfolge.
- Formularfelder haben Beschriftungen (sichtbar oder `sr-only`). Auswahlfelder in Zeilen tragen die Zahlung im Namen.
- Aktive Seite und aktiver Filter sind mit `aria-current` markiert.
- Status ist immer ein Wort, in Tabellen und im Raster zusätzlich für Screenreader ausgeschrieben (`sr-only`).
- Diagramme haben `role="img"` mit Text, die Werte stehen zusätzlich in der Legende.
- Das Menü auf dem Handy benutzt `<details>` und ist mit der Tastatur bedienbar. Es braucht kein JavaScript.
- Seiten sind bei 1280, 768 und 390 px ohne waagerechtes Scrollen der Seite geprüft. Nur Tabellen (Raster, Listen) scrollen im eigenen Rahmen.
- Nicht geprüft: echtes Screenreader-Verhalten. Das sollte vor der Umsetzung mit NVDA stichprobenartig getestet werden.

## Offene Fragen an dich

1. Welche Richtung (oder welche Mischung) gefällt dir am besten, und warum?
2. Welche Seiten nutzt du am häufigsten, welche nie? Was muss mit einem Klick erreichbar sein?
3. Soll die Startseite eher ein Dashboard (Kacheln, Zahlen) oder eine Aufgabenliste mit Schritten sein?
4. Reichen dir die Filter in Zahlungen, oder willst du gespeicherte Ansichten wirklich (bei Richtung 2 mit Aufwand)?
5. Nutzt du das Handy für die App, oder nur den Rechner?

## So wurden die Screenshots erstellt

Chrome im Headless-Modus (`--headless=new`), ohne Server, direkt von `file://`. Pro Seite: Desktop hell, Desktop dunkel (1280 px) und Handy hell (390 px, per `iframe`, weil Chrome Fenster nicht schmaler als 500 px öffnet). Die Höhe wird je Seite gemessen, damit die ganze Seite im Bild ist. Ablage: `docs/design/screens/<richtung>/<seite>-<desktop|dark|phone>.png`.
