# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""SAN storage: drivers for every supported make (with recorded-style answers), the health check, the reports, the terminal and web."""
import json
import time
import unittest

from fakes import ROOT, TempHome
from lshme import reports
from lshme.app import App
from lshme.core.model import ModuleResult
from lshme.ops import san
from lshme.reports import periodic

GB = 1024 ** 3


class FakeHttp(object):
    """Answers like a storage array: a dict of 'METHOD path-suffix' -> body (or (body, headers)); records every call."""

    def __init__(self, routes, down=()):
        self.routes, self.down, self.calls = routes, set(down), []

    def call(self, method, url, headers=None, verify=False, body=None):
        host = url.split("/")[2].split(":")[0]
        self.calls.append((method, url, dict(headers or {}), body))
        if host in self.down:
            raise san.SanError("cannot reach %s: timed out" % host)
        for key in sorted(self.routes, key=len, reverse=True):
            m, suffix = key.split(" ", 1)
            if m == method and url.split("?")[0].endswith(suffix.split("?")[0]):
                v = self.routes[key]
                if isinstance(v, Exception):
                    raise v
                return v if isinstance(v, tuple) else (v, {})
        raise san.SanError("HTTP 404 from %s" % host)

    def get(self, url, headers=None, verify=False):
        return self.call("GET", url, headers, verify)[0]

    def post(self, url, headers=None, verify=False, body=None):
        return self.call("POST", url, headers, verify, body if body is not None else {})


def de_routes():
    p = "/devmgr/v2/storage-systems/1"
    return {
        "GET /devmgr/v2/storage-systems": [{"id": "1"}],
        "GET " + p: {"name": "HHK-DE4000", "model": "DE4000H", "chassisSerialNumber": "S4A123", "fwVersion": "08.90.12.00", "status": "needsAttn"},
        "GET " + p + "/controllers": [{"physicalLocation": {"label": "A"}, "status": "optimal", "cacheMemorySize": 8192},
                                     {"physicalLocation": {"label": "B"}, "status": "optimal", "cacheMemorySize": 8192}],
        "GET " + p + "/drives": [{"physicalLocation": {"trayRef": "0E00", "slot": i}, "status": "failed" if i == 7 else "optimal", "driveMediaType": "hdd",
                                  "rawCapacity": str(4 * 10 ** 12), "hotSpare": i == 12, "pfa": i == 3} for i in range(1, 13)],
        "GET " + p + "/storage-pools": [{"id": "pool1", "volumeGroupRef": "pool1", "label": "DDP_VM", "raidLevel": "raidDiskPool", "totalRaidedSpace": str(30 * 10 ** 12),
                                         "usedSpace": str(int(25.5 * 10 ** 12)), "freeSpace": str(int(4.5 * 10 ** 12)), "raidStatus": "degraded"}],
        "GET " + p + "/volumes": [{"label": "ORACLE-DB-OS-PRI", "volumeGroupRef": "pool1", "capacity": str(2 * 10 ** 12), "status": "optimal", "mapped": True,
                                   "listOfMappings": [{"lunMappingRef": "x"}]},
                                  {"label": "VM-OS-DATA", "volumeGroupRef": "pool1", "capacity": str(10 ** 12), "status": "optimal", "mapped": True, "listOfMappings": []}],
        "GET " + p + "/hardware-inventory": {"fans": [{"status": "optimal"}, {"status": "failed"}], "powerSupplies": [{"status": "optimal"}, {"status": "optimal"}],
                                             "batteries": [{"status": "optimal"}]},
        "GET " + p + "/failures": [{"failureType": "failedDrive"}],
        "GET " + p + "/mel-events": [{"timeStamp": "2026-11-22 00:01", "priority": "critical", "description": "Drive failed"}],
        "GET " + p + "/analysed-system-statistics": {"readIOps": 820, "writeIOps": 410, "readThroughput": 95.5, "writeThroughput": 40.2,
                                                     "readResponseTime": 3.2, "writeResponseTime": 26.4},
    }


def arr(t, port=None, user="monitor", ctl=("172.16.101.55", "172.16.101.56")):
    return {"name": "hhk-de", "type": t, "controllers": list(ctl), "port": port or san.TYPES[t][1], "username": user, "password": "pw", "verify_tls": False, "enabled": True}


