# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""End-to-end tests through the real launcher, plus scripted wizard and menu sessions."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout

from fakes import ROOT, TempHome, slurp

LAUNCHER = os.path.join(ROOT, "healthcheck")


def hc(home, *args, **kw):
    env = dict(os.environ)
    env.update(kw.get("env", {}))
    p = subprocess.run([LAUNCHER, "--home", home, "--no-color"] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, env=env, timeout=600)
    return p.returncode, p.stdout.decode("utf-8", "replace")


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="lshme-cli-")
        os.makedirs(os.path.join(cls.home, "config"))
        for f in os.listdir(os.path.join(ROOT, "config")):
            shutil.copy(os.path.join(ROOT, "config", f), os.path.join(cls.home, "config", f))
            os.chmod(os.path.join(cls.home, "config", f), 0o600)  # as install.sh does
        # keep the suite fast and deterministic
        with open(os.path.join(cls.home, "config", "main.conf"), "a") as f:
            f.write("\n")
        rc, out = hc(cls.home, "config", "set", "main", "security", "check_updates", "no")
        assert rc == 0, out

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_01_run_all_formats(self):
        rc, out = hc(self.home, "run", "--formats", "all", "--report-type", "full")
        self.assertEqual(rc, 0, out)
        for ext in (".html", ".pdf", ".xlsx", ".pptx", ".txt", ".csv", ".json"):
            self.assertIn(ext, out)
        self.assertIn("Module", out)

    def test_02_c_locale(self):
        rc, out = hc(self.home, "run", "--formats", "txt", "-q", env={"LC_ALL": "C", "LANG": "C", "PYTHONIOENCODING": ""})
        self.assertEqual(rc, 0, out)

    def test_03_exit_status_and_nagios(self):
        rc, out = hc(self.home, "nagios")
        self.assertIn(rc, (0, 1, 2))
        self.assertTrue(out.startswith("LSHME "))
        rc2, out2 = hc(self.home, "run", "--no-report", "-q", "--exit-status")
        self.assertIn(rc2, (0, 1, 2))

    def test_04_agent_json(self):
        rc, out = hc(self.home, "agent", "--days", "2")
        data = json.loads(out)
        self.assertEqual(data["days"], 2)
        self.assertIn("health", data)

    def test_05_history_baseline_maintenance(self):
        self.assertEqual(hc(self.home, "history")[0], 0)
        self.assertEqual(hc(self.home, "baseline", "save")[0], 0)
        rc, out = hc(self.home, "baseline", "compare")
        self.assertIn("drifted", out)
        self.assertEqual(hc(self.home, "maintenance", "on", "--minutes", "5")[0], 0)
        rc, out = hc(self.home, "maintenance", "status")
        self.assertIn("Active until", out)
        hc(self.home, "maintenance", "off")

    def test_06_config_tools(self):
        rc, out = hc(self.home, "config", "validate")
        self.assertEqual(rc, 0, out)
        rc, out = hc(self.home, "config", "backup")
        archive = out.strip().split(": ", 1)[1]
        self.assertTrue(os.path.exists(archive))
        hc(self.home, "config", "set", "reports", "reports", "title", "Changed Title")
        self.assertEqual(hc(self.home, "config", "restore", archive)[0], 0)
        self.assertNotIn("Changed Title", slurp(os.path.join(self.home, "config", "reports.conf")))
        rc, out = hc(self.home, "config", "set", "nosuch", "a", "b", "c")
        self.assertEqual(rc, 2)

    def test_07_schedule_show_and_retention(self):
        rc, out = hc(self.home, "schedule", "show")
        self.assertEqual(rc, 0, out)
        self.assertIn("--schedule daily", out)
        self.assertEqual(hc(self.home, "retention", "preview")[0], 0)

    def test_08_bad_input(self):
        rc, out = hc(self.home, "run", "--modules", "nosuchmodule", "--no-report")
        self.assertEqual(rc, 2)
        self.assertIn("unknown module", out)
        rc, out = hc(self.home, "run", "--formats", "docx")
        self.assertEqual(rc, 2)

    def test_09_lock_busy(self):
        from lshme.core.lock import RunLock
        with RunLock(os.path.join(self.home, "data", "lshme.lock")):
            rc, out = hc(self.home, "run", "--no-report", "-q")
        self.assertEqual(rc, 75)
        self.assertIn("another LSHME run", out)

    def test_10_fleet_over_fake_ssh(self):
        """Central collection through a fake 'ssh' that runs the agent command locally."""
        agent_home = TempHome()
        shutil.copy(os.path.join(self.home, "config", "main.conf"), os.path.join(agent_home.dir, "config", "main.conf"))
        bindir = tempfile.mkdtemp()
        ssh = os.path.join(bindir, "ssh")
        with open(ssh, "w") as f:
            f.write('#!/bin/sh\nfor last; do :; done\ncase "$*" in *unreachable*) echo "ssh: no route" >&2; exit 255;; esac\nexec sh -c "$last"\n')
        os.chmod(ssh, 0o755)
        cmd = "%s --home %s agent --days {days}" % (LAUNCHER, agent_home.dir)
        hc(self.home, "config", "set", "servers", "server:node1", "host", "node1")
        hc(self.home, "config", "set", "servers", "server:node1", "remote_command", cmd)
        hc(self.home, "config", "set", "servers", "server:dead", "host", "unreachable")
        rc, out = hc(self.home, "fleet", "run", "--formats", "html,txt", env={"PATH": bindir + os.pathsep + os.environ["PATH"]})
        self.assertEqual(rc, 0, out)
        self.assertIn("node1", out)
        self.assertIn("UNREACHABLE", out)
        self.assertIn("fleet_", out)
        agent_home.cleanup()
        shutil.rmtree(bindir)


