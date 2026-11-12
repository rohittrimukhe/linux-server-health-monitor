"""Interactive DOS-style menu."""
import os
import time

from lshme import APP_NAME, VERSION
from lshme import modules as modreg
from lshme import reports
from lshme.ui import console as C


def quick_stats(app):
    r = app.runner
    try:
        load = r.read("/proc/loadavg").split()[:3]
    except Exception:
        load = ["?"] * 3
    mem = {}
    for line in r.read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.split():
            mem[k] = int(v.split()[0])
    mem_pct = 100.0 * (mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)) / mem["MemTotal"] if mem.get("MemTotal") else 0
    worst = 0.0
    d = r.run(["df", "-P", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs", "-x", "overlay"], timeout=15)
    for line in d.lines()[1:]:
        p = line.split()
        if len(p) >= 6 and p[4].endswith("%"):
            worst = max(worst, float(p[4][:-1]))
    return load, mem_pct, worst


class Menu(object):
    def __init__(self, app):
        self.app = app

    def banner(self):
        load, mem, disk = quick_stats(self.app)
        os.system("clear" if os.name != "nt" else "cls") if C.COLOR else None
        w = 74
        C.out(C.c(" " * w, "title"))
        C.out(C.c(("  %s  v%s" % (APP_NAME, VERSION)).ljust(w), "title"))
        C.out(C.c(("  Host: %s   %s" % (os.uname()[1], time.strftime("%Y-%m-%d %H:%M"))).ljust(w), "title"))
        C.out(C.c(" " * w, "title"))
        style = lambda v, wv, cv: "red" if v >= cv else "yellow" if v >= wv else "green"
        C.out("  Load %s   Memory %s   Fullest filesystem %s" % (
            " / ".join(load), C.c("%.0f%%" % mem, style(mem, 85, 95)), C.c("%.0f%%" % disk, style(disk, 80, 90))))
        from lshme.engine import maintenance
        if maintenance.active(self.app.paths):
            C.out(C.c("  MAINTENANCE MODE ACTIVE — alert emails suppressed", "yellow"))
        C.out()

    ITEMS = [
        ("1", "Complete health check (all modules + reports)"), ("2", "Check a single module"),
        ("3", "Storage & mount points"), ("4", "Services & processes"), ("5", "Database health"),
        ("6", "KVM virtual machines"), ("7", "Cockpit web console"), ("8", "Network & connectivity"),
        ("9", "Generate reports (choose formats / period)"), ("10", "SMTP & email settings"),
        ("11", "Scheduling & automation"), ("12", "Report & log retention"), ("13", "History & trends"),
        ("14", "Baseline & configuration drift"), ("15", "Maintenance mode"), ("16", "Multi-server (fleet)"),
        ("17", "Configuration & setup wizard"), ("18", "Self-diagnostics"), ("19", "View logs"), ("0", "Exit"),
    ]

    def loop(self):
        while True:
            self.banner()
            half = (len(self.ITEMS) + 1) // 2
            for i in range(half):
                left = self.ITEMS[i]
                right = self.ITEMS[i + half] if i + half < len(self.ITEMS) else None
                l = "[%2s] %-36s" % left
                r = "[%2s] %s" % right if right else ""
                C.out("  " + l + r)
            choice = C.ask("\n  Select option", "")
            if choice == "0" or choice.lower() in ("q", "exit", "quit"):
                return
            handler = getattr(self, "opt_%s" % choice, None)
            if not handler:
                continue
            try:
                handler()
            except KeyboardInterrupt:
                C.out("\ncancelled")
            except Exception as e:
                C.out(C.c("Error: %s" % e, "red"))
                self.app.log.exception("menu action failed")
            C.pause()

    # -- actions --------------------------------------------------------------
    def _quick(self, names, days=1):
        from lshme.ui.cli import print_run, progress_printer
        run = self.app.check(names, days, "adhoc", save_history=False, progress=progress_printer(False))
        print_run(run, None, True)
        if C.yes("Show detail tables", False):
            for m in run.modules:
                for t in m.tables:
                    C.out()
                    C.out(C.c("%s — %s" % (m.title, t.title), "cyan"))
                    C.table(t.headers, t.rows)

    def opt_1(self):
        from lshme.ui.cli import print_run, progress_printer
        days = int(C.ask("Report period in days (logs/trends)", "1", validate=C.num_validator(1, 3650)))
        fm = C.ask("Report formats (%s or all)" % ",".join(reports.FORMATS), self.app.cfg.get("reports", "reports", "formats", "html,pdf,json"))
        email = C.yes("Email the reports", False)
        run, result = self.app.full_run(None, days, fm, None, email or None, progress=progress_printer(False))
        print_run(run, result)

    def opt_2(self):
        mods = modreg.all_modules()
        for i, m in enumerate(mods, 1):
            C.out("  [%2d] %s" % (i, m.TITLE))
        v = C.ask("Module number", "1", validate=C.num_validator(1, len(mods)))
        self._quick(mods[int(v) - 1].NAME)

    def opt_3(self):
        self._quick("storage")

    def opt_4(self):
        self._quick("services")

    def opt_5(self):
        self._quick("database")

    def opt_6(self):
        self._quick("kvm")

    def opt_7(self):
        self._quick("cockpit")

    def opt_8(self):
        self._quick("network")

    def opt_9(self):
        from lshme.ui.cli import print_run, progress_printer
        days = int(C.ask("Report period in days", "7", validate=C.num_validator(1, 3650)))
        fm = C.ask("Formats (%s or all)" % ",".join(reports.FORMATS), "all")
        rt = C.ask("Report type", "technical", choices=["executive", "technical", "full"])
        run, result = self.app.full_run(None, days, fm, rt, None, progress=progress_printer(False))
        print_run(run, result)

    def opt_10(self):
        from lshme.ui.wizard import Wizard
        w = Wizard(self.app)
        C.out("  [1] Configure SMTP   [2] Send test email   [3] Show settings")
        v = C.ask("Choice", "3", choices=["1", "2", "3"])
        if v == "1":
            w.step_smtp()
        elif v == "2":
            from lshme.delivery import mailer
            res = mailer.send_test(mailer.MailSettings(self.app.cfg), self.app.paths.log_dir)
            C.out(C.c("Sent to %s" % ", ".join(res["recipients"]), "green") if res["ok"] else C.c("Failed: %s" % res["error"], "red"))
        else:
            s = dict(self.app.cfg.section("smtp", "smtp"))
            C.table(["Setting", "Value"], sorted(s.items()))
            C.out("Password stored: %s" % ("yes" if self.app.cfg.secret("smtp") else "no"))

    def opt_11(self):
        from lshme.ops import scheduler
        from lshme.ui.wizard import Wizard
        be, rows = scheduler.status(self.app.cfg, self.app.runner)
        C.out("Backend: %s" % be)
        C.table(["Schedule", "Enabled", "When", "Days", "Formats", "Email", "Installed", "Next"],
                [[r["name"], "yes" if r["enabled"] else "no", r["when"], r["days"], r["formats"], "yes" if r["email"] else "no",
                  "yes" if r["installed"] else "NO", r["next"]] for r in rows])
        C.out("\n  [1] Edit schedules   [2] Install/refresh schedules   [3] Remove all schedules   [0] Back")
        v = C.ask("Choice", "0", choices=["0", "1", "2", "3"])
        if v == "1":
            Wizard(self.app).step_schedules()
        elif v == "2":
            rep = scheduler.install(self.app.cfg, self.app.runner, self.app.launcher(), self.app.global_args())
            self.app.audit("schedule-install", installed=rep["installed"], errors=rep["errors"])
            for i in rep["installed"]:
                C.out(C.c("installed " + i, "green"))
            for e in rep["errors"]:
                C.out(C.c("error: " + e, "red"))
        elif v == "3" and C.yes("Remove all LSHME schedules", False):
            C.out("Removed: %s" % ", ".join(scheduler.uninstall(self.app.cfg, self.app.runner)))

    def opt_12(self):
        from lshme.ops import retention
        from lshme.ui.wizard import Wizard
        acts = retention.plan(self.app.cfg, self.app.paths)
        C.table(["Action", "Path", "Age", "Bytes", "Reason"], [[a["action"], a["path"], a["age_days"] or "", a["bytes"], a["reason"]] for a in acts])
        C.out("\n  [1] Apply now   [2] Change retention settings   [0] Back")
        v = C.ask("Choice", "0", choices=["0", "1", "2"])
        if v == "1" and C.yes("Apply the actions above", False):
            done = retention.apply(self.app.cfg, self.app.paths, acts)
            C.out("%d action(s) applied; see audit.log" % len(done))
        elif v == "2":
            Wizard(self.app).step_retention()

    def opt_13(self):
        from lshme.engine.history import History
        h = History(self.app.paths.history_db)
        rows = h.runs(None, int(C.ask("Days", "30")))
        C.table(["Time", "Host", "Schedule", "Score", "Status", "Crit", "Warn"],
                [[time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])), r["host"], r["schedule"], r["score"], r["status"], r["critical"], r["warning"]] for r in rows])
        rec = h.recurring(os.uname()[1], time.time() - 30 * 86400)
        if rec:
            C.out(C.c("\nRecurring issues (30 days)", "bold"))
            C.table(["Code", "Component", "Severity", "Runs", "Message"], [list(r) for r in rec])
        h.close()

    def opt_14(self):
        from lshme.core.context import Context
        from lshme.engine import baseline
        ctx = Context(self.app.cfg, self.app.paths, self.app.runner)
        C.out("  [1] Compare with baseline   [2] Save new baseline   [0] Back")
        v = C.ask("Choice", "1", choices=["0", "1", "2"])
        if v == "1":
            res = baseline.drift_module(ctx, self.app.paths)
            C.out(res.summary)
            for t in res.tables:
                C.table(t.headers, t.rows)
        elif v == "2":
            baseline.save(ctx, self.app.paths)
            self.app.audit("baseline-save")
            C.out(C.c("Baseline saved.", "green"))

    def opt_15(self):
        from lshme.engine import maintenance
        rec = maintenance.active(self.app.paths)
        if rec:
            C.out("Active until %s: %s" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(rec["until"])), rec["reason"]))
            if C.yes("End maintenance now", False):
                maintenance.stop(self.app.paths)
                self.app.audit("maintenance-off")
        else:
            mins = int(C.ask("Duration in minutes", "120", validate=C.num_validator(1, 100000)))
            reason = C.ask("Reason", "planned maintenance")
            maintenance.start(self.app.paths, mins, reason)
            self.app.audit("maintenance-on", minutes=mins, reason=reason)
            C.out(C.c("Maintenance mode on.", "green"))

    def opt_16(self):
        from lshme.ops import fleet
        from lshme.ui.wizard import Wizard
        svs = fleet.servers(self.app.cfg)
        C.table(["Name", "Host", "User", "Group", "Environment"], [[s.name, s.host, s.user, s.group, s.environment] for s in svs])
        C.out("\n  [1] Collect from all servers and build fleet report   [2] Add a server   [0] Back")
        v = C.ask("Choice", "0", choices=["0", "1", "2"])
        if v == "1":
            prog = lambda n, e: C.out("  %-28s %s" % (n, C.c("FAILED " + e[:80], "red") if e else C.c("ok", "green")))
            runs, errors, files, notes, _ = self.app.fleet_run(progress=prog)
            for f in files:
                C.out("  " + f)
        elif v == "2":
            Wizard(self.app).step_fleet()

    def opt_17(self):
        from lshme.ui.wizard import Wizard
        w = Wizard(self.app)
        C.out("  [1] Full setup wizard  [2] Thresholds  [3] Critical services  [4] Databases  [5] Report settings\n"
              "  [6] Backup jobs  [7] Back up configuration  [8] Validate configuration  [0] Back")
        v = C.ask("Choice", "0", choices=[str(i) for i in range(9)])
        {"1": w.run, "2": w.step_thresholds, "3": w.step_services, "4": w.step_databases, "5": w.step_reports,
         "6": w.step_backup}.get(v, lambda: None)()
        if v == "7":
            from lshme.core.config import backup_config
            C.out("Backup: %s" % backup_config(self.app.paths.config_dir, self.app.paths.config_backup_dir, "manual"))
        elif v == "8":
            from lshme.ui.cli import main
            main(["--config-dir", self.app.paths.config_dir, "config", "validate"] if not self.app.home else ["--home", self.app.home, "config", "validate"])

    def opt_18(self):
        from lshme.ops import doctor
        C.table(["Area", "Check", "Status", "Detail"], doctor.run(self.app.cfg, self.app.paths, self.app.runner))

    def opt_19(self):
        files = sorted(f for f in os.listdir(self.app.paths.log_dir) if f.endswith(".log"))
        for i, f in enumerate(files, 1):
            C.out("  [%d] %s" % (i, f))
        if not files:
            return
        v = C.ask("Log number", str(len(files)), validate=C.num_validator(1, len(files)))
        path = os.path.join(self.app.paths.log_dir, files[int(v) - 1])
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-60:]
        C.out("".join(lines))