class DriverTests(unittest.TestCase):
    def test_lenovo_de_full_health(self):
        http = FakeHttp(de_routes())
        d = san.collect(arr("santricity"), http, reach=lambda h, p: (True, ""))
        self.assertEqual((d["model"], d["firmware"], d["status"], d["reached"]), ("DE4000H", "08.90.12.00", "needs attention", "172.16.101.55"))
        self.assertEqual([c["status"] for c in d["controllers"]], ["optimal", "optimal"])
        self.assertEqual(sum(1 for x in d["drives"] if x["status"] == "failed"), 1)
        self.assertTrue(any(x["predictive_failure"] for x in d["drives"]))
        self.assertEqual(d["pools"][0]["used_percent"], 85.0)
        self.assertEqual(d["volumes"][0]["pool"], "DDP_VM")
        self.assertTrue(any(h["status"] == "failed" for h in d["hardware"]))
        self.assertEqual(d["perf"]["latency_ms"], 26.4)
        self.assertTrue(all(m == "GET" for m, _, _, _ in http.calls))                               # read-only: nothing but GET
        self.assertTrue(all(c[2].get("Authorization", "").startswith("Basic ") for c in http.calls))

    def test_controller_b_answers_when_a_is_down(self):
        d = san.collect(arr("santricity"), FakeHttp(de_routes(), down={"172.16.101.55"}), reach=lambda h, p: (h != "172.16.101.55", "timed out"))
        self.assertEqual(d["reached"], "172.16.101.56")
        with self.assertRaises(san.SanError):
            san.collect(arr("santricity"), FakeHttp(de_routes(), down={"172.16.101.55", "172.16.101.56"}), reach=lambda h, p: (False, "timed out"))

    def test_a_wrong_password_is_said_plainly_and_not_retried_on_b(self):
        http = FakeHttp({"GET /devmgr/v2/storage-systems": san.SanError("the storage refused the user name or password (HTTP 401)")})
        r = san.test(arr("santricity"), http, reach=lambda h, p: (True, ""))
        self.assertFalse(r["ok"])
        self.assertIn("refused", r["message"])
        self.assertEqual(len(http.calls), 1)

    def test_every_make(self):
        msa = {"GET /api/login/" + __import__("hashlib").sha256(b"monitor_pw").hexdigest(): {"status": [{"response": "KEY", "return-code": 1}]},
               "GET /api/show/system": {"system": [{"system-name": "MSA", "product-id": "MSA 2060", "midplane-serial-number": "M1", "health": "Degraded"}]},
               "GET /api/show/controllers": {"controllers": [{"controller-id": "A", "health": "OK", "sc-fw": "IN100"}, {"controller-id": "B", "health": "Fault"}]},
               "GET /api/show/disks": {"drives": [{"location": "1.1", "health": "OK", "size-numeric": "7814037168"}, {"location": "1.2", "health": "Fault"}]},
               "GET /api/show/pools": {"pools": [{"name": "A", "total-size-numeric": "20000000000", "total-avail-numeric": "2000000000", "health": "OK"}]},
               "GET /api/show/volumes": {"volumes": [{"volume-name": "v1", "size-numeric": "100000", "health": "OK"}]}}
        unity = {"GET /api/types/system/instances": {"entries": [{"content": {"name": "U1", "model": "Unity 480", "health": {"value": 20, "descriptions": ["major"]}}}]},
                 "GET /api/types/storageProcessor/instances": {"entries": [{"content": {"name": "SPA", "health": {"value": 5}}}]},
                 "GET /api/types/pool/instances": {"entries": [{"content": {"name": "Pool0", "sizeTotal": 100, "sizeUsed": 95, "sizeFree": 5, "health": {"value": 5}}}]}}
        primera = {"POST /api/v1/credentials": {"key": "K"}, "GET /api/v1/system": {"name": "3P", "model": "Primera 650", "onlineNodes": [0], "clusterNodes": [0, 1]},
                   "GET /api/v1/cpgs": {"members": [{"name": "SSD_r6", "UsrUsage": {"totalMiB": 1000, "usedMiB": 950}, "state": 1}]}, "GET /api/v1/volumes": {"members": []}}
        pure = {"GET /api/api_version": {"version": ["1.19", "2.4", "2.21"]}, "POST /api/2.21/login": ({}, {"x-auth-token": "T"}),
                "GET /api/2.21/arrays": {"items": [{"name": "FA1", "os": "Purity//FA", "version": "6.5.1"}]},
                "GET /api/2.21/controllers": {"items": [{"name": "CT0", "status": "ready", "model": "FA-X50R3"}, {"name": "CT1", "status": "not ready"}]},
                "GET /api/2.21/arrays/space": {"items": [{"capacity": 100, "space": {"total_physical": 40}}]}}
        svc = {"POST /rest/auth": {"token": "T"}, "POST /rest/lssystem": {"name": "FS", "product_name": "FlashSystem 5200", "code_level": "8.6"},
               "POST /rest/lsnodecanister": [{"name": "node1", "status": "online"}, {"name": "node2", "status": "offline"}],
               "POST /rest/lsdrive": [{"enclosure_id": "1", "slot_id": "3", "status": "offline", "capacity": "1000"}],
               "POST /rest/lsmdiskgrp": [{"name": "Pool0", "capacity": "1000", "free_capacity": "100", "status": "online"}], "POST /rest/lsvdisk": []}
        ocean = {"POST /deviceManager/rest/xxxxx/sessions": ({"error": {"code": 0}, "data": {"deviceid": "D1", "iBaseToken": "B"}}, {"set-cookie": "session=S; path=/"}),
                 "GET /deviceManager/rest/D1/system/": {"data": {"NAME": "OS", "PRODUCTMODE": "Dorado 5000", "HEALTHSTATUS": "1"}},
                 "GET /deviceManager/rest/D1/controller": {"data": [{"LOCATION": "CTE0.A", "HEALTHSTATUS": "1"}, {"LOCATION": "CTE0.B", "HEALTHSTATUS": "2"}]},
                 "GET /deviceManager/rest/D1/storagepool": {"data": [{"NAME": "P0", "USERTOTALCAPACITY": "2000", "USERFREECAPACITY": "200", "HEALTHSTATUS": "1"}]}}
        ontap = {"GET /api/cluster": {"name": "C1", "version": {"full": "9.14.1"}},
                 "GET /api/cluster/nodes": {"records": [{"name": "n1", "state": "up", "model": "AFF A250"}, {"name": "n2", "state": "down"}]},
                 "GET /api/storage/aggregates": {"records": [{"name": "aggr1", "state": "online", "space": {"block_storage": {"size": 100, "available": 10}}}]}}
        powerstore = {"GET /api/rest/appliance": [{"id": "A1", "name": "PS1", "model": "PowerStore 1200T", "service_tag": "ST"}],
                      "GET /api/rest/hardware": [{"name": "Node A", "type": "Node", "lifecycle_state": "Healthy"}, {"name": "Drive 3", "type": "Drive", "lifecycle_state": "Failed"}],
                      "POST /api/rest/metrics/generate": ([{"physical_total": 100, "physical_used": 91}], {})}
        for t, routes, check in (("msa", msa, lambda d: d["controllers"][1]["status"] == "fault" and d["pools"][0]["used_percent"] == 90.0),
                                 ("unity", unity, lambda d: d["status"] == "major" and d["pools"][0]["used_percent"] == 95.0),
                                 ("primera", primera, lambda d: d["controllers"][1]["status"] == "offline" and d["pools"][0]["used_percent"] == 95.0),
                                 ("pure", pure, lambda d: d["status"] == "needs attention" and d["pools"][0]["used_percent"] == 40.0),
                                 ("svc", svc, lambda d: d["drives"][0]["status"] == "failed" and d["pools"][0]["used_percent"] == 90.0),
                                 ("oceanstor", ocean, lambda d: d["controllers"][1]["status"] == "needs attention" and d["pools"][0]["used_percent"] == 90.0),
                                 ("ontap", ontap, lambda d: d["controllers"][1]["status"] == "down" and d["pools"][0]["used_percent"] == 90.0),
                                 ("powerstore", powerstore, lambda d: d["drives"][0]["status"] == "failed" and d["pools"][0]["used_percent"] == 91.0)):
            try:
                d = san.collect(arr(t, user="" if t == "pure" else "monitor"), FakeHttp(routes), reach=lambda h, p: (True, ""))
            except san.SanError as e:
                self.fail("%s: %s" % (t, e))
            self.assertTrue(check(d), (t, d))
        self.assertEqual(sorted(san.DRIVERS), sorted(t for t in san.TYPES if t != "reach"))
        self.assertEqual(set(san.HOWTO), set(san.TYPES))                                          # every make has its how-to

    def test_any_other_make_reachability(self):
        d = san.collect(arr("reach", port=443), reach=lambda h, p: (h.endswith("55"), "refused"))
        self.assertEqual((d["status"], len(d["unreachable"])), ("needs attention", 1))


