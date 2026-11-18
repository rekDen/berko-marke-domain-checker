#!/usr/bin/env python3
"""
Berko Checker — Markensuche in Europa (EUIPO Trademark Search API).

Eigenstaendiges Tool ohne externe Abhaengigkeiten (nur Python-Standardbibliothek).
Startet einen lokalen Webserver mit Suchoberflaeche und kapselt die OAuth2-
Authentifizierung sowie den Aufruf der EUIPO-API.

Aufruf:
    python3 berko_checker.py            # Server auf http://localhost:8000
    python3 berko_checker.py --port 9000
    python3 berko_checker.py --cli "Berko"   # einmalige Suche im Terminal
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ENV_PATH = os.path.join(BASE_DIR, ".env")

DEFAULT_CONFIG = {
    "token_url": "https://euipo.europa.eu/cas-server-webapp/oidc/accessToken",
    "api_base": "https://api.euipo.europa.eu/trademark-search",
    "scope": "uid",
}

# Erlaubte Werte laut EUIPO-API-Spezifikation (trademark_search.json).
STATUSES = {
    "RECEIVED", "UNDER_EXAMINATION", "APPLICATION_PUBLISHED", "REGISTRATION_PENDING",
    "REGISTERED", "WITHDRAWN", "REFUSED", "OPPOSITION_PENDING", "APPEALED",
    "CANCELLATION_PENDING", "CANCELLED", "SURRENDERED", "EXPIRED", "APPEALABLE",
    "START_OF_OPPOSITION_PERIOD", "ACCEPTANCE_PENDING", "ACCEPTED", "REMOVED_FROM_REGISTER",
}
MARK_FEATURES = {
    "WORD", "FIGURATIVE", "SHAPE_3D", "COLOUR", "SOUND", "HOLOGRAM", "OLFACTORY",
    "POSITION", "PATTERN", "MOTION", "MULTIMEDIA", "OTHER",
}
SORT_RE = re.compile(r"^[\w+]+(\.\w+)*:(asc|desc)$")
PAGE_SIZE_MIN, PAGE_SIZE_MAX = 10, 100
# Zeichen, die in RSQL nur innerhalb von Anführungszeichen erlaubt sind.
RSQL_RESERVED = set("\"'();,=!~<> \t")


class ConfigError(Exception):
    """Konfiguration oder Zugangsdaten fehlen bzw. sind ungültig."""


class InputError(ValueError):
    """Ungültige Suchparameter (wird als HTTP 400 gemeldet)."""


def load_dotenv(path=ENV_PATH):
    """Liest eine einfache .env-Datei (KEY=VALUE) in os.environ.
    Bereits gesetzte Umgebungsvariablen haben Vorrang."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


def load_config():
    """Endpunkte aus config.json (optional), Zugangsdaten aus der Umgebung
    bzw. .env. Zugangsdaten in config.json werden nur noch als Fallback gelesen."""
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                config.update(json.load(fh))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config.json ist kein gültiges JSON: {exc}") from exc

    load_dotenv()
    config["client_id"] = os.environ.get("EUIPO_CLIENT_ID") or config.get("client_id", "")
    config["client_secret"] = os.environ.get("EUIPO_CLIENT_SECRET") or config.get("client_secret", "")
    if not config["client_id"] or not config["client_secret"]:
        raise ConfigError(
            "Zugangsdaten fehlen: EUIPO_CLIENT_ID und EUIPO_CLIENT_SECRET in .env "
            "oder als Umgebungsvariablen setzen (Vorlage: .env.example)."
        )
    return config


