# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Real-time watcher: alert state machine, probes and delivery rules."""
import os
import time
import unittest

from fakes import ROOT, TempHome
from lshme.ops import watch


class EvaluateTest(unittest.TestCase):
    def run_ticks(self, samples, confirm=2, repeat=0, recovery=True, min_sev="WARNING"):
        state, events, now = {}, [], 1000.0
        for current in samples:
            a, r = watch.evaluate(state, current, now, confirm, repeat, min_sev, recovery)
            events.append(([x["kind"] + ":" + x["severity"] for x in a], [x["key"] for x in r]))
            now += 60
        return events

    def test_confirm_then_single_alert_then_recovery(self):
        bad = {"disk:/u01": ("CRITICAL", "/u01", "full", "")}
        ev = self.run_ticks([bad, bad, bad, {}])
        self.assertEqual(ev[0], ([], []))                   # first breach: not yet confirmed
        self.assertEqual(ev[1], (["NEW:CRITICAL"], []))     # confirmed
        self.assertEqual(ev[2], ([], []))                   # no duplicate
        self.assertEqual(ev[3], ([], ["disk:/u01"]))        # recovery

    def test_flapping_is_suppressed(self):
        bad = {"cpu": ("WARNING", "cpu", "busy", "")}
        ev = self.run_ticks([bad, {}, bad, {}, bad])
        self.assertTrue(all(e == ([], []) for e in ev))

    def test_escalation_and_reminder(self):
        w = {"mem": ("WARNING", "memory", "86%", "")}
        c = {"mem": ("CRITICAL", "memory", "97%", "")}
        ev = self.run_ticks([w, w, c, c, c], repeat=120)
        self.assertEqual(ev[1][0], ["NEW:WARNING"])
        self.assertEqual(ev[2][0], ["ESCALATED:CRITICAL"])
        self.assertEqual(ev[3][0], [])
        self.assertEqual(ev[4][0], ["STILL:CRITICAL"])      # reminder after 120 s

    def test_improvement_is_reported_once_and_logged(self):
        w = {"disk:/u01": ("WARNING", "/u01", "86%", "")}
        c = {"disk:/u01": ("CRITICAL", "/u01", "95%", "")}
        state, now, out = {}, 1000.0, []
        for cur in (c, c, w, w, {}):
            a, r = watch.evaluate(state, cur, now, 2, 0, "WARNING", True)
            out.append(([x["kind"] for x in a], [x["kind"] for x in r]))
            now += 60
        self.assertEqual(out, [([], []), (["NEW"], []), ([], ["IMPROVED"]), ([], []), ([], ["RECOVERED"])])
        self.assertEqual([e["event"] for e in state["pending_events"]], ["open", "improve", "close"])
        subject, body, _ = watch.format_alert("db01", [], [dict(c=1, kind="IMPROVED", previous="CRITICAL", severity="WARNING",
                                                                component="/u01", message="86%")])
        self.assertIn("1 improved", subject)
        self.assertIn("[IMPROVED] /u01: CRITICAL -> WARNING", body)

    def test_min_severity_and_no_recovery(self):
        w = {"swap": ("WARNING", "swap", "60%", "")}
        ev = self.run_ticks([w, w, {}], min_sev="CRITICAL", recovery=True)
        self.assertEqual(ev, [([], []), ([], []), ([], [])])  # never alerted -> no recovery either


class ProbeTest(unittest.TestCase):
    def test_fast_probes_real_system(self):
        home = TempHome({"thresholds": "[disk]\nwarning = 0\ncritical = 101\n[inode]\nwarning = 101\ncritical = 102\n"})
        state = {}
        p = watch.NetfsProber(2)
        watch.fast_probes(home.cfg, state, p)          # primes CPU sample
        out = watch.fast_probes(home.cfg, state, p)
        self.assertIn("cpu_prev", state)
        self.assertTrue(any(k.startswith("disk:") and v[0] == "WARNING" for k, v in out.items()))
        home.cleanup()

    def test_cpu_percent(self):
        self.assertEqual(watch.cpu_percent([100, 0, 100, 800, 0], [150, 0, 150, 900, 0]), 50.0)
        self.assertIsNone(watch.cpu_percent([], [1]))

    def test_tick_persists_state_and_formats_alert(self):
        home = TempHome({"main": "[watch]\nconfirm = 1\nrotation_modules =\nsyslog = no\nalert_email = no\n",
                         "services": "[critical]\nservices =\n[processes]\nghost = no-such-process-xyz\n"})
        from lshme.app import App
        app = App(ROOT, home=home.dir)
        w = watch.Watcher(app)
        alerts, recovered, delivery = w.tick()
        self.assertTrue(any(a["key"] == "proc:ghost" for a in alerts))
        self.assertTrue(os.path.exists(w.state_path))
        self.assertEqual(watch.Watcher(app).state["problems"]["proc:ghost"]["alerted"], "CRITICAL")
        subject, body, status = watch.format_alert("db01", alerts, [])
        self.assertTrue(subject.startswith("[LSHME ALERT] db01:"))
        self.assertIn("LRS Services Pvt Ltd", body)
        self.assertEqual(status, "CRITICAL")
        home.cleanup()

    def test_maintenance_suppresses_delivery(self):
        home = TempHome()
        from lshme.app import App
        from lshme.engine import maintenance
        app = App(ROOT, home=home.dir)
        maintenance.start(app.paths, 10, "patching")
        self.assertEqual(watch.deliver(app, "s", "b", "CRITICAL"), {"suppressed": "maintenance"})
        home.cleanup()


if __name__ == "__main__":
    unittest.main()
