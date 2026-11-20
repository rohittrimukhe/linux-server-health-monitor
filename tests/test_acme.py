# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Let's Encrypt (ACME) certificate for the web interface: client, domain rules, issue, renew, hot reload."""
import os
import socket
import ssl
import stat
import tempfile
import time
import unittest

from fake_acme import FakeCA
from fakes import ROOT, TempHome
from lshme.app import App
from lshme.web import acme, certs


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class DomainRuleTests(unittest.TestCase):
    def test_only_public_names_qualify(self):
        for good in ("portal.example.com", "vitals.lrs-services.in", "a.b.c.co.uk"):
            self.assertTrue(certs.valid_public_name(good), good)
        for bad in ("oraclelinux9", "192.168.1.3", "host.local", "db01.lan", "vm.internal", "localhost", "x.example", "", "bad_name.com", "-a.com"):
            self.assertFalse(certs.valid_public_name(bad), bad)

    def test_names_come_from_the_setting_or_the_servers_fqdn(self):
        home = TempHome()
        real = socket.getfqdn
        try:
            socket.getfqdn = lambda *a: "Portal.Example.com"
            self.assertEqual(certs.domains_for(home.cfg), ["portal.example.com"])
            socket.getfqdn = lambda *a: "oraclelinux9"
            self.assertEqual(certs.domains_for(home.cfg), [])                 # no public FQDN: nothing to ask for
            home.cfg.set("web", "web", "acme_domains", "a.example.org, b.example.org")
            self.assertEqual(certs.domains_for(home.cfg), ["a.example.org", "b.example.org"])
        finally:
            socket.getfqdn = real
            home.cleanup()


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.account = os.path.join(self.tmp, "account.key")
        self.key = os.path.join(self.tmp, "server.key")
        acme.new_key(self.account)
        acme.new_key(self.key)

    def test_account_key_becomes_a_jwk(self):
        jwk = acme.rsa_jwk(self.account)
        self.assertEqual(jwk["kty"], "RSA")
        self.assertEqual(len(acme.base64.urlsafe_b64decode(jwk["n"] + "==")), 256)     # 2048-bit modulus
        self.assertEqual(jwk["e"], "AQAB")                                              # 65537
        self.assertEqual(stat.S_IMODE(os.stat(self.account).st_mode), 0o600)

    def test_http_challenge_standalone(self):
        port = free_port()
        ca = FakeCA(challenge_url="http://127.0.0.1:%d" % port)
        try:
            pem = acme.Client(ca.directory, self.account, "me@example.com").issue(["portal.example.com", "www.example.com"], self.key, "http", port, poll_wait=0.05)
        finally:
            ca.stop()
        self.assertIn("BEGIN CERTIFICATE", pem)
        self.assertEqual(ca.orders, 1)
        self.assertTrue(any(r[0] == "/acct" and r[1] for r in ca.requests))            # the account is created with the public key
        self.assertTrue(all(r[2] for r in ca.requests if r[0] != "/acct"))              # everything after that uses the account address

    def test_http_challenge_with_a_webroot_cleans_up(self):
        webroot = tempfile.mkdtemp()
        ca = FakeCA(webroot=webroot)
        try:
            acme.Client(ca.directory, self.account, "").issue(["portal.example.com"], self.key, "http", 80, webroot=webroot, poll_wait=0.05)
        finally:
            ca.stop()
        self.assertEqual(os.listdir(os.path.join(webroot, ".well-known", "acme-challenge")), [])    # the answer file is removed afterwards

    def test_dns_challenge_uses_the_hook(self):
        records = os.path.join(self.tmp, "dns.txt")
        hook = os.path.join(self.tmp, "hook.sh")
        with open(hook, "w") as f:
            f.write("#!/bin/sh\n[ \"$1\" = add ] && echo \"$3\" >> %s\nexit 0\n" % records)
        os.chmod(hook, 0o755)
        ca = FakeCA(dns_file=records)
        try:
            pem = acme.Client(ca.directory, self.account, "").issue(["portal.example.com"], self.key, "dns", dns_hook=hook, dns_wait=0, poll_wait=0.05)
        finally:
            ca.stop()
        self.assertIn("BEGIN CERTIFICATE", pem)
        with open(records) as f:
            self.assertEqual(len(f.read().split()), 1)

    def test_a_refused_check_is_explained(self):
        port = free_port()
        ca = FakeCA(challenge_url="http://127.0.0.1:%d" % port, reject=True)
        try:
            with self.assertRaises(acme.AcmeError) as cm:
                acme.Client(ca.directory, self.account, "").issue(["portal.example.com"], self.key, "http", port, poll_wait=0.05)
        finally:
            ca.stop()
        self.assertIn("rejected by the test CA", str(cm.exception))

    def test_port_in_use_is_explained(self):
        s = socket.socket()
        s.bind(("", 0))
        s.listen(1)
        try:
            with self.assertRaises(acme.AcmeError) as cm:
                acme.Standalone(s.getsockname()[1]).start()
        finally:
            s.close()
        self.assertIn("acme_webroot", str(cm.exception))

    def test_unreachable_authority(self):
        with self.assertRaises(acme.AcmeError):
            acme.Client("http://127.0.0.1:%d/directory" % free_port(), self.account, "", timeout=2).start()


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.app = App(ROOT, home=self.home.dir)
        self.port = free_port()
        self.ca = FakeCA(challenge_url="http://127.0.0.1:%d" % self.port)
        cfg = self.app.cfg
        for k, v in (("acme", "yes"), ("acme_domains", "portal.example.com"), ("acme_directory", self.ca.directory), ("acme_http_port", str(self.port)),
                     ("acme_email", "ops@example.com")):
            cfg.set("web", "web", k, v)
        self.real_check = certs.check_domains
        certs.check_domains = lambda domains: [acme_ok(d) for d in domains]

    def tearDown(self):
        certs.check_domains = self.real_check
        self.ca.stop()
        self.home.cleanup()

    def test_issue_installs_the_certificate_and_renewal_waits_until_due(self):
        r = certs.issue(self.app)
        self.assertTrue(r["ok"] and r["renewed"], r)
        f = certs.files(self.app)
        self.assertTrue(os.path.exists(f["cert"]) and os.path.exists(f["key"]))
        self.assertEqual(stat.S_IMODE(os.stat(f["key"]).st_mode), 0o600)
        self.assertEqual(certs.active_pair(self.app), (f["cert"], f["key"]))
        st = certs.status(self.app)
        self.assertTrue(88 < st["certificate"]["days_left"] <= 90, st)
        self.assertEqual(st["certificate"]["domains"], ["portal.example.com"])
        again = certs.issue(self.app)
        self.assertTrue(again["ok"] and not again["renewed"])                  # 90 days left, not due
        self.assertEqual(self.ca.orders, 1)
        forced = certs.issue(self.app, force=True)
        self.assertTrue(forced["renewed"])
        self.assertEqual(self.ca.orders, 2)

    def test_a_certificate_that_is_close_to_expiry_is_renewed(self):
        self.ca.days = 10                                                       # the fake CA hands out 10-day certificates
        self.assertTrue(certs.issue(self.app)["renewed"])
        self.ca.days = 90
        r = certs.issue(self.app)                                               # 10 days left is inside the 30-day window
        self.assertTrue(r["renewed"], r)
        self.assertEqual(self.ca.orders, 2)

    def test_changing_the_names_gets_a_new_certificate(self):
        certs.issue(self.app)
        self.app.cfg.set("web", "web", "acme_domains", "portal.example.com, www.example.com")
        self.assertTrue(certs.issue(self.app)["renewed"])

    def test_the_web_server_picks_the_new_certificate_up_without_a_restart(self):
        from lshme.web import server
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)
        certs.register_context(ctx)
        try:
            self.assertEqual(certs.reload_contexts(self.app), 0)                # nothing issued yet
            certs.issue(self.app)
            self.assertGreaterEqual(certs.reload_contexts(self.app), 1)
            self.assertEqual(server.ensure_certificate(self.app), certs.active_pair(self.app))      # the web interface now serves it
        finally:
            certs._contexts.remove(ctx)

    def test_failures_are_recorded_and_the_old_certificate_is_kept(self):
        certs.issue(self.app)
        before = open(certs.files(self.app)["cert"]).read()
        self.ca.reject = True
        r = certs.issue(self.app, force=True)
        self.assertFalse(r["ok"])
        self.assertIn("rejected by the test CA", r["message"])
        self.assertEqual(open(certs.files(self.app)["cert"]).read(), before)
        self.assertIn("rejected", certs.status(self.app)["last_error"])

    def test_nothing_happens_when_switched_off_or_without_a_public_name(self):
        self.app.cfg.set("web", "web", "acme", "no")
        self.assertIn("switched off", certs.issue(self.app)["message"])
        self.assertIsNone(certs.active_pair(self.app))
        self.app.cfg.set("web", "web", "acme", "yes")
        self.app.cfg.set("web", "web", "acme_domains", "192.168.1.3")
        certs.check_domains = self.real_check
        r = certs.issue(self.app)
        self.assertFalse(r["ok"])
        self.assertIn("not a public domain name", r["message"])
        self.assertEqual(self.ca.orders, 0)                                     # the authority is never bothered


def acme_ok(domain):
    return True


if __name__ == "__main__":
    unittest.main()
