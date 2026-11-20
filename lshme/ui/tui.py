# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Full-screen configuration (healthcheck setup), in the style of nmtui.

Every setting is edited in a form: arrow keys to move, Enter to edit, Space to switch yes/no,
F10 to save, Esc to go back. Values are validated before anything is written, saved through the same
Config class as the rest of LSHME (comments kept, 0600 files, a backup of the configuration before
every change, an audit-log entry) and take effect on the next run. Nothing here changes the system
except the explicit "Install" / "Remove" buttons for schedules and the real-time monitor.
"""
import os
import re
import sys

from lshme import APP_NAME, VERSION
from lshme import modules as modreg
from lshme import reports
from lshme.core.config import as_bool, as_list, backup_config, restore_config
from lshme.ui.tui_kit import (MIN_COLS, MIN_ROWS, UI, Field, Form, Heading, ListScreen, Menu, run_curses)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")
NAME = re.compile(r"^[\w.-]+$")
YESNO = ("yes", "no")
ENGINES = ("oracle", "mysql", "postgresql", "mongodb", "sqlserver")


# ---------------------------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------------------------
def available():
    """(True, "") when the full-screen UI can run here, else (False, reason)."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False, "not an interactive terminal"
    if os.environ.get("TERM", "dumb") in ("dumb", ""):
        return False, "TERM is not set to a terminal type"
    try:
        import curses  # noqa: F401
    except ImportError:
        return False, "the Python curses module is not installed"
    return True, ""


def run(app, page=None):
    """Open the configuration screens. Returns (True, "") when they ran, (False, reason) otherwise
    so the caller can fall back to the text wizard."""
    ok, why = available()
    if not ok:
        return False, why

    def main(backend):
        ui = UI(backend, "%s  v%s  -  Configuration" % ("LSHME", VERSION))
        start(Ctx(app, ui), page)
    run_curses(main)
    return True, ""


def start(ctx, page=None):
    """Run the main menu (also used by tests with a scripted backend)."""
    rows, cols = ctx.ui.size()
    while rows < MIN_ROWS or cols < MIN_COLS:
        ctx.ui.background("", "Esc quit")
        ctx.ui.b.put(2, 2, "Terminal too small: %dx%d (need at least %dx%d)." % (cols, rows, MIN_COLS, MIN_ROWS), "title")
        ctx.ui.b.put(3, 2, "Make the window larger.", "title")
        ctx.ui.b.refresh()
        if ctx.ui.b.key() == "ESC":
            return
        rows, cols = ctx.ui.size()
    pages = dict((key, fn) for key, _, _, fn in PAGES)
    pages.update({"validate": page_validate, "backup": page_backup})  # also reachable directly: setup --page validate
    ctx.ui.on_error = lambda e: ctx.app.log.exception("configuration screen failed")
    if page:
        if page not in pages:
            raise ValueError("unknown configuration page '%s' (choose from %s)" % (page, ", ".join(sorted(pages))))
        pages[page](ctx)
        return
    entries = [(label, help_, (lambda fn=fn: fn(ctx))) for _, label, help_, fn in PAGES]
    Menu(ctx.ui, "Main menu", entries, intro=lambda: main_intro(ctx), back="Quit").run()


class Ctx(object):
    def __init__(self, app, ui):
        self.app, self.ui = app, ui

    @property
    def cfg(self):
        return self.app.cfg  # always the current one: it is replaced after every save


def main_intro(ctx):
    d = ctx.app.paths.config_dir
    return "Settings: %s%s" % (d, "" if os.access(d, os.W_OK) else "   READ-ONLY: run as root (sudo) to save changes")


# ---------------------------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------------------------
def v_emails(v):
    return None if not v or all(EMAIL.match(a) for a in as_list(v)) else "enter email addresses separated by commas"


def v_time(v):
    return None if re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", v) else "use HH:MM, for example 07:00"


def v_name(v):
    return None if NAME.match(v or "") else "use letters, digits, '.', '_' or '-' only"


def v_abs(v):
    return None if not v or v.startswith("/") else "enter an absolute path starting with /"


def v_pair(v):
    return None if re.match(r"^\d+(\.\d+)?,\d+(\.\d+)?$", v) else "use warning,critical - for example 90,95"


def v_url(v):
    return None if not v or re.match(r"^https?://\S+$", v) else "enter a URL starting with http:// or https://"


def v_modules(v):
    names = as_list(v)
    if not names or names == ["all"]:
        return None
    bad = [n for n in names if n not in modreg.MODULE_ORDER]
    return "unknown module(s): %s" % ", ".join(bad) if bad else None


def v_required(v):
    return None if v else "a value is required"


# ---------------------------------------------------------------------------------------------
# reading and writing
# ---------------------------------------------------------------------------------------------
def bound(ctx, label, file, section, option, kind="text", dflt="", new=False, **kw):
    """A Field showing (and later writing) file/section/option."""
    raw = ctx.cfg.get(file, section, option, None)
    if raw is None:
        raw = dflt
    if kind == "bool":
        raw = "yes" if as_bool(raw, False) else "no"
    if kind == "multi" and str(raw).strip().lower() == "all":
        raw = ",".join(c[0] if isinstance(c, tuple) else c for c in kw.get("choices", []))
    if kind == "choice":  # a stored "warning" matches the choice "WARNING"
        for c in kw.get("choices", []):
            value = c[0] if isinstance(c, tuple) else c
            if value.lower() == str(raw).strip().lower():
                raw = value
    return Field(label, kind, raw, bind=(file, section, option), new=new, **kw)


def write_fields(ctx, fields):
    """Put changed values into the configuration; returns the files touched."""
    touched = []
    for f in fields:
        if not f.bind or not f.changed():
            continue
        file, section, option = f.bind
        if f.value == "" and (f.original is None or f.secret):
            continue  # a new blank value, or an unchanged password: write nothing
        ctx.cfg.set(file, section, option, f.value)
        if file not in touched:
            touched.append(file)
    return touched


def persist(ctx, files):
    """Save the named files (backup + audit) and reload; returns a message."""
    app = ctx.app
    done = []
    try:
        for name in files:
            app.save_config(name)
            done.append(name + ".conf")
    except OSError as e:
        app.reload()  # drop the values that could not be written, so memory matches the files
        raise ValueError("Cannot write the configuration: %s\n\nRun as root (sudo healthcheck setup) to change it." % e)
    app.reload()
    return "Saved %s" % ", ".join(done) if done else "No changes"