def rsql_value(text):
    """Bereitet einen Suchbegriff für RSQL vor. Einfache Begriffe bleiben
    unverändert; enthält der Begriff Leer- oder Sonderzeichen, wird er in
    Anführungszeichen gesetzt und escaped."""
    if not any(ch in RSQL_RESERVED for ch in text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def parse_nice_classes(raw):
    classes = []
    for part in raw:
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or not 1 <= int(part) <= 45:
            raise InputError(f"Ungültige Nizza-Klasse „{part}“ (erlaubt: 1–45).")
        classes.append(int(part))
    return classes


def parse_enum_list(raw, allowed, label):
    values = [v.strip().upper() for v in raw.split(",") if v.strip()] if raw else []
    for v in values:
        if v not in allowed:
            raise InputError(f"Ungültiger Wert für {label}: „{v}“.")
    return ",".join(values)


class EuipoClient:
    """Kleiner Client fuer die EUIPO Trademark Search API mit Token-Caching."""

    def __init__(self, config):
        self.client_id = config["client_id"]
        self.client_secret = config["client_secret"]
        self.token_url = config["token_url"]
        self.api_base = config["api_base"].rstrip("/")
        self.scope = config.get("scope", "uid")
        self._token = None
        self._token_expiry = 0.0

    # -- Authentifizierung -------------------------------------------------
    def _get_token(self):
        # Token wiederverwenden, solange es noch >60s gueltig ist.
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.scope,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        self._token = payload["access_token"]
        self._token_expiry = time.time() + float(payload.get("expires_in", 3600))
        return self._token

    def _headers(self):
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "X-IBM-Client-Id": self.client_id,
            "Accept": "application/json",
        }

    # -- API-Aufrufe -------------------------------------------------------
    def _get(self, path, params=None, _retry=True):
        url = f"{self.api_base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            # Token abgelaufen oder widerrufen: einmal mit neuem Token versuchen.
            if exc.code == 401 and _retry:
                self._token = None
                return self._get(path, params, _retry=False)
            body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"title": "Error", "detail": body, "status": exc.code}
            return exc.code, parsed

    def search(self, query=None, nice_classes=None, status=None,
               mark_feature=None, page=0, size=25, sort="applicationNumber:asc"):
        """Baut eine RSQL-Abfrage und ruft /trademarks auf.
        Wirft InputError bei ungültigen Parametern."""
        if page < 0:
            raise InputError("Seite muss >= 0 sein.")
        size = max(PAGE_SIZE_MIN, min(PAGE_SIZE_MAX, size))
        if not SORT_RE.match(sort or "") or len(sort) > 50:
            raise InputError(f"Ungültige Sortierung „{sort}“ (Format: feld:asc|desc).")

        clauses = []
        if query:
            term = query.strip()
            if len(term) > 200:
                raise InputError("Suchbegriff ist zu lang (max. 200 Zeichen).")
            # Wildcard-Suche im Wortbestandteil der Marke.
            clauses.append(f"wordMarkSpecification.verbalElement=={rsql_value(f'*{term}*')}")
        if nice_classes:
            classes = ",".join(str(c) for c in nice_classes)
            clauses.append(f"niceClasses=all=({classes})")
        if status:
            clauses.append(f"status=in=({status})")
        if mark_feature:
            clauses.append(f"markFeature=in=({mark_feature})")

        rsql = " and ".join(clauses) if clauses else None
        params = {"page": page, "size": size, "sort": sort}
        if rsql:
            params["query"] = rsql
        return self._get("/trademarks", params)

    def get_trademark(self, application_number):
        if not re.fullmatch(r"[A-Za-z0-9]{1,20}", application_number or ""):
            raise InputError("Ungültige Anmeldenummer.")
        return self._get(f"/trademarks/{application_number}")


# ---------------------------------------------------------------------------
# Webserver
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    client = None  # wird vor dem Start gesetzt

    def log_message(self, fmt, *args):
        sys.stderr.write("[berko] " + (fmt % args) + "\n")

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except FileNotFoundError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_file(os.path.join(BASE_DIR, "index.html"), "text/html; charset=utf-8")
            return

        if path == "/api/search":
            qs = urllib.parse.parse_qs(parsed.query)
            first = lambda key, default="": (qs.get(key) or [default])[0].strip()  # noqa: E731
            try:
                try:
                    page = int(first("page", "0"))
                    size = int(first("size", "25"))
                except ValueError as exc:
                    raise InputError("Seite und Seitengröße müssen Zahlen sein.") from exc
                code, data = self.client.search(
                    query=first("q"),
                    nice_classes=parse_nice_classes(first("niceClasses").split(",")),
                    status=parse_enum_list(first("status"), STATUSES, "Status"),
                    mark_feature=parse_enum_list(first("markFeature"), MARK_FEATURES, "Art"),
                    page=page, size=size,
                    sort=first("sort", "applicationNumber:asc"),
                )
            except InputError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001 - Fehler an UI durchreichen
                self._send_json(502, {"error": str(exc)})
                return
            self._send_json(code, data)
            return

        if path.startswith("/api/trademark/"):
            app_no = path.rsplit("/", 1)[-1]
            try:
                code, data = self.client.get_trademark(app_no)
            except InputError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                self._send_json(502, {"error": str(exc)})
                return
            self._send_json(code, data)
            return

        self.send_error(404, "Not found")


def run_server(client, port):
    Handler.client = client
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"Berko Checker laeuft auf {url}")
    print("Zum Beenden: Strg+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")
        server.shutdown()


def run_cli(client, term):
    try:
        code, data = client.search(query=term, size=25)
    except InputError as exc:
        print(f"Fehler: {exc}")
        sys.exit(2)
    if code != 200:
        print(f"Fehler {code}: {json.dumps(data, ensure_ascii=False, indent=2)}")
        sys.exit(1)
    total = data.get("totalElements", 0)
    print(f"Treffer gesamt: {total}\n")
    for tm in data.get("trademarks", []):
        name = (tm.get("wordMarkSpecification") or {}).get("verbalElement", "—")
        print(f"  {tm.get('applicationNumber'):<12} {tm.get('status', ''):<14} "
              f"{tm.get('markFeature', ''):<12} {name}")


def main():
    parser = argparse.ArgumentParser(description="Berko Checker — EUIPO Markensuche")
    parser.add_argument("--port", type=int, default=8000, help="Port des Webservers")
    parser.add_argument("--cli", metavar="BEGRIFF", help="Einmalige Suche im Terminal")
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        sys.exit(1)
    client = EuipoClient(config)

    if args.cli:
        run_cli(client, args.cli)
    else:
        run_server(client, args.port)


if __name__ == "__main__":
    main()