class CheckAndReportTests(unittest.TestCase):
    def test_health_check_findings_and_report_sections(self):
        from lshme.modules import san as mod
        from test_periodic import make_run, seed
        home = TempHome()
        try:
            app = App(ROOT, home=home.dir)
            san.save(app, "hhk-de", "lenovo-de", "172.16.101.55, 172.16.101.56", "monitor", "secret-pw")
            self.assertEqual(app.cfg.secret("san:hhk-de"), "secret-pw")
            self.assertNotIn("secret-pw", open(home.dir + "/config/main.conf", encoding="utf-8").read())   # the password lives only in credentials.conf
            orig = san.collect
            san.collect = lambda a, http=None, reach=san.reachable: orig(a, FakeHttp(de_routes()), lambda h, p: (True, ""))
            try:
                res = ModuleResult("san", "SAN Storage", 10)

                class Ctx(object):
                    cfg = app.cfg

                    def t(self, s, k, d=0.0):
                        return app.cfg.threshold(s, k, d)
                mod.run(Ctx(), res)
            finally:
                san.collect = orig
            codes = sorted(set(f.code for f in res.findings))
            for c in ("SAN_NEEDS_ATTENTION", "SAN_DRIVE_FAILED", "SAN_DRIVE_PFA", "SAN_POOL_FULL", "SAN_POOL_DEGRADED", "SAN_HARDWARE", "SAN_LATENCY", "SAN_FAILURE"):
                self.assertIn(c, codes)
            self.assertEqual(res.metrics["san.pool_used_percent:hhk-de|DDP_VM"], 85.0)
            now = time.time()
            run = make_run(now)
            seed(app.paths, run)
            res.status = "WARNING"
            run.modules.append(res)
            for ptype, variant, want in (("daily", "summary", ["Storage systems", "Pools and volume groups", "Storage problems now", "I/O now"]),
                                         ("weekly", "summary", ["I/O over the period (average / peak)"]),
                                         ("monthly", "comprehensive", ["Drives of hhk-de", "Volumes of hhk-de", "Controllers and hardware of hhk-de",
                                                                       "Recent critical events on hhk-de"])):
                d = periodic.build(run, app.cfg, app.paths, ptype, variant, now)
                names = [b.get("title") for b in dict(d.sections)["SAN storage"] if b.get("type") == "table"]
                for w in want:
                    self.assertIn(w, names, (ptype, variant, names))
                self.assertTrue(any("SAN storage needs attention" in t for _, t in dict(d.sections)["In short"][0]["items"]))
                files = reports.write_periodic([d], ["html", "pdf"], home.dir, "san_" + ptype, "")[0]
                self.assertEqual(len(files), 2)
            run.modules = [m for m in run.modules if m.name != "san"]
            d = periodic.build(run, app.cfg, app.paths, "weekly", "summary", now)
            self.assertNotIn("SAN storage", [t for t, _ in d.sections])                             # nothing added: no section
        finally:
            home.cleanup()

    def test_no_storage_added_means_skipped(self):
        from lshme.modules import san as mod
        home = TempHome()
        try:
            res = ModuleResult("san", "SAN Storage", 10)

            class Ctx(object):
                cfg = home.cfg
            mod.run(Ctx(), res)
            self.assertEqual(res.status, "SKIPPED")
        finally:
            home.cleanup()


