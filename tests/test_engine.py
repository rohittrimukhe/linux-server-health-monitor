# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import time
import unittest

from fakes import TempHome
from lshme.core.model import ModuleResult, RunResult
from lshme.core.util import linear_forecast, parse_log_time, redact, redact_obj
from lshme.engine import baseline, maintenance, scoring
from lshme.engine.history import History, analyse


def run_with(*mods):
    r = RunResult("db01")
    r.modules = list(mods)
    return r


def mod(name, *sevs, **kw):
    m = ModuleResult(name, name.title(), kw.get("weight", 10))
    for i, s in enumerate(sevs):
        m.add(s, "C%d" % i, "x", "msg")
    return m


class ScoringTest(unittest.TestCase):
    def test_critical_caps_score(self):
        r = run_with(mod("a", weight=90), mod("b", "CRITICAL", weight=10))
        h = scoring.score_run(r)
        self.assertEqual(h["status"], "CRITICAL")
        self.assertLessEqual(h["score"], 69)

    def test_warning_caps_and_skipped_ignored(self):
        s = mod("skip")
        s.status = "SKIPPED"
        h = scoring.score_run(run_with(mod("a", "WARNING"), s))
        self.assertEqual(h["status"], "WARNING")
        self.assertEqual(h["score"], 89)
        self.assertIsNone(s.score)

    def test_healthy(self):
        h = scoring.score_run(run_with(mod("a", "INFO"), mod("b")))
        self.assertEqual((h["status"], h["score"]), ("HEALTHY", 100))


class HistoryTest(unittest.TestCase):
    def test_trend_forecast_recurring(self):
        home = TempHome()
        h = History(home.paths.history_db)
        now = time.time()
        size = 100 * 2 ** 30
        for i in range(10, 0, -1):  # 10 daily runs, +5 GiB/day, critical finding each run
            r = RunResult("db01")
            r.collected_at = now - i * 86400
            m = mod("storage", "CRITICAL")
            m.metric("fs.used_bytes:/u01", size * 0.4 + (10 - i) * 5 * 2 ** 30)
            m.metric("fs.size_bytes:/u01", size)
            m.metric("cpu.usage_percent", 20 + i)
            r.modules = [m]
            scoring.score_run(r)
            h.save(r)
        cur = RunResult("db01")
        cur.collected_at = now
        m = mod("storage")
        m.metric("fs.used_bytes:/u01", size * 0.4 + 10 * 5 * 2 ** 30)  # 90 GiB used of 100
        m.metric("fs.size_bytes:/u01", size)
        m.metric("cpu.usage_percent", 20)
        cur.modules = [m]
        t = analyse(h, cur, home.cfg)
        fc = [f for f in t.findings if f.code == "FS_FORECAST_FULL"]
        self.assertEqual(fc[0].severity, "CRITICAL")  # 10 GiB free / 5 GiB per day = 2 days (critical <= 7)
        f = [x for x in cur.trends["forecast"] if x["mountpoint"] == "/u01"][0]
        self.assertAlmostEqual(f["days_to_full"], 2.0, places=1)
        rec = cur.trends["recurring"]
        self.assertTrue(rec and rec[0][3] >= 5)
        cpu = [x for x in cur.trends["metrics"] if x["key"] == "cpu.usage_percent"][0]
        self.assertEqual(cpu["week_ago"], 27)
        h.close()
        home.cleanup()

    def test_no_forecast_from_runs_minutes_apart(self):
        home = TempHome()
        h = History(home.paths.history_db)
        now = time.time()
        size = 100 * 2 ** 30
        for i, used in ((3, 40), (2, 41), (1, 42)):  # three runs a minute apart, 1 GiB growth each
            r = RunResult("db01")
            r.collected_at = now - i * 60
            m = mod("storage")
            m.metric("fs.used_bytes:/", used * 2 ** 30)
            m.metric("fs.size_bytes:/", size)
            r.modules = [m]
            scoring.score_run(r)
            h.save(r)
        cur = RunResult("db01")
        cur.collected_at = now
        m = mod("storage")
        m.metric("fs.used_bytes:/", 43 * 2 ** 30)
        m.metric("fs.size_bytes:/", size)
        cur.modules = [m]
        t = analyse(h, cur, home.cfg)
        self.assertFalse([f for f in t.findings if f.code == "FS_FORECAST_FULL"])  # needs 24 h of history
        h.close()
        home.cleanup()

    def test_linear_forecast(self):
        pts = [(0, 10.0), (86400, 20.0), (2 * 86400, 30.0)]
        self.assertAlmostEqual(linear_forecast(pts, 100.0) / 86400, 7.0, places=3)
        self.assertIsNone(linear_forecast([(0, 5.0), (1, 5.0)], 10))

    def test_prune(self):
        home = TempHome()
        h = History(home.paths.history_db)
        r = RunResult("x")
        r.collected_at = time.time() - 400 * 86400
        r.modules = [mod("a")]
        scoring.score_run(r)
        h.save(r)
        self.assertEqual(h.prune(365), 1)
        h.close()
        home.cleanup()


class DriftMaintenanceTest(unittest.TestCase):
    def test_drift(self):
        base = {"enabled_services": ["sshd.service"], "listening_ports": ["tcp/22"], "uid0_accounts": ["root"],
                "packages": ["a"], "kernel": "1", "config_hashes": {"/etc/fstab": "aa"}}
        cur = {"enabled_services": ["sshd.service", "telnet.socket"], "listening_ports": ["tcp/22", "tcp/23"],
               "uid0_accounts": ["root", "toor"], "packages": ["a"], "kernel": "2", "config_hashes": {"/etc/fstab": "bb"}}
        diffs = dict((k, (a, r)) for k, a, r in baseline.compare(base, cur))
        self.assertEqual(diffs["uid0_accounts"][0], ["toor"])
        self.assertEqual(diffs["listening_ports"][0], ["tcp/23"])
        self.assertIn("kernel", diffs)
        self.assertEqual(diffs["config_hashes"][0], ["/etc/fstab"])
        self.assertNotIn("packages", diffs)

    def test_maintenance_window(self):
        home = TempHome()
        self.assertIsNone(maintenance.active(home.paths))
        maintenance.start(home.paths, 10, "patching")
        self.assertEqual(maintenance.active(home.paths)["reason"], "patching")
        maintenance.start(home.paths, -1, "expired")
        self.assertIsNone(maintenance.active(home.paths))
        home.cleanup()


class UtilTest(unittest.TestCase):
    def test_redaction(self):
        self.assertNotIn("Secret1", redact("connect scott/Secret1@ORCL"))
        self.assertNotIn("hunter2", redact("PASSWORD=hunter2 other"))
        self.assertNotIn("pw", redact("mongodb://user:pw@host/db"))
        d = redact_obj({"smtp_password": "x1", "nested": {"api_key": "k"}, "msg": "identified by abc123"})
        self.assertEqual(d["smtp_password"], "******")
        self.assertEqual(d["nested"]["api_key"], "******")
        self.assertNotIn("abc123", d["msg"])

    def test_log_time_formats(self):
        self.assertIsNotNone(parse_log_time("2026-11-12T10:00:00.123+00:00 msg"))
        self.assertIsNotNone(parse_log_time("Wed Nov 11 10:00:00 2026"))
        self.assertIsNotNone(parse_log_time("Nov 11 10:00:00 host msg"))
        self.assertIsNone(parse_log_time("no timestamp here"))


if __name__ == "__main__":
    unittest.main()
