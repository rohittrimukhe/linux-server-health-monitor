# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Command-line interface. Run without a command on a terminal to open the menu."""
import argparse
import json
import os
import sys
import time

from lshme import APP_NAME, COPYRIGHT, VERSION
from lshme.core.lock import LockBusy
from lshme.ui import console as C

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def progress_printer(quiet):
    if quiet:
        return None

    def p(i, n, title):
        if i < n:
            sys.stdout.write("\r  [%2d/%2d] %-40s" % (i + 1, n, title[:40]))
        else:
            sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()
    return p


def print_run(run, result=None, show_info=False):
    h = run.health
    C.out()
    C.out(C.c(" %s " % run.fqdn, "title") + "  " + C.c("%s  %s/100" % (h["status"], h["score"]), h["status"]))
    c = h.get("counts", {})
    C.out("  critical %d · warning %d · unknown %d · info %d   (%s, %d day window)" % (
        c.get("CRITICAL", 0), c.get("WARNING", 0), c.get("UNKNOWN", 0), c.get("INFO", 0),
        time.strftime("%Y-%m-%d %H:%M", time.localtime(run.collected_at)), run.days))
    if run.maintenance:
        C.out(C.c("  Maintenance window active: %s" % run.maintenance.get("reason", ""), "yellow"))
    C.out()
    C.table(["Module", "Score", "Status", "Crit", "Warn", "Summary"],
            [[m["title"], "" if m["score"] is None else m["score"],
              "SKIPPED" if m["score"] is None else {"CRITICAL": "CRITICAL", "WARNING": "WARNING"}.get(m["worst"], "OK"),
              m["critical"], m["warning"], (run.module(m["module"]).summary if run.module(m["module"]) else "")]
             for m in h.get("modules", [])])
    fs = run.findings("INFO" if show_info else "UNKNOWN")
    if fs:
        C.out()
        C.out(C.c("Findings", "bold"))
        for f in fs:
            C.out("  %s %s  %s" % (C.c("%-8s" % f.severity, f.severity), C.c(f.component[:40], "bold"), f.message[:200]))
            if f.recommendation and f.severity in ("CRITICAL", "WARNING"):
                C.out(C.c("           -> %s" % f.recommendation[:200], "dim"))
    if result:
        if result.get("files"):
            C.out()
            C.out(C.c("Reports", "bold") + "  " + result.get("report_dir", ""))
            for f in result["files"]:
                C.out("  " + os.path.basename(f))
        for n in result.get("notes", []):
            C.out(C.c("  note: " + n, "yellow"))
        e = result.get("email")
        if e:
            if e.get("ok"):
                C.out(C.c("  Email sent to %s (%d attempt(s))" % (", ".join(e["recipients"]), e["attempts"]), "green"))
            elif e.get("ok") is None:
                C.out(C.c("  Email not sent: %s" % e.get("skipped"), "dim"))
            else:
                C.out(C.c("  Email FAILED: %s" % e.get("error"), "red"))


class RunLockWatch(object):
    """Keep overlapping 'watch once' runs (cron every minute) from piling up."""

    def __init__(self, app):
        from lshme.core.lock import RunLock
        self.lock = RunLock(os.path.join(app.paths.data_dir, "watch.lock"))

    def __enter__(self):
        return self.lock.__enter__()

    def __exit__(self, *a):
        return self.lock.__exit__(*a)


