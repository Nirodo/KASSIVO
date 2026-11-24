---
name: kassivo-modellwahl
description: Legt fest, welches Modell ein Subagent in KASSIVO nutzt. Nutzen, bevor ein Agent gestartet wird.
---

# Modellwahl für Agents

Obergrenze ist Opus 4.8 mit der Modell-ID `claude-opus-4-8`. Andere Opus-Versionen und Fable werden nicht verwendet. Bei Sonnet und Haiku ist die Version egal.

| Aufgabe | Agent | Modell |
|---|---|---|
| Suchen, Dateien auflisten, einfache Fragen | `kassivo-explorer` | haiku |
| Normale Features, UI, Tests, klare Refactorings | `kassivo-builder` | sonnet |
| Architektur, Bankanbindung, Auth, Geldlogik | `kassivo-architect` | opus |
| Review von Geld-, Bank- und Auth-Code | `kassivo-reviewer` | opus |

Regeln:
- Die Opus-Agents (`kassivo-architect`, `kassivo-reviewer`) tragen `model: claude-opus-4-8` im Frontmatter. Beim Start gebe ich für sie keinen `model`-Parameter an, weil der Alias `opus` auf eine neuere Version zeigen kann und das Frontmatter überschreiben würde. Nie `fable`.
- Im Zweifel ein Modell höher, wenn Geld, Bankdaten oder Rechte betroffen sind. Sonst das günstigste, das die Aufgabe schafft.
- Agents werden nur gestartet, wenn der Nutzer es verlangt.
