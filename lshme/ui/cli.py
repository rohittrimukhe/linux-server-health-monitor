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
    r.add_argument("--report-type", choices=("daily", "weekly", "monthly", "executive", "technical", "full"))
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
    sp = sub.add_parser("setup", help="configuration: full-screen forms (like nmtui), or plain prompts with --text")
    sp.add_argument("--text", action="store_true", help="use plain text prompts instead of the full-screen forms")
    sp.add_argument("--page", help="open one screen directly (identity, modules, thresholds, services, databases, reports, "
                                   "smtp, schedules, retention, watch, backups, fleet, policy, tools, validate, backup)")
    sub.add_parser("menu", help="the classic numbered menu")
    hm = sub.add_parser("home", help="the health screen: live graphs, reports, settings (also: vitals)")
    hm.add_argument("--page", help="open one screen directly (dashboard, reports, run, scan, config, setup, help)")
    sn = sub.add_parser("scan", help="scan the server for what is present (KVM, databases, backups...) and leave out the rest")
    sn.add_argument("--apply", action="store_true", help="store the result and exclude modules with nothing to check (needs root)")
    sn.add_argument("--quiet", action="store_true", help="print nothing except problems")
    sn.add_argument("--json", action="store_true", help="print the scan result as JSON")
    wb = sub.add_parser("web", help="the web interface: start, install as a service, passwords and users")
    wb.add_argument("action", nargs="?", default="status", choices=("start", "status", "install", "remove", "passwd", "useradd", "userdel", "users", "cert", "url"))
    wb.add_argument("name", nargs="?", help="user name (passwd, useradd, userdel)")
    wb.add_argument("--bind", help="address to listen on (start; default from web.conf)")
    wb.add_argument("--port", type=int, help="port (start; default from web.conf)")
    wb.add_argument("--role", default="viewer", choices=("admin", "viewer"), help="role for useradd (default: viewer, read-only)")
    wb.add_argument("--force", action="store_true", help="cert renew: get a new certificate even if the current one is not due")
    wb.add_argument("--password-stdin", action="store_true", help="read the password from standard input instead of asking")
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
    f.add_argument("--report-type", default="technical", choices=("daily", "weekly", "monthly", "executive", "technical", "full"))
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

    from lshme.ui import archivecmd, logocmd, rawcmd, templatecmd
    archivecmd.add_parser(sub)
    rawcmd.add_parser(sub)
    logocmd.add_parser(sub)
    templatecmd.add_parser(sub)

    rp = sub.add_parser("report", help="re-render reports from a saved JSON run")
    rp.add_argument("json_file")
    rp.add_argument("--formats", default="html,pdf")
    rp.add_argument("--report-type", default="technical", choices=("daily", "weekly", "monthly", "executive", "technical", "full"))
    return p


SYSTEMHEALTH_PAGES = ("setup", "help", "dashboard", "reports")


def systemhealth_args(argv):
    """'vitals' is the same program: no command opens the health screen, and setup / help / dashboard /
    reports open that screen directly. Anything else (run, scan, ...) is passed to the normal command."""
    argv = list(argv)
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in ("--config-dir", "--home", "--app-dir") else 1
    if i >= len(argv):
        return argv + ["home"]
    if argv[i] in SYSTEMHEALTH_PAGES and argv[i + 1:] == []:
        return argv[:i] + ["home", "--page", argv[i]]
    if argv[i] == "config" and argv[i + 1:] == []:
        return argv[:i] + ["home", "--page", "config"]
    return argv


def main(argv=None):
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get("LSHME_PROG") in ("vitals", "systemhealth"):
        argv = systemhealth_args(argv)
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
            cmd = "home"
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


def read_password(args, name):
    """A new password, from stdin (--password-stdin) or asked for twice without echo."""
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    import getpass
    first = getpass.getpass("New password for %s: " % name)
    if first != getpass.getpass("Repeat the password: "):
        raise ValueError("the two passwords are not the same")
    return first


