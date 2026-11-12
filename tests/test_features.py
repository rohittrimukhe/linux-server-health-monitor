# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""SLA reporting, asset inventory, fleet dashboard and Cockpit page."""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from fakes import ROOT, FakeRunner, TempHome, slurp
from lshme.core.model import ModuleResult, RunResult
from lshme.engine import scoring
from lshme.ops import cockpit_page, dashboard, inventory, sla, watch


def t(d, h, m=0):
    return time.mktime((2026, 10, d, h, m, 0, 0, 0, -1))


EVENTS = [
    {"event": "open", "key": "svc:listener.service", "severity": "CRITICAL", "component": "listener.service", "message": "down", "ts": t(3, 2)},
    {"event": "close", "key": "svc:listener.service", "severity": "CRITICAL", "component": "listener.service", "message": "", "ts": t(3, 3)},
    {"event": "open", "key": "disk:/u01", "severity": "WARNING", "component": "/u01", "message": "86%", "ts": t(10, 0)},
    {"event": "escalate", "key": "disk:/u01", "severity": "CRITICAL", "component": "/u01", "message": "95%", "ts": t(10, 12)},
    {"event": "close", "key": "disk:/u01", "severity": "CRITICAL", "component": "/u01", "message": "", "ts": t(10, 14)},
    # overlaps the disk outage: server downtime must not double count
    {"event": "open", "key": "mod:kvm:VM_CRASHED:app01", "severity": "CRITICAL", "component": "app01", "message": "crashed", "ts": t(10, 13)},
    {"event": "close", "key": "mod:kvm:VM_CRASHED:app01", "severity": "CRITICAL", "component": "app01", "message": "", "ts": t(10, 15)},
    {"event": "open", "key": "memory", "severity": "WARNING", "component": "memory", "message": "88%", "ts": t(30, 23)},  # still open
]


class SlaTest(unittest.TestCase):
    def test_month_range(self):
        s, e, label = sla.month_range("2026-02")
        self.assertEqual(label, "February 2026")
        self.assertAlmostEqual((e - s) / 86400, 28, places=0)
        s, e, label = sla.month_range(None, now=time.mktime((2026, 1, 15, 0, 0, 0, 0, 0, -1)))
        self.assertEqual(label, "December 2025")

    def test_compute(self):
        s, e, _ = sla.month_range("2026-10")
        r = sla.compute(EVENTS, s, e, now=e, coverage={"2026-10-%02d" % d: 1440 for d in range(1, 32)}, interval=60)
        # downtime: listener 1h + union(disk 12:00-14:00, vm 13:00-15:00) = 1h + 3h = 4h
        self.assertAlmostEqual(r["downtime"], 4 * 3600)
        self.assertAlmostEqual(r["availability"], 100 * (1 - 4 * 3600 / (e - s)), places=6)
        self.assertEqual(len(r["incidents"]), 4)
        self.assertEqual(r["critical_incidents"], 3)
        self.assertTrue([i for i in r["incidents"] if i["ongoing"]])
        comps = dict(((c["group"], c["component"]), c) for c in r["components"])
        self.assertAlmostEqual(comps[("Filesystem", "/u01")]["downtime"], 2 * 3600)
        self.assertAlmostEqual(comps[("Filesystem", "/u01")]["degraded"], 12 * 3600)
        self.assertEqual(comps[("KVM Virtualisation", "app01")]["critical"], 1)
        self.assertAlmostEqual(r["coverage"], 100.0, places=3)
        self.assertAlmostEqual(r["mttr"], (3600 + 14 * 3600 + 2 * 3600) / 3.0)

    def test_report_with_breach(self):
        home = TempHome({"main": "[sla]\ntarget_percent = 99.9\n"})
        s, e, label = sla.month_range("2026-10")
        r = sla.compute(EVENTS, s, e, now=e, coverage={"2026-10-01": 100}, interval=60)
        d = sla.build_doc(r, label, home.cfg, "db01")
        codes = set(f["code"] for f in d.findings)
        self.assertIn("SLA_BREACH", codes)           # 99.46% < 99.9%
        self.assertIn("SLA_LOW_COVERAGE", codes)
        from lshme import reports
        out = tempfile.mkdtemp()
        files, notes = reports.write_all(d, ["html", "pdf", "xlsx", "txt"], out, "sla")
        self.assertEqual(len(files), 4, notes)
        self.assertIn("AVAILABILITY BY COMPONENT", slurp([f for f in files if f.endswith(".txt")][0]))
        shutil.rmtree(out)
        home.cleanup()

    def test_watch_writes_incident_log(self):
        home = TempHome({"main": "[watch]\nconfirm = 2\nrotation_modules =\nsyslog = no\nalert_email = no\n",
                         "services": "[critical]\nservices =\n[processes]\nghost = no-such-process-xyz-123\n"})
        from lshme.app import App
        w = watch.Watcher(App(ROOT, home=home.dir))
        w.tick(1000.0)
        self.assertFalse(os.path.exists(w.events_path))            # not confirmed yet
        w.tick(1060.0)
        ev = sla.load_events(w.events_path)
        opened = [e for e in ev if e["key"] == "proc:ghost"]
        self.assertEqual(opened[0]["event"], "open")
        self.assertEqual(opened[0]["ts"], 1000.0)                   # incident starts at the first bad sample
        self.assertIn(time.strftime("%Y-%m-%d", time.localtime(1060.0)), w.state["coverage"])
        home.cleanup()


