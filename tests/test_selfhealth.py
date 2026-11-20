# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Health of the tool itself and the retention overview."""
import json
import os
import time
import unittest

from fakes import FakeRunner, TempHome
from lshme.engine.history import History
from lshme.ops import selfhealth


class SelfHealthTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.sd = os.path.join(self.home.dir, "units")
        os.makedirs(self.sd)
        self.home.cfg.set("schedule", "scheduler", "systemd_dir", self.sd)
        self.runner = FakeRunner({"systemctl": (0, "")})

    def tearDown(self):
        self.home.cleanup()

    def checks(self):
        return dict((c["name"], c) for c in selfhealth.check(self.home.cfg, self.home.paths, self.runner))

    def metrics(self, age):
        os.makedirs(self.home.paths.data_dir, exist_ok=True)
        with open(os.path.join(self.home.paths.data_dir, "watch_metrics.json"), "w") as f:
            json.dump([{"t": time.time() - age, "cpu": 1}], f)

    def test_watcher_missing_stale_and_fine(self):
        self.assertEqual(self.checks()["Real-time watcher"]["code"], "TOOL_WATCHER_MISSING")
        open(os.path.join(self.sd, "lshme-watch.service"), "w").close()
        self.metrics(900)
        c = self.checks()["Real-time watcher"]
        self.assertEqual((c["status"], c["code"]), ("critical", "TOOL_WATCHER_STALE"))
        self.metrics(20)
        self.assertEqual(self.checks()["Real-time watcher"]["status"], "ok")

    def test_enabled_schedule_without_a_timer_is_reported(self):
        c = self.checks()["Schedule daily"]
        self.assertEqual(c["code"], "TOOL_SCHEDULE_MISSING")
        open(os.path.join(self.sd, "lshme-daily.timer"), "w").close()
        self.assertEqual(self.checks()["Schedule daily"]["status"], "ok")

    def test_failed_email_is_critical(self):
        h = History(self.home.paths.history_db)
        h.record_delivery("db01", None, False, ["a@example.com"], 3, "connection refused")
        h.close()
        c = self.checks()["E-mail delivery"]
        self.assertEqual((c["status"], c["code"]), ("critical", "TOOL_EMAIL_FAILED"))
        self.assertIn("connection refused", c["detail"])

    def test_retention_view_counts_reports_per_type(self):
        for cat, n in (("daily", 3), ("monthly", 1)):
            for i in range(n):
                d = os.path.join(self.home.paths.report_dir, "h1", cat, "2026110%d_0900" % i)
                os.makedirs(d)
                with open(os.path.join(d, "r.html"), "w") as f:
                    f.write("x" * 1000)
        v = selfhealth.retention_view(self.home.cfg, self.home.paths)
        by = dict((t["type"], t) for t in v["types"])
        self.assertEqual((by["daily"]["count"], by["daily"]["bytes"], by["daily"]["keep_days"]), (3, 3000, 30))
        self.assertEqual((by["monthly"]["count"], by["monthly"]["keep_days"]), (1, 365))
        self.assertEqual(by["adhoc"]["count"], 0)
        self.assertTrue(v["other"] and "free" in v["space"])


if __name__ == "__main__":
    unittest.main()
