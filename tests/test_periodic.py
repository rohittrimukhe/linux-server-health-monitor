# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Daily, weekly and monthly checklist reports."""
import json
import os
import time
import unittest
import zipfile

from fakes import TempHome
from lshme import reports
from lshme.core.model import ModuleResult, RunResult
from lshme.engine import scoring
from lshme.engine.history import History
from lshme.reports import periodic, periodic_html, periodic_net, periodic_top

GB = 1024 ** 3


def make_run(now):
    r = RunResult("db01", days=1, schedule="daily")
    r.collected_at = now
    r.fqdn, r.ip, r.version = "db01.example.com", "10.0.0.11", "test"
    r.meta = {"company": "Example Ltd", "customer": "ACME"}
    sy = ModuleResult("system", "Operating System", 8)
    sy.data = {"fqdn": "db01.example.com", "ip": "10.0.0.11", "distribution": "Oracle Linux 9", "kernel": "5.14", "architecture": "x86_64",
               "uptime": "41d", "boot_time": "x", "timezone": "UTC", "virtualization": "vmware", "reboot_required": False,
               "time_sync": True, "time_sync_source": "chronyc"}
    sy.metric("system.uptime_days", 41.0)
    st = ModuleResult("storage", "Storage", 20)
    st.data = {"filesystems": [{"mountpoint": "/u01", "fstype": "xfs", "size": 200 * GB, "used": 168 * GB, "free": 32 * GB, "used_percent": 84.0},
                               {"mountpoint": "/", "fstype": "xfs", "size": 50 * GB, "used": 23 * GB, "free": 27 * GB, "used_percent": 46.0}]}
    st.metric("fs.used_bytes:/u01", 168 * GB)
    st.metric("fs.size_bytes:/u01", 200 * GB)
    st.metric("fs.used_bytes:/", 23 * GB)
    st.metric("fs.size_bytes:/", 50 * GB)
    st.add("WARNING", "FS_USAGE", "/u01", "Filesystem /u01 is 84% full", recommendation="Extend it.")
    db = ModuleResult("database", "Databases", 15)
    db.metric("oracle.ORCL.datafile_bytes", 148 * GB)
    fo = ModuleResult("folders", "Folders", 0)
    fo.metric("folder.bytes:/u01/oradata", 148 * GB)
    fo.metric("folder.bytes:/tmp", 1024)
    bk = ModuleResult("backup", "Backup", 8)
    bk.metric("backup.age_hours:nightly", 30.0)
    bk.metric("backup.size_bytes:nightly", 40 * GB)
    ce = ModuleResult("certificates", "Certificates", 4)
    ce.metric("cert.days_left:/etc/pki/web.crt", 20.0)
    ce.metric("licence.days_left:Oracle support", 75.0)
    lg = ModuleResult("logs", "Logs", 8)
    lg.metric("logs.count:oom", 2)
    se = ModuleResult("security", "Security", 8)
    se.data = {"selinux": "enforcing", "firewall": {"tool": "firewalld", "active": True}, "sshd": {"permitrootlogin": "yes"}, "uid0_accounts": ["root"],
               "empty_passwords": [], "sudo_nopasswd": [], "auditd": "active", "updates": {"security": 7, "total": 20}, "last_patch": "2020-01-01"}
    se.metric("security.pending_security_updates", 7)
    cp = ModuleResult("cpu_memory", "CPU", 12)
    cp.metric("cpu.cores", 2)
    r.modules = [sy, st, db, fo, cp, bk, ce, lg, se]
    scoring.score_run(r)
    return r