def settings(ctx, title, items, **kw):
    def on_save(form):
        return persist(ctx, write_fields(ctx, form.fields))
    return Form(ctx.ui, title, items, on_save, **kw).run()


def threshold_check(form):
    """warning must not be above critical (for *_days settings the other way round)."""
    by = {}
    for f in form.fields:
        if f.bind:
            by[(f.bind[1], f.bind[2])] = f
    for (sec, opt), w in by.items():
        if "warning" not in opt:
            continue
        c = by.get((sec, opt.replace("warning", "critical")))
        if not c or w.error() or c.error() or w.value == "" or c.value == "":
            continue
        wv, cv = float(w.value), float(c.value)
        if opt.endswith("_days"):
            if cv > wv:
                return "%s: critical (%s) must not exceed warning (%s)" % (c.label, c.value, w.value)
        elif wv > cv:
            return "%s: warning (%s) must not be above critical (%s)" % (w.label, w.value, c.value)
    return None


# ---------------------------------------------------------------------------------------------
# pages: identity, modules, thresholds
# ---------------------------------------------------------------------------------------------
def page_identity(ctx):
    b = lambda label, opt, kind="text", **kw: bound(ctx, label, "main", "general", opt, kind, **kw)
    settings(ctx, "Organisation & report identity", [
        b("Company name", "company", help="Shown on the cover of every report."),
        b("Customer name", "customer", help="Who the reports are prepared for."),
        b("Environment", "environment", help="For example Production, DR, UAT or Development."),
        b("Location / data centre", "location", help="Shown in report headers."),
        b("Prepared by", "prepared_by", help="Your name or team."),
        Field("LRS Services logo", "readonly", "built in - always on every report", help="The LRS Services logo is part of the program and cannot be removed."),
        b("Customer logo (optional)", "logo", validate=v_abs, help="Extra PNG or JPG file, shown beside the LRS logo. Full path."),
        Heading("Run limits"),
        b("Command timeout (s)", "command_timeout", "int", lo=1, hi=3600, help="Longest any single system command may run."),
        b("Whole-run timeout (s)", "run_timeout", "int", lo=30, hi=86400, help="Longest a complete health check may run."),
    ])


def page_modules(ctx):
    disabled = set(ctx.cfg.getlist("main", "modules", "disabled", ""))
    fields = []
    for m in modreg.all_modules():
        f = Field(m.TITLE, "bool", "no" if m.NAME in disabled else "yes", help="Turn off to leave '%s' out of every run." % m.TITLE)
        f.module = m.NAME
        fields.append(f)

    def on_save(form):
        ctx.cfg.set("main", "modules", "disabled", ",".join(f.module for f in form.fields if f.value == "no"))
        return persist(ctx, ["main"])
    Form(ctx.ui, "Monitoring modules", fields, on_save,
         intro="Space switches a module on or off. Modules that do not apply (no databases, no KVM, ...) are skipped automatically.").run()


THRESHOLDS = (
    ("CPU", (("CPU warning (%)", "cpu", "warning", 100), ("CPU critical (%)", "cpu", "critical", 100),
             ("I/O wait warning (%)", "cpu", "iowait_warning", 100), ("Steal warning (%)", "cpu", "steal_warning", 100))),
    ("Load per CPU core", (("Load warning", "load", "per_core_warning", None), ("Load critical", "load", "per_core_critical", None))),
    ("Memory and swap", (("Memory warning (%)", "memory", "warning", 100), ("Memory critical (%)", "memory", "critical", 100),
                         ("Swap warning (%)", "swap", "warning", 100), ("Swap critical (%)", "swap", "critical", 100))),
    ("Filesystems", (("Space warning (%)", "disk", "warning", 100), ("Space critical (%)", "disk", "critical", 100),
                     ("Inode warning (%)", "inode", "warning", 100), ("Inode critical (%)", "inode", "critical", 100),
                     ("Disk busy warning (%)", "disk_io", "util_warning", 100), ("Disk wait warning (ms)", "disk_io", "await_warning_ms", None))),
    ("Network", (("Packet loss warning (%)", "network", "packet_loss_warning", 100), ("Packet loss critical (%)", "network", "packet_loss_critical", 100),
                 ("Latency warning (ms)", "network", "latency_warning_ms", None), ("Error ratio warning (%)", "network", "error_ratio_warning", 100))),
    ("Hardware, logs, services", (("Temperature warning (C)", "hardware", "temperature_warning_c", None), ("Temperature critical (C)", "hardware", "temperature_critical_c", None),
                                  ("SSH failures warning", "logs", "ssh_fail_warning", None), ("SSH failures critical", "logs", "ssh_fail_critical", None),
                                  ("Service restarts warning", "services", "restart_warning", None),
                                  ("Mail queue warning", "mail", "queue_warning", None), ("Mail queue critical", "mail", "queue_critical", None))),
    ("Certificates and capacity forecast", (("Certificate warning (days)", "certificates", "warning_days", None), ("Certificate critical (days)", "certificates", "critical_days", None),
                                            ("Disk-full forecast warning (days)", "history", "forecast_warning_days", None), ("Disk-full forecast critical (days)", "history", "forecast_critical_days", None))),
    ("Backups", (("Default backup max age (h)", "backup", "default_max_age_hours", None),)),
    ("KVM", (("Storage pool warning (%)", "kvm", "pool_warning", 100), ("Storage pool critical (%)", "kvm", "pool_critical", 100),
             ("vCPU overcommit warning (x)", "kvm", "vcpu_overcommit_warning", None), ("Memory overcommit warning (x)", "kvm", "memory_overcommit_warning", None),
             ("Snapshot age warning (days)", "kvm", "snapshot_age_warning_days", None), ("VM CPU warning (%)", "kvm", "vm_cpu_warning", 100))),
    ("Oracle", (("Tablespace warning (%)", "oracle", "tablespace_warning", 100), ("Tablespace critical (%)", "oracle", "tablespace_critical", 100),
                ("FRA warning (%)", "oracle", "fra_warning", 100), ("FRA critical (%)", "oracle", "fra_critical", 100),
                ("Backup max age (h)", "oracle", "backup_max_age_hours", None), ("Archive-log backup max age (h)", "oracle", "archivelog_backup_max_age_hours", None),
                ("Data Guard lag warning (s)", "oracle", "dg_lag_warning_seconds", None), ("Data Guard lag critical (s)", "oracle", "dg_lag_critical_seconds", None),
                ("Invalid objects warning", "oracle", "invalid_objects_warning", None))),
    ("MySQL / MariaDB", (("Connections warning (%)", "mysql", "connections_warning", 100), ("Connections critical (%)", "mysql", "connections_critical", 100),
                         ("Replication lag warning (s)", "mysql", "replication_lag_warning", None), ("Replication lag critical (s)", "mysql", "replication_lag_critical", None))),
    ("PostgreSQL", (("Connections warning (%)", "postgresql", "connections_warning", 100), ("Connections critical (%)", "postgresql", "connections_critical", 100),
                    ("Long query (minutes)", "postgresql", "long_query_minutes", None), ("Replication lag warning (MB)", "postgresql", "replication_lag_warning_mb", None),
                    ("Replication lag critical (MB)", "postgresql", "replication_lag_critical_mb", None))),
    ("MongoDB, SQL Server", (("MongoDB connections warning (%)", "mongodb", "connections_warning", 100), ("MongoDB connections critical (%)", "mongodb", "connections_critical", 100),
                             ("SQL Server backup max age (h)", "sqlserver", "backup_max_age_hours", None))),
)


