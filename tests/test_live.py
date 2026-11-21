# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout

from fakes import ROOT, FakeRunner, TempHome
from lshme.ops import livefleet, pulse, sla

SERVERS = "[defaults]\nuser = root\n\n[server:kvm2]\nhost = 10.0.0.2\ngroup = KVM\nenvironment = Production\n\n[server:db1]\nhost = 10.0.0.3\ngroup = Oracle\nport = 2222\n"


def write_watch(app, now, cpu=12.0, crit=0, age=5):
    dd = app.paths.data_dir
    os.makedirs(dd, exist_ok=True)
    problems = {}
    if crit:
        problems["port:1521"] = {"severity": "CRITICAL", "since": now - 600, "count": 10}
    with open(os.path.join(dd, "watch_state.json"), "w", encoding="utf-8") as f:
        json.dump({"interval": 60, "last_tick": now - age, "problems": problems}, f)
    with open(os.path.join(dd, "watch_metrics.json"), "w", encoding="utf-8") as f:
        json.dump([{"t": now - 120 + i * 60, "cpu": cpu + i, "mem": 55.0, "swap": 0, "load": 0.2, "cores": [1, 2], "net": {"eth0": [1000, 500]}, "disk": {"/": 41.0, "/u01": 83.5}} for i in range(3)], f)
    if crit:
        with open(os.path.join(dd, "watch_events.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "open", "ts": now - 600, "key": "port:1521", "component": "oracle:1521", "severity": "CRITICAL", "message": "Expected port 1521 is not listening"}) + "\n")


class PulseTest(unittest.TestCase):
    def setUp(self):
        from lshme.app import App
        self.home = TempHome({"servers": SERVERS})
        self.app = App(ROOT, home=self.home.dir)

    def tearDown(self):
        self.home.cleanup()

    def test_pulse_is_small_and_complete(self):
        now = time.time()
        write_watch(self.app, now, crit=1)
        p = pulse.collect(self.app, now)
        self.assertEqual((p["status"], p["critical"], p["warning"]), ("CRITICAL", 1, 0))
        self.assertEqual(p["disks"][0], {"mount": "/u01", "pct": 83.5})
        self.assertEqual((p["net_rx"], p["net_tx"]), (1000, 500))
        self.assertEqual(p["problems"][0]["message"], "Expected port 1521 is not listening")
        self.assertLessEqual(len(json.dumps(p)), 3000)
        write_watch(self.app, now)
        self.assertEqual(pulse.collect(self.app, now)["status"], "HEALTHY")
        write_watch(self.app, now, age=4000)                                   # the watcher stopped an hour ago
        self.assertEqual(pulse.collect(self.app, now)["status"], "STALE")

    def test_cli(self):
        from lshme.ui import cli
        write_watch(self.app, time.time())
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["--home", self.home.dir, "pulse"]), 0)
        self.assertEqual(json.loads(buf.getvalue())["status"], "HEALTHY")


class LiveFleetTest(unittest.TestCase):
    def setUp(self):
        from lshme.app import App
        self.home = TempHome({"servers": SERVERS})
        self.app = App(ROOT, home=self.home.dir)
        write_watch(self.app, time.time())

    def tearDown(self):
        self.home.cleanup()

    def test_every_server_is_asked_and_an_unreachable_one_is_shown_as_such(self):
        good = json.dumps({"host": "kvm2", "ts": time.time(), "status": "HEALTHY", "cpu": 20.0, "mem": 40.0, "critical": 0, "warning": 1, "disks": [{"mount": "/", "pct": 30}]})

        def ssh(args, input_text=None):
            if "10.0.0.3" in " ".join(args):
                return (255, "", "ssh: connect to host 10.0.0.3 port 2222: Connection timed out")
            return (0, good + "\n")
        self.app.runner = FakeRunner({"ssh": ssh})
        lf = livefleet.LiveFleet(self.app)
        snap = lf.poll_once()
        by = dict((s["name"], s) for s in snap)
        self.assertEqual([s["name"] for s in snap][1:], ["kvm2", "db1"])               # this server first, then servers.conf order
        self.assertEqual((by["kvm2"]["ok"], by["kvm2"]["status"], by["kvm2"]["group"]), (True, "HEALTHY", "KVM"))
        self.assertEqual((by["db1"]["ok"], by["db1"]["status"]), (False, "UNREACHABLE"))
        self.assertIn("timed out", by["db1"]["error"])
        calls = " ".join(self.app.runner.calls)
        self.assertIn("-p 2222", calls)
        self.assertIn("vitals pulse", calls)
        self.assertIn("BatchMode=yes", calls)
        self.app.runner = FakeRunner({"ssh": ssh})                                    # a later answer keeps the history
        lf.poll_once()
        self.assertEqual(len(lf.history["kvm2"]), 2)

    def test_garbage_from_a_server_is_an_error_not_a_crash(self):
        self.app.runner = FakeRunner({"ssh": (0, "bash: vitals: command not found")})
        snap = livefleet.LiveFleet(self.app).poll_once()
        self.assertEqual(snap[1]["status"], "UNREACHABLE")
        self.assertIn("vitals pulse", snap[1]["error"])

    def test_the_ssh_command_never_contains_web_input(self):
        sv = livefleet.fleet.servers(self.app.cfg)[0]
        cmd = livefleet.ssh_pulse_command(sv, "/var/lib/lshme/ssh")
        self.assertEqual(cmd[-1], "vitals pulse")
        self.assertIn("ControlPath=/var/lib/lshme/ssh/%C", cmd)
        self.assertEqual(cmd[0], "ssh")


