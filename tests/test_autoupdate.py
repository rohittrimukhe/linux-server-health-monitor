# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Safe automatic update: up to date, updated, rolled back when the new code fails, postponed while busy, off, unsafe downloads refused."""
import io
import json
import os
import shutil
import tarfile
import tempfile
import unittest

from fakes import ROOT, TempHome
from lshme.app import App
from lshme.ops import autoupdate, scheduler

NEW = "f" * 40
OLD = "a" * 40


class Done(object):
    def __init__(self, code=0, out=b""):
        self.returncode, self.stdout = code, out


def tarball(files, top="linux-server-health-monitor-" + NEW):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in files.items():
            data = text.encode("utf-8")
            ti = tarfile.TarInfo(name if name.startswith("/") or name.startswith("..") else top + "/" + name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome({"main": "[update]\nauto = yes\nwait_minutes = 0\nkeep_backups = 2\nnotify = no\n"})
        self.app = App(ROOT, home=self.home.dir)
        self.parent = tempfile.mkdtemp()
        self.dir = os.path.join(self.parent, "lshme")
        os.makedirs(os.path.join(self.dir, "lshme"))
        with open(os.path.join(self.dir, "lshme", "__init__.py"), "w") as f:
            f.write('VERSION = "1.4.0"\nOLD = True\n')
        with open(os.path.join(self.dir, ".lrs_commit"), "w") as f:
            f.write(OLD)
        self.selftest_ok = True
        self.files = {"lshme/__init__.py": 'VERSION = "1.4.1"\nNEW = True\n', "docs/README.md": "new docs", "healthcheck": "#!/bin/sh\n"}
        self.calls = []

    def tearDown(self):
        self.home.cleanup()
        shutil.rmtree(self.parent, ignore_errors=True)

    def fetch(self, url, timeout=30):
        if "api.github.com" in url:
            return json.dumps({"sha": NEW, "commit": {"message": "Better reports", "committer": {"date": "2026-11-22T20:00:00Z"}}}).encode()
        return tarball(self.files)

    def fake_run(self, args, timeout=120):
        self.calls.append(args)
        if "-c" in args:                                              # the self-test of the new code
            return Done(0 if self.selftest_ok else 1, b"1.4.1\n" if self.selftest_ok else b"ImportError: broken")
        if args[:2] == ["systemctl", "is-active"]:
            return Done(0)
        return Done(0)

    def updater(self):
        return autoupdate.Updater(self.app, app_dir=self.dir, fetch=self.fetch, run=self.fake_run, sleep=lambda s: None)

    def read(self, *p):
        with open(os.path.join(self.dir, *p)) as f:
            return f.read()

    def test_update_backs_up_replaces_tests_and_restarts(self):
        r = self.updater().update(auto=True)
        self.assertEqual(r["result"], "updated", r)
        self.assertIn("NEW = True", self.read("lshme", "__init__.py"))
        self.assertEqual(self.read(".lrs_commit").strip(), NEW)
        self.assertEqual(self.read("docs", "README.md"), "new docs")
        backups = [x for x in os.listdir(self.parent) if x.startswith("lshme.pre-upgrade.")]
        self.assertEqual(len(backups), 1)
        self.assertIn("OLD = True", open(os.path.join(self.parent, backups[0], "lshme", "__init__.py")).read())
        self.assertIn(["systemctl", "restart", "lshme-watch"], self.calls)
        st = self.updater().state()
        self.assertEqual((st["result"], st["installed"]), ("updated", NEW))
        self.assertEqual(self.updater().update(auto=True)["result"], "current")           # the next night: nothing to do

    def test_a_new_version_that_does_not_start_is_rolled_back(self):
        self.selftest_ok = False
        r = self.updater().update(auto=True)
        self.assertEqual(r["result"], "rolled back", r)
        self.assertIn("OLD = True", self.read("lshme", "__init__.py"))                     # the old program is back
        self.assertEqual(self.read(".lrs_commit").strip(), OLD)
        self.assertIn("restored", r["message"])

    def test_waits_for_running_reports_then_postpones(self):
        u = self.updater()
        u.busy = lambda: "another LSHME run is in progress (pid=4242)"                    # a health check or report is running
        r = u.update(auto=True)
        self.assertEqual(r["result"], "postponed")
        self.assertIn("OLD = True", self.read("lshme", "__init__.py"))

    def test_switched_off_and_unsafe_downloads(self):
        self.app.cfg.set("main", "update", "auto", "no")
        self.assertEqual(self.updater().update(auto=True)["result"], "off")
        self.app.cfg.set("main", "update", "auto", "yes")
        self.files = {"../../etc/evil": "x", "lshme/__init__.py": "VERSION = '9'"}
        r = self.updater().update(auto=True)
        self.assertEqual(r["result"], "failed")
        self.assertIn("nothing was changed", r["message"])
        self.assertIn("OLD = True", self.read("lshme", "__init__.py"))

    def test_daily_timer_at_21(self):
        job = scheduler.UpdateJob(self.app.cfg)
        self.assertEqual((job.enabled, job.on_calendar(), job.cron()), (True, "*-*-* 21:00:00", "0 21 * * *"))
        self.assertIn("update auto", scheduler.command_line("/opt/lshme/healthcheck", [], job, 2))


class UpdateWebTests(unittest.TestCase):
    def test_status_and_admin_only_actions(self):
        from test_web import WebCase
        case = type("T", (WebCase,), {"runTest": lambda self: None})()
        case.setUp()
        try:
            r = case.call("GET", "/api/update", user="viewer")
            self.assertEqual(r.status, 200)
            self.assertEqual(r.json["settings"]["time"], "21:00")
            self.assertEqual(case.call("POST", "/api/update", {"action": "now"}, user="viewer").status, 403)
            started = []
            orig = autoupdate.start_background
            autoupdate.start_background = lambda app, launcher: started.append(launcher) or True
            try:
                self.assertEqual(case.call("POST", "/api/update", {"action": "now"}, user="admin").status, 202)
            finally:
                autoupdate.start_background = orig
            self.assertEqual(len(started), 1)
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
