# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import json
import os
import time
import unittest

from fakes import ROOT, slurp, FakeRunner, TempHome
from lshme.core.lock import LockBusy, RunLock
from lshme.core.model import ModuleResult, RunResult
from lshme.engine import scoring
from lshme.ops import exporters, fleet, retention, scheduler


class SchedulerTest(unittest.TestCase):
    def test_render_and_validate(self):
        home = TempHome({"schedule": "[schedule:weekly]\nweekday = fri\ntime = 22:30\n[schedule:bad]\nfrequency = weekly\nweekday = xyz\ntime = 25:00\n"
                                     "[schedule:custom1]\nfrequency = custom\ncalendar = Mon..Fri *-*-* 06:30:00\nmodules = database\n"})
        s = dict((x.name, x) for x in scheduler.schedules(home.cfg))
        self.assertEqual(s["daily"].cron(), "0 7 * * *")
        self.assertEqual(s["weekly"].cron(), "30 22 * * 5")
        self.assertEqual(s["weekly"].on_calendar(), "Fri *-*-* 22:30:00")
        self.assertEqual(s["monthly"].on_calendar(), "*-*-01 09:00:00")
        self.assertEqual(s["custom1"].on_calendar(), "Mon..Fri *-*-* 06:30:00")
        self.assertEqual(len(s["bad"].validate()), 2)
        rep = scheduler.install(home.cfg, FakeRunner(), "/opt/lshme/healthcheck", ["--config-dir", "/etc/lshme"], dry_run=True)
        self.assertTrue(rep["errors"])  # the bad schedule blocks installation
        home.cleanup()

    def test_install_systemd_and_cron(self):
        home = TempHome()
        sd = os.path.join(home.dir, "systemd")
        home.cfg.set("schedule", "scheduler", "backend", "systemd")
        home.cfg.set("schedule", "scheduler", "systemd_dir", sd)
        home.cfg.set("schedule", "schedule:monthly", "enabled", "no")
        r = FakeRunner({"systemctl": (0, "")})
        rep = scheduler.install(home.cfg, r, "/opt/lshme/healthcheck", ["--config-dir", "/etc/lshme"])
        self.assertEqual(rep["errors"], [])
        self.assertEqual(sorted(os.listdir(sd)), ["lshme-daily.service", "lshme-daily.timer", "lshme-weekly.service", "lshme-weekly.timer"])
        svc = slurp(os.path.join(sd, "lshme-daily.service"))
        self.assertIn("ExecStart=/opt/lshme/healthcheck --config-dir /etc/lshme run --schedule daily --quiet --retries 2", svc)
        self.assertIn("OnCalendar=*-*-* 07:00:00", slurp(os.path.join(sd, "lshme-daily.timer")))
        self.assertIn("systemctl enable --now lshme-daily.timer", r.calls)
        removed = scheduler.uninstall(home.cfg, r)
        self.assertEqual(len(removed), 4)
        cron = os.path.join(home.dir, "cron-lshme")
        home.cfg.set("schedule", "scheduler", "backend", "cron")
        home.cfg.set("schedule", "scheduler", "cron_file", cron)
        rep = scheduler.install(home.cfg, r, "/opt/lshme/healthcheck", [])
        text = slurp(cron)
        self.assertIn("0 7 * * * root /opt/lshme/healthcheck run --schedule daily", text)
        self.assertIn("0 8 * * 1 root", text)
        home.cleanup()

    def test_uninstall_leaves_other_installations_alone(self):
        home = TempHome()
        cron = os.path.join(home.dir, "cron-lshme")
        home.cfg.set("schedule", "scheduler", "backend", "cron")
        home.cfg.set("schedule", "scheduler", "cron_file", cron)
        r = FakeRunner({})
        scheduler.install(home.cfg, r, "/opt/lshme/healthcheck", ["--config-dir", "/etc/lshme"])
        # a second installation (or the test suite) must not remove the production schedules
        self.assertEqual(scheduler.uninstall(home.cfg, r, ["--config-dir", "/tmp/other"]), [])
        self.assertEqual(scheduler.uninstall(home.cfg, r, ["--config-dir", "/etc/lsh"]), [])  # prefix of the real dir
        self.assertTrue(os.path.exists(cron))
        self.assertEqual(scheduler.uninstall(home.cfg, r, ["--config-dir", "/etc/lshme"]), [cron])
        from lshme.ops import cockpit_page
        from lshme.app import App
        dest = os.path.join(home.dir, "cockpit", "lshme")
        cockpit_page.install(App(ROOT, home=home.dir), dest=dest)
        self.assertIsNone(cockpit_page.remove(dest=dest, owner_args=["--config-dir", "/etc/lshme"]))
        self.assertTrue(os.path.isdir(dest))
        self.assertEqual(cockpit_page.remove(dest=dest, owner_args=["--home", home.dir]), dest)
        home.cleanup()