def page_thresholds(ctx):
    items = []
    for heading, rows in THRESHOLDS:
        items.append(Heading(heading))
        for label, section, option, hi in rows:
            items.append(bound(ctx, label, "thresholds", section, option, "float", lo=0, hi=hi,
                               help="Warning must not exceed critical (days: critical must not exceed warning)."))
    Form(ctx.ui, "Thresholds", items, lambda form: persist(ctx, write_fields(ctx, form.fields)), check=threshold_check,
         intro="Values at or above a warning or critical level raise a finding. Per-mount filesystem limits are under 'Filesystem overrides'.").run()


# ---------------------------------------------------------------------------------------------
# generic collection editors
# ---------------------------------------------------------------------------------------------
def kv_page(ctx, title, file, section, key_label, value_label, key_check=v_required, value_check=v_required,
            intro="", key_help="", value_help=""):
    """Edit a section whose keys are free text (ports, processes, filesystem overrides)."""
    ui = ctx.ui

    def rows():
        p = ctx.cfg.parsers[file]
        return [(k, "%s = %s" % (k, v.strip())) for k, v in (p.items(section) if p.has_section(section) else [])]

    def edit(old=None):
        parser = ctx.cfg.parsers[file]
        cur = parser.get(section, old).strip() if old else ""
        kf = Field(key_label, "text", old or "", help=key_help, validate=key_check, new=old is None)
        vf = Field(value_label, "text", cur, help=value_help, validate=value_check, new=old is None)

        def on_save(form):
            p = ctx.cfg.parsers[file]
            if old and old != kf.value and p.has_option(section, old):
                p.remove_option(section, old)
            ctx.cfg.set(file, section, kf.value, vf.value)
            return persist(ctx, [file])
        Form(ui, "Edit entry" if old else "Add entry", [kf, vf], on_save).run()

    def delete(key):
        if ui.confirm("Delete '%s'?" % key, "Delete", False):
            ctx.cfg.parsers[file].remove_option(section, key)
            try:
                ui.note(persist(ctx, [file]))
            except ValueError as e:
                ui.message(str(e), "Cannot save", "error")
    ListScreen(ui, title, rows, add=lambda: edit(None), edit=edit, delete=delete, intro=intro).run()


def section_page(ctx, title, file, prefixes, label, add, edit, can_delete=None, intro="", extra=None, empty=""):
    """Edit a collection stored as one section per item (databases, backup jobs, servers, schedules)."""
    ui = ctx.ui

    def rows():
        return [(s, label(s)) for s in ctx.cfg.sections(file) if any(s.startswith(p) for p in prefixes)]

    def delete(sec):
        if can_delete and not can_delete(sec):
            ui.message("This one is built in and cannot be removed. Edit it and switch it off instead.", "Cannot delete")
            return
        if ui.confirm("Delete '%s'?" % sec, "Delete", False):
            ctx.cfg.remove_section(file, sec)
            try:
                ui.note(persist(ctx, [file]))
            except ValueError as e:
                ui.message(str(e), "Cannot save", "error")
    kw = {"empty": empty} if empty else {}
    ListScreen(ui, title, rows, add=add, edit=edit, delete=delete, intro=intro, extra=extra, **kw).run()


def new_name(ctx, title, label, taken, default=""):
    def check(v):
        return v_name(v) or ("'%s' already exists" % v if v in taken else None)
    return ctx.ui.prompt(title, label, default, validate=check)


# ---------------------------------------------------------------------------------------------
# services, ports, processes, filesystem overrides
# ---------------------------------------------------------------------------------------------
def page_services(ctx):
    def critical():
        settings(ctx, "Critical services", [
            bound(ctx, "Critical services", "services", "critical", "services", help="Comma-separated service names that must be running, e.g. sshd, crond."),
            bound(ctx, "Ignore failed units", "services", "ignore", "failed_units", help="Comma-separated units whose failure is expected and should not be reported."),
        ])
    Menu(ctx.ui, "Services & processes", [
        ("Critical services", "Services that must be running.", critical),
        ("Expected listening ports", "Raise a finding when nothing listens on a port (for example a database listener).",
         lambda: kv_page(ctx, "Expected listening ports", "services", "ports", "Label", "Port", value_check=lambda v: None if v.isdigit() and 1 <= int(v) <= 65535 else "enter a port from 1 to 65535",
                         intro="Each entry is a label and a TCP port that must be listening.", key_help="A name such as oracle-listener.", value_help="Port number, 1 - 65535.")),
        ("Required processes", "Raise a finding when no process matches a pattern.",
         lambda: kv_page(ctx, "Required processes", "services", "processes", "Label", "Process pattern",
                         intro="Each entry is a label and a regular expression matched against the process list.", key_help="A name such as app-server.", value_help="Regular expression, e.g. java.*myapp")),
        ("Filesystem overrides", "Different disk-space limits for particular mount points.",
         lambda: kv_page(ctx, "Filesystem overrides", "thresholds", "disk_overrides", "Mount point", "Warning,critical %",
                         key_check=lambda v: None if v.startswith("/") else "a mount point starts with /", value_check=v_pair,
                         intro="Per-mount limits as 'warning,critical' percent, for example /archive = 90,97.", key_help="For example /archive.", value_help="Two numbers: 90,95")),
    ]).run()


