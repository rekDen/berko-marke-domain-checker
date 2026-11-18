"""Tests für domains.py — ohne Netzwerk. Start: python3 -m unittest discover tests"""

import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import domains  # noqa: E402

# Gekürzte echte Antworten der Registries (November 2026)
WHOIS_SAMPLES = {
    "de": ("Domain: google.de\nNserver: ns1.google.com\nStatus: connect\n",
           "Domain: zqxv7k3p9wlm2.de\nStatus: free\n"),
    "io": ("Domain Name: google.io\nRegistry Domain ID: REDACTED\n",
           "Domain not found.\n>>> Last update of WHOIS database <<<\n"),
    "at": ("domain:            google.at\nregistrar:         MarkMonitor Inc.\n",
           "% Copyright (c)2026 by NIC.AT (1)\n%\n% nothing found\n"),
    "it": ("Domain:             google.it\nStatus:             ok\n",
           "Domain:             zqxv7k3p9wlm2.it\nStatus:             AVAILABLE\n"),
    "eu": ("Domain: google.eu\nScript: LATIN\nRegistrant:\n        NOT DISCLOSED!\n",
           "Domain: zqxv7k3p9wlm2.eu\nScript: LATIN\nStatus: AVAILABLE\n"),
}


class ToLabelTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(domains.to_label("Berko"), "berko")

    def test_removes_spaces_and_symbols(self):
        self.assertEqual(domains.to_label("Berko AI"), "berkoai")
        self.assertEqual(domains.to_label("Berko's & Co."), "berkosco")

    def test_keeps_hyphen(self):
        self.assertEqual(domains.to_label("berko-ai"), "berko-ai")

    def test_umlaut_becomes_punycode(self):
        self.assertEqual(domains.to_label("Müller"), "xn--mller-kva")

    def test_invalid(self):
        for bad in ["", "   ", "&&&", "---", "a" * 64, "ab--cd"]:
            with self.subTest(bad=bad), self.assertRaises(domains.DomainInputError):
                domains.to_label(bad)


class ParseWhoisTest(unittest.TestCase):
    def test_registered_and_available(self):
        for tld, (taken, free) in WHOIS_SAMPLES.items():
            with self.subTest(tld=tld):
                self.assertEqual(domains.parse_whois(tld, taken), domains.REGISTERED)
                self.assertEqual(domains.parse_whois(tld, free), domains.AVAILABLE)

    def test_not_available_is_not_free(self):
        # .eu/.it kennen auch Status wie „NOT AVAILABLE“ – das ist nicht frei.
        text = "Domain: x.eu\nStatus: NOT AVAILABLE\n"
        self.assertEqual(domains.parse_whois("eu", text), domains.REGISTERED)

    def test_rate_limit_is_unknown(self):
        text = "55000000002 Connection refused; access control limit reached.\n"
        self.assertEqual(domains.parse_whois("de", text), domains.UNKNOWN)
        self.assertEqual(domains.parse_whois("at", "% Quota exceeded\n"), domains.UNKNOWN)


class RdapTest(unittest.TestCase):
    def _fake(self, code):
        def urlopen(req, timeout=0):
            self.assertIn("berko-checker", req.get_header("User-agent"))
            if code == 200:
                resp = mock.MagicMock(status=200)
                resp.__enter__.return_value = resp
                return resp
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b""))
        return urlopen

    def test_status_mapping(self):
        for code, expected in [(200, domains.REGISTERED), (404, domains.AVAILABLE), (403, domains.UNKNOWN)]:
            with self.subTest(code=code), mock.patch.object(domains.urllib.request, "urlopen", self._fake(code)):
                result = domains.check_domain("berko", "com")
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["source"], "RDAP")
                self.assertEqual(result["domain"], "berko.com")

    def test_network_error_is_unknown(self):
        def boom(req, timeout=0):
            raise urllib.error.URLError("timed out")
        with mock.patch.object(domains.urllib.request, "urlopen", boom):
            result = domains.check_domain("berko", "org")
        self.assertEqual(result["status"], domains.UNKNOWN)
        self.assertIn("Nicht erreichbar", result["detail"])


class CheckAllTest(unittest.TestCase):
    def setUp(self):
        domains._cache.clear()

    def test_covers_all_tlds_in_order(self):
        with mock.patch.object(domains, "check_domain",
                               side_effect=lambda label, tld: {"domain": f"{label}.{tld}", "tld": tld,
                                                               "status": domains.AVAILABLE, "source": "", "detail": ""}):
            data = domains.check_all("Berko AI")
        self.assertEqual(data["label"], "berkoai")
        self.assertEqual([r["tld"] for r in data["results"]], domains.TLDS)
        self.assertEqual(len(domains.TLDS), 10)

    def test_unknown_results_are_not_cached(self):
        calls = []

        def fake(label, tld):
            calls.append(tld)
            return {"domain": f"{label}.{tld}", "tld": tld, "status": domains.UNKNOWN, "source": "", "detail": ""}
        with mock.patch.object(domains, "check_domain", side_effect=fake):
            domains.check_all("berko", ["de"])
            domains.check_all("berko", ["de"])
        self.assertEqual(calls, ["de", "de"])


if __name__ == "__main__":
    unittest.main()