class WebTests(unittest.TestCase):
    def test_add_test_list_remove_from_the_dashboard(self):
        from test_web import WebCase
        case = type("T", (WebCase,), {"runTest": lambda self: None})()
        case.setUp()
        try:
            self.assertEqual(case.call("GET", "/api/san", user="viewer").status, 403)
            r = case.call("GET", "/api/san", user="admin")
            self.assertEqual(r.status, 200)
            self.assertIn("santricity", [t["type"] for t in r.json["types"]])
            body = {"action": "save", "name": "hhk-de", "type": "santricity", "controllers": ["172.16.101.55", "172.16.101.56"], "port": 8443, "username": "monitor", "password": "pw1"}
            self.assertEqual(case.call("POST", "/api/san", body, user="admin").status, 200)
            a = case.call("GET", "/api/san", user="admin").json["arrays"][0]
            self.assertEqual((a["name"], a["has_password"], a["controllers"]), ("hhk-de", True, ["172.16.101.55", "172.16.101.56"]))
            self.assertNotIn("password", a)                                                     # never sent to the browser
            orig = san.test
            san.test = lambda a, http=None, reach=None: {"ok": a["password"] == "pw1", "message": "checked " + ",".join(a["controllers"])}
            try:
                t = case.call("POST", "/api/san", {"action": "test", "saved": True, "name": "hhk-de"}, user="admin").json
                self.assertTrue(t["ok"])
                t = case.call("POST", "/api/san", dict(body, action="test", password=""), user="admin").json   # unsaved form, empty password: the saved one
                self.assertTrue(t["ok"])
            finally:
                san.test = orig
            v = case.call("GET", "/api/sanview", user="viewer", query={"name": ["hhk-de"], "days": ["7"]})                # its own page: everyone may look, nobody sees the password
            self.assertEqual(v.status, 200)
            self.assertEqual(v.json["array"]["name"], "hhk-de")
            self.assertNotIn("pw1", json.dumps(v.json))
            self.assertIn("pools", v.json["history"])
            self.assertEqual(case.call("GET", "/api/sanview", user="viewer", query={"name": ["nope"]}).status, 404)
            self.assertEqual(case.call("POST", "/api/san", {"action": "remove", "name": "hhk-de"}, user="admin").status, 200)
            self.assertEqual(case.call("GET", "/api/san", user="admin").json["arrays"], [])
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()


