---
name: kassivo-modellwahl
description: Legt fest, welches Modell ein Subagent in KASSIVO nutzt, abhängig von der Komplexität der Aufgabe. Nutzen, bevor ein Agent gestartet wird.
---

# Modellwahl für Agents

Das Modell richtet sich nach der Komplexität und dem Risiko der Aufgabe, nicht nach der Rolle. Einfache Aufgaben bekommen ein günstiges Modell (Haiku oder Sonnet). Schwere oder sehr umfangreiche Aufgaben bekommen Opus. Obergrenze ist Opus 5.5 (`claude-opus-5-5`). Fable wird nicht verwendet. Bei Sonnet und Haiku ist die Version egal.

## Entscheidungsregel

| Aufgabe | Modell |
|---|---|
| Suchen, Auflisten, einfache Fragen, Texte und Konfiguration, kleine mechanische Änderungen | haiku |
| Normale Features ohne Geldentscheidung: Oberfläche, Seiten, Formulare, Datenaggregation, Hilfsfunktionen, Backup, Dokumentation | sonnet |
| Geldlogik mit Entscheidungen (Zuordnung, Verteilung, Statusberechnung), Bankimport, Sicherheit, Auth, Datenmodell, Architektur, sehr umfangreiche oder verzahnte Aufgaben | opus |

Für Reviews gilt dieselbe Regel, angepasst an den Diff:
- Kleine oder mechanische Diffs und gezielte Re-Reviews kleiner Fixes: sonnet.
- Diffs mit Geld-, Bank-, Auth- oder Upload-Logik: opus.
- Der abschließende Review des ganzen Branches: opus.

## Umsetzung

- Die Agents `kassivo-explorer` (haiku), `kassivo-builder` (sonnet), `kassivo-architect` (opus) und `kassivo-reviewer` (opus) haben ein Standardmodell im Frontmatter. Der Parameter `model` beim Start überschreibt es. Ich setze ihn bei jedem Aufruf ausdrücklich nach der Entscheidungsregel, auch beim `kassivo-reviewer`.
- Im Zweifel ein Modell höher, wenn Geld, Bankdaten oder Rechte betroffen sind. Sonst das günstigste, das die Aufgabe sicher schafft.
- Nie `fable`.
- Agents werden nur gestartet, wenn der Nutzer es verlangt oder die Umsetzung eines freigegebenen Plans es erfordert.