# ---------------------------------------------------------------------------------------------
# databases
# ---------------------------------------------------------------------------------------------
DB_DEFAULTS = {
    "oracle": {"oracle_home": "/u01/app/oracle/product/19.0.0/dbhome_1", "os_user": "oracle", "backup_max_age_hours": "26"},
    "mysql": {"defaults_file": "/root/.my.cnf"},
    "postgresql": {"os_user": "postgres", "port": "5432"},
    "mongodb": {"uri": "mongodb://127.0.0.1:27017/admin"},
    "sqlserver": {"server": "localhost", "username": "lshme_monitor"},
}


def db_form(ctx, sec, new=False):
    engine, name = sec.split(":", 1)
    d = DB_DEFAULTS.get(engine, {}) if new else {}
    if new and engine == "oracle":
        d = dict(d, sid=name)

    def b(label, opt, kind="text", **kw):
        return bound(ctx, label, "database", sec, opt, kind, dflt=d.get(opt, ""), new=new, **kw)

    def pw(help_):
        return Field("Password", "text", "", secret=True, has_value=bool(ctx.cfg.secret(sec)), bind=("credentials", sec, "password"),
                     help=help_ + " Stored in credentials.conf (mode 600), never in reports.")
    if engine == "oracle":
        items = [b("ORACLE_SID", "sid", help="Instance name."), b("ORACLE_HOME", "oracle_home", validate=v_abs, help="Oracle software location."),
                 Heading("Authentication: OS owner, or a monitoring user"),
                 b("OS user (OS authentication)", "os_user", help="Instance owner: connects '/ as sysdba'. Leave the user and password blank."),
                 b("Monitoring user", "username", help="Alternative: a user with SELECT_CATALOG_ROLE."),
                 b("Connect identifier", "connect_identifier", help="TNS alias or EZConnect, e.g. //localhost:1521/ORCL."),
                 pw("Password for the monitoring user."),
                 b("Backup max age (hours)", "backup_max_age_hours", "int", lo=1, hi=8760, help="Latest successful RMAN backup must be newer than this.")]
    elif engine == "mysql":
        items = [b("Defaults file", "defaults_file", validate=v_abs, help="A my.cnf with the monitoring credentials (chmod 600)."),
                 b("Socket", "socket", validate=v_abs, help="Leave blank for the default socket.")]
    elif engine == "postgresql":
        items = [b("OS user (peer auth)", "os_user", help="Usually postgres. Leave blank when using host and password."),
                 b("Port", "port", "int", lo=1, hi=65535, help="TCP port, normally 5432.")]
    elif engine == "mongodb":
        items = [b("URI (no password)", "uri", help="For example mongodb://127.0.0.1:27017/admin"),
                 b("Username", "username", help="Leave blank for no authentication."), pw("Password for the user.")]
    else:
        items = [b("Server", "server", help="Host name or address."), b("Login", "username", help="Monitoring login."), pw("Password for the login.")]

    def on_save(form):
        return persist(ctx, write_fields(ctx, form.fields))
    Form(ctx.ui, "%s instance: %s" % (engine, name), items, on_save).run()


def page_databases(ctx):
    ui = ctx.ui

    def discovery():
        settings(ctx, "Database auto-discovery", [
            bound(ctx, "Oracle", "database", "discovery", "oracle_autodiscover", "bool", help="Find running Oracle instances automatically (OS authentication)."),
            bound(ctx, "PostgreSQL", "database", "discovery", "postgresql_autodiscover", "bool", help="Find running PostgreSQL servers."),
            bound(ctx, "MySQL / MariaDB", "database", "discovery", "mysql_autodiscover", "bool", help="Find running MySQL or MariaDB servers."),
            bound(ctx, "MongoDB", "database", "discovery", "mongodb_autodiscover", "bool", help="Find running MongoDB servers."),
        ], intro="Running databases are detected automatically. Configure an instance by hand only when it needs a login or a non-standard location.")

    def instances():
        def add():
            engine = ui.choose("Database engine", ENGINES, "oracle")
            if not engine:
                return
            taken = ctx.cfg.sections("database")
            name = ui.prompt("Instance label", "Short name, e.g. ORCL or main:", "ORCL" if engine == "oracle" else "main",
                             validate=lambda v: v_name(v) or ("already configured" if "%s:%s" % (engine, v) in taken else None))
            if name:
                db_form(ctx, "%s:%s" % (engine, name), new=True)
        section_page(ctx, "Database instances", "database", tuple(e + ":" for e in ENGINES),
                     lambda s: "%-12s %s" % (s.split(":")[0], s.split(":", 1)[1]), add, lambda s: db_form(ctx, s),
                     intro="Instances configured by hand. Running instances are also found automatically.")
    Menu(ctx.ui, "Databases", [("Auto-discovery", "Which database engines to look for automatically.", discovery),
                               ("Database instances", "Add or change an instance that needs a login or non-standard location.", instances)]).run()


# ---------------------------------------------------------------------------------------------
# reports and email
# ---------------------------------------------------------------------------------------------
def page_reports(ctx):
    b = lambda label, opt, kind="text", **kw: bound(ctx, label, "reports", "reports", opt, kind, **kw)
    settings(ctx, "Report settings", [
        b("Formats", "formats", "multi", choices=reports.FORMATS, help="Press Enter to tick the formats written on every run."),
        b("Report type", "report_type", "choice", choices=["executive", "technical", "full"], help="executive: summary; technical: findings and evidence; full: everything."),
        b("Title", "title", help="Leave blank for the standard title."),
        b("Include appendix", "include_appendix", "bool", help="Add the detailed tables at the end of reports."),
        b("Evidence length (chars)", "max_evidence_chars", "int", lo=200, hi=100000, help="How much command output to keep per finding."),
    ])


