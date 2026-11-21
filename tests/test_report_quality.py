# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import json
import os
import time
import unittest

from fakes import TempHome
from lshme import reports
from lshme.engine.history import History
from lshme.ops import sla
from lshme.reports import periodic, periodic_net, periodic_pdf, periodic_sla
from lshme.reports.minipdf import MARGIN, PAGE_W
from test_periodic import make_run, seed

DAY = 86400


def ev(kind, key, comp, t0, minutes, sev="CRITICAL"):
    return [{"event": "open", "ts": t0, "key": key, "component": comp, "severity": sev, "message": kind},
            {"event": "close", "ts": t0 + minutes * 60, "key": key}]


class SlaTest(unittest.TestCase):
    def test_a_full_disk_is_degraded_time_not_downtime(self):
        t0 = 1_800_000_000
        events = ev("down", "svc:oracle", "oracle", t0, 30) + ev("full", "disk:/u01", "/u01", t0 + 3600, 60)
        r = sla.compute(events, t0 - 3600, t0 + DAY, t0 + DAY, down_kinds=sla.down_kinds(TempHome().cfg))
        self.assertEqual(round(r["downtime"] / 60), 30)                  # only the service that stopped
        self.assertEqual(round(r["degraded"] / 60), 60)                  # the full disk is shown as degraded
        self.assertEqual(r["critical_incidents"], 1)
        every = sla.compute(events, t0 - 3600, t0 + DAY, t0 + DAY, down_kinds=None)
        self.assertEqual(round(every["downtime"] / 60), 90)              # [sla] count_as_downtime = all gives the old behaviour

    def build(self, ticks, events):
        home = TempHome()
        dd = home.paths.data_dir
        os.makedirs(dd, exist_ok=True)
        now = time.time()
        with open(os.path.join(dd, "watch_events.jsonl"), "w", encoding="utf-8") as f:
            for e in events(now):
                f.write(json.dumps(e) + "\n")
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        with open(os.path.join(dd, "watch_state.json"), "w", encoding="utf-8") as f:
            json.dump({"interval": 60, "coverage": {day: ticks}}, f)
        kpi, blocks = periodic_sla.blocks(home.cfg, home.paths, now, 1, "daily")
        rows = dict((str(r[0]), str(r[1])) for r in [b for b in blocks if b["type"] == "table"][0]["rows"])
        home.cleanup()
        return kpi, rows, blocks

    def test_not_rated_until_the_watcher_has_covered_half_the_period(self):
        kpi, rows, blocks = self.build(70, lambda now: ev("down", "svc:x", "x", now - 3000, 10))     # about an hour of monitoring
        self.assertEqual(kpi[0], "not rated yet")
        self.assertEqual(rows["Result"], "Not rated yet")
        self.assertIn("not rated yet", " ".join(b.get("text", "") for b in blocks if b["type"] == "note"))

    def test_rated_over_the_monitored_time(self):
        now_ticks = 1440
        kpi, rows, _ = self.build(now_ticks, lambda now: ev("down", "svc:x", "x", now - 7200, 30))
        self.assertEqual(rows["Result"], "Missed")                      # 30 minutes of a stopped service in a day is below 99.9 %
        self.assertEqual(kpi[1].startswith("SLA achieved"), True)
        kpi, rows, _ = self.build(now_ticks, lambda now: ev("full", "disk:/u01", "/u01", now - 7200, 120))
        self.assertEqual((rows["Result"], rows["Downtime (service or server unavailable)"]), ("Achieved", "none"))
        self.assertEqual(kpi[0], "100.00 %")


class NetworkTest(unittest.TestCase):
    def test_adapter_kinds_and_totals_count_each_byte_once(self):
        import tempfile
        root = tempfile.mkdtemp()
        try:
            def adapter(name, kind):
                d = os.path.join(root, name)
                os.makedirs(d)
                if kind == "bond":
                    os.makedirs(os.path.join(d, "bonding"))
                elif kind == "bridge":
                    os.makedirs(os.path.join(d, "bridge"))
                elif kind == "vm":
                    open(os.path.join(d, "tun_flags"), "w").close()
                elif kind == "physical":
                    os.makedirs(os.path.join(d, "device"))
                return d
            for n, k in (("ens1", "physical"), ("bond1", "bond"), ("br-lan", "bridge"), ("vnet4", "vm"), ("eth0.20", "plain")):
                self.assertEqual(periodic_net.kind_of(n, adapter(n, k)), "vlan" if n == "eth0.20" else k)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        ad = [{"name": n, "kind": k} for n, k in (("ens1", "physical"), ("ens2", "physical"), ("bond1", "bond"), ("br-lan", "bridge"), ("vnet4", "vm"))]
        traffic = dict((a["name"], {}) for a in ad)
        self.assertEqual(periodic_net.counted(ad, traffic), {"ens1", "ens2"})          # the bond, bridge and VM tap repeat the same bytes
        self.assertEqual(periodic_net.counted([{"name": "eth0", "kind": "virtual"}], {"eth0": {}}), {"eth0"})   # a VM with only a virtual NIC


