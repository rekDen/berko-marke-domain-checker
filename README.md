# Berko Checker

Tool zur Markensuche in Europa über die **EUIPO Trademark Search API**.
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
python3 berko_checker.py --cli "Berko"   # einmalige Suche im Terminal
```

## Funktionen

- Suche nach Markennamen (Wildcard, z. B. „Berko“ findet auch „Berkoff“), auch mit Leer- und Sonderzeichen („Berko AI“)
- Filter nach Nizza-Klassen (z. B. `25,35`), Status und Markenart
- Eingaben werden gegen die erlaubten Werte der API geprüft; Fehler kommen als verständliche Meldung (HTTP 400)
- Ergebnisliste mit Anmeldenummer, Status, Art, Klassen und Anmeldedatum
- Blättern durch Trefferseiten

## Aufbau

| Datei | Zweck |
|-------|-------|
| `berko_checker.py` | Lokaler Webserver + OAuth2-Token-Handling + API-Aufrufe |
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