class IncidentLogTest(unittest.TestCase):
    def events(self, now):
        def pair(key, comp, msg, ago_h, mins, sev):
            t0 = now - ago_h * 3600
            e = [{"event": "open", "ts": t0, "key": key, "component": comp, "severity": sev, "message": msg}]
            if mins:
                e.append({"event": "close", "ts": t0 + mins * 60, "key": key})
            return e
        return (pair("disk:/VM-OS-DATA", "/VM-OS-DATA", "Filesystem /VM-OS-DATA is 88% full", 3, 43, "CRITICAL") + pair("port:1521", "oracle:1521", "port down", 0.3, 0, "CRITICAL") +
                pair("cpu", "cpu", "CPU 96%", 40, 9, "WARNING"))

    def test_standard_incident_form(self):
        now = time.time()
        log, sm = sla.incident_log(self.events(now), now, 30, 200, {"port:1521": {"by": "rohit", "at": now}})
        by = dict((i["component"], i) for i in log)
        self.assertEqual(log[0]["component"], "oracle:1521")                           # newest first
        self.assertEqual((by["/VM-OS-DATA"]["status"], by["/VM-OS-DATA"]["priority"]), ("Resolved", "P1"))
        self.assertEqual(by["/VM-OS-DATA"]["duration"], 43 * 60)
        self.assertEqual((by["oracle:1521"]["status"], by["oracle:1521"]["resolved"]), ("Acknowledged", None))
        self.assertEqual(by["cpu"]["priority"], "P2")
        self.assertTrue(by["cpu"]["id"].startswith("INC-"))
        self.assertEqual(len(set(i["id"] for i in log)), 3)
        self.assertEqual((sm["open"], sm["resolved_24h"], sm["resolved_total"]), (1, 1, 2))
        self.assertAlmostEqual(sm["mttr"], (43 * 60 + 9 * 60) / 2.0)
        log24, _ = sla.incident_log(self.events(now), now, 1)
        self.assertEqual(len(log24), 2)                                                # the CPU incident is older than a day


if __name__ == "__main__":
    unittest.main()


class FleetPageTests(unittest.TestCase):
    def test_an_old_member_says_upgrade_not_unreachable(self):
        from lshme.ops import livefleet
        self.assertTrue(livefleet.outdated("healthcheck: error: argument cmd: invalid choice: 'pulse' (choose from 'run', 'check')"))
        self.assertFalse(livefleet.outdated("ssh: connect to host 172.16.101.54 port 22: Connection timed out"))

    def test_storage_summary_for_the_dashboard(self):
        import test_san
        from lshme.ops import livefleet, san
        a = test_san.arr("santricity")
        s = livefleet.summarize(a, collect=lambda x: san.collect(x, test_san.FakeHttp(test_san.de_routes()), lambda h, p: (True, "")))
        self.assertEqual((s["status"], s["failed_drives"], s["pools"][0]["pct"], s["hardware_bad"]), ("CRITICAL", 1, 85.0, ["Fan 2"]))
        bad = livefleet.summarize(a, collect=lambda x: (_ for _ in ()).throw(san.SanError("cannot reach 172.16.101.55: timed out")))
        self.assertEqual(bad["status"], "UNREACHABLE")

    def test_server_page_api(self):
        from test_web import WebCase
        case = type("T", (WebCase,), {"runTest": lambda self: None})()
        case.setUp()
        try:
            case.app.cfg.set("main", "fleet", "live", "no")
            r = case.call("GET", "/api/server", user="viewer", query={"name": ["_local"]})
            self.assertEqual(r.status, 200)
            self.assertTrue(r.json["local"])
            self.assertEqual(case.call("GET", "/api/server", user="viewer", query={"name": ["no-such-server"]}).status, 404)
        finally:
            case.tearDown()