class ExclusionWebTests(unittest.TestCase):
    def test_exclude_and_unexclude_with_one_click(self):
        from test_web import WebCase
        case = type("T", (WebCase,), {"runTest": lambda self: None})()
        case.setUp()
        try:
            latest = {"host": "hhk-kvm-h1", "collected_at": time.time(), "excluded": [], "modules": [
                {"name": "security", "findings": [{"severity": "WARNING", "code": "SSH_ROOT_LOGIN", "component": "sshd", "message": "SSH allows direct root login",
                                                   "recommendation": "Set PermitRootLogin no"},
                                                  {"severity": "INFO", "code": "X", "component": "y", "message": "info only"}]}]}
            import os
            os.makedirs(case.app.paths.data_dir, exist_ok=True)
            with open(os.path.join(case.app.paths.data_dir, "latest.json"), "w", encoding="utf-8") as f:
                json.dump(latest, f)
            d = case.call("GET", "/api/exclusions", user="viewer").json
            self.assertEqual([x["code"] for x in d["findings"]], ["SSH_ROOT_LOGIN"])                 # warnings and critical only
            add = {"action": "add", "code": "SSH_ROOT_LOGIN", "component": "sshd", "module": "security", "reason": "root login needed by the backup agent"}
            self.assertEqual(case.call("POST", "/api/exclusions", add, user="viewer").status, 403)
            self.assertEqual(case.call("POST", "/api/exclusions", dict(add, reason=""), user="admin").status, 400)   # a reason is required
            r = case.call("POST", "/api/exclusions", dict(add, until="2026-12-31"), user="admin").json
            self.assertEqual(r["name"], "ssh_root_login-sshd")
            rule = case.call("GET", "/api/exclusions", user="admin").json["rules"][0]
            self.assertEqual((rule["code"], rule["owner"], rule["until"], rule["status"]), ("SSH_ROOT_LOGIN", "admin", "2026-12-31", "active"))
            from lshme.engine import exclusions
            case.app.reload()
            self.assertTrue(any(x.matches("SSH_ROOT_LOGIN", "sshd", "security", "hhk-kvm-h1") for x in exclusions.load(case.app.cfg)))
            self.assertEqual(case.call("POST", "/api/exclusions", {"action": "remove", "name": r["name"]}, user="admin").status, 200)
            self.assertEqual(case.call("GET", "/api/exclusions", user="admin").json["rules"], [])
        finally:
            case.tearDown()