class InteractiveTest(unittest.TestCase):
    def scripted(self, answers):
        from lshme.ui import console
        it = iter(answers)

        def fake_input(prompt=""):
            try:
                return next(it)
            except StopIteration:
                raise EOFError("script exhausted at prompt: %s" % prompt)
        console.set_input(fake_input)
        return console

    def test_wizard_steps(self):
        from lshme.app import App
        from lshme.ui.wizard import Wizard
        home = TempHome()
        app = App(ROOT, home=home.dir)
        console = self.scripted([
            # identity
            "Example Ltd", "ACME", "Production", "DC1", "Ops Team", "",
            # smtp: method, host, security, port, username(empty), from, to, cc, bcc, critical_to, policy, attach, maxmb, test?
            "smtp", "smtp.example.com", "starttls", "587", "", "lshme@example.com", "ops@example.com", "", "", "oncall@example.com",
            "issues", "pdf,html", "10", "n",
            # retention (10 numbers + archive + auto)
            "14", "60", "400", "7", "30", "90", "90", "365", "20", "3",
            # space optimisation: enable, min free %, target %, cap GB, min keep days
            "y", "12", "20", "50", "2", "", "y",
        ])
        buf = io.StringIO()
        w = Wizard(app)
        with redirect_stdout(buf):
            w.step_general()
            w.step_smtp()
            w.step_retention()
        app.reload()
        self.assertEqual(app.cfg.get("main", "general", "company"), "Example Ltd")
        self.assertEqual(app.cfg.get("smtp", "smtp", "host"), "smtp.example.com")
        self.assertEqual(app.cfg.get("smtp", "smtp", "send_policy"), "issues")
        self.assertEqual(app.cfg.get("retention", "retention", "daily_days"), "14")
        self.assertEqual(app.cfg.get("retention", "retention", "max_report_dir_gb"), "50")
        self.assertEqual(app.cfg.get("retention", "retention", "free_target_percent"), "20")
        self.assertEqual(oct(os.stat(app.cfg.path("smtp")).st_mode & 0o777), "0o600")
        self.assertTrue(os.listdir(app.paths.config_backup_dir))
        console.set_input(input)
        home.cleanup()

    def test_wizard_rejects_bad_values(self):
        from lshme.app import App
        from lshme.ui.wizard import Wizard
        home = TempHome()
        app = App(ROOT, home=home.dir)
        nums = ["abc", "85"] + ["90"] * 15 + ["n"]  # first threshold answer invalid, then valid
        console = self.scripted(nums)
        with redirect_stdout(io.StringIO()) as buf:
            Wizard(app).step_thresholds()
        self.assertIn("Enter a number", buf.getvalue())
        app.reload()
        self.assertEqual(app.cfg.get("thresholds", "cpu", "warning"), "85")
        console.set_input(input)
        home.cleanup()

    def test_menu_doctor_and_exit(self):
        from lshme.app import App
        from lshme.ui.menu import Menu
        home = TempHome()
        app = App(ROOT, home=home.dir)
        console = self.scripted(["18", "", "3", "n", "", "0"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            Menu(app).loop()
        out = buf.getvalue()
        self.assertIn("Linux Server Health Monitor Enterprise", out)
        self.assertIn("Self-diagnostics", out)
        self.assertIn("SQLite history database", out)
        self.assertIn("Storage & Filesystems", out)
        console.set_input(input)
        home.cleanup()


if __name__ == "__main__":
    unittest.main()