def page_smtp(ctx):
    ui = ctx.ui
    b = lambda label, opt, kind="text", **kw: bound(ctx, label, "smtp", "smtp", opt, kind, **kw)
    items = [
        b("Delivery method", "method", "choice", choices=["smtp", "sendmail"], help="smtp: connect to a mail server. sendmail: hand mail to the local MTA."),
        Heading("SMTP server (when the method is smtp)"),
        b("Server", "host", help="Host name or address of the mail server."),
        b("Encryption", "security", "choice", choices=["starttls", "ssl", "none"], help="starttls usually uses port 587, ssl port 465, none port 25."),
        b("Port", "port", "int", lo=1, hi=65535, help="TCP port of the mail server."),
        b("Username", "username", help="Leave blank when the server needs no login."),
        Field("Password", "text", "", secret=True, has_value=bool(ctx.cfg.secret("smtp")), bind=("credentials", "smtp", "password"),
              help="Kept in credentials.conf (mode 600). Leave as is to keep the saved one."),
        Heading("Addresses"),
        b("Sender (From)", "from", validate=v_emails, help="For example lshme@yourhost.example.com"),
        b("Recipients (To)", "to", validate=v_emails, help="Comma-separated addresses."),
        b("CC", "cc", validate=v_emails, help="Optional, comma-separated."),
        b("BCC", "bcc", validate=v_emails, help="Optional, comma-separated."),
        b("Extra for CRITICAL", "critical_to", validate=v_emails, help="Additional people told only when a report is CRITICAL."),
        Heading("Sending"),
        b("Send policy", "send_policy", "choice", choices=["always", "issues", "critical", "never"], help="always, issues (warnings or worse), critical (only critical), never."),
        b("Attach formats", "attach_formats", "multi", choices=reports.FORMATS, help="Which report files are attached to the email."),
        b("Max attachment size (MB)", "max_attachment_mb", "int", lo=1, hi=500, help="Total size of attachments in one email."),
        b("Subject prefix", "subject_prefix", help="For example [LSHME]."),
        b("Retries", "retries", "int", lo=0, hi=20, help="Extra delivery attempts after a failure."),
        b("Timeout (s)", "timeout", "int", lo=5, hi=600, help="How long to wait for the mail server."),
    ]

    def test_mail(form):
        if form.changed() and not form.save():
            return
        from lshme.delivery import mailer
        ui.working("Sending a test email...")
        try:
            res = mailer.send_test(mailer.MailSettings(ctx.app.cfg), ctx.app.paths.log_dir)
        except Exception as e:  # a failed test must show the reason, not crash the screen
            ui.message("Test failed: %s" % e, "Test email", "error")
            return
        if res["ok"]:
            ui.message("Test email sent to: %s" % ", ".join(res["recipients"]), "Test email")
        else:
            ui.message("Test failed: %s" % res["error"], "Test email", "error")
    settings(ctx, "Email (SMTP)", items, buttons=[("Test email", test_mail)],
             intro="Where reports and alerts are emailed. Press Test email to send a message with the values below (they are saved first).")


# ---------------------------------------------------------------------------------------------
# schedules
# ---------------------------------------------------------------------------------------------
SCHEDULE_BUILTIN = ("daily", "weekly", "monthly")


def schedule_form(ctx, name, new=False, frequency=None):
    from lshme.ops import scheduler
    sec = "schedule:" + name
    cur = ctx.cfg.section("schedule", sec)
    freq = (frequency or cur.get("frequency", "daily")).lower()
    d = {"time": "00:15", "modules": "database,storage", "days": "1", "formats": "html,json", "send_policy": "issues", "email": "yes", "enabled": "yes"} if new else {}

    absent = {"enabled": "yes", "email": "yes", "report_type": "technical", "modules": "all",  # what the scheduler
              "formats": "html,pdf,json", "days": {"hourly": "1", "daily": "1", "weekly": "7", "monthly": "30"}.get(freq, "1")}  # assumes for missing keys

    def b(label, opt, kind="text", **kw):
        return bound(ctx, label, "schedule", sec, opt, kind, dflt=d.get(opt, absent.get(opt, "")), new=new, **kw)
    items = [Field("Frequency", "readonly", freq), b("Enabled", "enabled", "bool", help="Switch off to keep the settings but stop running it.")]
    if freq == "custom":
        items.append(b("Calendar", "calendar", validate=v_required, help="systemd OnCalendar, e.g. Mon..Fri *-*-* 06:30:00 (or 5-field cron)."))
    else:
        items.append(b("Minute past the hour (HH:MM)" if freq == "hourly" else "Time (HH:MM)", "time", validate=v_time, help="Use 24-hour time, for example 07:00."))
    if freq == "weekly":
        items.append(b("Weekday", "weekday", "choice", choices=["mon", "tue", "wed", "thu", "fri", "sat", "sun"], help="Day of the week to run."))
    if freq == "monthly":
        items.append(b("Day of month", "monthday", "int", lo=1, hi=28, help="1 - 28, so it exists in every month."))
    items += [
        b("Days of data", "days", "int", lo=1, hi=3650, help="How far back logs and trends are analysed."),
        b("Modules", "modules", validate=v_modules, help="'all' or a comma list such as database,storage."),
        b("Formats", "formats", "multi", choices=reports.FORMATS, help="Report files written by this schedule."),
        b("Report type", "report_type", "choice", choices=["executive", "technical", "full"], help="executive, technical or full."),
        b("Email the report", "email", "bool", help="Send the result by email."),
        b("Recipients", "to", validate=v_emails, help="Blank uses the addresses from the Email page."),
        b("CC", "cc", validate=v_emails, help="Optional."),
        b("Send policy", "send_policy", "choice", choices=["", "always", "issues", "critical", "never"], help="Blank uses the Email page's policy."),
    ]
    if freq == "monthly":
        items += [b("Attach SLA report", "sla_report", "bool", help="Attach last month's uptime report."), b("Attach inventory", "inventory_report", "bool", help="Attach the asset inventory.")]

    def check(form):
        values = dict((f.bind[2], f.value) for f in form.fields if f.bind)
        values["frequency"] = freq
        errors = scheduler.Schedule(name, values).validate()
        return "; ".join(errors) if errors else None

    def on_save(form):
        touched = write_fields(ctx, form.fields)
        if new:
            ctx.cfg.set("schedule", sec, "frequency", freq)
            if "schedule" not in touched:
                touched.append("schedule")
        return persist(ctx, touched)
    Form(ctx.ui, "Schedule: %s" % name, items, on_save, check=check).run()