def seed(paths, run, days=30):
    h = History(paths.history_db)
    now = run.collected_at
    cur = run.metrics()
    for back in range(days, 0, -1):
        ts = now - back * 86400
        c = h.con.cursor()
        c.execute("INSERT INTO runs(host, ts, schedule, score, status, critical, warning, unknown, duration, version, report_dir) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  ("db01", ts, "daily", 90, "WARNING", 0, 1, 0, 5.0, "t", ""))
        rid = c.lastrowid
        c.executemany("INSERT INTO metrics VALUES(?,?,?,?,?)",
                      [(rid, "db01", ts, k, (v - back * 0.5 * GB) if k.startswith(("fs.used", "oracle.", "folder.bytes:/u01")) else
                        (v * (1.0 if back < 5 else 1.8) if k.startswith("backup.size") else v + (back % 3)))
                       for k, v in cur.items() if k.startswith(("fs.used", "oracle.", "folder.bytes:/u01", "backup.", "logs.count", "security.pending"))])
    for hr in range(days * 24, 0, -1):
        h.add_hourly("db01", now - hr * 3600, 10 + (hr % 24), 60.0, 3.0, 0.3)
    for hr in range(days * 24, 0, -1):
        h.add_hourly("db01", now - hr * 3600 + 1, 10 + (hr % 24), 60.0, 3.0, 0.3, [20 + (hr % 7), 5], {"eth0": (125000, 62500)})
    h.save(run)
    h.close()


class PeriodicTest(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.now = time.time()
        self.run = make_run(self.now)
        seed(self.home.paths, self.run)

    def tearDown(self):
        self.home.cleanup()

    def doc(self, ptype, variant="summary"):
        return periodic.build(self.run, self.home.cfg, self.home.paths, ptype, variant, self.now)

    def titles(self, d):
        return [t for t, _ in d.sections]

    def test_daily_has_every_section(self):
        d = self.doc("daily")
        for want in ("Overall status", "Daily checklist", "CPU and memory", "Filesystem usage and growth", "Database size and growth",
                     "Top folders and growth", "Server overview", "What needs attention", "In short", "Top 10 resource users"):
            self.assertIn(want, self.titles(d))
        self.assertNotIn("Data growth by date", self.titles(d))

    def test_checklist_flags_the_full_filesystem(self):
        d = self.doc("daily")
        rows = [b for t, bl in d.sections if t == "Daily checklist" for b in bl if b["type"] == "table"][0]["rows"]
        fs = [r for r in rows if str(r[1]) == "Fullest filesystem"][0]
        self.assertEqual(fs[0].style, "chip-wn")
        self.assertEqual(d.status, "WARNING")

    def test_growth_is_computed_from_history(self):
        d = self.doc("weekly")
        folder = [b for t, bl in d.sections for b in bl if b["type"] == "table" and b["title"] == "Largest folders"][0]
        row = [r for r in folder["rows"] if str(r[0]) == "/u01/oradata"][0]
        self.assertTrue(str(row[3]).startswith("+"), row)
        self.assertNotIn("/tmp", [str(r[0]) for r in folder["rows"]])  # tiny folders are left out

    def test_weekly_and_monthly_have_growth_by_date(self):
        w, m = self.doc("weekly"), self.doc("monthly", "comprehensive")
        self.assertIn("Data growth by date", self.titles(w))
        self.assertIn("Data growth by date", self.titles(m))
        self.assertGreater(len(m.growth_rows), len(w.growth_rows))
        dates = set(r[0] for r in m.growth_rows if r[1] == "filesystem")
        self.assertGreaterEqual(len(dates), 28)
        self.assertEqual(len(periodic.build_all(self.run, self.home.cfg, self.home.paths, "monthly")), 2)
        self.assertEqual(len(periodic.build_all(self.run, self.home.cfg, self.home.paths, "daily")), 1)

    def test_capacity_forecast_is_weekly_and_monthly_by_default(self):
        daily, weekly, monthly = self.doc("daily"), self.doc("weekly"), self.doc("monthly")
        self.assertNotIn("Capacity forecast", self.titles(daily))
        self.assertIn("Capacity forecast", self.titles(weekly))
        self.assertIn("Capacity forecast", self.titles(monthly))
        heads = lambda d: [b["headers"] for t, bl in d.sections if t == "Filesystem usage and growth" for b in bl if b["type"] == "table"][0]
        self.assertNotIn("Full in", heads(daily))
        self.assertIn("Full in", heads(weekly))
        fc = [b for t, bl in weekly.sections if t == "Capacity forecast" for b in bl if b["type"] == "table"][0]
        self.assertIn("Act by", fc["headers"])
        self.assertEqual(len(fc["rows"]), 2)                 # /u01 and /
        self.home.cfg.set("reports", "reports", "capacity_forecast_in", "daily")
        daily2 = self.doc("daily")
        self.assertIn("Capacity forecast", self.titles(daily2))
        self.assertNotIn("Capacity forecast", self.titles(self.doc("weekly")))

    def test_weekly_has_comparison_log_digest_compliance_backups_and_calendar(self):
        d = self.doc("weekly")
        titles = self.titles(d)
        for want in ("This period against the last one", "Repeating problems and log errors", "Patch and compliance", "Backup verification",
                     "Certificate and licence calendar"):
            self.assertIn(want, titles)
        self.assertNotIn("Patch and compliance", self.titles(self.doc("daily")))
        tables = dict((b["title"], b) for t, bl in d.sections for b in bl if b["type"] == "table")
        comp = tables["Patch and compliance checks"]["rows"]
        by = dict((r[0], r) for r in comp)
        self.assertEqual(by["SSH root login"][3].style, "chip-wn")            # PermitRootLogin yes
        self.assertEqual(by["Pending security updates"][3].style, "chip-wn")  # 7 pending
        self.assertEqual(by["Last package change"][3].style, "chip-wn")       # patched in 2020
        self.assertEqual(by["SELinux"][3].style, "chip-ok")
        bk = tables["Backups: how old, how big"]["rows"][0]
        self.assertEqual(str(bk[0]), "nightly")
        self.assertEqual(bk[4].style, "chip-cr")                              # 30 h old and 44 % smaller than a week ago
        cal = tables["Certificate and licence expiry calendar"]["rows"]
        self.assertEqual([str(r[0]) for r in cal], ["/etc/pki/web.crt", "Oracle support"])   # soonest first
        self.assertEqual(cal[0][4].style, "chip-cr")                          # 20 days: within 30
        self.assertEqual(cal[1][4].style, "chip-wn")                          # 75 days: within 90
        cmp = [b for t, bl in d.sections if t == "This period against the last one" for b in bl][0]
        self.assertTrue(any(str(r[0]).startswith("CPU used") for r in cmp["rows"]))
        self.assertTrue(any(str(r[0]) == "Filesystem space added" for r in cmp["rows"]))

    def test_monthly_compares_with_last_month(self):
        self.assertIn("This month against last month", self.titles(self.doc("monthly")))

    def test_database_team_gets_a_database_only_report(self):
        self.home.cfg.set("schedule", "schedule:weekly", "to_database", "dba@example.com")
        self.home.cfg.set("schedule", "schedule:weekly", "to_summary", "boss@example.com")
        _, outdir, files, notes = reports.generate(self.run, self.home.cfg, self.home.paths, ["html"], "weekly", "weekly")
        names = [os.path.basename(f) for f in files]
        self.assertEqual(len(names), 2, names)
        self.assertTrue(any(n.endswith("_database.html") for n in names))
        d = self.doc("weekly", "database")
        titles = self.titles(d)
        self.assertIn("Database size and growth", titles)
        self.assertIn("Database health", titles)
        self.assertNotIn("Network", titles)
        self.assertNotIn("CPU and memory", titles)
        self.assertTrue(d.title.endswith("(db01.example.com, 10.0.0.11) (database)") or "(database)" in d.title)
        from lshme.ops import scheduler
        sch = scheduler.get(self.home.cfg, "weekly")
        self.assertEqual((sch.to_database, sch.to_summary), (["dba@example.com"], ["boss@example.com"]))

    def test_plain_english_and_no_recommendations(self):
        d = self.doc("daily")
        short = dict(d.sections)["In short"][0]["items"]
        self.assertTrue(any("/u01" in t for _, t in short), short)
        attention = dict(d.sections)["What needs attention"][0]["items"]
        self.assertTrue(any("full" in m.lower() for _, m, _ in attention))
        titles = " ".join(self.titles(d)).lower()
        self.assertNotIn("how to resolve", titles)
        self.assertNotIn("recommend", titles)

    def test_cpu_by_core_and_customer_details(self):
        d = self.doc("weekly")
        tables = [b["title"] for t, bl in d.sections for b in bl if b["type"] == "table"]
        self.assertTrue(any(t.startswith("CPU usage of every core (2 cores)") for t in tables), tables)
        self.assertIn(("Customer", "ACME"), d.details)

    def test_network_section_has_three_series_and_totals(self):
        d = self.doc("weekly")
        net = dict(d.sections)["Network"]
        wide = [b for b in net if b["type"] == "wide_charts"][0]["items"][0]
        self.assertEqual([s["name"] for s in wide["series"]], ["Sent", "Received", "Total"])
        self.assertEqual(wide["colors"], [periodic_net.SENT, periodic_net.RECV, periodic_net.TOTAL])
        tx, rx, tot = [s["pts"][5][1] for s in wide["series"]]
        self.assertAlmostEqual(tot, rx + tx, places=2)
        self.assertAlmostEqual(rx, 1.0, places=2)          # 125000 bytes per second received = 1 Mb/s
        self.assertAlmostEqual(tx, 0.5, places=2)
        titles = [b["title"] for b in net if b["type"] == "table"]
        self.assertTrue(any(t.startswith("Data sent and received") for t in titles), titles)
        self.assertTrue(any("by day" in t for t in titles), titles)

    def test_comprehensive_monthly_has_inventory(self):
        d = self.doc("monthly", "comprehensive")
        self.assertTrue(any(t in ("Server roles", "Server inventory") for t in self.titles(d)), self.titles(d))

    def test_every_format_is_written_and_monthly_has_two_reports(self):
        fmts = ["html", "pdf", "xlsx", "csv", "txt", "json"]
        _, outdir, files, notes = reports.generate(self.run, self.home.cfg, self.home.paths, fmts, "monthly", "monthly")
        names = [os.path.basename(f) for f in files]
        self.assertEqual(sum(1 for n in names if n.endswith("_comprehensive.pdf")), 1)
        self.assertEqual(sum(1 for n in names if n.endswith(".pdf")), 2)
        for f in files:
            self.assertGreater(os.path.getsize(f), 40, f)
        pdf = [f for f in files if f.endswith("_comprehensive.pdf")][0]
        with open(pdf, "rb") as fh:
            self.assertEqual(fh.read(5), b"%PDF-")
        for f in files:
            if f.endswith(".xlsx"):
                self.assertIsNone(zipfile.ZipFile(f).testzip())
        html = [f for f in files if f.endswith("_comprehensive.html")][0]
        with open(html, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("chartdata", body)
        data = json.loads(body.split("id='chartdata'>")[1].split("</script>")[0])
        self.assertGreaterEqual(len(data["charts"]), 4)
        self.assertEqual(notes, [])

    def test_email_body_is_inline_html(self):
        body = periodic_html.email_body(self.doc("daily"), ["/x/a.pdf"])
        self.assertIn("Fullest filesystem", body)
        self.assertNotIn("<script", body)

    def test_no_history_still_builds(self):
        home = TempHome()
        try:
            d = periodic.build(self.run, home.cfg, home.paths, "monthly", "comprehensive", self.now)
            self.assertTrue(d.sections)
        finally:
            home.cleanup()


SS = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
LISTEN 0      128    0.0.0.0:22      0.0.0.0:*     users:(("sshd",pid=10,fd=3))
ESTAB  0      0      10.0.0.1:22     10.0.0.9:5000 users:(("sshd",pid=11,fd=4))
	 cubic wscale:7,7 bytes_sent:1000 bytes_acked:1000 bytes_received:5000 segs_out:10
ESTAB  0      0      10.0.0.1:5432   10.0.0.7:6000 users:(("postgres",pid=20,fd=5))
	 cubic bytes_sent:200 bytes_received:300
ESTAB  0      0      10.0.0.1:5432   10.0.0.8:6001 users:(("postgres",pid=21,fd=5))
	 cubic bytes_sent:100 bytes_received:50
"""


class NetworkMathTest(unittest.TestCase):
    def test_bytes_moved_is_rate_times_time(self):
        pts = [(0, 100.0), (60, 100.0), (120, 100.0)]
        self.assertEqual(periodic_net.transferred(pts, 60.0), 100.0 * 180)   # first sample covers one step
        self.assertEqual(periodic_net.transferred([(0, 10.0), (100000, 10.0)], 60.0), 10.0 * (60 + 120))   # long gaps are capped


class TopUsersTest(unittest.TestCase):
    def test_ss_totals_per_program(self):
        by = dict((g["name"], g) for g in periodic_top.parse_ss(SS))
        self.assertEqual(by["sshd"]["listen"], 1)
        self.assertEqual((by["sshd"]["sent"], by["sshd"]["recv"]), (1000, 5000))
        self.assertEqual((by["postgres"]["conns"], by["postgres"]["sent"], by["postgres"]["recv"]), (2, 300, 350))

    def test_services_group_all_processes_of_a_unit(self):
        first = {"1": ("postgres", 0, 10, "postgresql"), "2": ("postgres", 0, 10, "postgresql"), "3": ("bash", 0, 5, None)}
        second = {"1": ("postgres", 40, 20, "postgresql"), "2": ("postgres", 40, 20, "postgresql"), "3": ("bash", 9, 5, None)}
        g = periodic_top.group(first, second, 1.0, 100, 2, 1000, True)
        self.assertEqual([x["name"] for x in g], ["postgresql"])          # processes outside a service are left out
        self.assertEqual((g[0]["procs"], g[0]["rss"]), (2, 40))
        self.assertAlmostEqual(g[0]["cpu"], 40.0)

    def test_cpu_and_memory_are_summed_per_program(self):
        first = {"1": ("db", 100, 10), "2": ("db", 100, 10), "3": ("web", 0, 5)}
        second = {"1": ("db", 150, 20), "2": ("db", 150, 20), "3": ("web", 10, 5)}
        g = dict((x["name"], x) for x in periodic_top.group(first, second, 1.0, 100, 2, 1000))
        self.assertEqual(g["db"]["procs"], 2)
        self.assertAlmostEqual(g["db"]["cpu"], 50.0)      # 100 ticks of 200 available
        self.assertEqual(g["db"]["rss"], 40)
        self.assertAlmostEqual(g["db"]["mem_pct"], 4.0)


if __name__ == "__main__":
    unittest.main()
