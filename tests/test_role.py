# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Central, standalone or member: the role is stored, and an installed program keeps only the files its role uses."""
import os
import tempfile
import unittest

from fakes import TempHome
from lshme.app import App
from lshme.ops import role

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_tree(base):
    for rel in ("lshme/web/static/app.js", "lshme/ops/role.py", "docs/guide.md", "tools/deploy_fleet.sh", "tools/upgrade_from_github.sh",
                "tools/demo_reports.py", "tools/release_check.sh", "packaging/lshme.spec"):
        p = os.path.join(base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")


def present(base):
    out = []
    for d, _, files in os.walk(base):
        out += [os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/") for f in files]
    return sorted(out)


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        make_tree(self.dir)

    def test_central_keeps_everything(self):
        self.assertEqual(role.prune(self.dir, "central"), [])
        self.assertIn("tools/deploy_fleet.sh", present(self.dir))

    def test_standalone_keeps_the_dashboard_but_not_the_fleet_tools(self):
        role.prune(self.dir, "standalone")
        files = present(self.dir)
        self.assertIn("lshme/web/static/app.js", files)
        self.assertIn("docs/guide.md", files)
        self.assertIn("tools/upgrade_from_github.sh", files)
        self.assertNotIn("tools/deploy_fleet.sh", files)

    def test_member_keeps_only_what_it_runs(self):
        role.prune(self.dir, "member")
        self.assertEqual(present(self.dir), ["lshme/ops/role.py", "tools/upgrade_from_github.sh"])

    def test_a_source_checkout_is_never_pruned(self):
        self.assertFalse(role.installed_tree(self.dir))             # not under /opt
        self.assertFalse(role.installed_tree("/opt/nothing-here"))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome({"main": "[general]\ncustomer_short = PDHHK\n[fleet]\nenabled = yes\nlive = yes\n"})
        self.app = App(ROOT, home=self.home.dir)

    def tearDown(self):
        self.home.cleanup()

    def test_no_role_behaves_as_central(self):
        self.assertEqual(role.get(self.app.cfg), "")
        self.assertTrue(role.has_fleet(self.app.cfg))

    def test_standalone_turns_the_fleet_off_and_is_stored(self):
        self.assertEqual(role.apply(self.app, "standalone"), [])      # a source checkout: nothing removed
        self.assertEqual(role.get(self.app.cfg), "standalone")
        self.assertTrue(role.is_standalone(self.app.cfg))
        self.assertFalse(role.has_fleet(self.app.cfg))
        self.assertFalse(self.app.cfg.getbool("main", "fleet", "enabled", True))
        conf = open(os.path.join(self.home.dir, "config", "main.conf"), encoding="utf-8").read()
        self.assertIn("role = standalone", conf)

    def test_member_then_central_again(self):
        role.apply(self.app, "member")
        self.assertTrue(role.is_member(self.app.cfg))
        self.assertFalse(self.app.cfg.getbool("main", "fleet", "live", True))
        role.apply(self.app, "central")
        self.assertTrue(role.has_fleet(self.app.cfg))
        self.assertTrue(self.app.cfg.getbool("main", "fleet", "live", False))

    def test_unknown_role_is_refused(self):
        with self.assertRaises(ValueError):
            role.apply(self.app, "boss")


if __name__ == "__main__":
    unittest.main()
