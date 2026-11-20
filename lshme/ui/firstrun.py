# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""First-time setup: a few pages, one at a time, in the style of nmtui.

Welcome > system scan > organisation > email > alert levels > services to watch > databases > schedules >
real-time alerts > review and finish. Every page can be skipped and everything can be changed later under
Configuration. The scan decides which modules apply to this server; the rest are left out of scans and reports."""
import os
import time

from lshme.core.config import as_list
from lshme.core.context import Context
from lshme.ops import discover
from lshme.ui import tui
from lshme.ui.tui_kit import Field, Heading, wrap

MARKER = "setup-complete"


def marker_path(app):
    return os.path.join(app.paths.data_dir, MARKER)


def needed(app):
    """True until the setup has been finished (or skipped for good) once."""
    return not os.path.exists(marker_path(app))


def mark_done(app):
    try:
        os.makedirs(app.paths.data_dir, exist_ok=True)
        with open(marker_path(app), "w", encoding="utf-8") as f:
            f.write("completed %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    except OSError:
        pass


def info_page(ctx, title, lines, buttons=("Back", "Next"), default=None):
    """A page of text with buttons. Returns the label pressed, or 'ESC'."""
    ui = ctx.ui
    focus = len(buttons) - 1 if default is None else default
    top = 0
    while True:
        rows, cols = ui.size()
        ui.background("", "Left/Right choose   Enter select   Up/Down scroll   Esc leave setup")
        body = []
        for line in lines:
            body += wrap(line, 68) if line else [""]
        r = ui.window(min(rows - 3, len(body) + 5), 74, title)
        bh = r.h - 4
        top = max(0, min(top, len(body) - bh))
        for i, text in enumerate(body[top:top + bh]):
            ui.b.put(r.y + 1 + i, r.x + 2, text, "label")
        if len(body) > bh:
            ui.b.put(r.y + r.h - 1, r.x + r.w - 10, " %d/%d " % (min(len(body), top + bh), len(body)), "wtitle")
        ui.buttons(r.y + r.h - 2, r.x, r.w, list(buttons), focus)
        ui.b.refresh()
        key = ui.key()
        if key in ("LEFT", "BTAB"):
            focus = max(0, focus - 1)
        elif key in ("RIGHT", "TAB"):
            focus = min(len(buttons) - 1, focus + 1)
        elif key == "ENTER":
            return buttons[focus]
        elif key == "ESC":
            return "ESC"
        elif key == "DOWN":
            top += 1
        elif key == "UP":
            top = max(0, top - 1)


class Setup(object):
    def __init__(self, ctx, rerun=False):
        self.ctx, self.ui, self.app, self.rerun = ctx, ctx.ui, ctx.app, rerun
        self.profile = None
        self.watch = True
        self.web_on = False
        self.first_report = True
        self.steps = [("Welcome", self.welcome), ("System scan", self.scan), ("Organisation", self.organisation), ("Email", self.email),
                      ("Alert levels", self.levels), ("Services to watch", self.services), ("Databases", self.databases),
                      ("Schedules", self.schedules), ("Real-time alerts", self.realtime), ("Web interface", self.web_step),
                      ("Review and finish", self.finish)]

    # -- driver -----------------------------------------------------------------------------
    def run(self):
        ctx, ui = self.ctx, self.ui
        d = self.app.paths.config_dir
        if not os.access(d, os.W_OK):
            info_page(ctx, "Setup needs root", ["The settings are stored in %s, which you cannot change as this user." % d, "",
                                                "Run  sudo vitals  to set this server up."], buttons=("OK",))
            return False
        i, n = 0, len(self.steps)
        direction = 1
        try:
            while 0 <= i < n:
                ctx.form_opts = {"save_label": "Next", "back_label": "Back", "skip_label": "Skip", "confirm_discard": False,
                                 "title_prefix": "Setup %d of %d - " % (i + 1, n)}
                result = self.steps[i][1](i + 1, n)
                if result == "next":
                    direction, i = 1, i + 1
                elif result == "back":
                    direction, i = -1, max(0, i - 1)
                elif result == "skip":  # nothing to ask on this page: carry on in the direction we were going
                    i = max(0, i + direction)
                elif result == "done":
                    return True
                elif result == "quit":
                    if ui.confirm("Leave the setup now?\nYou can run it again with  vitals setup.", "Leave setup", False):
                        if not self.rerun:
                            mark_done(self.app)  # asked for; do not ask again at every start
                        return False
                # "stay" runs the same page again
        finally:
            ctx.form_opts = {}
        return True

    def after_form(self, saved_exit):
        return "back" if saved_exit == "back" else "next"

    # -- pages ---------------------------------------------------------------------------------
    def welcome(self, n, total):
        text = ["Welcome to LRS Vitals, the server health monitor from LRS Services.",
                "",
                "This setup takes a few minutes. It will:",
                "  - scan this server to see what is present (KVM, databases, backup products...)",
                "  - ask for your organisation name and email settings",
                "  - set the alert levels and the services to watch",
                "  - schedule daily, weekly and monthly reports, and a weekly rescan",
                "",
                "Only what is present on this server is checked and reported. Every page can be skipped, and everything can be changed later under Configuration."]
        ans = info_page(self.ctx, "Setup %d of %d - Welcome" % (n, total), text, buttons=("Leave", "Start"))
        return "next" if ans == "Start" else "quit"

    def scan(self, n, total):
        ctx, ui = self.ctx, self.ui
        if self.profile is None:
            ui.working("Scanning this server...")
            self.profile = discover.scan(Context(self.app.cfg, self.app.paths, self.app.runner))
        p, inv = self.profile, self.profile["inventory"]
        found = [m["title"] for m in p["modules"].values() if m["present"] and not m["always"]]
        absent = [m["title"] for m in p["modules"].values() if not m["present"]]
        lines = ["%s" % inv["hostname"], "%s, kernel %s" % (inv["os"], inv["kernel"]),
                 "%d CPU, %d MB RAM, %s" % (inv["cpus"], inv["memory_mb"], inv["virtualization"]),
                 "%d filesystems mounted" % len(inv["filesystems"]), "",
                 "Always checked: operating system, CPU and memory, storage, services, network, hardware, logs, security.", "",
                 "Also found and checked: %s" % (", ".join(found) or "nothing else"), "",
                 "Not found, so left out of every scan and report: %s" % (", ".join(absent) or "nothing"), "",
                 "A rescan runs every Sunday at 02:00 and picks up anything new."]
        ans = info_page(ctx, "Setup %d of %d - System scan" % (n, total), lines, buttons=("Back", "Rescan", "Use this result"), default=2)
        if ans == "Back":
            return "back"
        if ans == "ESC":
            return "quit"
        if ans == "Rescan":
            self.profile = None
            return "stay"
        try:
            discover.apply(self.app, self.profile)
        except OSError as e:
            ui.message("The result could not be stored: %s" % e, "System scan", "error")
        return "next"

    def organisation(self, n, total):
        tui.page_identity(self.ctx)
        return self.after_form(self.ctx.last_exit)

    def email(self, n, total):
        tui.page_smtp(self.ctx)
        return self.after_form(self.ctx.last_exit)

    def levels(self, n, total):
        ctx = self.ctx
        b = lambda label, sec, opt, hi=100: tui.bound(ctx, label, "thresholds", sec, opt, "float", lo=0, hi=hi,
                                                      help="Warning must not exceed critical.")
        tui.settings(ctx, "Alert levels", [
            Heading("CPU"), b("CPU warning (%)", "cpu", "warning"), b("CPU critical (%)", "cpu", "critical"),
            Heading("Memory"), b("Memory warning (%)", "memory", "warning"), b("Memory critical (%)", "memory", "critical"),
            Heading("Filesystems"), b("Space warning (%)", "disk", "warning"), b("Space critical (%)", "disk", "critical"),
            b("Inode warning (%)", "inode", "warning"), b("Inode critical (%)", "inode", "critical"),
        ], check=tui.threshold_check,
            intro="When does a value count as a warning or as critical? The defaults suit most servers; press Next to keep them.")
        return self.after_form(ctx.last_exit)

    def services(self, n, total):
        ctx = self.ctx
        from lshme.modules.services import COMMON
        current = as_list(ctx.cfg.get("services", "critical", "services", "sshd, crond"))
        running = []
        if self.app.runner.has("systemctl"):
            out = self.app.runner.run(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--no-pager", "--plain"])
            running = [l.split()[0].replace(".service", "") for l in out.lines() if l.split()]
        choices = sorted(set(current) | set(s for s in running if s in COMMON))
        f = Field("Must be running", "multi", ", ".join(current), choices=choices, bind=("services", "critical", "services"),
                  help="Tick the services whose failure must raise an alert.")
        tui.settings(ctx, "Services to watch", [f],
                     intro="These running services look important. Tick the ones that must always be running; a stopped one is reported as critical.")
        return self.after_form(ctx.last_exit)

    def databases(self, n, total):
        engines = (self.profile or {}).get("inventory", {}).get("databases", [])
        if not engines:
            return "skip"
        text = ["Databases found on this server: %s." % ", ".join(engines), "",
                "Local instances are checked automatically (Oracle with operating-system login as the instance owner, PostgreSQL as the postgres user, MySQL with root's .my.cnf).", "",
                "Choose 'Configure' to add a login or a non-standard location. You can also do this later under Configuration > Databases."]
        ans = info_page(self.ctx, "Setup %d of %d - Databases" % (n, total), text, buttons=("Back", "Configure", "Next"), default=2)
        if ans == "Back":
            return "back"
        if ans == "Configure":
            tui.page_databases(self.ctx)
            return "stay"
        return "quit" if ans == "ESC" else "next"

    def schedules(self, n, total):
        ctx = self.ctx
        sch = lambda label, name, opt, kind="text", dflt="", **kw: tui.bound(ctx, label, "schedule", "schedule:" + name, opt, kind, dflt=dflt, **kw)
        scan = lambda label, opt, kind="text", **kw: tui.bound(ctx, label, "main", "scan", opt, kind, **kw)
        days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        tui.settings(ctx, "Schedules", [
            Heading("Daily report"), sch("Run daily", "daily", "enabled", "bool"), sch("Days (mon-sat, or empty for every day)", "daily", "weekdays", validate=tui.v_weekdays),
            sch("Time (HH:MM)", "daily", "time", validate=tui.v_time),
            Heading("Weekly report"), sch("Run every week", "weekly", "enabled", "bool"), sch("Weekday", "weekly", "weekday", "choice", choices=days),
            sch("Time (HH:MM)", "weekly", "time", validate=tui.v_time),
            Heading("Monthly report"), sch("Run every month", "monthly", "enabled", "bool"),
            sch("Day of month", "monthly", "monthday", "int", lo=1, hi=28), sch("Time (HH:MM)", "monthly", "time", validate=tui.v_time),
            Heading("Server rescan - finds new software"), scan("Rescan every week", "enabled", "bool"),
            scan("Weekday", "weekday", "choice", choices=days), scan("Time (HH:MM)", "time", validate=tui.v_time),
        ], intro="When should reports be written? They are emailed if you set up email. The rescan keeps the list of what is checked up to date.")
        return self.after_form(ctx.last_exit)

    def realtime(self, n, total):
        ctx = self.ctx
        f = Field("Check every minute", "bool", "yes" if self.watch else "no",
                  help="A light watcher checks CPU, memory, disks and services every minute and sends alerts.")

        def on_save(form):
            self.watch = f.value == "yes"
            return "Choice noted"
        tui.run_form(ctx, "Real-time alerts", [f], on_save,
                     intro="Switch this on to be alerted within minutes of a problem (after two bad checks in a row, so brief spikes stay quiet). It uses very little CPU and memory.")
        return self.after_form(ctx.last_exit)

    def web_step(self, n, total):
        from lshme.web import auth
        ctx, app = self.ctx, self.app
        users = auth.Users(app)
        cfg = app.cfg
        enabled = cfg.getbool("web", "web", "enabled", False)
        loopback = cfg.get("web", "web", "bind", "127.0.0.1") in ("127.0.0.1", "::1", "localhost")
        mode = Field("Browser access", "choice", ("local" if loopback else "network") if enabled else "off",
                     choices=[("off", "Off"), ("local", "On - this server only"), ("network", "On - from the network (HTTPS)")],
                     help="The same pages, in a web browser, with a sign-in. 'This server only' uses an SSH tunnel.")
        pw = Field("Administrator password", "text", "", secret=True, has_value=bool(users.names()), help="10+ characters with upper, lower case and a digit.")
        pw2 = Field("Repeat password", "text", "", secret=True, help="Type it again.")

        def on_save(form):
            if mode.value == "off":
                self.web_on = False
                return "The web interface stays off"
            if pw.value or not users.names():
                problem = auth.password_problem(pw.value) if pw.value else "set a password for the administrator"
                if problem:
                    raise ValueError("Password: " + problem)
                if pw.value != pw2.value:
                    raise ValueError("The two passwords are not the same.")
                users.set_password("admin", pw.value, None if "admin" in users.names() else "admin")
            c = app.cfg
            c.set("web", "web", "enabled", "yes")
            c.set("web", "web", "bind", "127.0.0.1" if mode.value == "local" else "0.0.0.0")
            c.set("web", "web", "tls", "no" if mode.value == "local" else "yes")
            self.web_on = True
            return tui.persist(ctx, ["web"])
        tui.run_form(ctx, "Web interface", [mode, pw, pw2], on_save,
                     intro="Do you want to use these pages in a web browser too? Everything can be viewed and changed there, after signing in.")
        return self.after_form(ctx.last_exit)

    def finish(self, n, total):
        ctx, ui, app = self.ctx, self.ui, self.app
        lines = ["Ready to finish. This will:", "",
                 "  - save your settings (already written to %s)" % app.paths.config_dir,
                 "  - install the report timers and the weekly server rescan",
                 "  - %s the real-time monitor" % ("install and start" if self.watch else "NOT install"),
                 "  - %s the web interface" % ("start" if self.web_on else "NOT start"),
                 "  - run a first health check so you have a report to look at", "",
                 "Afterwards, run  vitals  any time to see live graphs, reports and settings."]
        ans = info_page(ctx, "Setup %d of %d - Review and finish" % (n, total), lines, buttons=("Back", "Finish"))
        if ans == "Back":
            return "back"
        if ans == "ESC":
            return "quit"
        self.apply_actions()
        return "done"

    # -- finish -------------------------------------------------------------------------------
    def apply_actions(self):
        ctx, ui, app = self.ctx, self.ui, self.app
        from lshme.ops import scheduler, watch
        notes = []
        ui.working("Installing the report timers...")
        rep = scheduler.install(app.cfg, app.runner, app.launcher(), app.global_args())
        app.audit("schedule-install", installed=rep["installed"], errors=rep["errors"])
        notes += ["Installed: %s" % i for i in rep["installed"]] + ["Problem: %s" % e for e in rep["errors"]]
        if self.watch:
            ui.working("Starting the real-time monitor...")
            try:
                how, path, ok, err = watch.install(app)
                notes.append("Real-time monitor installed (%s)" % how if ok else "Problem with the real-time monitor: %s" % err)
            except OSError as e:
                notes.append("Problem with the real-time monitor: %s" % e)
        if self.web_on:
            from lshme.ops import webservice
            ui.working("Starting the web interface...")
            ok, message = webservice.install(app)
            notes.append(message if ok else "Problem with the web interface: %s" % message)
        if not self.rerun:
            mark_done(app)
        ui.message("\n".join(notes) or "Done.", "Setup complete", "error" if any(x.startswith("Problem") for x in notes) else "info")
        if ui.confirm("Run the first health check now?\nIt usually takes under a minute.", "First health check", True):
            from lshme.ui import home
            home.run_now(ctx)


def run(ctx, rerun=False):
    return Setup(ctx, rerun).run()
