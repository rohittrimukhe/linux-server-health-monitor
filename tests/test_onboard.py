# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Add a Linux server from the web: one password login adds the key, then install, settings, start, fleet entry.
The password is never stored, logged or put on a command line."""
import json
import os
import time
import unittest

from fakes import FakeRunner, TempHome
from lshme.app import App
from lshme.ops import onboard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Recorder(FakeRunner):
    def __init__(self, fail_on=None):
        FakeRunner.__init__(self, {"ssh": (0, "0\nOracle Linux Server 9.8\n3.9\noradb1"), "ssh-keygen": (0, "")}, tools=["ssh", "ssh-keygen", "scp"])
        self.full, self.fail_on = [], fail_on

    def run(self, args, timeout=None, input_text=None, env=None):
        self.full.append({"args": [str(a) for a in args], "env": dict(env or {}), "input": input_text or ""})
        if self.fail_on and any(self.fail_on in str(a) for a in args):
            from lshme.core.runner import Result
            return Result(list(args), 1, "", "Permission denied, please try again.")
        return FakeRunner.run(self, args, timeout=timeout, input_text=input_text, env=env)


class OnboardTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome({"smtp": "[smtp]\nhost = mail.example\n", "main": "[general]\ncustomer_short = PDHHK\n"})
        self.app = App(ROOT, home=self.home.dir)
        self.app.cfg.set("credentials", "smtp", "password", "mail-secret")
        self.orig_key = onboard.ensure_key
        onboard.ensure_key = lambda app: "ssh-ed25519 AAAATEST lrs-vitals-central"

    def tearDown(self):
        onboard.ensure_key = self.orig_key
        self.home.cleanup()

    def wait(self, jid):
        for _ in range(200):
            st = onboard.status(jid)
            if st["done"]:
                return st
            time.sleep(0.02)
        self.fail("onboarding did not finish")

    def test_password_once_then_install_and_fleet_entry(self):
        self.app.runner = r = Recorder()
        st = self.wait(onboard.start_job(self.app, "oradb1", "10.0.0.31", 22, "root", "S3cret-pw!", "Oracle", "Production"))
        self.assertTrue(st["ok"], st["steps"])
        self.assertEqual([s["state"] for s in st["steps"]], ["ok"] * 8)
        first = r.full[0]
        self.assertIn("PreferredAuthentications=password,keyboard-interactive", first["args"])
        self.assertEqual(first["env"].get("LRS_ONBOARD_PW"), "S3cret-pw!")              # only to the one login helper, through its environment
        for c in r.full:
            self.assertNotIn("S3cret-pw!", " ".join(c["args"]))                           # never on a command line
        for c in r.full[1:]:
            self.assertNotIn("LRS_ONBOARD_PW", c["env"])
            self.assertIn("BatchMode=yes", c["args"])                                      # after the first login only the key is used
        self.assertNotIn("S3cret-pw!", json.dumps(st))
        self.assertEqual(self.app.cfg.get("servers", "server:oradb1", "host", ""), "10.0.0.31")
        self.assertEqual(self.app.cfg.get("servers", "server:oradb1", "environment", ""), "Production")
        conf = open(os.path.join(self.home.dir, "config", "servers.conf"), encoding="utf-8").read()
        self.assertNotIn("S3cret-pw!", conf)
        sent = " ".join(c["args"][-1] for c in r.full)
        self.assertIn("vitals watch install", sent)
        self.assertIn("customer_short PDHHK", sent)

    def test_wrong_password_stops_before_anything_is_installed(self):
        self.app.runner = r = Recorder(fail_on="PreferredAuthentications=password")
        st = self.wait(onboard.start_job(self.app, "oradb2", "10.0.0.32", 22, "root", "wrong"))
        self.assertFalse(st["ok"])
        self.assertEqual(st["steps"][0]["state"], "failed")
        self.assertIn("password login", st["steps"][0]["detail"])
        self.assertEqual(len(r.full), 1)                                                   # nothing else was tried
        self.assertEqual(self.app.cfg.get("servers", "server:oradb2", "host", ""), "")

    def test_settings_carry_only_the_mail_password(self):
        import base64
        import io
        import tarfile
        self.app.cfg.set("credentials", "san:hhk-de", "password", "san-secret")
        data = base64.b64decode(onboard.settings_tar(self.app))
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            names = t.getnames()
            creds = t.extractfile("credentials.smtp").read().decode("utf-8")
        self.assertIn("smtp.conf", names)
        self.assertNotIn("main.conf", names)                                                # SAN, hardware and fleet settings stay central
        self.assertIn("mail-secret", creds)
        self.assertNotIn("san-secret", creds)


if __name__ == "__main__":
    unittest.main()
