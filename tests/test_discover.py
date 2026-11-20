# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Server scan: what is present, which modules are left out, and how that reaches the runs and reports."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout

from fakes import ROOT, FakeRunner, TempHome
from lshme import modules as modreg
from lshme.app import App
from lshme.core.context import Context
from lshme.core.model import RunResult
from lshme.engine import pipeline
from lshme.ops import discover

PS_MYSQL = "USER PID COMMAND\nmysql 812 /usr/sbin/mysqld --basedir=/usr\n"


def ctx_for(home, runner):
    return Context(home.cfg, home.paths, runner)


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()

    def tearDown(self):
        self.home.cleanup()

    def present(self, runner):
        return dict((n, m["present"]) for n, m in discover.detect(ctx_for(self.home, runner)).items())

    def test_a_plain_server_has_only_the_core_modules(self):
        p = self.present(FakeRunner({"ps": (0, "USER PID COMMAND\nroot 1 /sbin/init\n")}))
        for name in discover.ALWAYS:
            self.assertTrue(p[name], name)
        for name in ("kvm", "cockpit", "database", "backup", "certificates", "containers", "cluster", "applications", "custom"):
            self.assertFalse(p[name], name)

    def test_kvm_needs_the_daemon_not_just_the_client(self):
        self.assertFalse(self.present(FakeRunner(tools=["virsh"]))["kvm"])
        self.assertTrue(self.present(FakeRunner(tools=["virsh"], files={"/usr/sbin/libvirtd": ""}))["kvm"])

    def test_database_found_from_a_running_engine(self):
        p = self.present(FakeRunner({"ps": (0, PS_MYSQL)}))
        self.assertTrue(p["database"])
        self.assertEqual(discover.database_engines(ctx_for(self.home, FakeRunner({"ps": (0, PS_MYSQL)}))), ["mysql"])

    def test_cockpit_package_container_cluster_and_backup(self):
        r = FakeRunner({"rpm -qa": (0, "cockpit-ws 300-1\n")}, tools=["podman", "pcs", "restic"])
        p = self.present(r)
        for name in ("cockpit", "containers", "cluster", "backup"):
            self.assertTrue(p[name], name)

    def test_configured_things_count_as_present(self):
        self.home.cfg.set("certificates", "certificates", "endpoints", "mail.example.com:443")
        self.home.cfg.set("main", "applications", "http_checks", "https://example.com/")
        self.home.cfg.set("backup", "job:daily", "path", "/backup")
        self.home.cfg.set("custom", "check:x", "command", "/bin/true")
        p = self.present(FakeRunner())
        for name in ("certificates", "applications", "backup", "custom"):
            self.assertTrue(p[name], name)

    def test_a_failing_probe_keeps_the_module_on(self):
        original = discover.DETECTORS["kvm"]
        discover.DETECTORS["kvm"] = lambda ctx: 1 / 0
        try:
            m = discover.detect(ctx_for(self.home, FakeRunner()))["kvm"]
        finally:
            discover.DETECTORS["kvm"] = original
        self.assertTrue(m["present"])
        self.assertIn("stays on", m["reason"])

    def test_inventory_lists_real_filesystems_only(self):
        mounts = "/dev/sda1 / xfs rw 0 0\nproc /proc proc rw 0 0\n/dev/sdb1 /data ext4 rw 0 0\ntmpfs /run tmpfs rw 0 0\n"
        inv = discover.inventory(ctx_for(self.home, FakeRunner(files={"/proc/mounts": mounts, "/etc/os-release": 'PRETTY_NAME="Test OS 1"\n'})))
        self.assertEqual(inv["filesystems"], ["/", "/data"])
        self.assertEqual(inv["os"], "Test OS 1")


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.app = App(ROOT, home=self.home.dir)
        self.profile = {"scanned_at": 1.0, "scanned_at_text": "x", "version": "t", "host": "h",
                        "inventory": {"databases": [], "container_engines": [], "filesystems": ["/"]},
                        "modules": dict((n, {"title": n, "present": n not in ("kvm", "cluster"), "reason": "r", "always": False}) for n in modreg.MODULE_ORDER)}

    def tearDown(self):
        self.home.cleanup()

    def test_apply_excludes_absent_modules_from_every_run(self):
        changes, excluded = discover.apply(self.app, self.profile)
        self.assertEqual(excluded, ["kvm", "cluster"])
        self.assertEqual(changes, [])  # first scan: nothing to compare with
        self.assertEqual(self.app.cfg.get("main", "modules", "auto_excluded"), "kvm,cluster")
        names = [m.NAME for m in modreg.resolve(self.app.cfg)]
        self.assertNotIn("kvm", names)
        self.assertNotIn("cluster", names)
        self.assertIn("storage", names)
        self.assertTrue(os.path.exists(discover.profile_path(self.app.paths)))

    def test_forced_on_and_explicit_requests_override(self):
        discover.apply(self.app, self.profile)
        self.app.cfg.set("main", "modules", "force_enabled", "kvm")
        self.assertIn("kvm", [m.NAME for m in modreg.resolve(self.app.cfg)])
        self.app.cfg.set("main", "modules", "force_enabled", "")
        self.assertEqual([m.NAME for m in modreg.resolve(self.app.cfg, "kvm,storage")], ["storage", "kvm"])  # asked for by name: always run

    def test_rescan_reports_what_changed_and_brings_modules_back(self):
        discover.apply(self.app, self.profile)
        newer = json.loads(json.dumps(self.profile))
        newer["modules"]["kvm"]["present"] = True
        newer["modules"]["kvm"]["reason"] = "libvirt is installed"
        newer["inventory"]["databases"] = ["postgresql"]
        newer["inventory"]["filesystems"] = ["/", "/data"]
        changes, excluded = discover.apply(self.app, newer)
        self.assertEqual(excluded, ["cluster"])
        self.assertTrue(any("kvm" in c.lower() and "now present" in c for c in changes), changes)
        self.assertIn("New database engine: postgresql", changes)
        self.assertIn("New filesystem: /data", changes)
        self.assertIn("kvm", [m.NAME for m in modreg.resolve(self.app.cfg)])
        self.assertTrue(os.path.exists(discover.profile_path(self.app.paths).replace(".json", ".previous.json")))

    def test_scan_is_recorded_in_the_audit_log(self):
        discover.apply(self.app, self.profile)
        with open(os.path.join(self.app.paths.log_dir, "audit.log"), encoding="utf-8") as f:
            self.assertIn("server-scan", f.read())


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()

    def tearDown(self):
        self.home.cleanup()

    def run_modules(self, runner):
        ctx = ctx_for(self.home, runner)
        return pipeline.run_modules(ctx, [modreg.load("kvm"), modreg.load("cluster"), modreg.load("cpu_memory")])

    def test_modules_with_nothing_to_check_are_left_out_of_the_run(self):
        run = self.run_modules(FakeRunner())
        self.assertEqual([m.name for m in run.modules], ["cpu_memory"])  # a module that is not skipped stays
        self.assertEqual(sorted(x["name"] for x in run.not_present), ["cluster", "kvm"])
        self.assertTrue(all(x["reason"] for x in run.not_present))

    def test_not_present_survives_json_and_can_be_turned_off(self):
        run = self.run_modules(FakeRunner())
        again = RunResult.from_dict(json.loads(json.dumps(run.to_dict())))
        self.assertEqual(len(again.not_present), 2)
        self.home.cfg.set("main", "modules", "hide_not_present", "no")
        kept = self.run_modules(FakeRunner())
        self.assertEqual([m.name for m in kept.modules], ["kvm", "cluster", "cpu_memory"])
        self.assertEqual(kept.not_present, [])


class ScanCommandTests(unittest.TestCase):
    def test_scan_json_then_apply(self):
        from lshme.ui.cli import main
        home = TempHome()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", home.dir, "scan", "--json"]), 0)
        profile = json.loads(buf.getvalue())
        self.assertTrue(profile["modules"]["system"]["present"])
        self.assertFalse(os.path.exists(os.path.join(home.dir, "data", "profile.json")))  # a plain scan changes nothing
        with redirect_stdout(io.StringIO()):
            main(["--home", home.dir, "scan", "--apply", "--quiet"])
        self.assertTrue(os.path.exists(os.path.join(home.dir, "data", "profile.json")))
        app = App(ROOT, home=home.dir)
        self.assertEqual(app.cfg.get("main", "modules", "auto_excluded"), ",".join(discover.excluded_modules(discover.load_profile(app.paths))))
        home.cleanup()

    def test_scan_prints_a_readable_table(self):
        from lshme.ui.cli import main
        home = TempHome()
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["--home", home.dir, "scan"])
        out = buf.getvalue()
        self.assertIn("Operating System", out)
        self.assertIn("Nothing was changed", out)
        home.cleanup()


if __name__ == "__main__":
    unittest.main()
