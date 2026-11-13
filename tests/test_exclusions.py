# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Exclusion rules: matching, expiry, validation, effect on scoring, reports and the watcher."""
import time
import unittest

from fakes import TempHome
from lshme.core.model import ModuleResult, RunResult
from lshme.engine import exclusions, scoring
from lshme.ops import watch

RULES = """
[exclude:cockpit-self-signed]
code = COCKPIT_SELF_SIGNED
module = cockpit
reason = Internal console
owner = infra
[exclude:archive]
code = DISK_USAGE
component = /archive*
reason = Migration CHG-1
until = 2099-12-31
[exclude:expired]
code = SWAP_HIGH
reason = old
until = 2020-01-01
[exclude:no-reason]
code = CPU_HIGH
[exclude:dev-only]
code = ORACLE_INVALID_*
hosts = dev-*
reason = dev databases
"""


def make_run(host="db01"):
    r = RunResult(host)
    c = ModuleResult("cockpit", "Cockpit", 4)
    c.add("INFO", "COCKPIT_SELF_SIGNED", "/etc/cockpit/ws-certs.d/0.cert", "self-signed")
    c.add("WARNING", "COCKPIT_UNENCRYPTED", "cockpit.conf", "unencrypted")
    s = ModuleResult("storage", "Storage", 20)
    s.add("CRITICAL", "DISK_USAGE", "/archive", "archive 97% full")
    s.add("CRITICAL", "DISK_USAGE", "/u01", "u01 96% full")
    m = ModuleResult("cpu_memory", "CPU", 12)
    m.add("WARNING", "SWAP_HIGH", "swap", "swap 60%")
    m.add("CRITICAL", "CPU_HIGH", "cpu", "cpu 99%")
    d = ModuleResult("database", "DB", 15)
    d.add("WARNING", "ORACLE_INVALID_OBJECTS", "oracle:ORCL", "60 invalid")
    r.modules = [c, s, m, d]
    return r


class ExclusionTest(unittest.TestCase):
    def setUp(self):
        self.home = TempHome({"exclusions": RULES})
        self.rules = exclusions.load(self.home.cfg)

    def tearDown(self):
        self.home.cleanup()

    def test_component_list(self):
        r = exclusions.Rule("mounts", {"code": "FS_READ_ONLY", "component": "/opt/a, /opt/b*", "reason": "platform"})
        self.assertTrue(r.matches("FS_READ_ONLY", "/opt/a", "storage", "h"))
        self.assertTrue(r.matches("FS_READ_ONLY", "/opt/b2", "storage", "h"))
        self.assertFalse(r.matches("FS_READ_ONLY", "/opt/c", "storage", "h"))

    def test_apply_moves_findings_and_reports_rule_problems(self):
        run = make_run()
        excluded = exclusions.apply(run, self.rules)
        codes = sorted((x["code"], x["component"]) for x in excluded)
        self.assertEqual(codes, [("COCKPIT_SELF_SIGNED", "/etc/cockpit/ws-certs.d/0.cert"), ("DISK_USAGE", "/archive")])
        remaining = [(f.code, f.component) for f in run.findings()]
        self.assertIn(("DISK_USAGE", "/u01"), remaining)          # other mount still reported
        self.assertIn(("SWAP_HIGH", "swap"), remaining)           # expired rule no longer applies
        self.assertIn(("CPU_HIGH", "cpu"), remaining)             # rule without a reason is ignored
        self.assertIn(("ORACLE_INVALID_OBJECTS", "oracle:ORCL"), remaining)  # host db01 is not dev-*
        notes = dict((f.component, f.code) for f in run.module("exclusions").findings)
        self.assertEqual(notes, {"expired": "EXCLUSION_EXPIRED", "no-reason": "EXCLUSION_INVALID"})
        self.assertEqual(excluded[0]["rule"] in ("cockpit-self-signed", "archive"), True)

    def test_host_glob_and_score_effect(self):
        run = make_run("dev-db1")
        exclusions.apply(run, self.rules)
        self.assertNotIn("ORACLE_INVALID_OBJECTS", [f.code for f in run.findings()])
        base = make_run()
        scoring.score_run(base)
        run2 = make_run()
        exclusions.apply(run2, self.rules)
        scoring.score_run(run2)
        self.assertGreaterEqual(run2.module("storage").score, base.module("storage").score)

    def test_expiry_and_validation(self):
        r = exclusions.Rule("x", {"code": "A", "reason": "r", "until": time.strftime("%Y-%m-%d")})
        self.assertFalse(r.expired(time.time()))            # valid through the whole day
        self.assertTrue(exclusions.Rule("x", {"code": "A", "reason": "r", "until": "31-12-2026"}).validate())
        self.assertTrue(exclusions.Rule("x", {"code": "A", "reason": "r", "until": "31-12-2026"}).expired(time.time()))

    def test_report_section_and_roundtrip(self):
        from lshme.reports import doc
        run = make_run()
        exclusions.apply(run, self.rules)
        scoring.score_run(run)
        d = doc.build(RunResult.from_dict(run.to_dict()), self.home.cfg, "executive")
        sec = [s for s in d.sections if s.name == "excluded"][0]
        self.assertEqual(len(sec.tables[0].rows), 2)
        self.assertIn("Internal console", str(sec.tables[0].rows))

    def test_watch_respects_exclusions(self):
        current = {"disk:/archive": ("CRITICAL", "/archive", "full", ""), "disk:/u01": ("CRITICAL", "/u01", "full", ""),
                   "mod:cockpit:COCKPIT_SELF_SIGNED:/etc/cockpit/ws-certs.d/0.cert": ("INFO", "/etc/cockpit/ws-certs.d/0.cert", "x", "")}
        out = watch.filter_excluded(self.home.cfg, current)
        self.assertEqual(sorted(out), ["disk:/u01"])


if __name__ == "__main__":
    unittest.main()