class ReportContentTest(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.now = time.time()
        self.run = make_run(self.now)

    def tearDown(self):
        self.home.cleanup()

    def build(self, ptype="weekly"):
        return periodic.build(self.run, self.home.cfg, self.home.paths, ptype, "summary", self.now)

    def test_accepted_exceptions_are_listed_not_hidden(self):
        self.run.excluded = [{"severity": "CRITICAL", "component": "mpatha", "message": "All 2 paths of mpatha have failed", "rule": "lun-cleanup",
                              "reason": "LUN removed from the SAN", "owner": "Rohit", "until": "2026-11-30"}]
        sec = dict(self.build("daily").sections)
        rows = [b for b in sec["Accepted exceptions"] if b["type"] == "table"][0]["rows"]
        self.assertEqual([str(c) for c in rows[0][1:]], ["mpatha", "All 2 paths of mpatha have failed", "lun-cleanup", "LUN removed from the SAN", "Rohit", "2026-11-30"])
        self.run.excluded = []
        self.assertNotIn("Accepted exceptions", dict(self.build("daily").sections))

    def test_a_new_installation_says_how_little_data_there_is(self):
        h = History(self.home.paths.history_db)
        for k in range(3):
            h.add_hourly(self.run.host, self.now - (2 - k) * 3600, 10, 50, 0, 0.2)
        h.save(self.run)
        h.close()
        d = self.build("weekly")
        story = [b for b in dict(d.sections)["In short"] if b["type"] == "points"][0]["items"]
        self.assertTrue([t for lv, t in story if t.startswith("Only ")], story)
        health = [b for b in dict(d.sections)["Health by day"] if b["type"] == "table"][0]
        self.assertEqual(len(health["rows"]), 1)                                  # no rows of 'no run' for the days before monitoring began
        self.assertIn("earlier day", health["legend"])
        seed(self.home.paths, self.run)                                           # with a month of history there is nothing to say
        story = [b for b in dict(self.build("weekly").sections)["In short"] if b["type"] == "points"][0]["items"]
        self.assertFalse([t for lv, t in story if t.startswith("Only ")])

    def test_log_findings_get_their_own_reason(self):
        impact = periodic.impact_for_finding(type("F", (), {"code": "LOG_SELINUX", "module": "logs"})())
        self.assertIn("SELinux", impact)
        self.assertNotIn("hardware or software faults", impact)

    def test_hour_buckets_start_on_the_local_hour(self):
        for ts in (1_800_000_000, 1_800_001_234, 1_800_003_599):
            hs = History.hour_start(ts)
            self.assertEqual(time.localtime(hs).tm_min, 0)
            self.assertTrue(0 <= ts - hs < 3600)


class PdfLayoutTest(unittest.TestCase):
    def test_text_is_measured_with_real_helvetica_widths(self):
        self.assertAlmostEqual(periodic_pdf.tw("Hello", 10), 22.78, places=2)
        self.assertGreater(periodic_pdf.tw("Hello", 10, True), periodic_pdf.tw("Hello", 10))
        text = "P. D. Hinduja Hospital & Medical Research Centre, Khar Facility " * 3
        for line in periodic_pdf.wrap(text, 200, 10.5, True, 20):
            self.assertLessEqual(periodic_pdf.tw(line, 10.5, True), 200.5)
        self.assertEqual(periodic_pdf.fit_one("short", 100, 10), "short")
        self.assertTrue(periodic_pdf.fit_one("x" * 200, 100, 10).endswith("..."))
        self.assertEqual(periodic_pdf.keep_units("Growth (30 d)"), "Growth (30\xa0d)")

    def test_nothing_runs_past_the_margins(self):
        home = TempHome()
        try:
            now = time.time()
            run = make_run(now)
            run.meta["company"] = "P. D. Hinduja Hospital & Medical Research Centre, Khar Facility"
            run.meta["customer"] = "HHK IT"
            seed(home.paths, run)
            over = []
            real = periodic_pdf.PeriodicPDF.put

            def watch(self, x, y, s, size=9, font="F1", color=periodic_pdf.INK):
                width = periodic_pdf.tw(s, size, font == "F2")
                m, pw = periodic_pdf.MARGIN, periodic_pdf.PAGE_W              # the page being drawn (size and orientation vary)
                if x < m - 0.5 or x + width > pw - m + 0.5 or y < 0 or y > periodic_pdf.PAGE_H:
                    over.append((round(x), round(width), s[:50]))
                return real(self, x, y, s, size, font, color)
            periodic_pdf.PeriodicPDF.put = watch
            try:
                for size, orient in (("a4", "auto"), ("a4", "landscape"), ("letter", "portrait"), ("a3", "auto")):
                    home.cfg.set("reports", "reports", "pdf_page_size", size)
                    home.cfg.set("reports", "reports", "pdf_orientation", orient)
                    for t in ("daily", "weekly", "monthly"):
                        _, d, files, _ = reports.generate(run, home.cfg, home.paths, ["pdf"], t, t)
                        self.assertTrue(files)
            finally:
                periodic_pdf.PeriodicPDF.put = real
            self.assertEqual(over, [])
            self.assertEqual((periodic_pdf.PAGE_W, periodic_pdf.MARGIN), (PAGE_W, MARGIN))     # the page settings are put back afterwards
        finally:
            home.cleanup()

    def test_page_sizes_and_small_margins(self):
        w, h, m = periodic_pdf.page_geometry("a4", "portrait", 8)
        self.assertEqual((round(w), round(h), round(m, 1)), (595, 842, 22.7))
        self.assertEqual(round(periodic_pdf.page_geometry("a4", "landscape", 8)[0]), 842)
        self.assertEqual(round(periodic_pdf.page_geometry("letter", "portrait", 8)[1]), 792)
        self.assertEqual(round(periodic_pdf.page_geometry("bogus", "portrait", 1)[2], 1), 14.2)   # unknown paper is A4; margin at least 5 mm

        class Doc(object):
            sections = [("Wide", [{"type": "table", "headers": ["COLUMN %d" % i for i in range(16)],
                                   "rows": [["value-%d-long" % i for i in range(16)]]}])]
        self.assertGreater(periodic_pdf.page_geometry("a4", "auto", 8, Doc())[0], 800)            # too wide for portrait: turned sideways


if __name__ == "__main__":
    unittest.main()


class ExecutiveTemplateTest(unittest.TestCase):
    def setUp(self):
        self.home = TempHome({"schedule": "[schedule:daily]\nfrequency = daily\nto_summary = ceo@example.com\nformats = html\n"})
        self.now = time.time()
        self.run = make_run(self.now)
        seed(self.home.paths, self.run)

    def tearDown(self):
        self.home.cleanup()

    def test_the_executive_summary_is_short_plain_and_has_the_answers(self):
        d = periodic.build(self.run, self.home.cfg, self.home.paths, "weekly", "executive", self.now)
        titles = [t for t, _ in d.sections]
        self.assertTrue(d.title.startswith("Executive Summary - Last 7 Days"))
        self.assertEqual(titles[:2], ["At a glance", "In plain words"])
        self.assertIn("Are we meeting the service target?", titles)
        self.assertIn("What needs a decision", titles)
        for technical in ("CPU and memory", "Network", "Top 10 resource users", "Filesystem usage and growth"):
            self.assertNotIn(technical, titles)
        tiles = [b for b in dict(d.sections)["At a glance"] if b["type"] == "kpis"][0]["items"]
        self.assertTrue(4 <= len(tiles) <= 6)
        self.assertEqual(d.basename_suffix(), "_executive")

    def test_management_gets_only_the_executive_files(self):
        _, outdir, files, notes = reports.generate(self.run, self.home.cfg, self.home.paths, ["html", "json"], "daily", "daily")
        names = sorted(os.path.basename(f) for f in files)
        self.assertEqual([n for n in names if "_executive" in n], [n for n in names if n.endswith(("_executive.html", "_executive.json"))])
        self.assertEqual(len([n for n in names if "_executive" in n]), 2)
        self.assertTrue([n for n in names if "_executive" not in n])               # the technical report is still written

    def test_the_template_catalog_and_live_preview(self):
        from lshme.app import App
        from lshme.reports import templates
        ids = [t["id"] for t in templates.catalog()]
        self.assertEqual(len(ids), len(set(ids)))
        for t in templates.catalog():
            self.assertIn(t["ptype"], ("daily", "weekly", "monthly"))
            self.assertIn(t["variant"], ("summary", "comprehensive", "database", "executive"))
            self.assertTrue(t["about"] and t["sections"] and t["audience"])
        with self.assertRaises(ValueError):
            templates.get("nope")
        app = App(self.home.dir and __import__("fakes").ROOT, home=self.home.dir)
        with self.assertRaises(ValueError) as cm:
            templates.preview(app, "executive-daily", "html")
        self.assertIn("health check", str(cm.exception))
        app.save_latest(self.run)
        body, ctype = templates.preview(app, "executive-weekly", "html")
        self.assertEqual(ctype, "text/html; charset=utf-8")
        self.assertIn(b"Executive Summary - Last 7 Days", body)
        pdf, ctype = templates.preview(app, "daily", "pdf")
        self.assertTrue(pdf.startswith(b"%PDF") and ctype == "application/pdf")
        self.assertEqual(os.listdir(os.path.join(self.home.dir, "data")).count("preview"), 0)    # nothing is left behind


class ClosedIssuesTest(unittest.TestCase):
    """A problem that has been fixed is history: it is listed as closed and no longer weighs on the score."""

    def setUp(self):
        self.home = TempHome()
        self.now = time.time()
        self.run = make_run(self.now)
        seed(self.home.paths, self.run)
        h = History(self.home.paths.history_db)
        h.con.executemany("INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)",
                          [(9000 + i, self.run.host, self.now - 5 * 3600 + i * 600, "CRITICAL", "MULTIPATH_ALL_FAILED", "mpatha", "storage", "All 2 paths of mpatha have failed")
                           for i in range(6)])
        h.con.commit()
        h.close()

    def tearDown(self):
        self.home.cleanup()

    def analyse(self):
        from lshme.engine import history
        h = History(self.home.paths.history_db)
        try:
            res = history.analyse(h, self.run, self.home.cfg)
        finally:
            h.close()
        return [f for f in res.findings if f.code == "RECURRING_CRITICAL"]

    def test_a_fixed_problem_no_longer_raises_a_repeating_warning(self):
        self.assertEqual(self.analyse(), [])                                    # six old criticals, none now: closed
        self.run.module("storage").add("CRITICAL", "MULTIPATH_ALL_FAILED", "mpatha", "All 2 paths of mpatha have failed")
        found = self.analyse()                                                  # still failing: it is a repeating problem
        self.assertEqual([f.component for f in found], ["mpatha"])

    def test_closed_problems_are_listed_under_closed_only(self):
        events = os.path.join(self.home.paths.data_dir, "watch_events.jsonl")
        os.makedirs(self.home.paths.data_dir, exist_ok=True)
        with open(events, "w", encoding="utf-8") as f:
            for e in (ev("full", "disk:/VM-OS-DATA", "/VM-OS-DATA", self.now - 4 * 3600, 43)):
                f.write(json.dumps(e) + "\n")
        sections = dict(periodic.build(self.run, self.home.cfg, self.home.paths, "weekly", "summary", self.now).sections)
        closed = [b for b in sections["Closed issues"] if b["type"] == "table"]
        self.assertEqual([b["title"][:8] for b in closed], ["Problems", "Incident"])
        self.assertEqual(str(closed[0]["rows"][0][1]), "mpatha")
        self.assertEqual(str(closed[0]["rows"][0][-1]), "closed")
        self.assertIn("/VM-OS-DATA", [str(c) for c in closed[1]["rows"][0]])
        self.assertTrue([b for b in sections["Closed issues"] if b["type"] == "note" and "do not count" in b["text"]])
        for title in ("Repeating problems and log errors", "Health by day"):
            for b in sections.get(title, []):
                if b["type"] == "table":
                    self.assertFalse([r for r in b["rows"] if "mpatha" in " ".join(str(c) for c in r)], title)
        attention = [b for b in sections["What needs attention"] if b["type"] == "actions"]
        self.assertFalse([a for b in attention for a in b["items"] if "mpatha" in a[1]])
        ex = dict(periodic.build(self.run, self.home.cfg, self.home.paths, "weekly", "executive", self.now).sections)
        self.assertIn("Resolved during the period", ex)
