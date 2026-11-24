---
name: kassivo-modellwahl
description: Legt fest, welches Modell ein Subagent in KASSIVO nutzt. Nutzen, bevor ein Agent gestartet wird.
---

# Modellwahl für Agents

Obergrenze ist Opus. Fable wird nicht verwendet.

| Aufgabe | Agent | Modell |
|---|---|---|
| Suchen, Dateien auflisten, einfache Fragen | `kassivo-explorer` | haiku |
| Normale Features, UI, Tests, klare Refactorings | `kassivo-builder` | sonnet |
| Architektur, Bankanbindung, Auth, Geldlogik | `kassivo-architect` | opus |
| Review von Geld-, Bank- und Auth-Code | `kassivo-reviewer` | opus |

Regeln:
- Beim Start eines Agents setze ich `model` ausdrücklich. Nie `fable`.
- Im Zweifel ein Modell höher, wenn Geld, Bankdaten oder Rechte betroffen sind. Sonst das günstigste, das die Aufgabe schafft.
- Agents werden nur gestartet, wenn der Nutzer es verlangt.
