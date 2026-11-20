# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The vitals home screen: live dashboard, report browser, run now, server scan and the setup wizard."""
import json
import os
import unittest

from fakes import ROOT, FakeRunner, TempHome
from lshme.app import App
from lshme.core.model import ModuleResult, RunResult
from lshme.engine.history import History
from lshme.ops import discover, reportlist
from lshme.ui import cli, firstrun, helptext, home, tui
from lshme.ui.tui_kit import UI
from test_tui import MemoryBackend


def snapshot(cores=2, mounts=None):
    """A canned live sample, shaped like Sampler.sample()."""
    mounts = mounts if mounts is not None else [
        {"mount": "/", "device": "/dev/sda1", "fstype": "xfs", "total": 20 * 1024 ** 3, "used": 12 * 1024 ** 3, "free": 8 * 1024 ** 3, "pct": 60.0,
         "inode_pct": 5.0, "readonly": False, "error": False},
        {"mount": "/data", "device": "/dev/sdb1", "fstype": "ext4", "total": 100 * 1024 ** 3, "used": 93 * 1024 ** 3, "free": 7 * 1024 ** 3, "pct": 93.0,
         "inode_pct": 91.0, "readonly": True, "error": False}]
    return {"ts": 1.0, "hostname": "oraclelinux9", "uptime": 93784.0, "load": [0.42, 0.51, 0.48],
            "cpu": {"total": 75.0, "user": 50.0, "system": 20.0, "iowait": 5.0, "steal": 0.0, "cores": cores, "per_core": [80.0, 70.0] * (cores // 2) + [10.0] * (cores % 2)},
            "mem": {"total": 4 * 1024 ** 3, "used": 3 * 1024 ** 3, "available": 1024 ** 3, "pct": 75.0, "cache": 0,
                    "swap_total": 2 * 1024 ** 3, "swap_used": 512 * 1024 ** 2, "swap_pct": 25.0},
            "mounts": mounts,
            "net": [{"iface": "eth0", "rx_bps": 12345.0, "tx_bps": 2345.0, "rx_total": 10 ** 9, "tx_total": 10 ** 8, "errors": 0, "state": "up"},
                    {"iface": "lo", "rx_bps": 1.0, "tx_bps": 1.0, "rx_total": 1, "tx_total": 1, "errors": 0, "state": "up"}],
            "disks": [{"dev": "sda", "name": "sda", "read_bps": 2048.0, "write_bps": 4096.0, "util": 37.0}],
            "procs": {"total": 120, "running": 2,
                      "top_cpu": [{"pid": 4242, "name": "java", "cpu": 88.5, "rss": 700 * 1024 ** 2, "user": "oracle"}],
                      "top_mem": [{"pid": 4343, "name": "mysqld", "cpu": 3.0, "rss": 900 * 1024 ** 2, "user": "mysql"}]},
            "history": {"cpu": [10, 40, 80, 60, 75], "mem": [70, 72, 75], "rx": [0, 100, 200], "tx": [0, 50, 20], "load": [0.1, 0.4, 0.5]}}


class FakeSampler(object):
    def __init__(self, snap=None):
        self.snap = snap or snapshot()
        self.calls = 0
        self.history = self.snap["history"]

    def read(self, path):
        return 'PRETTY_NAME="Test Linux 9"\n' if path == "/etc/os-release" else ""

    def sample(self, processes=True):
        self.calls += 1
        return self.snap


class HomeCase(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.app = App(ROOT, home=self.home.dir)

    def tearDown(self):
        self.home.cleanup()

    def ctx(self, keys, rows=24, cols=80):
        self.be = MemoryBackend(keys, rows, cols)
        return tui.Ctx(self.app, UI(self.be))


class DashboardTests(HomeCase):
    def run_dash(self, keys, snap=None, rows=24, cols=80, **kw):
        ctx = self.ctx(keys, rows, cols)
        self.sampler = FakeSampler(snap)
        d = home.Dashboard(ctx, sampler=self.sampler, warm=False, **kw)
        d.run()
        return d

    def test_overview_shows_cpu_memory_filesystems_and_network(self):
        self.run_dash(["TIMEOUT", "q"])
        for text in ("oraclelinux9", "Test Linux 9", "Up 1d 2h", "75.0%", "RAM", "Swap", "/data", "93%", "eth0", "In ", "Out ", "Filesystems"):
            self.assertTrue(self.be.saw(text), text)
        self.assertTrue(self.be.saw("#"))  # plain-terminal bars

    def test_every_view_draws(self):
        self.run_dash(["2", "3", "4", "5", "q"])
        for text in ("CPU cores (2)", "Load and CPU time", "Busiest processes", "Filesystems and mounts (2)", "RO", "Network interfaces",
                     "Disk I/O", "Top by CPU", "Top by memory", "java", "mysqld", "Inode%", "93%"):
            self.assertTrue(self.be.saw(text), text)

    def test_tab_and_arrow_keys_switch_views(self):
        d = self.run_dash(["TAB", "TAB", "BTAB", "RIGHT", "LEFT", "q"])
        self.assertEqual(d.tab, 1)

    def test_pause_stops_sampling_and_resume_restarts_it(self):
        self.run_dash(["TIMEOUT", "TIMEOUT", "p", "TIMEOUT", "TIMEOUT", "p", "TIMEOUT", "q"])
        self.assertEqual(self.sampler.calls, 1 + 2 + 1)  # first sample, two ticks, paused ticks skipped, one tick
        self.assertTrue(self.be.saw("PAUSED"))

    def test_speed_keys(self):
        d = self.run_dash(["+", "+", "q"], interval=2.0)
        self.assertEqual(d.interval, 0.5)
        d = self.run_dash(["-", "-", "-", "q"], interval=4.0)
        self.assertEqual(d.interval, 10.0)  # capped

    def test_scrolling_a_long_filesystem_list(self):
        many = [{"mount": "/m%02d" % i, "device": "d", "fstype": "xfs", "total": 10 ** 9, "used": 10 ** 8, "free": 9 * 10 ** 8, "pct": float(i),
                 "inode_pct": 1.0, "readonly": False, "error": False} for i in range(40)]
        d = self.run_dash(["3", "PGDN", "PGDN", "q"], snap=snapshot(mounts=many))
        self.assertTrue(self.be.saw("of 40"))
        self.assertGreater(d.scroll, 0)

    def test_many_cores_and_many_sizes_do_not_crash(self):
        for rows, cols in ((24, 80), (30, 100), (40, 140), (20, 70)):
            self.run_dash(["2", "q"], snap=snapshot(cores=64), rows=rows, cols=cols)
            self.assertTrue(self.be.frames)

    def test_no_swap_and_no_mounts(self):
        s = snapshot(mounts=[])
        s["mem"].update(swap_total=0, swap_used=0, swap_pct=0.0)
        self.run_dash(["3", "1", "q"], snap=s)
        self.assertTrue(self.be.saw("none configured"))

    def test_the_real_proc_of_this_machine(self):
        if not os.path.isdir("/proc/self"):
            self.skipTest("no /proc")
        ctx = self.ctx(["TIMEOUT", "5", "q"])
        home.Dashboard(ctx, warm=False).run()
        self.assertTrue(self.be.saw("Top by CPU"))

    def test_vbars_chart(self):
        rows = home.vbars([0, 50, 100], 4, 100.0, unicode_blocks=False)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][2], "#")   # only the 100 reaches the top row
        self.assertEqual(rows[0][0], " ")
        self.assertNotEqual(rows[3][0], "#")  # a 0 has no bar


def fake_run(score=69, status="CRITICAL"):
    run = RunResult("h1")
    run.health = {"score": score, "status": status, "counts": {"CRITICAL": 1, "WARNING": 1}}
    m = ModuleResult("storage", "Storage & Filesystems", 10)
    m.score, m.summary = 90, "2 filesystems"
    m.add("CRITICAL", "DISK_FULL", "/data", "Filesystem /data is 93% full", recommendation="Free space or extend the volume.")
    m.add("INFO", "NOTE", "x", "Just so you know")
    run.modules = [m]
    return run


class ReportTests(HomeCase):
    def write_report(self, stamp, category="adhoc", score=69):
        run = fake_run(score)
        d = os.path.join(self.app.paths.report_dir, "h1", category, stamp)
        os.makedirs(d)
        for name, text in (("h1_x.html", "<html></html>"), ("h1_x.json", json.dumps(run.to_dict()))):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(text)
        return d

    def test_lists_reports_newest_first_with_scores(self):
        self.write_report("20261101_080000", "daily", 90)
        self.write_report("20261120_134824", "adhoc", 69)
        items = reportlist.list_reports(self.app.paths)
        self.assertEqual([r["category"] for r in items], ["adhoc", "daily"])
        self.assertEqual(items[0]["score"], 69)
        self.assertEqual(items[0]["critical"], 1)
        self.assertEqual(items[0]["files"], ["h1_x.html", "h1_x.json"])
        self.assertEqual(items[0]["when"], "2026-11-20 13:48:24")

    def test_empty_and_broken_reports_do_not_break_the_list(self):
        os.makedirs(os.path.join(self.app.paths.report_dir, "h1", "adhoc", "20261120_134824"))
        d = self.write_report("20261121_100000")
        with open(os.path.join(d, "h1_x.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        items = reportlist.list_reports(self.app.paths)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(r["score"] is None for r in items))

    def test_browser_shows_chart_list_and_opens_a_report(self):
        self.write_report("20261120_134824")
        h = History(self.app.paths.history_db)
        for score in (50, 69, 80):
            run = fake_run(score)
            run.schedule = "adhoc"
            h.save(run)
        h.close()
        ctx = self.ctx(["ENTER", "ESC", "q"])
        home.reports_screen(ctx)
        for text in ("Health score over time (3 runs)", "Reports (1), newest first", "adhoc", "CRITICAL", "69"):
            self.assertTrue(self.be.saw(text), text)
        for text in ("Findings that need attention", "Filesystem /data is 93% full", "Free space or extend the volume.", "Just so you know", "h1_x.html"):
            self.assertTrue(self.be.saw(text), text)

    def test_browser_with_nothing_yet(self):
        ctx = self.ctx(["q"])
        home.reports_screen(ctx)
        self.assertTrue(self.be.saw("No reports yet"))
        self.assertTrue(self.be.saw("No history yet"))

    def test_latest_summary_and_series(self):
        self.assertIsNone(reportlist.latest_summary(self.app.paths))
        run = fake_run()
        with open(os.path.join(self.app.paths.data_dir, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(run.to_dict(), f)
        s = reportlist.latest_summary(self.app.paths)
        self.assertEqual((s["score"], s["status"], s["counts"]["CRITICAL"]), (69, "CRITICAL", 1))
        self.assertEqual(s["modules"][0]["title"], "Storage & Filesystems")

    def test_run_lines_is_readable(self):
        text = "\n".join(reportlist.run_lines(fake_run(), ["/x/a.html"]))
        self.assertIn("Health score 69 / 100 - CRITICAL", text)
        self.assertIn("[CRITICAL] Filesystem /data is 93% full", text)
        self.assertIn("-> Free space or extend the volume.", text)
        self.assertIn("/x/a.html", text)

    def test_run_now_shows_progress_then_results(self):
        run = fake_run()

        def fake_full_run(*args, **kw):
            kw["progress"](1, 3, "Storage")
            return run, {"files": ["/r/a.html"], "report_dir": "/r"}
        self.app.full_run = fake_full_run
        ctx = self.ctx(["y", "ENTER", "y", "ESC"])  # run, close result, view it, close viewer
        home.run_now(ctx)
        self.assertTrue(self.be.saw("Checking: Storage"))
        self.assertTrue(self.be.saw("Score 69 / 100 - CRITICAL"))
        self.assertTrue(self.be.saw("Filesystem /data is 93% full"))

    def test_run_now_when_another_run_is_active(self):
        from lshme.core.lock import LockBusy

        def busy(*a, **k):
            raise LockBusy("busy")
        self.app.full_run = busy
        ctx = self.ctx(["y", "ENTER"])
        home.run_now(ctx)
        self.assertTrue(self.be.saw("already running"))

    def test_run_now_can_be_declined(self):
        self.app.full_run = lambda *a, **k: self.fail("must not run")
        home.run_now(self.ctx(["n"]))


class ScanScreenTests(HomeCase):
    def setUp(self):
        HomeCase.setUp(self)
        self.app.runner = FakeRunner()

    def test_scan_lists_modules_and_apply_stores_the_result(self):
        ctx = self.ctx(["TAB", "TAB", "TAB", "ENTER", "ENTER", "ESC"])  # Apply, close the message, leave
        home.scan_screen(ctx)
        self.assertTrue(self.be.saw("not present - left out"))
        self.assertTrue(self.be.saw("Operating System"))
        self.app.reload()
        self.assertIn("kvm", self.app.cfg.get("main", "modules", "auto_excluded").split(","))
        self.assertTrue(os.path.exists(discover.profile_path(self.app.paths)))

    def test_force_a_missing_module_on_and_off(self):
        ctx = self.ctx(["DOWN"] * 6 + ["ENTER", "y", "ESC"])  # row 7 is KVM
        home.scan_screen(ctx)
        self.app.reload()
        self.assertEqual(self.app.cfg.get("main", "modules", "force_enabled"), "kvm")
        ctx = self.ctx(["DOWN"] * 6 + ["ENTER", "ESC"])
        home.scan_screen(ctx)
        self.assertTrue(self.be.saw("checked (forced on)"))
        self.app.reload()
        self.assertEqual(self.app.cfg.get("main", "modules", "force_enabled"), "")  # Enter again switches it off

    def test_a_present_module_cannot_be_toggled(self):
        ctx = self.ctx(["ENTER", "ENTER", "ESC"])
        home.scan_screen(ctx)
        self.assertTrue(self.be.saw("is checked because it was found"))


class MenuTests(HomeCase):
    def test_main_menu_lists_everything_and_opens_help(self):
        ctx = self.ctx(["END", "ENTER", "ESC", "q"])  # last entry is Help: open it, close it, quit
        home.start(ctx)
        first = self.be.frames[0]
        for label in ("Live dashboard", "Reports", "Run a health check now", "Server scan", "Web interface", "Configuration", "First-time setup", "Help"):
            self.assertIn(label, first)
        self.assertIn("No health check has been run yet", first)
        self.assertTrue(self.be.saw("vitals setup"))

    def test_direct_pages_and_unknown_page(self):
        home.start(self.ctx(["ESC"]), "help")
        self.assertTrue(self.be.saw("vitals setup"))
        with self.assertRaises(ValueError):
            home.start(self.ctx([]), "nope")

    def test_menu_shows_the_last_score(self):
        with open(os.path.join(self.app.paths.data_dir, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(fake_run().to_dict(), f)
        home.start(self.ctx(["q"]))
        self.assertTrue(self.be.saw("69/100 CRITICAL"))

    def test_small_terminal(self):
        home.start(self.ctx(["ESC"], rows=10, cols=40))
        self.assertTrue(self.be.saw("Terminal too small"))

    def test_help_text_names_the_commands(self):
        text = "\n".join(helptext.full_help())
        for word in ("vitals", "vitals setup", "sudo vitals", "systemhealth", "Ctrl-N", "Sunday", "/etc/lshme"):
            self.assertIn(word, text)
        self.assertIn("sudo vitals", helptext.INSTALLED_BANNER)


class SystemhealthNameTests(unittest.TestCase):
    def test_argument_mapping(self):
        m = cli.systemhealth_args
        self.assertEqual(m([]), ["home"])
        self.assertEqual(m(["setup"]), ["home", "--page", "setup"])
        self.assertEqual(m(["help"]), ["home", "--page", "help"])
        self.assertEqual(m(["--config-dir", "/etc/x", "reports"]), ["--config-dir", "/etc/x", "home", "--page", "reports"])
        self.assertEqual(m(["--home", "/h"]), ["--home", "/h", "home"])
        self.assertEqual(m(["config"]), ["home", "--page", "config"])
        self.assertEqual(m(["config", "show"]), ["config", "show"])   # the normal command still works
        self.assertEqual(m(["run", "--formats", "all"]), ["run", "--formats", "all"])
        self.assertEqual(m(["scan", "--apply"]), ["scan", "--apply"])

    def test_launcher_passes_its_name(self):
        with open(os.path.join(ROOT, "healthcheck"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('export LSHME_PROG="$PROG"', text)

    def test_home_without_a_terminal_prints_the_guide(self):
        import io
        from contextlib import redirect_stdout
        home_dir = TempHome()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--home", home_dir.dir, "home"])
        self.assertEqual(code, 1)
        self.assertIn("vitals setup", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--home", home_dir.dir, "home", "--page", "help"])
        self.assertEqual(code, 0)
        self.assertIn("What this does", buf.getvalue())
        home_dir.cleanup()


class SetupWizardTests(HomeCase):
    PROFILE = {"scanned_at": 1.0, "scanned_at_text": "2026-11-20 02:00:00", "version": "t", "host": "h",
               "inventory": {"hostname": "h", "os": "Test OS", "kernel": "5.14", "cpus": 4, "memory_mb": 8192, "virtualization": "kvm",
                             "filesystems": ["/", "/data"], "databases": [], "container_engines": [], "systemd": True},
               "modules": {}}

    def setUp(self):
        HomeCase.setUp(self)
        from lshme import modules as modreg
        for n in modreg.MODULE_ORDER:
            self.PROFILE["modules"][n] = {"title": n, "present": n not in ("kvm", "cluster"), "reason": "x", "always": n in discover.ALWAYS}
        self.units = os.path.join(self.home.dir, "units")
        os.makedirs(self.units)
        self.app.cfg.set("schedule", "scheduler", "backend", "systemd")
        self.app.cfg.set("schedule", "scheduler", "systemd_dir", self.units)
        self.app.cfg.save("schedule")
        self.app.reload()
        self.app.runner = FakeRunner({"systemctl": (0, "")})  # nothing real is installed or started
        self.scan = discover.scan
        discover.scan = lambda ctx: dict(self.PROFILE)

    def tearDown(self):
        discover.scan = self.scan
        HomeCase.tearDown(self)

    def test_needed_until_finished(self):
        self.assertTrue(firstrun.needed(self.app))
        firstrun.mark_done(self.app)
        self.assertFalse(firstrun.needed(self.app))

    def test_the_whole_setup_end_to_end(self):
        keys = ["ENTER"]                                    # 1 welcome: Start
        keys += ["ENTER"]                                   # 2 scan: Use this result
        keys += ["ENTER"] + list("Acme Bank") + ["ENTER", "F10"]  # 3 organisation: company, Next
        keys += ["F10"]                                     # 4 email: Next (nothing entered)
        keys += ["F10"]                                     # 5 alert levels
        keys += ["F10"]                                     # 6 services
        keys += ["F10"]                                     # 8 schedules (7 databases is skipped: none found)
        keys += ["F10"]                                     # 9 real-time alerts (stay on)
        keys += ["F10"]                                     # 10 web interface (stays off)
        keys += ["ENTER", "ENTER", "n"]                     # 11 Finish, close the summary, no first health check
        ctx = self.ctx(keys)
        self.assertTrue(firstrun.run(ctx))
        self.app.reload()
        self.assertEqual(self.app.cfg.get("main", "general", "company"), "Acme Bank")
        self.assertEqual(self.app.cfg.get("main", "modules", "auto_excluded"), "kvm,cluster")  # the scan was applied
        self.assertFalse(firstrun.needed(self.app))
        self.assertEqual(sorted(os.listdir(self.units)), ["lshme-daily.service", "lshme-daily.timer", "lshme-monthly.service", "lshme-monthly.timer",
                                                          "lshme-scan.service", "lshme-scan.timer", "lshme-watch.service", "lshme-weekly.service", "lshme-weekly.timer"])
        for text in ("Welcome to LRS Vitals", "Not found, so left out of every scan and report", "Setup 3 of 11 - Organisation",
                     "Setup 6 of 11 - Services to watch", "Ready to finish", "Installed:"):
            self.assertTrue(self.be.saw(text), text)

    def test_the_web_interface_can_be_switched_on_in_the_wizard(self):
        from lshme.web import auth
        auth.ITERATIONS = 2000
        pw = list("Str0ngPassw0rd")
        keys = ["ENTER", "ENTER", "F10", "F10", "F10", "F10", "F10", "F10"]   # welcome .. real-time alerts
        keys += [" ", "DOWN", "ENTER"] + pw + ["ENTER", "DOWN", "ENTER"] + pw + ["ENTER", "F10"]  # web: local, password twice, Next
        keys += ["ENTER", "ENTER", "n"]                                          # finish
        self.assertTrue(firstrun.run(self.ctx(keys)))
        self.app.reload()
        self.assertEqual(self.app.cfg.get("web", "web", "enabled"), "yes")
        self.assertEqual(self.app.cfg.get("web", "web", "bind"), "127.0.0.1")
        self.assertEqual(self.app.cfg.get("web", "web", "tls"), "no")
        self.assertEqual(auth.Users(self.app).verify("admin", "Str0ngPassw0rd"), "admin")
        with open(self.app.cfg.path("web"), encoding="utf-8") as f:
            self.assertNotIn("Str0ngPassw0rd", f.read())  # only the hash is stored
        self.assertTrue(self.be.saw("start the web interface"))

    def test_a_weak_web_password_is_refused(self):
        keys = ["ENTER", "ENTER", "F10", "F10", "F10", "F10", "F10", "F10"]
        keys += [" ", "DOWN", "ENTER"] + list("weak") + ["ENTER", "F10", "ENTER"]  # the refusal popup, closed
        keys += ["ESC"] * 8 + ["y"]                          # walk back to the scan page, Esc, leave
        self.assertFalse(firstrun.run(self.ctx(keys)))
        self.assertTrue(self.be.saw("Password: use at least 10 characters"))
        self.app.reload()
        self.assertEqual(self.app.cfg.get("web", "web", "enabled"), "no")

    def test_back_goes_to_the_previous_page_and_values_are_kept(self):
        keys = ["ENTER", "ENTER"]                           # welcome, scan
        keys += ["ENTER"] + list("Keep Me") + ["ENTER", "F10"]  # organisation, Next
        keys += ["ESC"]                                     # email: Back
        keys += ["ESC"]                                     # organisation: Back, to the scan page
        keys += ["ESC", "y"]                                # scan page: Esc asks to leave, yes
        ctx = self.ctx(keys)
        self.assertFalse(firstrun.run(ctx))
        self.app.reload()
        self.assertEqual(self.app.cfg.get("main", "general", "company"), "Keep Me")  # saved before going back
        self.assertEqual(sum(1 for f in self.be.frames if "Setup 3 of 11 - Organisation" in f) > 1, True)  # shown twice

    def test_leaving_at_the_welcome_page_is_remembered(self):
        ctx = self.ctx(["LEFT", "ENTER", "y"])              # choose Leave, confirm
        self.assertFalse(firstrun.run(ctx))
        self.assertFalse(firstrun.needed(self.app))

    def test_rerun_does_not_touch_the_marker(self):
        ctx = self.ctx(["LEFT", "ENTER", "y"])
        self.assertFalse(firstrun.run(ctx, rerun=True))
        self.assertTrue(firstrun.needed(self.app))

    def test_a_database_page_appears_when_one_was_found(self):
        self.PROFILE["inventory"]["databases"] = ["mysql"]
        keys = ["ENTER", "ENTER", "F10", "F10", "F10", "F10"]   # welcome, scan, organisation, email, levels, services
        keys += ["ESC"]                                          # databases page: leave setup
        ctx = self.ctx(keys + ["n"])
        with self.assertRaises(AssertionError):
            firstrun.run(ctx)
        self.assertTrue(self.be.saw("Databases found on this server: mysql"))
        self.PROFILE["inventory"]["databases"] = []

    def test_without_write_access_it_asks_for_root(self):
        os.chmod(self.app.paths.config_dir, 0o500)
        try:
            if os.access(self.app.paths.config_dir, os.W_OK):
                self.skipTest("running as root: the directory is writable anyway")
            self.assertFalse(firstrun.run(self.ctx(["ENTER"])))
            self.assertTrue(self.be.saw("Setup needs root"))
        finally:
            os.chmod(self.app.paths.config_dir, 0o700)


if __name__ == "__main__":
    unittest.main()
