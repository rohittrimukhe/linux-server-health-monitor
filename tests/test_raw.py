# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import gzip
import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout

from fakes import ROOT, FakeRunner, TempHome
from lshme.ops import rawstore, retention

DAY = 86400


def at(day, hms):
    return time.mktime(time.strptime("%s %s" % (day, hms), "%Y-%m-%d %H:%M:%S"))


class RawStoreTest(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        rawstore.configure(self.home.paths)
        self.base = rawstore.root()
        self.host = rawstore.host_name()

    def tearDown(self):
        self.home.cleanup()

    def test_one_file_per_day_from_midnight_to_one_second_before_midnight(self):
        rawstore.write("sample", {"cpu": 1}, at("2026-11-10", "23:59:59"))
        rawstore.write("sample", {"cpu": 2}, at("2026-11-11", "00:00:00"))
        rawstore.write("sample", {"cpu": 3}, at("2026-11-11", "00:00:00") - 0.5)       # 23:59:59.5 still belongs to the 10th
        p10, p11 = rawstore.day_path(self.host, "2026-11-10"), rawstore.day_path(self.host, "2026-11-11")
        self.assertTrue(p10.endswith("/2026/11/%s_2026-11-10_00-00-00_to_23-59-59.jsonl" % self.host))
        recs10 = rawstore.read(self.host, "2026-11-10")
        self.assertEqual([r["type"] for r in recs10], ["day", "sample", "sample"])     # server facts first
        self.assertEqual([r["data"]["cpu"] for r in recs10 if r["type"] == "sample"], [1, 3])
        self.assertEqual(recs10[0]["data"]["period"], "00:00:00 to 23:59:59 local time")
        self.assertEqual([r["data"].get("cpu") for r in rawstore.read(self.host, "2026-11-11")], [None, 2])
        self.assertTrue(os.path.isfile(p11))

    def test_sealed_two_hours_after_the_day_ends_with_manifest(self):
        day = "2026-11-10"
        for m in range(0, 24 * 60):
            rawstore.write("sample", {"cpu": m % 100}, at(day, "00:00:00") + m * 60 + 5)
        rawstore.write("run", {"health": {"score": 90}}, at(day, "09:00:00"))
        self.assertEqual(rawstore.seal_due(at("2026-11-11", "01:59:00")), [])            # not before 02:00
        done = rawstore.seal_due(at("2026-11-11", "02:00:30"))
        self.assertEqual(len(done), 1)
        m = done[0]
        self.assertEqual((m["from"], m["to"]), ("2026-11-10 00:00:00", "2026-11-10 23:59:59"))
        self.assertEqual((m["samples"], m["by_type"]["run"], m["by_type"]["day"]), (1440, 1, 1))
        self.assertTrue(m["complete"])
        self.assertEqual(m["coverage_pct"], 100.0)
        gz = rawstore.day_path(self.host, day) + ".gz"
        self.assertFalse(os.path.exists(rawstore.day_path(self.host, day)))
        with gzip.open(gz, "rt", encoding="utf-8") as f:                                # zcat works
            self.assertEqual(sum(1 for _ in f), 1442)
        self.assertEqual(rawstore.verify(), [(self.host, day, True, "1442 records")])
        # a late record for the sealed day: kept, and sealed again as another gzip member
        rawstore.write("event", {"event": "close"}, at(day, "23:00:00"))
        self.assertEqual(len(rawstore.read(self.host, day)), 1443)
        rawstore.seal_due(at("2026-11-12", "03:00:00"))
        self.assertEqual(rawstore.verify(), [(self.host, day, True, "1443 records")])
        self.assertEqual([r["type"] for r in rawstore.read(self.host, day)][-1], "sample")   # read back in time order
        with open(gz, "ab") as f:
            f.write(b"tampered")
        self.assertFalse(rawstore.verify()[0][2])

    def test_gaps_are_reported(self):
        day = "2026-11-09"
        for m in list(range(0, 600)) + list(range(700, 1440)):
            rawstore.write("sample", {}, at(day, "00:00:00") + m * 60 + 1)
        m = rawstore.seal_due(at("2026-11-12", "03:00:00"))[0]
        self.assertFalse(m["complete"])
        self.assertEqual(m["gap_count"], 1)
        self.assertEqual(m["gaps"][0][0][:5], "09:59")

    def test_watcher_sample_is_complete(self):
        now = time.time()
        rawstore.watcher_tick(type("A", (), {"runner": FakeRunner({"systemctl": (0, "sshd.service loaded active running OpenSSH\nx.service loaded failed failed X\n")})})(),
                              {"cpu": 12.5, "cores": [10, 15], "mem": 40.0, "swap": 0.0, "load": 0.2, "disk": {"/": 50}},
                              {"disk:/": {"severity": "WARNING", "component": "/", "message": "full"}}, now, {})
        recs = rawstore.read(self.host, rawstore.day_of(now))
        kinds = [r["type"] for r in recs]
        for k in ("day", "sample", "top", "connections", "services"):
            self.assertIn(k, kinds)
        s = [r for r in recs if r["type"] == "sample"][0]["data"]
        for key in ("cpu", "cores", "memory", "load", "filesystems", "net_counters", "vm", "problems", "uptime_s"):
            self.assertIn(key, s)
        self.assertIn("/", s["filesystems"])
        self.assertIn("inodes", s["filesystems"]["/"])
        self.assertEqual(s["problems"]["disk:/"]["severity"], "WARNING")
        sv = [r for r in recs if r["type"] == "services"][0]["data"]
        self.assertEqual(sv["failed"], ["x.service"])
        self.assertTrue([r for r in recs if r["type"] == "top"][0]["data"]["by_memory"])

    def test_old_raw_days_expire(self):
        old = time.time() - 800 * DAY
        rawstore.write("sample", {}, old)
        rawstore.write("sample", {}, time.time())
        acts = [a for a in retention.plan(self.home.cfg, self.home.paths) if a["action"] == "delete-raw"]
        self.assertEqual([a["day"] for a in acts], [rawstore.day_of(old)])
        retention.apply(self.home.cfg, self.home.paths, acts)
        self.assertEqual([e["day"] for e in rawstore.days()], [rawstore.day_of(time.time())])


class BackfillReplayTest(unittest.TestCase):
    def setUp(self):
        from lshme.app import App
        self.home = TempHome()
        self.app = App(ROOT, home=self.home.dir)

    def tearDown(self):
        self.home.cleanup()

    def test_backfill_then_replay_restores_the_history(self):
        from lshme.engine.history import History
        from lshme.ops import rawreplay
        from test_periodic import make_run
        me = os.uname()[1]
        h = History(self.app.paths.history_db)
        past = time.time() - 5 * DAY
        run = make_run(past)
        run.host = me
        h.save(run)
        for hr in range(48):
            h.add_hourly(me, past + hr * 3600, 20 + hr % 5, 50.0, 1.0, 0.3, [10, 30], {"eth0": (1000, 500)})
        h.close()
        with open(os.path.join(self.app.paths.data_dir, "watch_events.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "open", "ts": past + 100, "key": "cpu", "severity": "WARNING"}) + "\n")
        res = rawreplay.backfill(self.app)
        self.assertEqual((res["history-runs"], res["hourly"], res["incidents"]), (1, 48, 1))
        self.assertEqual(rawreplay.backfill(self.app)["hourly"], "already done")          # never twice
        days = rawstore.days()
        self.assertTrue(days and all(e["sealed"] for e in days))                          # past days are sealed straight away
        os.remove(self.app.paths.history_db)                                               # the history database is lost
        out = rawreplay.replay(self.app)
        self.assertEqual(out["hours"], 48)
        h = History(self.app.paths.history_db)
        rows = h.hourly(me, past - 3600)
        self.assertEqual(len(rows), 48)
        self.assertAlmostEqual(rows[0]["cpu"], 20.0)
        self.assertEqual(len(h.hourly_cores(me, past - 3600)[1]), 48)
        h.close()
        self.assertEqual(rawreplay.replay(self.app)["hours"], 0)                          # nothing added twice

    def test_full_runs_replay_into_history(self):
        from lshme.engine.history import History
        from lshme.ops import rawreplay
        from lshme.app import raw_run
        from test_periodic import make_run
        run = make_run(time.time() - 2 * DAY)
        run.host = os.uname()[1]
        raw_run(run, "daily", True)
        out = rawreplay.replay(self.app)
        self.assertEqual(out["runs"], 1)
        h = History(self.app.paths.history_db)
        self.assertEqual(len(h.runs(run.host, 5)), 1)
        self.assertTrue(h.series(run.host, "fs.used_bytes:/u01", 0))
        h.close()

    def test_cli(self):
        from lshme.ui import cli
        rawstore.write("sample", {"cpu": 7, "mem_pct": 30, "filesystems": {"/": {"used_pct": 40}}})
        rawstore.write("sample", {"cpu": 99, "mem_pct": 30}, time.time() - 3 * DAY)
        buf = io.StringIO()
        base = ["--home", self.home.dir, "--no-color", "raw"]
        with redirect_stdout(buf):
            self.assertEqual(cli.main(base + ["tail", "today", "-n", "3"]), 0)
            self.assertEqual(cli.main(base + ["seal", "--all"]), 0)
            self.assertEqual(cli.main(base + ["list"]), 0)
            self.assertEqual(cli.main(base + ["verify"]), 0)
            self.assertEqual(cli.main(base + ["grep", '"cpu": 99']), 0)
            self.assertEqual(cli.main(base + ["stats"]), 0)
        out = buf.getvalue()
        self.assertIn("cpu 7%", out)
        self.assertIn("00:00:00-23:59:59", out)
        self.assertIn("sealed", out)
        self.assertIn('"cpu": 99', out)


if __name__ == "__main__":
    unittest.main()