INV_FILES = {"/proc/cpuinfo": "processor : 0\nmodel name : Xeon Gold\nphysical id : 0\nprocessor : 1\nphysical id : 0\n",
             "/proc/meminfo": "MemTotal: 16384000 kB\n", "/sys/class/dmi/id/sys_vendor": "Dell Inc.",
             "/sys/class/dmi/id/product_name": "PowerEdge R740", "/sys/class/dmi/id/product_serial": "ABC123",
             "/sys/class/net/eth0/address": "aa:bb:cc:dd:ee:ff", "/sys/class/net/eth0/operstate": "up",
             "/sys/class/net/eth0/device": "", "/etc/os-release": 'PRETTY_NAME="Oracle Linux Server 8.9"\n',
             "/etc/oratab": "ORCL:/u01/app/oracle/product/19.0.0/dbhome_1:Y\n", "/proc/uptime": "86400 1"}


def inv_runner():
    return FakeRunner({
        "rpm -qa": (0, "bash\t4.4.20-4.el8\tx86_64\tOracle America\t1700000000\nopenssl\t1.1.1k-9.el8\tx86_64\tOracle America\t1700000000\n"),
        "lsblk": (0, 'NAME="sda" TYPE="disk" SIZE="536870912000" MODEL="PERC H740P" SERIAL="X1" ROTA="1" TRAN="sas" VENDOR="DELL"\n'
                     'NAME="zram0" TYPE="disk" SIZE="0" MODEL="" SERIAL="" ROTA="0" TRAN="" VENDOR=""\n'),
        "df": (0, "Filesystem Type 1-blocks Used Available Capacity Mounted on\n/dev/sda1 xfs 1000 500 500 50% /\n"),
        "ip -o addr show": (0, "2: eth0    inet 10.0.0.11/24 brd x scope global eth0\n"),
        "ps -eo user,args": (0, "USER COMMAND\noracle ora_pmon_ORCL\npostgres /usr/pgsql-15/bin/postgres -D /var/lib/pgsql\n"),
        "psql --version": (0, "psql (PostgreSQL) 15.5"),
        "virsh -c qemu:///system list --all --name": (0, "vm1\n"),
        "virsh -c qemu:///system dominfo vm1": (0, "State:          running\nCPU(s):         4\nMax memory:     8388608 KiB\nAutostart:      enable\n"),
        "ss -H -lntup": (0, 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'),
    }, files=INV_FILES, dirs=["/sys/class/net/eth0"])


class InventoryTest(unittest.TestCase):
    def test_collect(self):
        inv = inventory.collect(inv_runner())
        srv = inv["Servers"][0]
        self.assertEqual((srv["manufacturer"], srv["model"], srv["serial"]), ("Dell Inc.", "PowerEdge R740", "ABC123"))
        self.assertEqual(srv["os"], "Oracle Linux Server 8.9")
        self.assertEqual(srv["logical_cpus"], 2)
        self.assertEqual([d["device"] for d in inv["Disks"]], ["sda"])          # zero-size zram skipped
        self.assertEqual(len(inv["Packages"]), 2)
        engines = dict((d["engine"], d) for d in inv["Databases"])
        self.assertEqual(engines["Oracle"]["version"], "19.0.0")
        self.assertEqual(engines["PostgreSQL"]["version"], "15.5")
        self.assertEqual(engines["PostgreSQL"]["owner"], "postgres")
        self.assertEqual(inv["VMs"][0]["vcpus"], "4")
        self.assertEqual(inv["Services"][0]["port"], 22)

    def test_doc_and_csv(self):
        inv = inventory.collect(inv_runner())
        d = inventory.as_doc(inventory.merge([inv, inv]), None, errors={"db9": "unreachable"})
        self.assertEqual(len(d.module_rows), 3)
        out = tempfile.mkdtemp()
        files = inventory.write_csv(inv, out, "inv")
        self.assertTrue(any(f.endswith("_packages.csv") for f in files))
        from lshme import reports
        written, notes = reports.write_all(d, ["xlsx"], out, "inv")
        self.assertEqual(len(written), 1, notes)
        try:
            import openpyxl
            self.assertIn("Packages", openpyxl.load_workbook(written[0]).sheetnames)
        except ImportError:
            pass
        shutil.rmtree(out)


def fleet_run(host, status, score, group, age=0):
    r = RunResult(host)
    m = ModuleResult("storage", "Storage", 20)
    if status != "HEALTHY":
        m.add(status, "DISK_USAGE", "/u01", "Filesystem /u01 is full")
    r.modules = [m]
    scoring.score_run(r)
    r.meta = {"group": group, "environment": group}
    r.collected_at = time.time() - age * 3600
    return r


class DashboardTest(unittest.TestCase):
    def test_build_and_serve(self):
        home = TempHome()
        runs = [fleet_run("db01", "CRITICAL", 40, "Production"), fleet_run("db02", "WARNING", 85, "DR"),
                fleet_run("app01", "HEALTHY", 100, "UAT"), fleet_run("old01", "HEALTHY", 100, "UAT", age=48)]
        dashboard.save_results(home.paths, runs, {"db09": "No route to host"})
        path, n, counts = dashboard.build(home.cfg, home.paths)
        self.assertEqual(n, 5)
        self.assertEqual(counts, {"CRITICAL": 1, "WARNING": 1, "HEALTHY": 1, "STALE": 1, "UNREACHABLE": 1})
        page = slurp(path)
        self.assertIn("LRS Services", page)
        self.assertIn('http-equiv="refresh"', page)
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(path), "servers", "db01.html")))
        httpd = dashboard.serve(os.path.dirname(path), "127.0.0.1", 0)
        port = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever)
        th.daemon = True
        th.start()
        try:
            self.assertEqual(urlopen("http://127.0.0.1:%d/" % port, timeout=5).getcode(), 200)
            for bad in ("/servers/", "/../config/smtp.conf"):
                with self.assertRaises(HTTPError):
                    urlopen("http://127.0.0.1:%d%s" % (port, bad), timeout=5)
        finally:
            httpd.shutdown()
            httpd.server_close()
        home.cleanup()


class CockpitPageTest(unittest.TestCase):
    def test_install_remove(self):
        home = TempHome()
        from lshme.app import App
        app = App(ROOT, home=home.dir)
        dest = os.path.join(home.dir, "cockpit", "lshme")
        cockpit_page.install(app, dest=dest)
        manifest = json.loads(slurp(os.path.join(dest, "manifest.json")))
        self.assertEqual(manifest["menu"]["index"]["label"], "LSHME Health")
        cfg = json.loads(slurp(os.path.join(dest, "config.json")))
        self.assertEqual(cfg["data_dir"], app.paths.data_dir)
        self.assertIn("--home", cfg["command"])
        self.assertTrue(os.path.exists(os.path.join(dest, "lrs_logo.png")))
        js = slurp(os.path.join(dest, "lshme.js"))
        self.assertNotIn("eval(", js)
        self.assertIn('superuser: "require"', js)    # running a check needs admin rights in Cockpit
        self.assertEqual(cockpit_page.remove(dest=dest), dest)
        home.cleanup()


if __name__ == "__main__":
    unittest.main()
