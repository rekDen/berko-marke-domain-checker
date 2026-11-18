# Berko Checker

Tool zur Markensuche in Europa über die **EUIPO Trademark Search API**, mit
gleichzeitigem **Domain-Check** für die gängigsten Endungen.
Eigenständig, ohne externe Abhängigkeiten — nur Python 3 (Standardbibliothek).

## Einrichten

Zugangsdaten aus dem EUIPO Developer Portal in eine `.env` eintragen:

```bash
cp .env.example .env
# dann EUIPO_CLIENT_ID und EUIPO_CLIENT_SECRET ausfüllen
```

Alternativ können beide Werte als Umgebungsvariablen gesetzt werden; diese haben Vorrang vor `.env`.
`.env` steht in `.gitignore` und darf nicht committet werden.

## Starten

```bash
python3 berko_checker.py
```

Dann im Browser öffnen: **http://localhost:8000**

Optionen:

```bash
python3 berko_checker.py --port 9000     # anderer Port
python3 berko_checker.py --cli "Berko"   # einmalige Suche im Terminal (Marken + Domains)
python3 berko_checker.py --cli "Berko AI" --domain berko-ai   # eigener Domainname
```

Tests (ohne Netzwerk):

```bash
python3 -m unittest discover tests
```

## Funktionen

- Suche nach Markennamen (Wildcard, z. B. „Berko“ findet auch „Berkoff“), auch mit Leer- und Sonderzeichen („Berko AI“)
- Filter nach Nizza-Klassen (z. B. `25,35`), Status und Markenart
- Eingaben werden gegen die erlaubten Werte der API geprüft; Fehler kommen als verständliche Meldung (HTTP 400)
- Ergebnisliste mit Anmeldenummer, Status, Art, Klassen und Anmeldedatum
- Blättern durch Trefferseiten
- Domain-Check bei jeder Suche (siehe unten)

## Domain-Check

Bei jeder Suche prüft das Tool parallel, ob der Name unter diesen Endungen noch frei ist:
`.de`, `.com`, `.ai`, `.io`, `.at`, `.it`, `.pl`, `.eu`, `.co.uk`, `.org`.

- **Name:** Der Markenname wird kleingeschrieben, Leer- und Sonderzeichen fallen weg
  („Berko AI“ → `berkoai`). Umlaute werden als IDN kodiert („Müller“ → `xn--mller-kva`).
  Über das Feld **Domainname (optional)** bzw. `--domain` lässt sich ein anderer Name prüfen, z. B. `berko-ai`.
- **Quellen:** Das Tool fragt direkt bei den Registries an, ohne Drittanbieter:
  - RDAP für `.com`, `.ai`, `.org`, `.pl`, `.co.uk` (HTTP 404 = frei)
  - WHOIS für `.de`, `.io`, `.at`, `.it`, `.eu`, die kein öffentliches RDAP anbieten
- **Ergebnis:** *frei* = nicht registriert, *vergeben* = registriert, *unbekannt* = Registry nicht
  erreichbar oder Rate-Limit (Details im Tooltip).
- Ergebnisse werden 10 Minuten zwischengespeichert, um die Rate-Limits der WHOIS-Server zu schonen.

„Frei“ heißt nur, dass die Domain nicht registriert ist. Ob sie zum normalen Preis bestellbar ist
(Premium-Domain, Reservierung, Sperrfrist nach Löschung), zeigt erst der Registrar.

Hinweis: Für die Prüfung geht der Domainname an die jeweilige Registry.

## Aufbau

| Datei | Zweck |
|-------|-------|
| `berko_checker.py` | Lokaler Webserver + OAuth2-Token-Handling + API-Aufrufe |
| `domains.py` | Domain-Check über RDAP und WHOIS |
| `tests/` | Unit-Tests (`python3 -m unittest discover tests`) |
| `index.html` | Suchoberfläche |
| `config.json` | API-Endpunkte (optional, ohne Zugangsdaten) |
| `.env` | Zugangsdaten `EUIPO_CLIENT_ID` / `EUIPO_CLIENT_SECRET` (lokal, nicht im Repo) |
| `.env.example` | Vorlage für `.env` |
| `trademark_search.json` | Offizielle OpenAPI-Spezifikation der EUIPO-API |

## Authentifizierung

Das Tool authentifiziert sich per **OAuth2 Client Credentials**:

1. Holt ein Access-Token von `…/oidc/accessToken` (mit `client_id` + `client_secret`).
2. Ruft die API mit den Headern `Authorization: Bearer <token>` und
   `X-IBM-Client-Id: <client_id>` auf.

Tokens werden im Speicher zwischengespeichert und vor Ablauf automatisch erneuert.
Lehnt die API ein Token mit 401 ab, holt das Tool einmalig ein neues und wiederholt die Anfrage.

## Wichtig: Fehler 403 „Unauthorized request“

Falls die Suche `403 Forbidden / Unauthorized request` zurückgibt:
Token und Client-ID sind korrekt, aber die App zu diesem API-Key ist im
**EUIPO Developer Portal noch nicht für das Produkt „Trademark search“
freigeschaltet/abonniert**.

Zu beheben unter https://dev.euipo.europa.eu :
1. Einloggen → eigene App öffnen.
2. Die API **„Trademark search“** (Produkt) abonnieren / einen Plan auswählen.
3. Sicherstellen, dass `EUIPO_CLIENT_ID`/`EUIPO_CLIENT_SECRET` in `.env` zu genau
   dieser App gehören.

Sobald das Abo aktiv ist, funktioniert das Tool ohne Codeänderung.
