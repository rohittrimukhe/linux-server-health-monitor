# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""vitals web: users and passwords from the command line, the service unit, and the settings page."""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fakes import ROOT, FakeRunner, TempHome
from lshme.app import App
from lshme.ops import webservice
from lshme.ui import cli, tui
from lshme.ui.tui_kit import UI
from lshme.web import auth
from test_tui import MemoryBackend

auth.ITERATIONS = 2000
GOOD = "Str0ngPassw0rd"


class WebCliTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()

    def tearDown(self):
        self.home.cleanup()

    def run_cli(self, *argv, **kw):
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(kw.get("stdin", ""))), redirect_stdout(buf):
            code = cli.main(["--home", self.home.dir, "web"] + list(argv))
        return code, buf.getvalue()

    def test_first_password_creates_the_administrator(self):
        code, out = self.run_cli("passwd", "--password-stdin", stdin=GOOD + "\n")
        self.assertEqual(code, 0)
        app = App(ROOT, home=self.home.dir)
        self.assertEqual(auth.Users(app).verify("admin", GOOD), "admin")
        code, out = self.run_cli("users")
        self.assertIn("admin", out)

    def test_add_change_and_remove_users(self):
        self.run_cli("passwd", "--password-stdin", stdin=GOOD + "\n")
        code, out = self.run_cli("useradd", "bob", "--role", "viewer", "--password-stdin", stdin=GOOD + "1\n")
        self.assertEqual(code, 0)
        app = App(ROOT, home=self.home.dir)
        self.assertEqual(auth.Users(app).verify("bob", GOOD + "1"), "viewer")
        self.assertEqual(self.run_cli("useradd", "bob", "--password-stdin", stdin=GOOD + "\n")[0], 2)   # already exists
        self.assertEqual(self.run_cli("passwd", "bob", "--password-stdin", stdin="Fresh1PasswordX\n")[0], 0)
        app = App(ROOT, home=self.home.dir)
        self.assertEqual(auth.Users(app).verify("bob", "Fresh1PasswordX"), "viewer")  # still read-only
        self.assertEqual(self.run_cli("userdel", "bob")[0], 0)
        self.assertEqual(self.run_cli("userdel", "admin")[0], 2)                      # the last administrator stays

    def test_weak_passwords_are_refused(self):
        code, out = self.run_cli("passwd", "--password-stdin", stdin="short\n")
        self.assertEqual(code, 2)
        self.assertIn("at least", out)

    def test_status_and_url(self):
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("Set an administrator password first", out)
        code, out = self.run_cli("url")
        self.assertIn("https://", out)
        self.assertIn(":8443/", out)

    def test_certificate_status_explains_a_private_name(self):
        code, out = self.run_cli("cert", "status")
        self.assertEqual(code, 0, out)
        self.assertIn("self-signed", out)
        self.home.cfg.set("web", "web", "tls_source", "letsencrypt")
        self.home.cfg.set("web", "web", "acme_domains", "")
        self.home.cfg.save("web")
        code, out = self.run_cli("cert", "issue")
        self.assertEqual(code, 1)
        self.assertTrue("public" in out or "FQDN" in out, out)

    def test_install_needs_a_password_first(self):
        code, out = self.run_cli("install")
        self.assertEqual(code, 2)
        self.assertIn("passwd admin", out)

    def test_start_refuses_without_users(self):
        self.home.cfg.set("web", "web", "system_users", "")
        self.home.cfg.save("web")
        code, out = self.run_cli("start")
        self.assertEqual(code, 2)
        self.assertIn("no web users", out)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.units = os.path.join(self.home.dir, "units")
        os.makedirs(self.units)
        self.app = App(ROOT, home=self.home.dir)
        self.app.cfg.set("schedule", "scheduler", "systemd_dir", self.units)
        self.app.cfg.save("schedule")
        self.app.reload()
        self.app.runner = FakeRunner({"systemctl": (0, "active\n")})

    def tearDown(self):
        self.home.cleanup()

    def test_unit_is_limited_and_owned(self):
        if not os.path.isdir("/run/systemd/system"):
            self.skipTest("no systemd")
        ok, message = webservice.install(self.app)
        self.assertTrue(ok, message)
        text = open(webservice.unit_path(self.app), encoding="utf-8").read()
        for line in ("ExecStart=", "web start", "MemoryMax=256M", "TasksMax=64", "# LSHME_MANAGED", "Restart=on-failure"):
            self.assertIn(line, text)
        self.assertIn("--home", text)  # it runs this installation
        self.assertEqual(webservice.state(self.app), "active")
        self.assertIn("systemctl restart lshme-web.service", self.app.runner.calls)  # a changed address takes effect
        self.assertTrue(webservice.uninstall(self.app))
        self.assertFalse(webservice.installed(self.app))
        self.assertFalse(webservice.uninstall(self.app))

    def test_another_installations_unit_is_left_alone(self):
        with open(webservice.unit_path(self.app), "w", encoding="utf-8") as f:
            f.write("[Service]\nExecStart=/opt/other/healthcheck --config-dir /etc/other web start\n# LSHME_MANAGED\n")
        self.assertFalse(webservice.uninstall(self.app))
        self.assertTrue(webservice.installed(self.app))

    def test_without_systemd_it_explains(self):
        self.app.runner = FakeRunner()
        ok, message = webservice.install(self.app)
        self.assertFalse(ok)
        self.assertIn("vitals web start", message)

    def test_schedule_removal_leaves_the_web_unit_alone(self):
        from lshme.ops import scheduler
        with open(webservice.unit_path(self.app), "w", encoding="utf-8") as f:
            f.write("[Service]\nExecStart=/opt/lshme/healthcheck --home %s web start\n# LSHME_MANAGED\n" % self.home.dir)
        scheduler.uninstall(self.app.cfg, self.app.runner, self.app.global_args())
        self.assertTrue(webservice.installed(self.app))


class WebPageTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.app = App(ROOT, home=self.home.dir)

    def tearDown(self):
        self.home.cleanup()

    def ctx(self, keys):
        self.be = MemoryBackend(keys)
        return tui.Ctx(self.app, UI(self.be))

    def test_page_shows_address_service_and_saves_settings(self):
        ctx = self.ctx(["DOWN", "ENTER", "CTRL_U"] + list("0.0.0.0") + ["ENTER", "F10"])
        tui.page_web(ctx)
        self.assertTrue(self.be.saw("Web interface"))
        self.assertTrue(self.be.saw("none yet"))
        self.app.reload()
        self.assertEqual(self.app.cfg.get("web", "web", "bind"), "0.0.0.0")

    def test_set_password_button(self):
        keys = ["END", "UP", "UP", "UP", "UP", "ENTER"]  # buttons are Set password, Install, Remove, Save, Back
        keys += ["ENTER"] + list(GOOD) + ["ENTER"] + list(GOOD) + ["ENTER"] + ["ENTER", "ESC"]
        ctx = self.ctx(keys)
        tui.page_web(ctx)
        self.app.reload()
        self.assertEqual(auth.Users(self.app).verify("admin", GOOD), "admin")

    def test_menu_lists_the_page(self):
        ctx = self.ctx(["ESC"])
        tui.start(ctx)
        self.assertTrue(self.be.saw("Web interface"))


if __name__ == "__main__":
    unittest.main()