def page_schedules(ctx):
    from lshme.ops import scheduler
    ui = ctx.ui

    def label(sec):
        s = scheduler.Schedule(sec.split(":", 1)[1], ctx.cfg.section("schedule", sec))
        return "%-10s %-8s %s" % (s.name, "on" if s.enabled else "off", s.describe())

    def add():
        taken = [s.split(":", 1)[1] for s in ctx.cfg.sections("schedule", "schedule:")]
        name = new_name(ctx, "New schedule", "Name (letters, digits, - and _):", taken, "hourly-db")
        if not name:
            return
        freq = ui.choose("Frequency", ["hourly", "daily", "weekly", "monthly", "custom"], "daily")
        if freq:
            schedule_form(ctx, name, new=True, frequency=freq)

    def install(_sel):
        if not ui.confirm("Create or refresh the systemd timers (or cron entries) for the enabled schedules on this server?", "Install schedules", True):
            return
        ui.working("Installing schedules...")
        rep = scheduler.install(ctx.cfg, ctx.app.runner, ctx.app.launcher(), ctx.app.global_args())
        ctx.app.audit("schedule-install", installed=rep["installed"], errors=rep["errors"])
        lines = ["Installed: %s" % i for i in rep["installed"]] + ["Error: %s" % e for e in rep["errors"]]
        ui.message("\n".join(lines) or "Nothing to install.", "Install schedules", "error" if rep["errors"] else "info")

    section_page(ctx, "Schedules", "schedule", ("schedule:",), label, add, lambda s: schedule_form(ctx, s.split(":", 1)[1]),
                 can_delete=lambda s: s.split(":", 1)[1] not in SCHEDULE_BUILTIN,
                 extra=[("Install", install)],
                 intro="Reports run by systemd timers (or cron). Edit a schedule, add your own, then press Install to put the changes on this server.")


# ---------------------------------------------------------------------------------------------
# retention, real-time monitoring, backups, fleet, policy
# ---------------------------------------------------------------------------------------------
def page_retention(ctx):
    b = lambda label, opt, kind="int", **kw: bound(ctx, label, "retention", "retention", opt, kind, **kw)
    days = lambda label, opt, help_: b(label, opt, lo=0, hi=36500, help=help_)
    settings(ctx, "Retention & cleanup", [
        Heading("How long to keep reports (days)"),
        days("Daily reports", "daily_days", "Daily scheduled reports."), days("Weekly reports", "weekly_days", "Weekly scheduled reports."),
        days("Monthly reports", "monthly_days", "Monthly scheduled reports."), days("Manual reports", "adhoc_days", "Reports you run by hand."),
        days("Fleet reports", "fleet_days", "Multi-server reports."), days("Custom schedules", "custom_days", "Reports from your own schedules."),
        Heading("Logs and history (days)"),
        days("Execution logs", "logs_days", "Normal run logs."), days("Error logs", "error_logs_days", "Error logs."),
        days("Audit logs", "audit_logs_days", "Who changed what."), days("Email delivery log", "email_log_days", "Record of emails sent."),
        days("Historical metrics", "history_days", "Scores and trend data used for forecasts."),
        b("Config backups to keep", "config_backups_keep", lo=1, hi=1000, help="Copies of the settings taken before each change."),
        b("Compress after (days)", "compress_after_days", lo=0, hi=36500, help="Zip older reports; 0 never compresses."),
        Heading("Free space"),
        b("Space-based cleanup", "space_cleanup", "bool", help="Also delete the oldest reports when space runs low."),
        b("Start below free (%)", "min_free_percent", lo=0, hi=100, help="Cleanup begins when free space on the report disk drops below this."),
        b("Clean until free (%)", "free_target_percent", lo=0, hi=100, help="...and continues until this much is free."),
        b("Report folder cap (GB)", "max_report_dir_gb", "float", lo=0, help="0 means no cap."),
        b("Never delete newer than (days)", "space_min_keep_days", lo=0, hi=36500, help="Safety limit for space-based cleanup."),
        b("Archive folder", "archive_dir", "text", validate=v_abs, help="Move expired reports here instead of deleting. Blank deletes."),
        b("Keep latest report", "keep_latest", "bool", help="Always keep the newest report of each kind."),
        b("Apply after scheduled runs", "auto_apply", "bool", help="Run the cleanup automatically after each scheduled report."),
    ])


def page_watch(ctx):
    ui = ctx.ui
    b = lambda label, opt, kind="text", **kw: bound(ctx, label, "main", "watch", opt, kind, **kw)
    items = [
        b("Check every (seconds)", "interval", "int", lo=10, hi=3600, help="How often CPU, memory, disks and critical services are checked."),
        b("Confirm after (checks)", "confirm", "int", lo=1, hi=20, help="Checks in a row a problem must last before alerting, so spikes stay quiet."),
        b("Heavy modules every (min)", "rotation_minutes", "int", lo=1, hi=1440, help="Database, KVM, network and other heavier modules, one at a time."),
        Heading("Alerts"),
        b("Lowest severity to alert", "alert_min_severity", "choice", choices=["INFO", "WARNING", "CRITICAL"], help="Findings below this level are recorded but not sent."),
        b("Email alerts", "alert_email", "bool", help="Send alerts to the Email page's recipients."),
        b("Alert recipients", "alert_to", validate=v_emails, help="Blank uses the Email page's recipients."),
        b("Write to syslog", "syslog", "bool", help="Also log alerts to syslog."),
        b("Webhook URL", "webhook_url", validate=v_url, help="Optional: POST each alert as JSON to this URL (Teams, Slack, ticketing)."),
        b("Reminder every (min)", "repeat_minutes", "int", lo=0, hi=10080, help="Repeat an unresolved alert this often; 0 turns reminders off."),
        b("Tell me when fixed", "recovery_alerts", "bool", help="Send one message when a problem clears."),
        Heading("Limits"),
        b("Restart if memory over (MB)", "max_rss_mb", "int", lo=20, hi=4096, help="The watcher restarts itself if it grows past this."),
        b("Keep live graphs (hours)", "metrics_hours", "int", lo=1, hi=720, help="History kept for the live graphs."),
    ]

    def action(name, ask=None):
        def cb(form):
            if form.changed():
                if not ui.confirm("Save your changes first?", "Unsaved changes", True) or not form.save():
                    return
            if ask and not ui.confirm(ask, "Confirm", False):
                return
            from lshme.ui.cli import main as cli_main
            with ui.console():
                cli_main(ctx.app.global_args() + ["watch", name])
                try:
                    input("\nPress Enter to return to the configuration screens...")
                except EOFError:
                    pass
        return cb
    settings(ctx, "Real-time monitoring & alerts", items,
             buttons=[("Status", action("status")), ("Run once", action("once")),
                      ("Install", action("install", "Install and start the real-time monitor as a service on this server?")),
                      ("Remove", action("remove", "Stop and remove the real-time monitor service?"))],
             intro="A light watcher that checks the server every minute and sends alerts. Save the settings, then Install to start it.")


