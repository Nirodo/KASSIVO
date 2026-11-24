---
name: kassivo-bank-sicherheit
description: Sicherheitsregeln für Bankanbindung, Zugangsdaten und personenbezogene Daten in KASSIVO. Nutzen bei Bank-API, Auth, Tokens, Logging und Datenbank-Schema.
---

# Bank- und Datensicherheit in KASSIVO

- Bank-PINs, API-Keys und Tokens stehen nie im Repo. Sie kommen aus Umgebungsvariablen oder einem Secret-Store. `.env` ist in `.gitignore`.
- Gespeicherte Tokens werden verschlüsselt abgelegt. Klartext in der Datenbank ist verboten.
- Logs enthalten keine IBANs in voller Länge, keine Tokens, keine PINs. IBANs werden maskiert.
- Der Bankzugriff ist nur lesend (Kontoumsätze abrufen). Es gibt keine Zahlungsauslösung, solange das nicht ausdrücklich beschlossen wurde.
- Jeder Datenzugriff prüft, ob der Nutzer zur jeweiligen Gruppenkasse gehört und welche Rolle er hat.
- Personenbezogene Daten (Name, IBAN, Verwendungszweck) gelten nach DSGVO. Speichere nur, was gebraucht wird, und halte eine Löschmöglichkeit bereit.
- Vor jedem Merge von Code, der Bankdaten oder Auth berührt, läuft `kassivo-reviewer` und `/security-review`.
