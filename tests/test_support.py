# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Support export: settings go out with every secret blanked; import only into a folder of its own."""
import os
import tarfile
import tempfile
import unittest

from fakes import TempHome
from lshme.app import App
from lshme.ops import support

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NL = chr(10)


class RedactTests(unittest.TestCase):
    def test_secrets_are_blanked_and_the_rest_kept(self):
        text = NL.join(["[smtp]", "host = mail.example", "password = Mail-Secret1", "[users]", "admin = pbkdf2_sha256$1$abc$def",
                        "[acme]", "api_token = tok-123", "[bmc:x]", "address = 10.0.0.5", "username = monitor", "community = public2",
                        "[general]", "customer = PDHHK", "# password = comment stays"])
        out = support.redact_conf(text)
        for secret in ("Mail-Secret1", "pbkdf2_sha256", "tok-123", "public2"):
            self.assertNotIn(secret, out)
        for kept in ("host = mail.example", "address = 10.0.0.5", "username = monitor", "customer = PDHHK", "# password = comment stays"):
            self.assertIn(kept, out)


class ExportTests(unittest.TestCase):
    def test_export_has_no_credentials_and_imports_into_its_own_folder(self):
        home = TempHome({"smtp": "[smtp]" + NL + "host = mail.example" + NL, "main": "[general]" + NL + "customer = PDHHK" + NL})
        tmp = tempfile.mkdtemp()
        try:
            app = App(ROOT, home=home.dir)
            app.cfg.set("credentials", "smtp", "password", "very-secret-pw")
            app.save_config("credentials")
            path, notes = support.export(app, os.path.join(tmp, "x.tar.gz"), days=7, reports=1)
            with tarfile.open(path) as t:
                names = t.getnames()
                blob = b"".join(t.extractfile(m).read() for m in t.getmembers() if m.isfile())
            self.assertIn("manifest.json", names)
            self.assertIn("live_snapshot.json", names)
            self.assertNotIn("config/credentials.conf", names)
            self.assertNotIn(b"very-secret-pw", blob)
            with self.assertRaises(ValueError):
                support.import_to(path, "/etc/lshme")
            info = support.import_to(path, os.path.join(tmp, "imp"))
            self.assertEqual(info["days"], 7)
            main = open(os.path.join(tmp, "imp", "config", "main.conf"), encoding="utf-8").read()
            self.assertIn("live = no", main)
        finally:
            home.cleanup()


if __name__ == "__main__":
    unittest.main()
