# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Self-repair, HTML alert e-mails, the VM CPU figure with its cause, and the estate report (all servers in one)."""
import json
import os
import time
import unittest

from fakes import ROOT, FakeRunner, TempHome
from lshme.app import App
from lshme.ops import selfrepair

JOURNAL = """Started LSHME daily (daily).
Traceback (most recent call last):
  File "/opt/lshme/lshme/app.py", line 120, in run
lshme.ops.san.SanError: cannot reach 172.16.101.55: timed out
lshme-daily.service: Main process exited, code=exited, status=1/FAILURE
"""


class SelfRepairTests(unittest.TestCase):
    def test_reason_from_the_log_and_clearing(self):
        r = FakeRunner({"journalctl -u lshme-daily.service": (0, JOURNAL), "systemctl reset-failed lshme-daily.service": (0, ""),
                        "systemctl list-units lshme-*": (0, "lshme-daily.service loaded failed failed LSHME daily\n")})
        self.assertEqual(selfrepair.failed_jobs(r), ["lshme-daily.service"])
        self.assertEqual(selfrepair.cause(r, "lshme-daily.service"), "lshme.ops.san.SanError: cannot reach 172.16.101.55: timed out")
        r2 = FakeRunner({"journalctl -u lshme-weekly.service": (0, "Starting...\nError: SMTP host is not configured\nlshme-weekly.service: Failed with result 'exit-code'.\n")})
        self.assertIn("Failed with result", selfrepair.cause(r2, "lshme-weekly.service"))
        home = TempHome()
        try:
            app = App(ROOT, home=home.dir)
            app.runner = r
            problems = {"mod:services:SERVICE_FAILED:lshme-daily.service": {"alerted": "WARNING"}, "cpu": {"alerted": "WARNING"}}
            state = {}
            self.assertEqual(selfrepair.auto(app, problems, state), ["lshme-daily.service"])
            self.assertEqual(selfrepair.auto(app, problems, state), [])                 # at most once an hour
            self.assertIn("systemctl reset-failed lshme-daily.service", r.calls)
            steps = selfrepair.repair(app)
            job = [s for s in steps if s["step"].startswith("Job ")][0]
            self.assertTrue(job["fixed"])
            self.assertIn("cannot reach 172.16.101.55", job["detail"])
            self.assertTrue([s for s in steps if s["step"] == "Program"][0]["ok"])
        finally:
            home.cleanup()


class AlertMailTests(unittest.TestCase):
    def test_designed_alert_email(self):
        from lshme.reports import alertmail
        alerts = [{"severity": "WARNING", "kind": "STILL", "component": "OracleDBLive-PRi", "message": "VM OracleDBLive-PRi: its 4 vCPU are 97% busy",
                   "recommendation": "Check the busiest programs inside the VM.", "since": time.time() - 3600}]
        rec = [{"kind": "RECOVERED", "component": "chronyd", "message": "Critical service chronyd is inactive", "duration": 360}]
        html, images = alertmail.build("hhk-kvm-h1", alerts, rec, "WARNING", "https://172.16.101.52:8443/")
        for want in ("Real-time alert", "hhk-kvm-h1", "1 warning", "Still open", "What to do:", "Resolved or improved", "Open the dashboard"):
            self.assertIn(want, html)
        for bad in ("<script", "<svg", "display:flex"):
            self.assertNotIn(bad, html)


class VmCpuTests(unittest.TestCase):
    def test_vcpu_percent_never_over_100_and_the_cause_is_named(self):
        from lshme.modules import kvm
        vm = {"host_cpu_cores": 9.3, "emulation_cpu_cores": 5.3, "disk_read_bps": 120 * 2 ** 20, "disk_write_bps": 80 * 2 ** 20, "net_rx_bps": 0, "net_tx_bps": 0}
        t = kvm.cause_text(vm)
        self.assertIn("host CPU for this VM 9.3 cores", t)
        self.assertIn("disk/network emulation 5.3", t)
        self.assertIn("120.0 MB read", t)
        self.assertIn("virtio", kvm.cpu_advice(vm, ""))


class EstateTests(unittest.TestCase):
    def test_one_report_for_every_server(self):
        from lshme.ops import dashboard
        from lshme.reports import consolidated
        from test_periodic import make_run, seed
        home = TempHome()
        try:
            app = App(ROOT, home=home.dir)
            now = time.time()
            local = make_run(now)
            local.host = "hhk-kvm-h1"
            seed(app.paths, local)
            app.save_latest(local)
            m1, m2 = make_run(now), make_run(now)
            m1.host, m2.host = "kvm-h2", "oracledblive-sec"
            m1.meta["fleet_name"], m2.meta["fleet_name"] = "kvm-h2", "oracledblive-sec"
            dashboard.save_results(app.paths, [m1, m2], {"pacs-srv-01": "ssh: connect to host 10.0.0.9 port 22: Connection timed out"})
            doc, outdir, files, notes = consolidated.generate(app, "weekly")
            titles = [t for t, _ in doc.sections]
            self.assertEqual(titles[0], "Estate at a glance")
            for name in ("hhk-kvm-h1", "kvm-h2", "oracledblive-sec"):
                self.assertTrue(any(t.startswith(name + " - ") for t in titles), name)
            glance = dict(doc.sections)["Estate at a glance"]
            servers = [b for b in glance if b.get("type") == "table" and b.get("title") == "Servers"][0]
            self.assertEqual(len(servers["rows"]), 4)                                    # three answered, one unreachable
            self.assertTrue(any("pacs-srv-01" in t for _, t in glance[1]["items"]))
            self.assertEqual(len(files), 2)
            self.assertTrue(all(os.path.getsize(f) > 10000 for f in files))
        finally:
            home.cleanup()


class RepairWebTests(unittest.TestCase):
    def test_repair_this_server_from_the_dashboard(self):
        from test_web import WebCase
        case = type("T", (WebCase,), {"runTest": lambda self: None})()
        case.setUp()
        try:
            self.assertEqual(case.call("POST", "/api/repair", {"server": "_local"}, user="viewer").status, 403)
            r = case.call("POST", "/api/repair", {"server": "_local", "check": True}, user="admin")
            self.assertEqual(r.status, 200)
            self.assertTrue(any(s["step"] == "Program" for s in r.json["steps"]))
            self.assertEqual(case.call("POST", "/api/repair", {"server": "nobody"}, user="admin").status, 404)
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
