# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Custom date range reports: From .. To, only data inside the range."""
import os
import time
import unittest

from fakes import TempHome
from lshme.engine.history import History
from lshme.reports import custom
from test_periodic import make_run, seed

DAY = 86400


def ymd(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class WindowTests(unittest.TestCase):
    def test_whole_local_days(self):
        since, until = custom.window("2026-11-01", "2026-11-15", now=time.mktime((2026, 11, 26, 10, 0, 0, 0, 0, -1)))
        self.assertEqual(time.localtime(since)[:6], (2026, 11, 1, 0, 0, 0))
        self.assertEqual(time.localtime(until)[:6], (2026, 11, 15, 23, 59, 59))
        self.assertEqual(custom.layout(since, until), "monthly")

    def test_today_ends_now_and_layout_follows_length(self):
        now = time.mktime((2026, 11, 26, 10, 0, 0, 0, 0, -1))
        since, until = custom.window("2026-11-26", "2026-11-26", now=now)
        self.assertEqual(until, now)
        self.assertEqual(custom.layout(since, until), "daily")
        self.assertEqual(custom.layout(*custom.window("2026-11-20", "2026-11-26", now=now)), "weekly")

    def test_bad_ranges_are_refused(self):
        now = time.mktime((2026, 11, 26, 10, 0, 0, 0, 0, -1))
        for a, b in (("2026-11-10", "2026-11-01"), ("2027-01-01", "2027-01-02"), ("26/11/2026", ""), ("2024-01-01", "2026-11-01")):
            with self.assertRaises(ValueError):
                custom.window(a, b, now=now)


class BoundedTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.now = time.time()
        self.run = make_run(self.now)
        seed(self.home.paths, self.run)

    def tearDown(self):
        self.home.cleanup()

    def test_history_after_the_end_is_not_seen(self):
        h = History(self.home.paths.history_db)
        try:
            end = self.now - 10 * DAY
            b = custom.Bounded(h, end)
            self.assertTrue(all(r["ts"] <= end for r in b.runs("db01", 30, 1000)))
            self.assertTrue(b.runs("db01", 30, 1000))
            self.assertTrue(all(r["ts"] <= end for r in b.hourly("db01", self.now - 30 * DAY)))
            self.assertTrue(all(t <= end for t, _ in b.series("db01", "fs.used_bytes:/u01", self.now - 30 * DAY)))
            full = h.series("db01", "fs.used_bytes:/u01", self.now - 30 * DAY)
            self.assertGreater(len(full), len(b.series("db01", "fs.used_bytes:/u01", self.now - 30 * DAY)))
        finally:
            h.close()

    def test_report_for_a_past_range(self):
        start, end = ymd(self.now - 20 * DAY), ymd(self.now - 11 * DAY)
        d = custom.build(self.run, self.home.cfg, self.home.paths, start, end)
        self.assertIn("Server Report - ", d.title)
        self.assertIn("custom range", d.subtitle)
        self.assertEqual(d.report_type, "monthly")                     # 10 days: the monthly layout
        since, until = d.custom_range
        self.assertLessEqual(until, self.now - 10 * DAY)

    def test_files_are_written_under_custom(self):
        start = end = ymd(self.now - 3 * DAY)
        doc, outdir, files, notes = custom.generate(self.run, self.home.cfg, self.home.paths, start, end, ["html", "json"])
        self.assertEqual(os.path.basename(os.path.dirname(outdir)), "custom")
        self.assertTrue(any(f.endswith(".html") for f in files), (files, notes))
        self.assertTrue(all("_custom_" in os.path.basename(f) for f in files if f.endswith((".html", ".json"))))


if __name__ == "__main__":
    unittest.main()
