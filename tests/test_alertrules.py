# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Alert rules: quiet hours, minimum duration, routing, acknowledgement, digest, Slack and Teams."""
import time
import unittest

import fakes  # noqa: F401  (puts the application on the import path)
from lshme.ops import alertrules as A
from lshme.ops.watch import code_of


def at(hour, minute=0):
    return time.mktime((2026, 11, 20, hour, minute, 0, 0, 0, -1))


def alert(key="cpu", sev="WARNING", kind="NEW", since=0):
    return {"key": key, "severity": sev, "kind": kind, "since": since, "component": key, "message": "m"}


class RuleTests(unittest.TestCase):
    def test_quiet_hours_across_midnight(self):
        self.assertEqual(A.parse_quiet("22:00-06:00"), (1320, 360))
        self.assertIsNone(A.parse_quiet("nonsense"))
        self.assertIsNone(A.parse_quiet("10:00-10:00"))
        self.assertTrue(A.in_quiet("22:00-06:00", at(23, 30)))
        self.assertTrue(A.in_quiet("22:00-06:00", at(3)))
        self.assertFalse(A.in_quiet("22:00-06:00", at(12)))
        self.assertFalse(A.in_quiet("", at(3)))
        self.assertTrue(A.in_quiet("09:00-17:00", at(12)))

    def test_quiet_hours_hold_back_warnings_but_not_critical(self):
        send, held = A.apply_rules([alert("a", "WARNING"), alert("b", "CRITICAL")], at(2), "22:00-06:00", "CRITICAL", 0, 0)
        self.assertEqual([a["key"] for a in send], ["b"])
        self.assertEqual([a["key"] for a in held], ["a"])
        send, held = A.apply_rules([alert("a", "WARNING")], at(12), "22:00-06:00", "CRITICAL", 0, 0)
        self.assertEqual((len(send), len(held)), (1, 0))

    def test_problem_must_last_the_minimum_time(self):
        now = at(12)
        young, old = alert("y", since=now - 120), alert("o", since=now - 1200)
        send, held = A.apply_rules([young, old], now, "", "CRITICAL", 10, 0)
        self.assertEqual(([a["key"] for a in send], [a["key"] for a in held]), (["o"], ["y"]))
        crit = alert("c", "CRITICAL", since=now - 60)
        self.assertEqual(len(A.apply_rules([crit], now, "", "CRITICAL", 10, 0)[0]), 1)       # critical goes at once
        self.assertEqual(len(A.apply_rules([crit], now, "", "CRITICAL", 10, 5)[1]), 1)       # unless a delay is set for critical too
        reminder = alert("r", kind="STILL", since=now - 60)
        self.assertEqual(len(A.apply_rules([reminder], now, "", "CRITICAL", 10, 0)[0]), 1)   # only new problems wait

    def test_routing_by_area(self):
        routes = {"database": "dba@example.com, dba2@example.com", "storage": "storage@example.com"}
        alerts = [alert("mod:database:ORA_X:orcl"), alert("mod:storage:FS_FULL:/u01"), alert("cpu"), alert("mem")]
        groups = A.route(routes, ["ops@example.com"], alerts, [], code_of)
        by = dict((tuple(to), [a["key"] for a in al]) for to, al, _ in groups)
        self.assertEqual(by[("dba@example.com", "dba2@example.com")], ["mod:database:ORA_X:orcl"])
        self.assertEqual(by[("storage@example.com",)], ["mod:storage:FS_FULL:/u01"])
        self.assertEqual(sorted(by[("ops@example.com",)]), ["cpu", "mem"])
        self.assertEqual(A.route({}, [], [alert("cpu")], [], code_of)[0][0], None)           # nobody configured: the default recipients

    def test_acknowledged_reminders_stop_but_escalations_do_not(self):
        acks = {"cpu": {"by": "admin", "at": 1}}
        out = A.drop_acknowledged([alert("cpu", kind="STILL"), alert("cpu", "CRITICAL", kind="ESCALATED"), alert("mem", kind="STILL")], acks)
        self.assertEqual([(a["key"], a["kind"]) for a in out], [("cpu", "ESCALATED"), ("mem", "STILL")])

    def test_muted_problems_send_nothing_until_the_time_is_up(self):
        from fakes import TempHome
        from lshme.ops import watch as W
        home = TempHome()
        try:
            now = at(12)
            W.mute(home.paths, "disk:/u01", "admin", 6, now)
            W.mute(home.paths, "svc:chronyd", "admin", 0, now)                          # until it ends
            acks = W.load_acks(home.paths)
            self.assertTrue(W.is_muted(acks["disk:/u01"], now + 5 * 3600))
            self.assertFalse(W.is_muted(acks["disk:/u01"], now + 7 * 3600))
            self.assertTrue(W.is_muted(acks["svc:chronyd"], now + 30 * 86400))
            problems = {"disk:/u01": {}, "svc:chronyd": {}, "cpu": {}}
            alerts = [alert("disk:/u01", kind="STILL"), alert("disk:/u01", "CRITICAL", kind="ESCALATED"), alert("cpu", kind="NEW")]
            out, rec, changed = W.apply_mutes(acks, alerts, [alert("svc:chronyd", kind="RECOVERED")], problems, now + 3600)
            self.assertEqual([a["key"] for a in out], ["cpu"])                           # reminders and escalations of muted problems: none
            self.assertEqual(rec, [])                                                     # nor the recovery notice
            self.assertFalse(changed)
            out, _, changed = W.apply_mutes(acks, alerts, [], problems, now + 7 * 3600)  # six hours later the mute has ended
            self.assertEqual(len(out), 3)
            self.assertTrue(changed and "disk:/u01" not in acks)
            W.unmute(home.paths, "svc:chronyd")
            self.assertNotIn("svc:chronyd", W.load_acks(home.paths))
            with self.assertRaises(ValueError):
                W.mute(home.paths, "x", "admin", -1)
            self.assertEqual(W.DEFAULTS["repeat_minutes"], "360")                         # reminders every 6 hours by default
        finally:
            home.cleanup()

    def test_slack_and_teams_messages(self):
        self.assertEqual(A.detect_format("https://hooks.slack.com/services/T/B/x"), "slack")
        self.assertEqual(A.detect_format("https://contoso.webhook.office.com/webhookb2/x"), "teams")
        self.assertEqual(A.detect_format("https://prod-1.westus.logic.azure.com/workflows/x"), "teams")
        self.assertEqual(A.detect_format("https://example.com/hook"), "generic")
        self.assertEqual(A.detect_format("https://example.com/hook", "slack"), "slack")
        s = A.payload("slack", "[ALERT] db01: 1 CRITICAL", "body", "CRITICAL")
        self.assertIn("red_circle", s["text"])
        t = A.payload("teams", "[ALERT] db01", "body", "WARNING")
        card = t["attachments"][0]["content"]
        self.assertEqual(card["type"], "AdaptiveCard")
        self.assertEqual(card["body"][0]["color"], "Warning")
        self.assertEqual(A.payload("generic", "s", "b", "WARNING")["status"], "WARNING")

    def test_daily_digest(self):
        now = at(8, 5)
        events = [{"ts": now - 3600, "event": "open", "severity": "CRITICAL", "component": "cpu", "message": "CPU 99%"},
                  {"ts": now - 1800, "event": "close", "severity": "CRITICAL", "component": "cpu", "message": "CPU 99%"},
                  {"ts": now - 90000, "event": "open", "severity": "WARNING", "component": "old", "message": "older than a day"}]
        subject, body, status = A.digest("db01", events, now)
        self.assertIn("1 alert(s)", subject)
        self.assertIn("STARTED", body)
        self.assertNotIn("older than a day", body)
        self.assertEqual(status, "CRITICAL")
        self.assertIsNone(A.digest("db01", [], now))
        self.assertTrue(A.digest_due("08:00", now, "2026-11-19"))
        self.assertFalse(A.digest_due("08:00", now, "2026-11-20"))      # already sent today
        self.assertFalse(A.digest_due("08:00", at(7, 30), "2026-11-19"))  # not yet 08:00
        self.assertFalse(A.digest_due("", now, ""))


if __name__ == "__main__":
    unittest.main()