class RetentionTest(unittest.TestCase):
    def make(self, home, host, cat, stamp, age_days):
        d = os.path.join(home.paths.report_dir, host, cat, stamp)
        os.makedirs(d)
        with open(os.path.join(d, "r.html"), "w") as f:
            f.write("x" * 100)
        t = time.time() - age_days * 86400
        os.utime(d, (t, t))
        return d

    def test_plan_apply(self):
        home = TempHome({"retention": "[retention]\ndaily_days = 30\nmonthly_days = 365\ncompress_after_days = 7\n"})
        old1 = self.make(home, "db01", "daily", "20250101_0700", 60)
        old2 = self.make(home, "db01", "daily", "20250102_0700", 59)
        mid = self.make(home, "db01", "daily", "20260101_0700", 10)
        new = self.make(home, "db01", "daily", "20260110_0700", 1)
        lonely = self.make(home, "db02", "daily", "20240101_0700", 400)  # only report: kept
        mon = self.make(home, "db01", "monthly", "20250101_0900", 100)
        mon2 = self.make(home, "db01", "monthly", "20250201_0900", 70)
        log = os.path.join(home.paths.log_dir, "lshme-20240101.log")
        open(log, "w").close()
        os.utime(log, (time.time() - 90 * 86400,) * 2)
        with open(os.path.join(home.paths.log_dir, "email.log"), "w") as f:
            f.write(json.dumps({"time": "2020-01-01 00:00:00"}) + "\n" + json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
        acts = retention.plan(home.cfg, home.paths)
        by = dict((a["path"], a["action"]) for a in acts)
        self.assertEqual(by.get(old1), "delete")
        self.assertEqual(by.get(old2), "delete")
        self.assertEqual(by.get(mid), "compress")
        self.assertNotIn(new, by)
        self.assertNotIn(lonely, by)
        self.assertEqual(by.get(mon), "compress")  # monthly kept 365 days
        self.assertNotIn(mon2, by)                  # latest monthly
        self.assertEqual(by.get(log), "delete")
        self.assertTrue(os.path.exists(old1))       # plan is a dry run
        retention.apply(home.cfg, home.paths, acts)
        self.assertFalse(os.path.exists(old1))
        self.assertTrue(os.path.exists(mid + ".tar.gz") and not os.path.exists(mid))
        self.assertTrue(os.path.exists(new) and os.path.exists(lonely))
        with open(os.path.join(home.paths.log_dir, "email.log")) as f:
            self.assertEqual(len(f.readlines()), 1)
        audit = slurp(os.path.join(home.paths.log_dir, "audit.log"))
        self.assertIn("retention-delete", audit)
        home.cleanup()

    def test_space_cleanup_low_free_space(self):
        """Filesystem nearly full: least valuable, oldest reports go first; newest/recent ones stay."""
        home = TempHome({"retention": "[retention]\nmin_free_percent = 10\nfree_target_percent = 15\ncompress_after_days = 0\n"})
        mk = lambda host, cat, stamp, age, kb: self.make_sized(home, host, cat, stamp, age, kb)
        ad1 = mk("db01", "adhoc", "a1", 5, 300)
        mk("db01", "adhoc", "a2", 3, 300)            # newest adhoc: kept
        d1 = mk("db01", "daily", "d1", 9, 300)
        d2 = mk("db01", "daily", "d2", 8, 300)
        recent = mk("db01", "daily", "d3", 0.2, 300)  # younger than space_min_keep_days
        mk("db01", "daily", "d4", 0.1, 300)           # newest daily
        m1 = mk("db01", "monthly", "m1", 60, 300)
        mk("db01", "monthly", "m2", 30, 300)
        total = 100 * 1024 * 1024

        def usage(path):
            return total, int(0.08 * total)  # 8% free < 10% minimum; target 15% -> ~7 MiB to free
        acts = retention.plan(home.cfg, home.paths, usage=usage)
        space = [a["path"] for a in acts if a.get("space")]
        # ~7 MiB needed > all eligible reports (4 x 300 KiB): all go, in priority order, plus a warning
        self.assertEqual(space, [ad1, d1, d2, m1])
        self.assertNotIn(recent, space)
        self.assertTrue(any(a["action"] == "space-warning" for a in acts))
        home.cleanup()

    def test_space_cleanup_size_cap(self):
        home = TempHome({"retention": "[retention]\nmax_report_dir_gb = 0.001\ncompress_after_days = 0\n"})  # ~1 MiB cap
        reps = [self.make_sized(home, "h", "daily", "r%d" % i, 10 - i, 300) for i in range(6)]  # 6 x 300 KiB = 1.8 MiB
        acts = retention.plan(home.cfg, home.paths, usage=lambda p: (10 ** 12, 10 ** 11))
        space = [a["path"] for a in acts if a.get("space")]
        self.assertEqual(space, reps[:3])  # oldest first until under the cap
        self.assertFalse(any(a["action"] == "space-warning" for a in acts))
        done = retention.space_check(home.cfg, home.paths, usage=lambda p: (10 ** 12, 10 ** 11))
        self.assertEqual(sorted(a["path"] for a in done), sorted(reps[:3]))
        self.assertTrue(all(not os.path.exists(p) for p in reps[:3]) and os.path.exists(reps[5]))
        self.assertIn("space:", slurp(os.path.join(home.paths.log_dir, "audit.log")))
        home.cleanup()

    def test_space_cleanup_not_needed_or_disabled(self):
        home = TempHome()
        self.make_sized(home, "h", "daily", "a", 10, 10)
        self.make_sized(home, "h", "daily", "b", 9, 10)
        self.assertFalse([a for a in retention.plan(home.cfg, home.paths, usage=lambda p: (100, 50)) if a.get("space")])
        home.cfg.set("retention", "retention", "space_cleanup", "no")
        self.assertFalse([a for a in retention.plan(home.cfg, home.paths, usage=lambda p: (100, 1)) if a.get("space")])
        home.cleanup()

    def make_sized(self, home, host, cat, stamp, age_days, kb):
        d = os.path.join(home.paths.report_dir, host, cat, stamp)
        os.makedirs(d)
        with open(os.path.join(d, "r.pdf"), "wb") as f:
            f.write(b"x" * kb * 1024)
        t = time.time() - age_days * 86400
        os.utime(d, (t, t))
        return d

    def test_archive_instead_of_delete(self):
        home = TempHome()
        arch = os.path.join(home.dir, "archive")
        home.cfg.set("retention", "retention", "archive_dir", arch)
        old = self.make(home, "h", "adhoc", "a", 90)
        self.make(home, "h", "adhoc", "b", 1)
        retention.apply(home.cfg, home.paths, retention.plan(home.cfg, home.paths))
        self.assertTrue(os.path.isdir(os.path.join(arch, "h", "adhoc", "a")))
        self.assertFalse(os.path.exists(old))
        home.cleanup()


def agent_payload(host, sev=None):
    r = RunResult(host)
    m = ModuleResult("storage", "Storage", 20)
    if sev:
        m.add(sev, "DISK_USAGE", "/u01", "Filesystem /u01 is 95% full")
    r.modules = [m]
    scoring.score_run(r)
    return json.dumps(r.to_dict())


class FleetTest(unittest.TestCase):
    def test_collect_and_report(self):
        home = TempHome({"servers": "[defaults]\nuser = monitor\n[server:db01]\nhost = 10.0.0.11\ngroup = Production\n"
                                    "[server:db02]\nhost = 10.0.0.12\ngroup = DR\n[server:down]\nhost = 10.0.0.99\n"
                                    "[server:off]\nhost = x\nenabled = no\n"})

        def ssh(args, i):
            target = [a for a in args if "@" in a][0]
            if target.endswith("10.0.0.11"):
                return (0, "Warning: banner\n" + agent_payload("db01", "CRITICAL"))
            if target.endswith("10.0.0.12"):
                return (0, agent_payload("db02"))
            return (255, "", "ssh: connect to host 10.0.0.99 port 22: No route to host")
        r = FakeRunner({"ssh": ssh})
        runs, errors = fleet.collect(home.cfg, r, 7)
        self.assertEqual(sorted(x.host for x in runs), ["db01", "db02"])
        self.assertIn("No route to host", errors["down"])
        self.assertTrue(any("--days 7" in c and "monitor@10.0.0.11" in c and "BatchMode=yes" in c for c in r.calls))
        self.assertEqual([x for x in runs if x.host == "db01"][0].meta["group"], "Production")
        from lshme import reports
        doc, outdir, files, notes = reports.generate_fleet(runs, home.cfg, home.paths, ["html", "txt", "xlsx", "json"], "technical", errors)
        self.assertEqual(doc.status, "CRITICAL")
        txt = slurp([f for f in files if f.endswith(".txt")][0])
        self.assertIn("UNREACHABLE", txt)
        self.assertIn("db01: /u01", txt)
        home.cleanup()

    def test_dropdir(self):
        home = TempHome()
        d = os.path.join(home.dir, "drop")
        os.makedirs(d)
        for name in ("a", "b"):
            with open(os.path.join(d, name + ".json"), "w") as f:
                f.write(agent_payload(name))
        with open(os.path.join(d, "junk.json"), "w") as f:
            f.write("not json")
        self.assertEqual(sorted(r.host for r in fleet.read_dropdir(d)), ["a", "b"])
        home.cleanup()


class ExportLockTest(unittest.TestCase):
    def test_nagios_prometheus(self):
        r = RunResult.from_dict(json.loads(agent_payload("db01", "WARNING")))
        code, text = exporters.nagios(r)
        self.assertEqual(code, 1)
        self.assertTrue(text.startswith("LSHME WARNING - score 89/100"))
        self.assertIn("| score=89", text)
        prom = exporters.prometheus(r)
        self.assertIn('lshme_health_score{host="db01"} 89', prom)
        self.assertIn('lshme_findings{host="db01",severity="warning"} 1', prom)

    def test_lock(self):
        home = TempHome()
        with RunLock(home.paths.lock_file):
            with self.assertRaises(LockBusy):
                with RunLock(home.paths.lock_file):
                    pass
        with RunLock(home.paths.lock_file):
            pass
        home.cleanup()


if __name__ == "__main__":
    unittest.main()
