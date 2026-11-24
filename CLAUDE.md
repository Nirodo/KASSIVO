# KASSIVO

Verwaltung von Gruppenkassen (Verein, Mannschaft, Freundeskreis): Einzahlungen, Ausgaben, Kassenstände. Ziel: Zahlungseingänge automatisch aus einem Bankkonto abrufen.

## Stand

Planungsphase. Stack, Speicherung (DB oder anderes) und Bankanbieter sind noch nicht entschieden. Erst brainstormen, Design freigeben lassen, dann planen, dann bauen.

## Arbeitsweise

- Sprache: Deutsch.
- Vor neuen Features: `superpowers:brainstorming`, danach `superpowers:writing-plans`.
- Tests zuerst (`superpowers:test-driven-development`).
- Vor "fertig": `superpowers:verification-before-completion`.
- Änderungen an Geld-, Bank- oder Auth-Code: `kassivo-reviewer` und `/security-review`.

## Projekt-Skills (`.claude/skills/`)

- `kassivo-geldlogik`: Beträge in Cent, Buchungen unveränderlich, Deduplizierung.
- `kassivo-bank-sicherheit`: Secrets, Verschlüsselung, Logging, DSGVO.
- `kassivo-modellwahl`: Welches Modell für welche Aufgabe.

## Agents und Modelle (`.claude/agents/`)

Obergrenze ist Opus 5.5 (Modell-ID `claude-opus-5-5`). Fable wird nicht verwendet. Bei Sonnet und Haiku ist die Version egal.

| Aufgabe | Agent | Modell |
|---|---|---|
| Suchen, einfache Fragen | `kassivo-explorer` | haiku |
| Normale Features, UI, Tests | `kassivo-builder` | sonnet |
| Architektur, Bank, Auth, Geldlogik | `kassivo-architect` | opus |
| Review kritischer Änderungen | `kassivo-reviewer` | opus |

Agents nur starten, wenn der Nutzer es verlangt. Für `kassivo-architect` und `kassivo-reviewer` gilt `claude-opus-5-5` aus dem Frontmatter.

## Harte Regeln

- Geldbeträge sind Integer in Cent, nie Float.
- Keine Zugangsdaten, Tokens oder PINs im Repo oder in Logs.
- Bankzugriff nur lesend, solange nichts anderes beschlossen ist.