def build_parser():
    p = argparse.ArgumentParser(prog="healthcheck", description="%s v%s — read-only Linux health monitoring, reporting and alerting." % (APP_NAME, VERSION),
                                epilog=COPYRIGHT)
    p.add_argument("--config-dir", help="configuration directory (default /etc/lshme or ./config)")
    p.add_argument("--home", help="portable mode: keep config, data, reports and logs under this directory")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--app-dir", default=APP_DIR, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="run a health check, write reports and optionally email them")
    r.add_argument("--modules", help="comma-separated modules (default: all enabled)")
    r.add_argument("--days", type=int, default=None, help="days of logs/history the report covers (default 1)")
    r.add_argument("--formats", help="html,pdf,xlsx,pptx,txt,csv,json or all")
    r.add_argument("--report-type", choices=("executive", "technical", "full"))
    r.add_argument("--email", action="store_true", help="email reports according to send_policy")
    r.add_argument("--force-email", action="store_true", help="email even if policy/maintenance would suppress it")
    r.add_argument("--to", help="override recipients (comma-separated)")
    r.add_argument("--schedule", help="use the settings of a configured schedule")
    r.add_argument("--no-report", action="store_true", help="do not write report files")
    r.add_argument("--retries", type=int, default=0, help="retry the run this many times if it errors")
    r.add_argument("--quiet", "-q", action="store_true")
    r.add_argument("--all-findings", action="store_true", help="also print INFO findings")
    r.add_argument("--exit-status", action="store_true", help="exit 0/1/2 for healthy/warning/critical")

    c = sub.add_parser("check", help="quick on-screen check of one or more modules (no reports)")
    c.add_argument("modules", help="module name(s), comma-separated; see 'healthcheck modules'")
    c.add_argument("--days", type=int, default=1)
    c.add_argument("--details", action="store_true", help="print every table")

    a = sub.add_parser("agent", help="print the run as JSON (used by central/fleet collection)")
    a.add_argument("--days", type=int, default=1)
    a.add_argument("--modules")

    sub.add_parser("modules", help="list monitoring modules")
    sub.add_parser("setup", help="interactive configuration wizard")
    sub.add_parser("menu", help="interactive DOS-style menu")
    sub.add_parser("doctor", help="self-diagnostics: dependencies, permissions, config")
    sub.add_parser("version")
    sub.add_parser("email-test", help="send a test email using smtp.conf")

    w = sub.add_parser("watch", help="real-time monitoring with alerts (lightweight, runs continuously)")
    w.add_argument("action", nargs="?", default="status", choices=("run", "once", "install", "remove", "status"))
    w.add_argument("--interval", type=int, help="seconds between checks (default from main.conf [watch])")

    x = sub.add_parser("exclude", help="exclusions: accept known findings so they stop affecting score and alerts")
    x.add_argument("action", nargs="?", default="list", choices=("list", "add", "remove"))
    x.add_argument("name", nargs="?", help="rule name (add/remove)")
    x.add_argument("--code", help="finding code, wildcards allowed (e.g. COCKPIT_SELF_SIGNED, ORACLE_INVALID_*)")
    x.add_argument("--component", default="*", help="component glob(s), comma-separated (e.g. /mnt/backup*, oracle:ORCL*)")
    x.add_argument("--module", default="", help="limit to a module (see 'healthcheck modules')")
    x.add_argument("--hosts", default="*", help="host glob(s) for fleet use, comma-separated")
    x.add_argument("--reason", help="why this is accepted (shown in reports)")
    x.add_argument("--owner", default="", help="who approved it")
    x.add_argument("--until", default="", help="expiry date YYYY-MM-DD (blank = no expiry)")

    iv = sub.add_parser("inventory", help="asset inventory (hardware, OS, packages, databases, VMs, network) for CMDB/audits")
    iv.add_argument("--json", action="store_true", help="print this server's inventory as JSON (used by fleet collection)")
    iv.add_argument("--fleet", action="store_true", help="collect from every server in servers.conf over SSH")
    iv.add_argument("--servers", help="with --fleet: limit to these server names")
    iv.add_argument("--formats", default="xlsx,csv,json", help="xlsx,csv,json,html,pdf,txt")

    db = sub.add_parser("dashboard", help="fleet web dashboard (self-contained HTML, optional read-only web server)")
    db.add_argument("action", nargs="?", default="build", choices=("build", "serve"))
    db.add_argument("--bind", default="127.0.0.1", help="serve: address to listen on (default 127.0.0.1)")
    db.add_argument("--port", type=int, default=8088)

    lv = sub.add_parser("live", help="live graphical monitoring page in a browser (for servers without Cockpit)")
    lv.add_argument("--bind", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    lv.add_argument("--port", type=int, default=8089)

    cp = sub.add_parser("cockpit-page", help="install/remove the 'LSHME Health' page in Cockpit")
    cp.add_argument("action", choices=("install", "remove"))
    cp.add_argument("--user", action="store_true", help="install for the current user only (~/.local/share/cockpit)")

    sl = sub.add_parser("sla", help="availability/SLA report from the real-time monitoring incident log")
    sl.add_argument("--month", help="YYYY-MM (default: current month to date)")
    sl.add_argument("--formats", default="html,pdf,xlsx")

    s = sub.add_parser("schedule", help="manage scheduled runs")
    s.add_argument("action", choices=("status", "install", "remove", "show"))

    rt = sub.add_parser("retention", help="report/log retention")
    rt.add_argument("action", choices=("preview", "apply", "space"),
                    help="preview/apply all rules; 'space' = only space-based cleanup (add --apply to execute)")
    rt.add_argument("--apply", action="store_true", help="with 'space': delete instead of preview")

    h = sub.add_parser("history", help="previous runs and score trend")
    h.add_argument("--days", type=int, default=30)
    h.add_argument("--host")
    h.add_argument("--deliveries", action="store_true", help="show email delivery history")

    b = sub.add_parser("baseline", help="configuration baseline / drift")
    b.add_argument("action", choices=("save", "compare", "show"))

    m = sub.add_parser("maintenance", help="maintenance window (suppresses alert emails)")
    m.add_argument("action", choices=("on", "off", "status"))
    m.add_argument("--minutes", type=int, default=120)
    m.add_argument("--reason", default="planned maintenance")

    f = sub.add_parser("fleet", help="central multi-server monitoring")
    f.add_argument("action", choices=("run", "list", "import"))
    f.add_argument("--servers", help="limit to these server names")
    f.add_argument("--formats")
    f.add_argument("--report-type", default="technical", choices=("executive", "technical", "full"))
    f.add_argument("--email", action="store_true")
    f.add_argument("--days", type=int, default=1)
    f.add_argument("--dir", help="for import: directory of agent JSON files")

    cf = sub.add_parser("config", help="configuration utilities")
    cf.add_argument("action", choices=("show", "validate", "backup", "restore", "set", "path"))
    cf.add_argument("args", nargs="*", help="restore: ARCHIVE | set: FILE SECTION KEY VALUE")

    n = sub.add_parser("nagios", help="Nagios/Icinga plugin output and exit code")
    n.add_argument("--modules")
    n.add_argument("--days", type=int, default=1)

    e = sub.add_parser("export", help="export metrics")
    e.add_argument("format", choices=("prometheus", "json"))
    e.add_argument("--output", help="file path (default stdout)")
    e.add_argument("--modules")

    rp = sub.add_parser("report", help="re-render reports from a saved JSON run")
    rp.add_argument("json_file")
    rp.add_argument("--formats", default="html,pdf")
    rp.add_argument("--report-type", default="technical", choices=("executive", "technical", "full"))
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.no_color:
        C.disable_color()
    from lshme.app import App
    try:
        app = App(args.app_dir, args.config_dir, args.home, args.verbose)
    except OSError as e:
        C.out(C.c("Cannot initialise directories: %s (run as root or use --home DIR)" % e, "red"))
        return 3
    cmd = args.cmd
    if cmd is None:
        if sys.stdin.isatty():
            cmd = "menu"
        else:
            parser.print_help()
            return 0
    try:
        return dispatch(app, cmd, args) or 0
    except LockBusy as e:
        C.out(C.c(str(e), "yellow"))
        return 75
    except KeyboardInterrupt:
        C.out("\ninterrupted")
        return 130
    except ValueError as e:
        C.out(C.c("Error: %s" % e, "red"))
        return 2


def dispatch(app, cmd, args):
    from lshme import modules as modreg
    from lshme.engine import baseline, maintenance
    from lshme.engine.history import History
    from lshme.ops import doctor, exporters, retention, scheduler
    from lshme.core.config import backup_config, restore_config

    if cmd == "version":
        C.out("%s %s" % (APP_NAME, VERSION))
        C.out(COPYRIGHT)
    elif cmd == "menu":
        from lshme.ui.menu import Menu
        Menu(app).loop()
    elif cmd == "setup":
        from lshme.ui.wizard import Wizard
        Wizard(app).run()
    elif cmd == "modules":
        C.table(["Module", "Title", "Weight"], [[m.NAME, m.TITLE, m.WEIGHT] for m in modreg.all_modules()])
    elif cmd == "run":
        attempts = max(0, args.retries) + 1
        delay = app.cfg.getint("schedule", "scheduler", "retry_delay", 300)
        for attempt in range(1, attempts + 1):
            try:
                run, result = app.full_run(args.modules, args.days or 1, args.formats, args.report_type,
                                           True if (args.email or args.force_email) else None, args.schedule,
                                           args.force_email, not args.no_report, progress_printer(args.quiet),
                                           to=[x.strip() for x in args.to.split(",")] if args.to else None)
                break
            except LockBusy:
                raise
            except ValueError:
                raise
            except Exception as e:
                app.log.exception("run attempt %d failed", attempt)
                if attempt >= attempts:
                    C.out(C.c("Run failed: %s" % e, "red"))
                    return 3
                time.sleep(delay)
        if not args.quiet:
            print_run(run, result, args.all_findings)
        if args.exit_status:
            return {"HEALTHY": 0, "WARNING": 1, "CRITICAL": 2}.get(run.health["status"], 3)
    elif cmd == "check":
        run = app.check(args.modules, args.days, "adhoc", save_history=False, progress=progress_printer(False))
        print_run(run, None, True)
        if args.details:
            for m in run.modules:
                for t in m.tables:
                    C.out()
                    C.out(C.c("%s — %s" % (m.title, t.title), "cyan"))
                    C.table(t.headers, t.rows)
    elif cmd == "agent":
        run = app.check(args.modules, args.days, "agent", save_history=True)
        from lshme.core.util import redact_obj
        sys.stdout.write(json.dumps(redact_obj(run.to_dict()), default=str) + "\n")
    elif cmd == "doctor":
        rows = doctor.run(app.cfg, app.paths, app.runner)
        C.table(["Area", "Check", "Status", "Detail"], rows)
        return 1 if any(r[2] == "CRITICAL" for r in rows) else 0
    elif cmd == "email-test":
        from lshme.delivery import mailer
        res = mailer.send_test(mailer.MailSettings(app.cfg), app.paths.log_dir)
        if res["ok"]:
            C.out(C.c("Test email sent to %s" % ", ".join(res["recipients"]), "green"))
        else:
            C.out(C.c("Test email failed: %s" % res["error"], "red"))
            return 1
    elif cmd == "watch":
        from lshme.ops import watch
        if args.interval:
            app.cfg.set("main", "watch", "interval", str(args.interval))
        if args.action == "run":
            return watch.Watcher(app).run_forever()
        if args.action == "once":
            watch.lower_priority()
            with RunLockWatch(app):
                alerts, recovered, delivery = watch.Watcher(app).tick()
            for a in alerts:
                C.out("%s %s: %s" % (C.c("%-8s" % a["severity"], a["severity"]), a["component"], a["message"]))
            for r in recovered:
                C.out("%s %s: %s" % (C.c(r["kind"], "green"), r["component"], r["message"]))
            if delivery:
                C.out("alert delivery: %s" % delivery)
        elif args.action == "install":
            mode, path, ok, err = watch.install(app)
            app.audit("watch-install", mode=mode, path=path)
            C.out(C.c("Real-time monitoring installed (%s): %s" % (mode, path), "green") if ok else C.c("Install failed: %s" % err, "red"))
            return 0 if ok else 1
        elif args.action == "remove":
            removed = watch.uninstall(app)
            app.audit("watch-remove", removed=removed)
            C.out("Removed: %s" % (", ".join(removed) or "nothing"))
        else:
            w = watch.Watcher(app)
            st = w.state
            if not st.get("last_tick"):
                C.out("Real-time monitoring has not run yet. Start it with: healthcheck watch install")
                return 0
            C.out("Last check: %s   checks: %s   last tick CPU: %s ms   memory: %s MB" % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["last_tick"])), st.get("ticks"), st.get("last_tick_cpu_ms"), st.get("rss_mb")))
            probs = st.get("problems", {})
            C.table(["Severity", "Component", "Problem", "Since", "Alerted"],
                    [[p["severity"], p.get("component", ""), p.get("message", ""), time.strftime("%m-%d %H:%M", time.localtime(p["since"])),
                      p.get("alerted") or "pending"] for p in sorted(probs.values(), key=lambda x: x["since"])] or [["OK", "", "no active problems", "", ""]])
    elif cmd == "exclude":
        from lshme.engine import exclusions
        if args.action == "list":
            now = time.time()
            rows = []
            for r in exclusions.load(app.cfg):
                st = "disabled" if not r.enabled else ("INVALID: " + "; ".join(r.validate())) if r.validate() else "EXPIRED" if r.expired(now) else "active"
                rows.append([r.name, r.code, r.component, r.module or "*", ",".join(r.hosts), r.until or "never", st, r.reason])
            C.table(["Rule", "Code", "Component", "Module", "Hosts", "Until", "Status", "Reason"], rows or [["(none)", "", "", "", "", "", "", ""]])
        elif args.action == "add":
            import re as _re
            if not args.name or not _re.match(r"^[\w.-]+$", args.name):
                raise ValueError("exclude add NAME --code CODE --reason TEXT  (NAME: letters, digits, . _ -)")
            sec = "exclude:" + args.name
            fields = {"code": args.code or "", "component": args.component, "module": args.module, "hosts": args.hosts,
                      "reason": args.reason or "", "owner": args.owner, "until": args.until}
            problems = exclusions.Rule(args.name, fields).validate()
            if problems:
                raise ValueError("; ".join(problems))
            for k, v in fields.items():
                if v:
                    app.cfg.set("exclusions", sec, k, v)
            app.save_config("exclusions")
            app.audit("exclusion-add", rule=args.name, **fields)
            C.out(C.c("Exclusion '%s' added: %s on %s (%s)" % (args.name, args.code, args.component, args.until or "no expiry"), "green"))
        else:
            if not args.name or ("exclude:" + args.name) not in app.cfg.parsers["exclusions"].sections():
                raise ValueError("no exclusion named %s" % args.name)
            app.cfg.remove_section("exclusions", "exclude:" + args.name)
            app.save_config("exclusions")
            app.audit("exclusion-remove", rule=args.name)
            C.out("Exclusion '%s' removed" % args.name)
    elif cmd == "inventory":
        from lshme.ops import inventory
        if args.json:
            sys.stdout.write(json.dumps(inventory.collect(app.runner)) + "\n")
            return 0
        fm = [f.strip() for f in args.formats.split(",") if f.strip()]
        bad = [f for f in fm if f not in ("xlsx", "csv", "json", "html", "pdf", "txt")]
        if bad:
            raise ValueError("unknown inventory format(s): %s" % ", ".join(bad))
        inv, files, errors = inventory.export(app, fm, args.fleet, args.servers.split(",") if args.servers else None)
        C.table(["Sheet", "Records"], [[s, len(inv.get(s, []))] for s in inventory.SHEETS])
        for host, err in errors.items():
            C.out(C.c("  %s: %s" % (host, err), "red"))
        for f in files:
            C.out("  " + f)
    elif cmd == "dashboard":
        from lshme.ops import dashboard
        path, n, counts = dashboard.build(app.cfg, app.paths)
        C.out("Dashboard: %s (%d servers: %s)" % (path, n, ", ".join("%s %d" % kv for kv in sorted(counts.items())) or "none"))
        if args.action == "serve":
            from lshme.core.priority import set_priority
            set_priority(19, True)
            httpd = dashboard.serve(os.path.dirname(path), args.bind, args.port)
            C.out("Serving read-only on http://%s:%d/  (Ctrl+C to stop)" % (args.bind, args.port))
            if args.bind not in ("127.0.0.1", "localhost", "::1"):
                C.out(C.c("Note: the dashboard has no login; restrict access with a firewall or a reverse proxy.", "yellow"))
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                httpd.server_close()
    elif cmd == "live":
        from lshme.core.priority import set_priority
        from lshme.ops import live
        set_priority(19, True)
        httpd = live.make_server(app.paths.data_dir, args.bind, args.port)
        C.out("Live monitoring page: http://%s:%d/   (Ctrl+C to stop)" % (args.bind, httpd.server_address[1]))
        if not os.path.exists(os.path.join(app.paths.data_dir, "watch_metrics.json")):
            C.out(C.c("Graphs fill in once real-time monitoring runs: healthcheck watch install", "yellow"))
        if args.bind not in ("127.0.0.1", "localhost", "::1"):
            C.out(C.c("Note: the page has no login; restrict access with a firewall, an SSH tunnel or a reverse proxy.", "yellow"))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
    elif cmd == "cockpit-page":
        from lshme.ops import cockpit_page
        if args.action == "install":
            dest = cockpit_page.install(app, args.user)
            app.audit("cockpit-page-install", path=dest)
            C.out(C.c("Cockpit page installed in %s. Open Cockpit and choose 'LSHME Health' in the menu." % dest, "green"))
        else:
            dest = cockpit_page.remove(args.user, owner_args=app.global_args())
            app.audit("cockpit-page-remove", path=dest)
            C.out("Removed %s" % dest if dest else "Cockpit page was not installed")
    elif cmd == "sla":
        from lshme import reports
        from lshme.core.util import fmt_duration
        from lshme.ops import sla
        result, doc, outdir, files, notes = sla.generate(app, args.month or sla.current_month(), reports.parse_formats(args.formats))
        C.out(C.c(" %s " % doc.subtitle, "title"))
        C.out("Availability %.3f%%  downtime %s  degraded %s  incidents %d (critical %d)  MTTR %s  coverage %s" % (
            result["availability"], fmt_duration(result["downtime"]), fmt_duration(result["degraded"]), len(result["incidents"]),
            result["critical_incidents"], fmt_duration(result["mttr"]) if result["mttr"] else "n/a",
            "%.1f%%" % result["coverage"] if result["coverage"] is not None else "unknown"))
        C.table(doc.overview()[1], doc.module_rows[:20])
        for f in files:
            C.out("  " + f)
        for n in notes:
            C.out(C.c("  note: " + n, "yellow"))
    elif cmd == "schedule":
        if args.action in ("status", "show"):
            be, rows = scheduler.status(app.cfg, app.runner)
            C.out("Backend: %s" % be)
            C.table(["Schedule", "Enabled", "When", "Days", "Formats", "Type", "Email", "Installed", "Next run"],
                    [[r["name"], "yes" if r["enabled"] else "no", r["when"], r["days"], r["formats"], r["report_type"],
                      "yes" if r["email"] else "no", "yes" if r["installed"] else "NO", r["next"]] for r in rows])
            if args.action == "show":
                rep = scheduler.install(app.cfg, app.runner, app.launcher(), app.global_args(), dry_run=True)
                for path, text in rep["files"].items():
                    C.out(C.c("\n# %s" % path, "cyan"))
                    C.out(text)
        elif args.action == "install":
            rep = scheduler.install(app.cfg, app.runner, app.launcher(), app.global_args())
            app.audit("schedule-install", backend=rep["backend"], installed=rep["installed"], errors=rep["errors"])
            C.out("Backend: %s" % rep["backend"])
            for i in rep["installed"]:
                C.out(C.c("  installed " + i, "green"))
            for i in rep["removed"]:
                C.out("  removed " + i)
            for e in rep["errors"]:
                C.out(C.c("  error: " + e, "red"))
            return 1 if rep["errors"] else 0
        else:
            removed = scheduler.uninstall(app.cfg, app.runner, app.global_args())
            app.audit("schedule-remove", removed=removed)
            C.out("Removed: %s" % (", ".join(removed) or "nothing"))
    elif cmd == "retention":
        acts = retention.plan(app.cfg, app.paths)
        if args.action == "space":
            acts = [a for a in acts if a.get("space") or a["action"] == "space-warning"]
            total, free = retention.disk_usage(app.paths.report_dir)
            from lshme.core.util import fmt_bytes
            C.out("Report filesystem: %.1f%% free; report directory uses %s (cap: %s)" % (
                100.0 * free / total if total else 0, fmt_bytes(retention._size(app.paths.report_dir)),
                ("%s GB" % app.cfg.get("retention", "retention", "max_report_dir_gb")) if app.cfg.getfloat("retention", "retention", "max_report_dir_gb", 0) else "none"))
            if not acts:
                C.out(C.c("No space cleanup needed.", "green"))
                return 0
            if args.apply:
                acts = retention.apply(app.cfg, app.paths, acts)
        elif args.action == "apply":
            acts = retention.apply(app.cfg, app.paths, acts)
        C.table(["Action", "Path", "Age (days)", "Size", "Reason", "Result"],
                [[a["action"], a["path"], a["age_days"] if a["age_days"] is not None else "", a["bytes"], a["reason"], a.get("result", "preview")] for a in acts])
        w = retention.free_space_warning(app.cfg, app.paths)
        if w:
            C.out(C.c(w, "yellow"))
    elif cmd == "history":
        h = History(app.paths.history_db)
        if args.deliveries:
            C.table(["Time", "Host", "Run", "OK", "Recipients", "Attempts", "Detail"],
                    [[time.strftime("%Y-%m-%d %H:%M", time.localtime(r[0])), r[1], r[2], "yes" if r[3] else "NO", r[4], r[5], r[6]] for r in h.deliveries(args.days)])
        else:
            rows = h.runs(args.host, args.days)
            C.table(["Time", "Host", "Schedule", "Score", "Status", "Crit", "Warn", "Duration s", "Reports"],
                    [[time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])), r["host"], r["schedule"], r["score"], r["status"],
                      r["critical"], r["warning"], "%.0f" % (r["duration"] or 0), r["report_dir"] or ""] for r in rows])
            if rows:
                scores = [r["score"] for r in reversed(rows) if r["score"] is not None]
                bars = "▁▂▃▄▅▆▇█"
                C.out("\nScore trend: " + "".join(bars[min(7, int(s / 12.5))] for s in scores[-60:]))
        h.close()
    elif cmd == "baseline":
        from lshme.core.context import Context
        ctx = Context(app.cfg, app.paths, app.runner)
        if args.action == "save":
            snap = baseline.save(ctx, app.paths)
            app.audit("baseline-save")
            C.out("Baseline saved: %d enabled services, %d ports, %d mounts, %d packages" % (
                len(snap["enabled_services"]), len(snap["listening_ports"]), len(snap["mounts"]), len(snap["packages"])))
        elif args.action == "show":
            b = baseline.load(app.paths)
            C.out(json.dumps(b, indent=1)[:20000] if b else "No baseline saved.")
        else:
            res = baseline.drift_module(ctx, app.paths)
            C.out(res.summary)
            for t in res.tables:
                C.table(t.headers, t.rows)
    elif cmd == "maintenance":
        if args.action == "on":
            rec = maintenance.start(app.paths, args.minutes, args.reason)
            app.audit("maintenance-on", minutes=args.minutes, reason=args.reason)
            C.out("Maintenance mode on until %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(rec["until"])))
        elif args.action == "off":
            maintenance.stop(app.paths)
            app.audit("maintenance-off")
            C.out("Maintenance mode off")
        else:
            rec = maintenance.active(app.paths)
            C.out("Active until %s (%s, by %s)" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(rec["until"])), rec["reason"], rec["by"]) if rec else "Not active")
    elif cmd == "fleet":
        from lshme.ops import fleet
        if args.action == "list":
            C.table(["Name", "Host", "User", "Port", "Group", "Environment"],
                    [[s.name, s.host, s.user, s.port, s.group, s.environment] for s in fleet.servers(app.cfg)])
            return 0
        if args.action == "import" and not args.dir:
            raise ValueError("fleet import needs --dir")
        prog = lambda name, err: C.out("  %-30s %s" % (name, C.c("FAILED: " + err[:100], "red") if err else C.c("ok", "green")))
        runs, errors, files, notes, mail = app.fleet_run(args.servers.split(",") if args.servers else None, args.formats,
                                                         args.report_type, args.email, args.days, args.dir, prog)
        C.out()
        C.table(["Server", "Status", "Score", "Crit", "Warn"],
                [[r.host, r.health.get("status"), r.health.get("score"), r.health.get("counts", {}).get("CRITICAL", 0),
                  r.health.get("counts", {}).get("WARNING", 0)] for r in runs] + [[k, "UNREACHABLE", "", "", ""] for k in errors])
        C.out("\nFleet reports:")
        for f in files:
            C.out("  " + f)
        for n in notes:
            C.out(C.c("  note: " + n, "yellow"))
        if mail:
            C.out("Email: %s" % ("sent" if mail.get("ok") else mail.get("error") or mail.get("skipped")))
    elif cmd == "config":
        if args.action == "path":
            C.out(app.paths.config_dir)
        elif args.action == "show":
            for name in sorted(app.cfg.parsers):
                if name == "credentials":
                    continue
                C.out(C.c("# %s" % app.cfg.path(name), "cyan"))
                C.out(open(app.cfg.path(name), encoding="utf-8").read() if os.path.exists(app.cfg.path(name)) else "(defaults)")
        elif args.action == "validate":
            problems = list(app.cfg.load_errors) + app.cfg.permission_problems()
            for s in scheduler.schedules(app.cfg):
                problems += ["schedule %s: %s" % (s.name, e) for e in s.validate()]
            from lshme.engine import exclusions
            for r in exclusions.load(app.cfg):
                problems += ["exclusion %s: %s" % (r.name, e) for e in r.validate()]
            try:
                modreg.resolve(app.cfg)
            except ValueError as e:
                problems.append(str(e))
            from lshme.delivery.mailer import MailSettings
            ms = MailSettings(app.cfg)
            if ms.host or ms.to:
                problems += ["smtp: " + p for p in ms.validate()]
            C.out("\n".join(problems) if problems else C.c("Configuration is valid.", "green"))
            return 1 if problems else 0
        elif args.action == "backup":
            path = backup_config(app.paths.config_dir, app.paths.config_backup_dir, "manual")
            app.audit("config-backup", path=path)
            C.out("Backup written: %s" % path)
        elif args.action == "restore":
            if not args.args:
                raise ValueError("config restore ARCHIVE")
            restore_config(args.args[0], app.paths.config_dir, app.paths.config_backup_dir)
            app.audit("config-restore", archive=args.args[0])
            C.out("Configuration restored from %s" % args.args[0])
        elif args.action == "set":
            if len(args.args) != 4:
                raise ValueError("config set FILE SECTION KEY VALUE")
            name, section, key, value = args.args
            if name.endswith(".conf"):
                name = name[:-5]  # accept "main.conf" as well as "main"
            if name not in app.cfg.parsers:
                raise ValueError("unknown config file %s" % name)
            app.cfg.set(name, section, key, value)
            app.save_config(name)
            C.out("%s.conf [%s] %s = %s" % (name, section, key, "******" if name == "credentials" else value))
    elif cmd == "nagios":
        run = app.check(args.modules, args.days, "nagios", save_history=False)
        code, text = exporters.nagios(run)
        C.out(text)
        return code
    elif cmd == "export":
        run = app.check(args.modules, 1, "export", save_history=False)
        text = exporters.prometheus(run) if args.format == "prometheus" else json.dumps(run.to_dict(), default=str)
        if args.output:
            if args.format == "prometheus":
                exporters.write_prometheus(run, args.output)
            else:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(text)
            C.out("written %s" % args.output)
        else:
            sys.stdout.write(text)
    elif cmd == "report":
        from lshme import reports
        from lshme.core.model import RunResult
        with open(args.json_file, encoding="utf-8") as fh:
            data = json.load(fh)
        run = RunResult.from_dict(data)
        _, outdir, files, notes = reports.generate(run, app.cfg, app.paths, reports.parse_formats(args.formats), args.report_type, "adhoc")
        for f in files:
            C.out(f)
    return 0
