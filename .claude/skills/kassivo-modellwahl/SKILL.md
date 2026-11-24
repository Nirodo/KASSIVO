---
name: kassivo-modellwahl
description: Legt fest, welches Modell ein Subagent in KASSIVO nutzt. Nutzen, bevor ein Agent gestartet wird.
---

# Modellwahl für Agents

Obergrenze ist Opus 5.5 mit der Modell-ID `claude-opus-5-5`. Fable wird nicht verwendet. Bei Sonnet und Haiku ist die Version egal.

| Aufgabe | Agent | Modell |
|---|---|---|
| Suchen, Dateien auflisten, einfache Fragen | `kassivo-explorer` | haiku |
| Normale Features, UI, Tests, klare Refactorings | `kassivo-builder` | sonnet |
| Architektur, Bankanbindung, Auth, Geldlogik | `kassivo-architect` | opus |
| Review von Geld-, Bank- und Auth-Code | `kassivo-reviewer` | opus |

Regeln:
- Die Opus-Agents (`kassivo-architect`, `kassivo-reviewer`) tragen `model: claude-opus-5-5` im Frontmatter. Beim Start gebe ich `model: opus` an oder lasse den Parameter weg. Nie `fable`.
- Im Zweifel ein Modell höher, wenn Geld, Bankdaten oder Rechte betroffen sind. Sonst das günstigste, das die Aufgabe schafft.
- Agents werden nur gestartet, wenn der Nutzer es verlangt.