def web_command(app, args):
    from lshme.ops import webservice
    from lshme.web import auth, server
    users = auth.Users(app)
    act, name = args.action, args.name
    try:
        if act == "start":
            srv, _ = server.make_server(app, args.bind, args.port)
            host, port = srv.server_address[:2]
            C.out("LSHME web interface listening on %s:%s (%s). Press Ctrl-C to stop." % (host, port, "https" if app.cfg.getbool("web", "web", "tls", True) else "http"))
            app.audit("web-start", bind=host, port=port)
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                C.out("\nstopped")
            finally:
                srv.server_close()
        elif act == "passwd":
            name = name or "admin"
            users.set_password(name, read_password(args, name), None if name in users.names() else "admin")
            C.out(C.c("Password set for %s." % name, "green"))
        elif act == "useradd":
            if not name:
                raise ValueError("give the new user name: vitals web useradd NAME [--role admin|viewer]")
            if name in users.names():
                raise ValueError("%s already exists (use 'web passwd %s' to change the password)" % (name, name))
            users.set_password(name, read_password(args, name), args.role)
            C.out(C.c("User %s added (%s)." % (name, args.role), "green"))
        elif act == "userdel":
            users.remove(name or "")
            C.out(C.c("User %s removed." % name, "green"))
        elif act == "users":
            C.table(["User", "Role"], [[u["user"], u["role"]] for u in users.listing()])
        elif act == "install":
            if not users.names():
                raise ValueError("set a password first: vitals web passwd admin")
            app.cfg.set("web", "web", "enabled", "yes")
            app.save_config("web")
            app.reload()
            ok, message = webservice.install(app)
            C.out(C.c(message, "green" if ok else "red"))
            return 0 if ok else 1
        elif act == "remove":
            C.out("Removed the web service." if webservice.uninstall(app) else "The web service is not installed (or belongs to another installation).")
        elif act == "cert":
            from lshme.web import certs
            if name in ("status", "issue", "renew"):
                if name == "status":
                    st = certs.status(app)
                    c = st["certificate"]
                    C.table(["Item", "Value"], [["Certificate source", {"letsencrypt": "Let's Encrypt (genuine; self-signed is never used)", "self-signed": "self-signed (the default)", "custom": "your own certificate (tls_source = custom)"}[st["source"]]],
                                                ["Let's Encrypt", ("on (%s, check by %s)" % (st["directory"], st["challenge"] + (" via " + st["dns_provider"] if st["dns_provider"] else ""))) if st["enabled"] else "off"], ["Names", ", ".join(st["domains"]) or "none found"],
                                                ["Certificate", ("expires %s (%d days left)" % (time.strftime("%Y-%m-%d", time.localtime(c["expires"])), c["days_left"])) if c else "none yet"],
                                                ["Used by the web interface", "yes" if c and c["in_use"] else ("no - HTTPS waits for a genuine certificate" if st["source"] == "letsencrypt" else "no (the web interface uses its own certificate)")],
                                                ["Last error", st["last_error"] or "-"]])
                    if st["problem"]:
                        C.out(C.c(st["problem"], "yellow"))
                    return 0
                r = certs.issue(app, force=(name == "issue" or args.force), log=C.out)
                C.out(C.c(r["message"], "green" if r["ok"] else "red"))
                return 0 if r["ok"] else 1
            cert, key = server.ensure_certificate(app)
            C.out("Certificate: %s\nKey:         %s" % (cert, key))
        elif act == "url":
            C.out(webservice.url(app))
        else:
            cfg = app.cfg
            C.table(["Setting", "Value"], [["Enabled", "yes" if cfg.getbool("web", "web", "enabled", False) else "no"], ["Address", webservice.url(app)],
                                           ["Encryption (TLS)", "yes" if cfg.getbool("web", "web", "tls", True) else "no"], ["Users", ", ".join(u["user"] for u in users.listing()) or "none yet"],
                                           ["Service", webservice.state(app)]])
            if not users.names():
                C.out("\nSet an administrator password first:  vitals web passwd admin")
    except (ValueError, OSError) as e:
        C.out(C.c("Error: %s" % e, "red"))
        return 2
    return 0


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
    elif cmd == "home":
        from lshme.ui import helptext, home
        page = getattr(args, "page", None)  # plain 'healthcheck' on a terminal arrives here without the option
        ran, why = home.run(app, page)
        if not ran:
            C.out(C.c("The health screen needs an interactive terminal (%s)." % why, "yellow"))
            C.out(helptext.QUICK_START if page != "help" else os.linesep.join(helptext.full_help()))
            return 0 if page == "help" else 1
    elif cmd == "setup":
        from lshme.ui.wizard import Wizard
        if not args.text:
            from lshme.ui import tui
            ran, why = tui.run(app, args.page)
            if ran:
                return 0
            C.out(C.c("Full-screen forms are not available here (%s); using plain prompts." % why, "yellow"))
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
    elif cmd == "scan":
        from lshme.core.context import Context
        from lshme.ops import discover
        profile = discover.scan(Context(app.cfg, app.paths, app.runner))
        changes, excluded = [], discover.excluded_modules(profile)
        if args.apply:
            try:
                changes, excluded = discover.apply(app, profile)
            except OSError as e:
                C.out(C.c("Cannot store the scan: %s (run as root)" % e, "red"))
                return 1
        else:
            changes = discover.compare(discover.load_profile(app.paths), profile)
        if args.json:
            C.out(json.dumps(profile, indent=1, sort_keys=True))
        elif not args.quiet:
            inv = profile["inventory"]
            C.out("%s  |  %s  |  %d CPU, %d MB RAM  |  %s" % (inv["hostname"], inv["os"], inv["cpus"], inv["memory_mb"], inv["virtualization"]))
            C.out()
            C.table(["Area", "Status", "Why"], discover.rows(profile))
            if changes:
                C.out(C.c("\nChanged since the last scan:", "bold"))
                for line in changes:
                    C.out("  " + line)
            C.out("\n%s" % (("Applied: %d module(s) with nothing to check are left out of every run and report." % len(excluded))
                            if args.apply else "Nothing was changed. Run 'healthcheck scan --apply' (as root) to use this result."))
    elif cmd == "web":
        return web_command(app, args)
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
        if args.action in ("run", "once"):
            # one watcher per installation: a second one would send every alert twice
            try:
                lock = RunLockWatch(app).__enter__()
            except LockBusy as e:
                holder = str(e).split("(", 1)[-1].rstrip(")")
                if args.action == "once":
                    return 0  # cron tick while the previous one (or the service) is still running
                C.out(C.c("Real-time monitoring is already running (%s); not starting a second watcher." % holder, "yellow"))
                return 1
        if args.action == "run":
            try:
                return watch.Watcher(app).run_forever()
            finally:
                lock.__exit__(None, None, None)
        if args.action == "once":
            watch.lower_priority()
            try:
                alerts, recovered, delivery = watch.Watcher(app).tick()
            finally:
                lock.__exit__(None, None, None)
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
    elif cmd == "templates":
        from lshme.ui import templatecmd
        return templatecmd.run(app, args)
    elif cmd == "logo":
        from lshme.ui import logocmd
        return logocmd.run(app, args)
    elif cmd == "raw":
        from lshme.ui import rawcmd
        return rawcmd.run(app, args)
    elif cmd == "archive":
        from lshme.ui import archivecmd
        return archivecmd.run(app, args)
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