def job_form(ctx, sec, new=False, kind=None):
    cur = ctx.cfg.section("backup", sec)
    kind = kind or cur.get("type", "directory")
    d = {"max_age_hours": "26", "min_size_mb": "1", "pattern": "*.dmp*", "success_pattern": "completed successfully",
         "path": "/backup" if kind == "directory" else "/var/log/backup.log"} if new else {}

    def b(label, opt, k="text", **f):
        return bound(ctx, label, "backup", sec, opt, k, dflt=d.get(opt, ""), new=new, **f)
    items = [Field("Type", "readonly", kind), b("Path", "path", validate=v_abs, help="Directory holding the backup files, or the backup log file.")]
    if kind == "directory":
        items += [b("File pattern", "pattern", help="Which files count as backups, e.g. *.dmp*"), b("Min size of newest (MB)", "min_size_mb", "float", lo=0, help="A newest file smaller than this means a failed backup.")]
    else:
        items.append(b("Success line (regex)", "success_pattern", help="A line that proves the job succeeded."))
    items.append(b("Max age (hours)", "max_age_hours", "int", lo=1, hi=8760, help="The newest backup must be younger than this."))

    def on_save(form):
        touched = write_fields(ctx, form.fields)
        if new:
            ctx.cfg.set("backup", sec, "type", kind)
            if "backup" not in touched:
                touched.append("backup")
        return persist(ctx, touched)
    Form(ctx.ui, "Backup job: %s" % sec.split(":", 1)[1], items, on_save).run()


def page_backup_jobs(ctx):
    ui = ctx.ui

    def add():
        name = new_name(ctx, "New backup job", "Job name:", [s.split(":", 1)[1] for s in ctx.cfg.sections("backup", "job:")], "daily-export")
        kind = name and ui.choose("What does the job produce?", [("directory", "Files in a directory"), ("logfile", "A log file with a success line")], "directory")
        if name and kind:
            job_form(ctx, "job:" + name, new=True, kind=kind)
    section_page(ctx, "Backup jobs", "backup", ("job:",), lambda s: "%-16s %s" % (s.split(":", 1)[1], ctx.cfg.get("backup", s, "path", "")),
                 add, lambda s: job_form(ctx, s),
                 intro="Extra backups to verify by age and size. NetBackup, Backup Exec, Veeam and Oracle RMAN are checked automatically.")


def server_form(ctx, sec, new=False):
    d = {"user": ctx.cfg.get("servers", "defaults", "user", "root"), "group": "Production", "environment": "Production", "host": sec.split(":", 1)[1]} if new else {}
    b = lambda label, opt, kind="text", **kw: bound(ctx, label, "servers", sec, opt, kind, new=new, **dict({"dflt": d.get(opt, "")}, **kw))
    items = [b("Enabled", "enabled", "bool", dflt="yes", help="Switch off to skip this server in fleet runs."),
             b("Host / IP", "host", validate=v_required, help="Address the central server connects to."),
             b("SSH user", "user", help="Blank uses the default from servers.conf."),
             b("SSH port", "port", "int", lo=1, hi=65535, optional=True, help="Blank uses 22."),
             b("Identity file", "identity_file", validate=v_abs, help="SSH private key for this server. Blank uses the default."),
             b("Group", "group", help="For grouping in the fleet dashboard."), b("Environment", "environment", help="Production, DR, UAT...")]
    Form(ctx.ui, "Server: %s" % sec.split(":", 1)[1], items, lambda form: persist(ctx, write_fields(ctx, form.fields))).run()


def page_fleet(ctx):
    def add():
        name = new_name(ctx, "New server", "Server name:", [s.split(":", 1)[1] for s in ctx.cfg.sections("servers", "server:")], "db01")
        if name:
            server_form(ctx, "server:" + name, new=True)
    section_page(ctx, "Multi-server (fleet)", "servers", ("server:",),
                 lambda s: "%-18s %s" % (s.split(":", 1)[1], ctx.cfg.get("servers", s, "host", "")), add, lambda s: server_form(ctx, s),
                 intro="Servers the central host collects health data from over SSH (keys only, no passwords).")


