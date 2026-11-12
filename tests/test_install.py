# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Installer/upgrade/uninstaller lifecycle in a sandbox (needs root; touches only temp dirs)."""
import os
import shutil
import subprocess
import tempfile
import unittest

from fakes import ROOT, slurp


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "installer tests need root")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="lshme-inst-")
        self.prefix = os.path.join(self.t, "opt", "lshme")
        self.conf = os.path.join(self.t, "site", "config")
        self.bin = os.path.join(self.t, "bin", "healthcheck")
        self.args = ["--prefix", self.prefix, "--config", self.conf, "--bin", self.bin]

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def sh(self, *cmd):
        p = subprocess.run(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        return p.returncode, p.stdout.decode("utf-8", "replace")

    def test_lifecycle(self):
        rc, out = self.sh(os.path.join(ROOT, "install.sh"), *self.args)
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.islink(self.bin))
        self.assertEqual(oct(os.stat(self.conf).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(os.path.join(self.conf, "credentials.conf")).st_mode & 0o777), "0o600")
        rc, out = self.sh(self.bin, "--config-dir", self.conf, "version")
        self.assertIn("Linux Server Health Monitor Enterprise", out)
        # customise config, then upgrade: config must survive, previous code kept for rollback
        with open(os.path.join(self.conf, "main.conf"), "a") as f:
            f.write("\n# site customisation marker\n")
        rc, out = self.sh(os.path.join(ROOT, "install.sh"), *self.args)
        self.assertEqual(rc, 0, out)
        self.assertIn("Upgrading", out)
        self.assertIn("site customisation marker", slurp(os.path.join(self.conf, "main.conf")))
        self.assertTrue(os.path.isdir(self.prefix + ".previous"))
        # uninstall keeps data; purge removes it
        rc, out = self.sh(os.path.join(ROOT, "uninstall.sh"), "--yes", *self.args)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.prefix) or os.path.lexists(self.bin))
        self.assertTrue(os.path.isdir(self.conf))
        rc, out = self.sh(os.path.join(ROOT, "uninstall.sh"), "--yes", "--purge", *self.args)
        self.assertFalse(os.path.exists(self.conf))

    def test_check_only(self):
        rc, out = self.sh(os.path.join(ROOT, "install.sh"), "--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("Dependency check", out)


if __name__ == "__main__":
    unittest.main()
