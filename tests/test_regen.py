# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import hashlib
import json
import os
import subprocess
import sys
import time
import unittest

from fakes import ROOT, TempHome
from lshme import reports
from lshme.ops import archive, retention
from lshme.reports import periodic, snapshot
from test_periodic import make_run, seed

DAY = 86400


def sha(data):
    return hashlib.sha256(data).hexdigest()


def age(d, days):
    t = time.time() - days * DAY
    os.utime(d, (t, t))


class RegenTest(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.now = time.time() - 60
        self.run = make_run(self.now)
        seed(self.home.paths, self.run)

    def tearDown(self):
        self.home.cleanup()

    def originals(self, outdir):
        return {f: open(os.path.join(outdir, f), "rb").read() for f in os.listdir(outdir) if not f.startswith(".")}

    def check_identical(self, outdir):
        orig = self.originals(outdir)
        self.assertTrue(os.path.isfile(os.path.join(outdir, snapshot.SOURCE)))
        src = snapshot.load(outdir)
        self.assertEqual(sorted(src["files"]), sorted(orig))
        for name, data in orig.items():
            rebuilt, identical = snapshot.rebuild(outdir, name)
            self.assertTrue(identical, name)
            self.assertEqual(sha(rebuilt), sha(data), name)
        return orig

    def test_periodic_reports_rebuild_byte_for_byte(self):
        fmts = ["html", "pdf", "xlsx", "csv", "txt", "json"]
        _, outdir, files, notes = reports.generate(self.run, self.home.cfg, self.home.paths, fmts, "monthly", "monthly")
        orig = self.check_identical(outdir)
        self.assertEqual(len(orig), 12)                           # summary and comprehensive, six formats each

    def test_standard_reports_rebuild_byte_for_byte(self):
        fmts = ["html", "pdf", "xlsx", "pptx", "csv", "txt", "json"]
        _, outdir, files, notes = reports.generate(self.run, self.home.cfg, self.home.paths, fmts, "technical", "adhoc")
        orig = self.check_identical(outdir)
        self.assertIn(".pptx", "".join(orig))

    def test_rebuild_in_another_process_is_identical(self):
        """A rebuild days later runs in another process (different hash seed, different clock)."""
        _, outdir, files, notes = reports.generate(self.run, self.home.cfg, self.home.paths, ["html", "pdf", "xlsx", "txt"], "weekly", "weekly")
        self.cross_process(outdir)
        _, outdir, files, notes = reports.generate(self.run, self.home.cfg, self.home.paths, ["pptx", "xlsx", "pdf", "csv"], "technical", "adhoc")
        self.cross_process(outdir)

    def cross_process(self, outdir):
        time.sleep(2.1)                                       # the clock has moved on: nothing may depend on it
        orig = self.originals(outdir)
        code = ("import sys, json, hashlib; sys.path.insert(0, %r); from lshme.reports import snapshot; "
                "print(json.dumps({n: [hashlib.sha256(snapshot.rebuild(sys.argv[1], n)[0]).hexdigest(), snapshot.rebuild(sys.argv[1], n)[1]] "
                "for n in sys.argv[2:]}))" % ROOT)
        env = dict(os.environ, PYTHONHASHSEED="12345")
        out = subprocess.check_output([sys.executable, "-c", code, outdir] + sorted(orig), env=env).decode()
        got = json.loads(out.strip().splitlines()[-1])
        for n, data in orig.items():
            self.assertEqual(got[n], [sha(data), True], n)

    def test_retention_keeps_data_and_rebuilds_on_request(self):
        fmts = ["html", "pdf", "xlsx", "csv", "txt", "json"]
        _, old, _, _ = reports.generate(self.run, self.home.cfg, self.home.paths, fmts, "daily", "daily")
        orig = self.originals(old)
        time.sleep(1.1)
        _, new, _, _ = reports.generate(self.run, self.home.cfg, self.home.paths, fmts, "daily", "daily")
        age(old, 9)
        acts = retention.plan(self.home.cfg, self.home.paths)
        slim = [a for a in acts if a["action"] == "slim"]
        self.assertEqual([a["path"] for a in slim], [old])            # the newest report is younger than 7 days
        retention.apply(self.home.cfg, self.home.paths, acts)
        left = sorted(f for f in os.listdir(old))
        self.assertEqual([archive.display_name(f) for f in left if not f.startswith(".")], [n for n in sorted(orig) if n.endswith(".json")])
        self.assertIn(snapshot.SOURCE, left)
        cat = dict((f["name"], f) for f in archive.catalog(old))
        self.assertEqual(sorted(cat), sorted(orig))                   # every format can still be had
        for name, data in orig.items():
            got, how = archive.materialize(old, name)
            self.assertEqual(sha(got), sha(data), name)
            self.assertEqual(how, "stored" if name.endswith(".json") else "rebuilt")
        self.assertEqual(sorted(os.listdir(old)), left)               # nothing was written back
        self.assertGreater(time.time() - os.path.getmtime(old), 8 * DAY)
        self.assertFalse([a for a in retention.plan(self.home.cfg, self.home.paths) if a["action"] == "slim"])

    def test_monthly_pdf_is_kept_for_audits(self):
        _, old, _, _ = reports.generate(self.run, self.home.cfg, self.home.paths, ["pdf", "html", "json"], "monthly", "monthly")
        time.sleep(1.1)
        reports.generate(self.run, self.home.cfg, self.home.paths, ["pdf"], "monthly", "monthly")
        age(old, 9)
        retention.apply(self.home.cfg, self.home.paths, retention.plan(self.home.cfg, self.home.paths))
        kept = sorted(archive.display_name(f) for f in os.listdir(old) if not f.startswith("."))
        self.assertTrue(kept and all(f.endswith((".pdf", ".json")) for f in kept), kept)

    def test_a_file_that_would_differ_is_never_removed(self):
        _, old, _, _ = reports.generate(self.run, self.home.cfg, self.home.paths, ["txt", "json"], "daily", "daily")
        time.sleep(1.1)
        reports.generate(self.run, self.home.cfg, self.home.paths, ["txt"], "daily", "daily")
        txt = [f for f in os.listdir(old) if f.endswith(".txt")][0]
        with open(os.path.join(old, txt), "a", encoding="utf-8") as f:
            f.write("edited by hand\n")
        age(old, 9)
        done = retention.apply(self.home.cfg, self.home.paths, retention.plan(self.home.cfg, self.home.paths))
        self.assertTrue(os.path.exists(archive.real_path(os.path.join(old, txt))))
        self.assertTrue([a for a in done if a["action"] == "slim" and a.get("kept_files")])

    def test_sla_achieved_section(self):
        dd = self.home.paths.data_dir
        os.makedirs(dd, exist_ok=True)
        t0 = self.now - 3 * 3600
        with open(os.path.join(dd, "watch_events.jsonl"), "w", encoding="utf-8") as f:
            for e in ({"event": "open", "ts": t0, "key": "svc:oracle", "component": "oracle", "severity": "CRITICAL", "message": "down"},
                      {"event": "close", "ts": t0 + 1800, "key": "svc:oracle"}):
                f.write(json.dumps(e) + "\n")
        day = time.strftime("%Y-%m-%d", time.localtime(self.now))
        with open(os.path.join(dd, "watch_state.json"), "w", encoding="utf-8") as f:
            json.dump({"interval": 60, "coverage": {day: 2000}}, f)
        d = periodic.build(self.run, self.home.cfg, self.home.paths, "daily", "summary", self.now)
        sec = dict(d.sections)["SLA achieved"]
        rows = dict((str(r[0]), str(r[1])) for r in sec[1]["rows"])
        self.assertEqual(rows["SLA target"], "99.9 % availability")
        self.assertEqual(rows["Result"], "Missed")                    # 30 min down in 24 h is below 99.9 %
        self.assertEqual(rows["Availability achieved"], "97.917 %")
        self.assertIn("30", rows["Downtime (service or server unavailable)"])
        kpi = [i for i in dict(d.sections)["Overall status"][0]["items"] if i[1].startswith("SLA achieved")]
        self.assertEqual(kpi[0][0], "97.92 %")
        self.home.cfg.set("main", "sla", "target_percent", "95")
        d = periodic.build(self.run, self.home.cfg, self.home.paths, "daily", "summary", self.now)
        rows = dict((str(r[0]), str(r[1])) for r in dict(d.sections)["SLA achieved"][1]["rows"])
        self.assertEqual(rows["Result"], "Achieved")


if __name__ == "__main__":
    unittest.main()
