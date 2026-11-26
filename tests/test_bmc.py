# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Server hardware through its BMC (Redfish and IPMI) and the dependency map: hardware -> server -> VMs -> servers -> databases,
SAN pool -> volume -> host disk -> mount -> VM."""
import json
import os
import time
import unittest

from fakes import TempHome
from lshme.app import App
from lshme.core.model import ModuleResult
from lshme.ops import bmc, topology
from test_san import FakeHttp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REDFISH = {
    "GET /redfish/v1/": {"Systems": {"@odata.id": "/redfish/v1/Systems"}, "Chassis": {"@odata.id": "/redfish/v1/Chassis"}, "Managers": {"@odata.id": "/redfish/v1/Managers"}},
    "GET /redfish/v1/Systems": {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
    "GET /redfish/v1/Systems/1": {"Model": "ThinkSystem SR650 V2", "Manufacturer": "Lenovo", "SerialNumber": "J30ABCD", "PowerState": "On", "BiosVersion": "AFE1",
                                  "Status": {"Health": "Critical", "State": "Enabled"},
                                  "ProcessorSummary": {"Count": 2, "Status": {"Health": "OK"}}, "MemorySummary": {"TotalSystemMemoryGiB": 512, "Status": {"Health": "OK"}}},
    "GET /redfish/v1/Chassis": {"Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]},
    "GET /redfish/v1/Chassis/1": {"Thermal": {"@odata.id": "/redfish/v1/Chassis/1/Thermal"}, "Power": {"@odata.id": "/redfish/v1/Chassis/1/Power"}},
    "GET /redfish/v1/Chassis/1/Thermal": {"Temperatures": [{"Name": "Ambient", "ReadingCelsius": 24, "UpperThresholdNonCritical": 40, "UpperThresholdCritical": 45, "Status": {"Health": "OK"}}],
                                          "Fans": [{"Name": "Fan 1", "Reading": 6200, "Status": {"Health": "OK"}}, {"Name": "Fan 2", "Reading": 0, "Status": {"Health": "Critical"}}]},
    "GET /redfish/v1/Chassis/1/Power": {"PowerSupplies": [{"Name": "PSU 1", "Status": {"Health": "OK"}}, {"Name": "PSU 2", "Status": {"State": "Absent"}}],
                                        "PowerControl": [{"PowerConsumedWatts": 412}]},
    "GET /redfish/v1/Managers": {"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
    "GET /redfish/v1/Managers/1": {"FirmwareVersion": "XCC 4.10"},
}


def item(**kw):
    a = {"name": "kvm1-xcc", "type": "xcc", "address": "10.0.0.50", "port": 443, "username": "monitor", "password": "pw", "verify_tls": False, "enabled": True, "server": "_local"}
    a.update(kw)
    return a


class BmcTests(unittest.TestCase):
    def test_redfish_reading(self):
        d = bmc.collect(item(), FakeHttp(REDFISH))
        self.assertEqual((d["model"], d["serial"], d["power"], d["status"], d["power_watts"], d["bmc_firmware"]),
                         ("ThinkSystem SR650 V2", "J30ABCD", "on", "critical", 412.0, "XCC 4.10"))
        self.assertEqual([f["name"] for f in d["fans"] if f["status"] == "critical"], ["Fan 2"])
        self.assertEqual([p["name"] for p in d["psus"]], ["PSU 1"])                 # an empty bay is left out
        self.assertEqual(d["temps"][0]["crit"], 45.0)

    def test_wrong_type_and_refused_login(self):
        with self.assertRaises(bmc.BmcError):
            bmc.collect(item(), FakeHttp({"GET /redfish/v1/": {}}))
        res = bmc.test(item(address="127.0.0.1", port=1))
        self.assertFalse(res["ok"])

    def test_ipmi_reading(self):
        out = {"chassis status": "System Power         : on\n",
               "sdr elist full": "Inlet Temp | 04h | ok | 7.1 | 23 degrees C\nFan1 | 30h | cr | 29.1 | 0 RPM\nPS1 Status | 63h | ok | 10.1 | Presence detected\n",
               "sel elist last 30": "   1 | 11/24/2026 | 10:00:00 | Power Supply PS2 | Failure detected | Asserted\n",
               "fru print 0": " Product Name          : PowerEdge R740\n Product Serial        : ABC123\n"}
        d = bmc.collect(item(type="ipmi", port=623), run=lambda a, args: out[" ".join(args)])
        self.assertEqual((d["power"], d["status"], d["model"], d["serial"]), ("on", "critical", "PowerEdge R740", "ABC123"))
        self.assertEqual(d["events"][0]["severity"], "critical")

    def test_module_findings(self):
        from lshme.modules import bmc as mod
        home = TempHome({"main": "[bmc:kvm1-xcc]\ntype = xcc\naddress = 10.0.0.50\nusername = monitor\nserver = _local\n"})
        try:
            app = App(ROOT, home=home.dir)
            orig = bmc.collect
            bmc.collect = lambda a, http=None, run=None: orig(a, FakeHttp(REDFISH))
            try:
                res = ModuleResult(mod.NAME, mod.TITLE, mod.WEIGHT)

                class Ctx(object):
                    cfg = app.cfg

                    def t(self, s, k, d=0):
                        return d
                mod.run(Ctx(), res)
            finally:
                bmc.collect = orig
            codes = dict((f.code + ":" + f.component, f.severity) for f in res.findings)
            self.assertEqual(codes["BMC_HEALTH:kvm1-xcc"], "CRITICAL")
            self.assertEqual(codes["BMC_FAN:kvm1-xcc/Fan 2"], "CRITICAL")
            self.assertEqual(res.metrics["bmc.power_watts:kvm1-xcc"], 412.0)
        finally:
            home.cleanup()

    def test_old_event_log_lines_are_history(self):
        from lshme.modules import bmc as mod
        now = time.time()
        iso = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))
        self.assertAlmostEqual(mod.event_time(iso(now - 3600)), now - 3600, delta=2)
        self.assertAlmostEqual(mod.event_time(time.strftime("%m/%d/%Y %H:%M:%S", time.localtime(now - 7200))), now - 7200, delta=2)
        self.assertIsNone(mod.event_time("Pre-Init"))
        data = {"name": "kvm2-xcc", "type": "xcc", "make": "Lenovo", "model": "SR650", "serial": "X", "bios": "", "bmc_firmware": "", "power": "on", "status": "ok",
                "cpus": 2, "memory_gb": 128, "temps": [], "fans": [], "psus": [{"name": "PSU 1", "watts": 140, "status": "ok"}], "power_watts": 146, "drives": [],
                "memory_faults": [], "cpu_faults": [], "reached": "10.0.0.51", "server": "",
                "events": [{"time": iso(now - 160 * 86400), "severity": "critical", "text": "PSU 1 input failure has transitioned to critical"},
                           {"time": iso(now - 1800), "severity": "critical", "text": "Fan 3 failed"}]}
        home = TempHome({"main": "[bmc:kvm2-xcc]" + chr(10) + "type = xcc" + chr(10) + "address = 10.0.0.51" + chr(10) + "username = monitor" + chr(10)})
        try:
            app = App(ROOT, home=home.dir)
            orig = bmc.collect
            bmc.collect = lambda a, http=None, run=None: dict(data)
            try:
                res = ModuleResult(mod.NAME, mod.TITLE, mod.WEIGHT)

                class Ctx(object):
                    cfg = app.cfg

                    def t(self, s, k, d=0):
                        return d
                mod.run(Ctx(), res)
            finally:
                bmc.collect = orig
            self.assertNotIn("BMC_EVENT", [f.code for f in res.findings])     # every part reads healthy now: closed, not counted
            closed = [f for f in res.findings if f.code == "BMC_EVENT_CLOSED"]
            self.assertEqual([f.severity for f in closed], ["INFO"])          # only the recent one is listed; the 160-day-old one is history
            self.assertIn("Fan 3 failed", closed[0].message)
            data["fans"] = [{"name": "Fan 3", "rpm": 0, "status": "critical"}]           # still broken: it counts
            res = ModuleResult(mod.NAME, mod.TITLE, mod.WEIGHT)
            bmc.collect = lambda a, http=None, run=None: dict(data)
            try:
                mod.run(Ctx(), res)
            finally:
                bmc.collect = orig
            self.assertEqual([f.severity for f in res.findings if f.code == "BMC_EVENT"], ["WARNING"])
        finally:
            home.cleanup()

    def test_save_never_shows_the_password(self):
        home = TempHome()
        try:
            app = App(ROOT, home=home.dir)
            bmc.save(app, "kvm1-xcc", "lenovo", "10.0.0.50", "monitor", "secret-pw", server="_local")
            a = bmc.find(app.cfg, "kvm1-xcc")
            self.assertEqual((a["type"], a["password"], a["server"]), ("xcc", "secret-pw", "_local"))
            self.assertNotIn("secret-pw", json.dumps(bmc.public(a)))
            self.assertNotIn("secret-pw", open(os.path.join(home.dir, "config", "main.conf"), encoding="utf-8").read())
            bmc.remove(app, "kvm1-xcc")
            self.assertEqual(bmc.bmcs(app.cfg), [])
        finally:
            home.cleanup()


def run_dict(host, modules, status="HEALTHY", fleet_name=None):
    return {"host": host, "collected_at": time.time(), "health": {"status": status, "score": 90, "counts": {}}, "meta": {"fleet_name": fleet_name or host},
            "excluded": [], "modules": [dict({"findings": [], "tables": [], "metrics": {}, "errors": [], "skipped": [], "status": "OK", "title": m["name"], "weight": 1,
                                              "summary": "", "duration": 0.1}, **m) for m in modules]}


class TopologyTests(unittest.TestCase):
    def test_hardware_san_host_vm_server_database_chain(self):
        home = TempHome({"servers": "[server:oracledblive-sec]\nhost = 172.16.101.23\n"})
        try:
            app = App(ROOT, home=home.dir)
            os.makedirs(os.path.join(app.paths.data_dir, "fleet"), exist_ok=True)
            central = run_dict("hhk-kvm-h1", [
                {"name": "san", "data": {"arrays": [{"name": "hhk-de", "status": "optimal", "drives": [{"slot": "1", "status": "failed"}],
                                                     "pools": [{"name": "DDP_VM", "status": "degraded", "used_percent": 85}],
                                                     "volumes": [{"name": "OracleDB-OS-SEC", "pool": "DDP_VM", "status": "optimal", "wwn": "6d039ea000ecac8f000001486ad919a7"}]}]}},
                {"name": "bmc", "data": {"bmcs": [{"name": "kvm1-xcc", "server": "_local", "status": "ok", "power": "on", "make": "Lenovo", "model": "SR650"}]}},
                {"name": "storage", "data": {"multipath": {"maps": [{"name": "mpathf", "wwid": "36d039ea000ecac8f000001486ad919a7", "paths": 2, "active": 2, "failed": 0}]},
                                             "filesystems": [{"mountpoint": "/oracledbsec-os", "device": "/dev/mapper/mpathf", "used_percent": 7}]}},
                {"name": "kvm", "data": {"vms": [{"name": "OracleDBLive-SEC", "state": "running", "macs": ["52:54:00:aa:bb:cc"],
                                                  "disks": [{"name": "vda", "path": "/oracledbsec-os/OracleDB-SEC"}]}]}}])
            member = run_dict("oracledblive-sec", [
                {"name": "network", "data": {"interfaces": [{"name": "eth0", "mac": "52:54:00:AA:BB:CC"}]}},
                {"name": "database", "data": {"instances": [{"engine": "oracle", "instance": "ORCL", "role": "PHYSICAL STANDBY"}]}}], fleet_name="oracledblive-sec")
            with open(os.path.join(app.paths.data_dir, "latest.json"), "w", encoding="utf-8") as f:
                json.dump(central, f)
            with open(os.path.join(app.paths.data_dir, "fleet", "oracledblive-sec.json"), "w", encoding="utf-8") as f:
                json.dump(member, f)
            g = topology.build(app)
            path = ["san:hhk-de", "pool:hhk-de:DDP_VM", "vol:hhk-de:OracleDB-OS-SEC", "lun:hhk-kvm-h1:mpathf", "mnt:hhk-kvm-h1:/oracledbsec-os",
                    "vm:hhk-kvm-h1:OracleDBLive-SEC", "srv:oracledblive-sec", "db:oracledblive-sec:oracle:ORCL"]
            for parent, child in zip(path, path[1:]):
                self.assertIn(child, g.children.get(parent, []), "%s -> %s" % (parent, child))
            self.assertIn("srv:hhk-kvm-h1", g.children["bmc:kvm1-xcc"])            # hardware runs the central server
            imp = topology.impacts(g)
            self.assertEqual(imp[0]["cause"]["id"], "san:hhk-de")                   # the failed drive is the first failure on the path
            hit = [a["id"] for a in imp[0]["affected"]]
            self.assertIn("db:oracledblive-sec:oracle:ORCL", hit)
            self.assertNotIn("pool:hhk-de:DDP_VM", [i["cause"]["id"] for i in imp])  # the degraded pool is below the failed array, not a second cause
            snap = topology.snapshot(app)
            self.assertEqual(snap["counts"]["database"], 1)
        finally:
            home.cleanup()


if __name__ == "__main__":
    unittest.main()
