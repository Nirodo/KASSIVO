---
name: kassivo-geldlogik
description: Regeln für Geldbeträge und Buchungen in KASSIVO. Nutzen, sobald Code Beträge, Salden, Buchungen, Rundung oder Währung berührt.
---

# Geldlogik in KASSIVO

- Beträge sind ganze Zahlen in Cent (Integer), nie Float oder Double.
- Jede Buchung hat Betrag, Währung, Datum, Kategorie und eine eindeutige ID.
- Buchungen werden nie überschrieben oder gelöscht. Korrekturen sind Gegenbuchungen (Storno).
- Der Kassenstand ist die Summe aller Buchungen, kein separat gepflegter Wert. Ein gespeicherter Saldo ist nur ein Cache und muss neu berechenbar sein.
- Importierte Kontoumsätze werden über ihre Bank-Referenz oder einen Hash dedupliziert, damit nichts doppelt gebucht wird.
- Teilungen (z. B. Beitrag pro Mitglied) verteilen Restcents explizit und deterministisch. Die Summe der Teile muss exakt dem Gesamtbetrag entsprechen.
- Jede Geldfunktion bekommt Tests für: 0, negative Beträge, Rundung, Restcents, doppelte Buchung.