def page_policy(ctx):
    sec = lambda label, s, opt, kind="text", **kw: bound(ctx, label, "main", s, opt, kind, **kw)
    cert = lambda label, opt, **kw: bound(ctx, label, "certificates", "certificates", opt, **kw)
    settings(ctx, "Security & network policy", [
        Heading("Security checks"),
        sec("Expected SELinux mode", "security", "expected_selinux", "choice", choices=["enforcing", "permissive", "disabled"], help="A different mode raises a finding."),
        sec("Require firewall", "security", "require_firewall", "bool", help="Raise a finding when no firewall is active."),
        sec("Allow root SSH login", "security", "permit_root_login_allowed", "bool", help="Yes: root login is accepted policy. No: a finding is raised."),
        sec("Allow SSH passwords", "security", "password_authentication_allowed", "bool", help="Yes: password login is accepted policy."),
        sec("Require auditd", "security", "require_auditd", "bool", help="Raise a finding when the audit daemon is not running."),
        sec("Check pending updates", "security", "check_updates", "bool", help="Count pending package updates (uses the package manager)."),
        Heading("Network checks"),
        sec("DNS test names", "network", "dns_test_names", help="Comma-separated names that must resolve."),
        sec("Ping targets", "network", "ping_targets", help="Comma-separated hosts that must answer ping."),
        sec("TCP checks", "network", "tcp_checks", help="Comma-separated host:port pairs that must accept connections."),
        sec("Expected interfaces", "network", "expected_interfaces", help="Interfaces that must be up."),
        sec("Ping the gateway", "network", "ping_gateway", "bool", help="Check the default gateway answers."),
        Heading("TLS certificates (expiry limits are on the Thresholds page)"),
        cert("Certificate files", "files", help="Comma-separated PEM files to check for expiry."),
        cert("TLS endpoints", "endpoints", help="Comma-separated host:port endpoints to check."),
        cert("Scan folders", "scan_dirs", help="Comma-separated folders searched for certificates."),
        Heading("Other"),
        sec("Cockpit required", "cockpit", "required", "bool", help="Raise a finding when Cockpit is not running."),
        sec("Application URLs", "applications", "http_checks", help="Comma-separated HTTP(S) URLs that must respond."),
        sec("Check mail queue", "applications", "check_mail_queue", "bool", help="Look at the local mail queue."),
        sec("Log lines to analyse", "logs", "max_lines", "int", lo=1000, hi=50000000, help="Upper limit when reading the journal."),
        Heading("Dashboard, uptime and impact"),
        sec("Dashboard title", "dashboard", "title", help="Title of the fleet dashboard page."),
        sec("Stale after (hours)", "dashboard", "stale_hours", "int", lo=1, hi=8760, help="A server with no result for this long shows as stale."),
        sec("Uptime target (%)", "sla", "target_percent", "float", lo=0, hi=100, help="Used by the SLA report."),
        sec("Run priority (nice)", "performance", "nice", "int", lo=0, hi=19, help="Higher is gentler on the server."),
        sec("Idle I/O priority", "performance", "idle_io", "bool", help="Run disk work only when the disks are otherwise idle."),
    ])


def page_tools(ctx):
    Menu(ctx.ui, "Tools", [
        ("Validate configuration", "Check the files, permissions, schedules and tools for problems.", lambda: page_validate(ctx)),
        ("Back up / restore configuration", "Save a copy of all settings, or go back to an earlier copy.", lambda: page_backup(ctx)),
    ]).run()


# ---------------------------------------------------------------------------------------------
# validate, backup and restore
# ---------------------------------------------------------------------------------------------
def page_validate(ctx):
    from lshme.ops import doctor, scheduler
    ui = ctx.ui
    ui.working("Checking the configuration...")
    ctx.app.reload()
    cfg = ctx.cfg
    problems = list(cfg.load_errors) + cfg.permission_problems()
    for s in scheduler.schedules(cfg):
        problems += ["schedule %s: %s" % (s.name, e) for e in s.validate()]
    lines = ["Configuration files: %s" % ctx.app.paths.config_dir, ""]
    lines += ["PROBLEM  %s" % p for p in problems] if problems else ["OK       configuration files and schedules are valid"]
    lines += ["", "%-9s %-34s %-8s %s" % ("Area", "Check", "Status", "Detail"), "-" * 70]
    for area, check, status, detail in doctor.run(cfg, ctx.app.paths, ctx.app.runner):
        lines.append("%-9s %-34s %-8s %s" % (area, check, status, detail))
    ui.view("Validate configuration", lines)


def page_backup(ctx):
    ui, app = ctx.ui, ctx.app

    def now():
        try:
            path = backup_config(app.paths.config_dir, app.paths.config_backup_dir, "manual")
        except OSError as e:
            ui.message("Cannot write the backup: %s" % e, "Backup", "error")
            return
        app.audit("config-backup", archive=os.path.basename(path))
        ui.message("Saved to:\n%s" % path, "Configuration backed up")

    def restore():
        d = app.paths.config_backup_dir
        found = sorted((f for f in os.listdir(d) if f.startswith("config_") and f.endswith(".tar.gz")), reverse=True) if os.path.isdir(d) else []
        if not found:
            ui.message("There are no configuration backups yet.", "Restore")
            return
        pick = ui.choose("Restore which backup?", found)
        if not pick or not ui.confirm("Replace the current configuration with %s?\nThe current settings are backed up first." % pick, "Restore", False):
            return
        try:
            restore_config(os.path.join(d, pick), app.paths.config_dir, d)
        except (OSError, ValueError) as e:
            ui.message("Restore failed: %s" % e, "Restore", "error")
            return
        app.reload()
        app.audit("config-restore", archive=pick)
        ui.note("Restored %s" % pick)
    Menu(ui, "Back up / restore", [("Back up now", "Save a copy of all settings.", now), ("Restore a backup", "Go back to an earlier copy.", restore)]).run()


# ---------------------------------------------------------------------------------------------
# main menu
# ---------------------------------------------------------------------------------------------
PAGES = (
    ("identity", "Organisation & report identity", "Company, customer and report details.", page_identity),
    ("modules", "Monitoring modules", "Switch whole areas of checking on or off.", page_modules),
    ("thresholds", "Thresholds", "When CPU, memory, disks, databases... become warnings or critical.", page_thresholds),
    ("services", "Services, ports & processes", "What must be running, listening or present.", page_services),
    ("databases", "Databases", "Oracle, MySQL, PostgreSQL, MongoDB, SQL Server.", page_databases),
    ("reports", "Report settings", "Formats and type of the reports.", page_reports),
    ("smtp", "Email (SMTP)", "Mail server, addresses and test message.", page_smtp),
    ("schedules", "Schedules", "Daily, weekly, monthly and custom report runs.", page_schedules),
    ("retention", "Retention & cleanup", "How long reports and logs are kept.", page_retention),
    ("watch", "Real-time monitoring & alerts", "The always-on watcher and how it alerts.", page_watch),
    ("backups", "Backup jobs", "Extra backups to verify.", page_backup_jobs),
    ("fleet", "Multi-server (fleet)", "Other servers monitored from this one.", page_fleet),
    ("policy", "Security, network, certificates & other", "Rules checked on this server, certificates to watch, dashboard and priority.", page_policy),
    ("tools", "Tools: validate, back up, restore", "Check the configuration for problems, or save and restore copies.", page_tools),
)
