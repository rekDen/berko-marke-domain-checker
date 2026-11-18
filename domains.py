"""
Domain-Verfügbarkeit für die gängigsten Endungen.

Nur Python-Standardbibliothek. Pro Endung wird die Registry direkt gefragt:
- RDAP (HTTPS/JSON) für .com, .ai, .org, .pl, .co.uk:
  HTTP 404 = frei, HTTP 200 = vergeben.
- WHOIS (TCP-Port 43) für .de, .io, .at, .it, .eu, die (noch) kein
  öffentliches RDAP im IANA-Verzeichnis haben. Die Antworttexte der
  Registries werden per Muster ausgewertet.

„Frei“ heißt: nicht registriert. Ob eine Domain tatsächlich bestellbar ist
(Premium-Preis, reserviert, Sperrfrist), entscheidet erst der Registrar.
"""

import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

USER_AGENT = "berko-checker/1.0 (domain availability check)"
TIMEOUT = 10
CACHE_TTL = 600  # Sekunden; schont die Rate-Limits der WHOIS-Server

AVAILABLE = "available"
REGISTERED = "registered"
UNKNOWN = "unknown"

# Reihenfolge = Anzeige-Reihenfolge
TLDS = ["de", "com", "ai", "io", "at", "it", "pl", "eu", "co.uk", "org"]

RDAP_SERVERS = {
    "com": "https://rdap.verisign.com/com/v1/",
    "ai": "https://rdap.identitydigital.services/rdap/",
    "org": "https://rdap.publicinterestregistry.org/rdap/",
    "pl": "https://rdap.dns.pl/",
    "co.uk": "https://rdap.nominet.uk/uk/",
}


def _m(pattern):
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


# server, Abfrageformat, Muster für „frei“, Muster für „vergeben“
WHOIS_SERVERS = {
    "de": ("whois.denic.de", "-T dn {domain}", _m(r"^Status:\s*free\s*$"), _m(r"^Domain:\s*\S")),
    "io": ("whois.nic.io", "{domain}", _m(r"^Domain not found\."), _m(r"^Domain Name:\s*\S")),
    "at": ("whois.nic.at", "{domain}", _m(r"^%\s*nothing found"), _m(r"^domain:\s*\S")),
    "it": ("whois.nic.it", "{domain}", _m(r"^Status:\s*AVAILABLE\s*$"), _m(r"^Status:\s*\S")),
    "eu": ("whois.eu", "{domain}", _m(r"^Status:\s*AVAILABLE\s*$"), _m(r"^Domain:\s*\S")),
}

_cache = {}
_cache_lock = threading.Lock()


class DomainInputError(ValueError):
    """Aus dem Suchbegriff lässt sich kein gültiger Domainname bilden."""


def to_label(text):
    """Macht aus einem Markennamen einen Domain-Label.
    „Berko AI“ -> „berkoai“, „Müller & Söhne“ -> „xn--mllershne-…“ (IDN).
    Wirft DomainInputError, wenn nichts Gültiges übrig bleibt."""
    raw = (text or "").strip().lower()
    # Leerzeichen entfernen, nur Buchstaben, Ziffern und Bindestriche behalten
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch == "-")
    cleaned = cleaned.strip("-")
    if not cleaned:
        raise DomainInputError("Aus dem Suchbegriff lässt sich kein Domainname bilden.")
    try:
        label = cleaned.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DomainInputError(f"„{cleaned}“ ist kein gültiger Domainname.") from exc
    if len(label) > 63:
        raise DomainInputError("Domainname ist zu lang (max. 63 Zeichen).")
    # Bindestriche an Stelle 3 und 4 sind für IDN-Präfixe („xn--“) reserviert.
    reserved_hyphens = label[2:4] == "--" and not label.startswith("xn--")
    if label.startswith("-") or label.endswith("-") or reserved_hyphens:
        raise DomainInputError(f"„{cleaned}“ ist kein gültiger Domainname.")
    return label


def parse_whois(tld, text):
    """Wertet eine WHOIS-Antwort aus: available, registered oder unknown."""
    _, _, free_re, taken_re = WHOIS_SERVERS[tld]
    if free_re.search(text):
        return AVAILABLE
    if taken_re.search(text):
        return REGISTERED
    return UNKNOWN  # z. B. Rate-Limit oder unerwartetes Format


def _whois_query(server, query):
    with socket.create_connection((server, 43), timeout=TIMEOUT) as sock:
        sock.settimeout(TIMEOUT)
        sock.sendall((query + "\r\n").encode("utf-8"))
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if sum(len(c) for c in chunks) > 200_000:
                break
    return b"".join(chunks).decode("utf-8", errors="replace")


def _rdap_status(url):
    req = urllib.request.Request(
        url, headers={"Accept": "application/rdap+json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return REGISTERED if resp.status == 200 else UNKNOWN, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return AVAILABLE, "HTTP 404"
        return UNKNOWN, f"HTTP {exc.code}"


def check_domain(label, tld):
    """Prüft label.tld. Liefert ein Dict mit domain, tld, status, source, detail."""
    domain = f"{label}.{tld}"
    result = {"domain": domain, "tld": tld, "status": UNKNOWN, "source": "", "detail": ""}
    try:
        if tld in RDAP_SERVERS:
            result["source"] = "RDAP"
            result["status"], result["detail"] = _rdap_status(f"{RDAP_SERVERS[tld]}domain/{domain}")
        elif tld in WHOIS_SERVERS:
            server, fmt, _, _ = WHOIS_SERVERS[tld]
            result["source"] = "WHOIS"
            text = _whois_query(server, fmt.format(domain=domain))
            result["status"] = parse_whois(tld, text)
            if result["status"] == UNKNOWN:
                first = next((ln.strip() for ln in text.splitlines()
                              if ln.strip() and not ln.startswith(("%", "#"))), "")
                result["detail"] = first[:120] or "Unerwartete Antwort"
        else:
            result["detail"] = "Endung nicht unterstützt"
    except (OSError, urllib.error.URLError) as exc:
        result["detail"] = f"Nicht erreichbar: {exc}"[:160]
    return result


def _cached_check(label, tld):
    key = (label, tld)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    result = check_domain(label, tld)
    if result["status"] != UNKNOWN:  # Fehler nicht cachen
        with _cache_lock:
            _cache[key] = (now, result)
    return result


def check_all(text, tlds=None):
    """Prüft den aus `text` gebildeten Namen für alle Endungen parallel."""
    label = to_label(text)
    tlds = tlds or TLDS
    with ThreadPoolExecutor(max_workers=len(tlds)) as pool:
        results = list(pool.map(lambda t: _cached_check(label, t), tlds))
    return {"label": label, "results": results}
