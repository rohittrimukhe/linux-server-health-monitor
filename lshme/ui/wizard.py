"""First-run / reconfiguration wizard.

Discovers the system, then walks through identity, modules, thresholds,
critical services, databases, reports, SMTP, schedules, retention, backups
and validation. Every step can also be run on its own from the menu.
"""
import os
import re

from lshme import modules as modreg
from lshme import reports
from lshme.core.config import as_list
from lshme.core.context import Context
from lshme.ui import console as C


class Wizard(object):
    def __init__(self, app):
        self.app = app
        self.cfg = app.cfg

    def title(self, text):
        C.out()
        C.out(C.c(" %s " % text, "title"))

    def save(self, *names):
        for n in names:
            self.app.save_config(n)
        C.out(C.c("  saved %s" % ", ".join(n + ".conf" for n in names), "green"))

    def run(self):
        self.title("LSHME setup wizard")
        C.out("Settings are written to %s (a backup is taken before every change)." % self.app.paths.config_dir)
        steps = [("System discovery", self.step_discovery), ("Organisation & report identity", self.step_general),
                 ("Monitoring modules", self.step_modules), ("Thresholds", self.step_thresholds),
                 ("Critical services", self.step_services), ("Databases", self.step_databases),
                 ("Reports", self.step_reports), ("SMTP email", self.step_smtp), ("Schedules", self.step_schedules),
                 ("Retention", self.step_retention), ("Backup jobs", self.step_backup), ("Validation", self.step_validate)]
        for i, (name, fn) in enumerate(steps, 1):
            if i > 1 and not C.yes("\nStep %d/%d: %s — configure now" % (i, len(steps), name), True):
                continue
            fn()
        C.out(C.c("\nSetup complete. Run 'healthcheck run' for a first report.", "green"))

    # ------------------------------------------------------------------------
    def step_discovery(self):
        self.title("System discovery")
        r = self.app.runner
        osr = dict(l.split("=", 1) for l in r.read("/etc/os-release").splitlines() if "=" in l)
        from lshme.modules.database import discover_processes
        ctx = Context(self.cfg, self.app.paths, r)
        dbs = discover_processes(ctx)
        rows = [["Operating system", osr.get("PRETTY_NAME", "?").strip('"')], ["Kernel", os.uname()[2]],
                ["Hostname", os.uname()[1]], ["Running as root", "yes" if r.is_root() else "NO"],
                ["systemd", "yes" if os.path.isdir("/run/systemd/system") else "no"],
                ["KVM/libvirt", "yes" if r.has("virsh") else "no"],
                ["Cockpit", "yes" if os.path.isdir("/usr/share/cockpit") else "no"],
                ["LVM / multipath", "%s / %s" % ("yes" if r.has("lvs") else "no", "yes" if r.has("multipath") else "no")],
                ["Databases running", ", ".join(sorted(dbs)) or "none"],
                ["NetBackup", "yes" if os.path.exists("/usr/openv/netbackup") else "no"],
                ["Pacemaker", "yes" if r.has("pcs") or r.has("crm_mon") else "no"],
                ["Containers", ", ".join(x for x in ("docker", "podman") if r.has(x)) or "none"]]
        C.table(["Item", "Detected"], rows)
        self.discovered = dbs

    def step_general(self):
        self.title("Organisation & report identity")
        for key, label in (("company", "Company name"), ("customer", "Customer name"), ("environment", "Environment (Production/DR/UAT/Development)"),
                           ("location", "Location / data centre"), ("prepared_by", "Prepared by (name or team)"), ("logo", "Logo file for reports (PNG/JPG, optional)")):
            self.cfg.set("main", "general", key, C.ask(label, self.cfg.get("main", "general", key, "")))
        self.save("main")

    def step_modules(self):
        self.title("Monitoring modules")
        current = self.cfg.getlist("main", "modules", "disabled", "")
        C.out("Available: %s" % ", ".join(m.NAME for m in modreg.all_modules()))
        C.out("Modules that do not apply are skipped automatically, so disabling is optional.")
        v = C.ask("Modules to DISABLE (comma-separated, blank for none)", ",".join(current))
        bad = [x for x in as_list(v) if x not in modreg.MODULE_ORDER]
        if bad:
            C.out(C.c("Ignoring unknown: %s" % ", ".join(bad), "yellow"))
        self.cfg.set("main", "modules", "disabled", ",".join(x for x in as_list(v) if x in modreg.MODULE_ORDER))
        self.save("main")

    def step_thresholds(self):
        self.title("Thresholds (percent unless stated)")
        num = C.num_validator(0, 1000000)
        for section, key, label in (("cpu", "warning", "CPU warning"), ("cpu", "critical", "CPU critical"),
                                    ("memory", "warning", "Memory warning"), ("memory", "critical", "Memory critical"),
                                    ("swap", "warning", "Swap warning"), ("swap", "critical", "Swap critical"),
                                    ("disk", "warning", "Filesystem warning"), ("disk", "critical", "Filesystem critical"),
                                    ("inode", "warning", "Inode warning"), ("inode", "critical", "Inode critical"),
                                    ("oracle", "tablespace_warning", "Oracle tablespace warning"), ("oracle", "tablespace_critical", "Oracle tablespace critical"),
                                    ("oracle", "fra_warning", "Oracle FRA warning"), ("oracle", "backup_max_age_hours", "Oracle backup max age (hours)"),
                                    ("kvm", "pool_warning", "KVM storage pool warning"), ("certificates", "warning_days", "Certificate expiry warning (days)")):
            self.cfg.set("thresholds", section, key, C.ask(label, self.cfg.get("thresholds", section, key, ""), validate=num))
        C.out("Per-mount overrides use 'mountpoint = warning,critical', e.g. /archive = 90,97")
        while C.yes("Add a filesystem override", False):
            mp = C.ask("Mount point", "/", validate=lambda x: None if x.startswith("/") else "must start with /")
            val = C.ask("warning,critical", "90,95", validate=lambda x: None if re.match(r"^\d+(\.\d+)?,\d+(\.\d+)?$", x) else "format: 90,95")
            self.cfg.set("thresholds", "disk_overrides", mp, val)
        self.save("thresholds")

    def step_services(self):
        self.title("Critical services")
        r = self.app.runner
        running = []
        if r.has("systemctl"):
            out = r.run(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--no-pager", "--plain"])
            running = [l.split()[0].replace(".service", "") for l in out.lines() if l.split()]
        from lshme.modules.services import COMMON
        suggest = [s for s in running if s in COMMON]
        if suggest:
            C.out("Running infrastructure services: %s" % ", ".join(suggest))
        cur = self.cfg.get("services", "critical", "services", "sshd, crond")
        self.cfg.set("services", "critical", "services", C.ask("Critical services (comma-separated)", cur))
        while C.yes("Add an expected listening port (e.g. listener 1521)", False):
            name = C.ask("Label", "oracle-listener")
            port = C.ask("Port", "1521", validate=C.num_validator(1, 65535))
            self.cfg.set("services", "ports", name, port)
        while C.yes("Add a required process pattern (regex)", False):
            name = C.ask("Label", "app")
            pat = C.ask("Pattern", "java.*myapp")
            self.cfg.set("services", "processes", name, pat)
        self.save("services")

    def step_databases(self):
        self.title("Databases")
        from lshme.modules.database import oracle
        ctx = Context(self.cfg, self.app.paths, self.app.runner)
        found, homes = oracle.discover(ctx)
        if found:
            C.out("Oracle instances running: %s" % ", ".join("%s (home %s, owner %s)" % (k, v["oracle_home"] or "?", v["os_user"]) for k, v in found.items()))
            C.out("They are checked automatically with OS authentication ('/ as sysdba' as the owner) when LSHME runs as root.")
        for sid, inf in found.items():
            if not inf["oracle_home"] and C.yes("Set ORACLE_HOME for %s" % sid, True):
                home = C.ask("ORACLE_HOME for %s" % sid, homes.get(sid, "/u01/app/oracle/product/11.2.0/dbhome_1"))
                sec = "oracle:%s" % sid
                self.cfg.set("database", sec, "oracle_home", home)
                self.cfg.set("database", sec, "os_user", inf["os_user"])
        while C.yes("Add/configure a database instance manually", False):
            engine = C.ask("Engine", "oracle", choices=["oracle", "mysql", "postgresql", "mongodb", "sqlserver"])
            name = C.ask("Instance label", "ORCL" if engine == "oracle" else "main", validate=lambda x: None if re.match(r"^[\w.-]+$", x) else "letters/digits only")
            sec = "%s:%s" % (engine, name)
            if engine == "oracle":
                self.cfg.set("database", sec, "sid", C.ask("ORACLE_SID", name))
                self.cfg.set("database", sec, "oracle_home", C.ask("ORACLE_HOME", "/u01/app/oracle/product/19.0.0/dbhome_1"))
                mode = C.ask("Authentication: os (/ as sysdba) or password", "os", choices=["os", "password"])
                if mode == "os":
                    self.cfg.set("database", sec, "os_user", C.ask("Instance owner OS user", "oracle"))
                else:
                    self.cfg.set("database", sec, "username", C.ask("Monitoring user (needs SELECT_CATALOG_ROLE)", "lshme_monitor"))
                    self.cfg.set("database", sec, "connect_identifier", C.ask("TNS alias / EZConnect", "//localhost:1521/%s" % name))
                    self.cfg.set("credentials", sec, "password", C.ask("Password", "", secret=True))
                    self.save("credentials")
                self.cfg.set("database", sec, "backup_max_age_hours", C.ask("Max RMAN backup age (hours)", "26"))
            elif engine == "mysql":
                self.cfg.set("database", sec, "defaults_file", C.ask("Defaults file with credentials (chmod 600)", "/root/.my.cnf"))
                self.cfg.set("database", sec, "socket", C.ask("Socket (blank for default)", ""))
            elif engine == "postgresql":
                self.cfg.set("database", sec, "os_user", C.ask("OS user for peer auth (blank if using host/password)", "postgres"))
                self.cfg.set("database", sec, "port", C.ask("Port", "5432"))
            elif engine == "mongodb":
                self.cfg.set("database", sec, "uri", C.ask("URI (no password)", "mongodb://127.0.0.1:27017/admin"))
                user = C.ask("Username (blank for none)", "")
                if user:
                    self.cfg.set("database", sec, "username", user)
                    self.cfg.set("credentials", sec, "password", C.ask("Password", "", secret=True))
                    self.save("credentials")
            else:
                self.cfg.set("database", sec, "server", C.ask("Server", "localhost"))
                self.cfg.set("database", sec, "username", C.ask("Login", "lshme_monitor"))
                self.cfg.set("credentials", sec, "password", C.ask("Password", "", secret=True))
                self.save("credentials")
        self.save("database")

    def step_reports(self):
        self.title("Reports")
        self.cfg.set("reports", "reports", "formats", C.ask("Default formats (%s or all)" % ",".join(reports.FORMATS),
                                                            self.cfg.get("reports", "reports", "formats", "html,pdf,json"),
                                                            validate=lambda x: _fmt_err(x)))
        self.cfg.set("reports", "reports", "report_type", C.ask("Default report type", self.cfg.get("reports", "reports", "report_type", "technical"),
                                                                choices=["executive", "technical", "full"]))
        self.cfg.set("reports", "reports", "title", C.ask("Report title (blank for default)", self.cfg.get("reports", "reports", "title", "")))
        self.save("reports")

    def step_smtp(self):
        self.title("SMTP email")
        g = lambda k: self.cfg.get("smtp", "smtp", k, "")
        s = lambda k, v: self.cfg.set("smtp", "smtp", k, v)
        s("method", C.ask("Delivery method", g("method") or "smtp", choices=["smtp", "sendmail"]))
        if g("method") == "smtp":
            s("host", C.ask("SMTP server", g("host")))
            s("security", C.ask("Encryption", g("security") or "starttls", choices=["starttls", "ssl", "none"]))
            s("port", C.ask("Port", g("port") or {"starttls": "587", "ssl": "465", "none": "25"}[g("security")], validate=C.num_validator(1, 65535)))
            s("username", C.ask("Username (blank for no authentication)", g("username")))
            if g("username"):
                pw = C.ask("Password (stored in credentials.conf, mode 600)", self.cfg.secret("smtp"), secret=True)
                self.cfg.set("credentials", "smtp", "password", pw)
                self.save("credentials")
        email_ok = lambda x: None if all(re.match(r"^[^@\s]+@[^@\s]+$", a) for a in as_list(x)) else "enter email addresses separated by commas"
        s("from", C.ask("Sender address", g("from") or "lshme@%s" % os.uname()[1], validate=email_ok))
        s("to", C.ask("Recipients (To)", g("to"), validate=email_ok))
        s("cc", C.ask("CC (optional)", g("cc"), validate=lambda x: None if not x else email_ok(x)))
        s("bcc", C.ask("BCC (optional)", g("bcc"), validate=lambda x: None if not x else email_ok(x)))
        s("critical_to", C.ask("Extra recipients for CRITICAL reports (optional)", g("critical_to"), validate=lambda x: None if not x else email_ok(x)))
        s("send_policy", C.ask("Send policy", g("send_policy") or "always", choices=["always", "issues", "critical", "never"]))
        s("attach_formats", C.ask("Attach formats", g("attach_formats") or "pdf,xlsx,html", validate=_fmt_err))
        s("max_attachment_mb", C.ask("Maximum total attachment size (MB)", g("max_attachment_mb") or "15", validate=C.num_validator(1, 500)))
        self.save("smtp")
        if C.yes("Send a test email now", True):
            from lshme.delivery import mailer
            self.app.reload()
            res = mailer.send_test(mailer.MailSettings(self.app.cfg), self.app.paths.log_dir)
            C.out(C.c("Test email sent." if res["ok"] else "Test failed: %s" % res["error"], "green" if res["ok"] else "red"))
            self.cfg = self.app.cfg

    def step_schedules(self):
        self.title("Schedules")
        from lshme.ops import scheduler
        for s in scheduler.schedules(self.cfg):
            sec = "schedule:" + s.name
            C.out(C.c("\n%s — %s" % (s.name, s.describe()), "bold"))
            en = C.yes("Enable the %s schedule" % s.name, s.enabled)
            self.cfg.set("schedule", sec, "enabled", "yes" if en else "no")
            if not en:
                continue
            self.cfg.set("schedule", sec, "time", C.ask("Time (HH:MM)", s.time, validate=lambda x: None if re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", x) else "HH:MM"))
            if s.frequency == "weekly":
                self.cfg.set("schedule", sec, "weekday", C.ask("Weekday", s.weekday, choices=["mon", "tue", "wed", "thu", "fri", "sat", "sun"]))
            if s.frequency == "monthly":
                self.cfg.set("schedule", sec, "monthday", C.ask("Day of month (1-28)", s.monthday, validate=C.num_validator(1, 28)))
            self.cfg.set("schedule", sec, "days", C.ask("Days of data the report covers", str(s.days), validate=C.num_validator(1, 3650)))
            self.cfg.set("schedule", sec, "formats", C.ask("Formats", s.formats, validate=_fmt_err))
            self.cfg.set("schedule", sec, "report_type", C.ask("Report type", s.report_type, choices=["executive", "technical", "full"]))
            self.cfg.set("schedule", sec, "modules", C.ask("Modules (all or comma list)", s.modules))
            self.cfg.set("schedule", sec, "email", "yes" if C.yes("Email this report", s.email) else "no")
            self.cfg.set("schedule", sec, "to", C.ask("Recipients for this schedule (blank = smtp.conf default)", ",".join(s.to)))
            self.cfg.set("schedule", sec, "send_policy", C.ask("Send policy (blank = smtp.conf default)", s.send_policy))
        while C.yes("Add a custom schedule", False):
            name = C.ask("Name", "hourly-db", validate=lambda x: None if re.match(r"^[A-Za-z0-9_-]+$", x) else "letters, digits, - and _")
            sec = "schedule:" + name
            freq = C.ask("Frequency", "hourly", choices=["hourly", "daily", "weekly", "monthly", "custom"])
            self.cfg.set("schedule", sec, "frequency", freq)
            if freq == "custom":
                self.cfg.set("schedule", sec, "calendar", C.ask("systemd OnCalendar (or 5-field cron with backend=cron)", "Mon..Fri *-*-* 06:30:00"))
            else:
                self.cfg.set("schedule", sec, "time", C.ask("Time HH:MM (minute only for hourly)", "00:15"))
            self.cfg.set("schedule", sec, "modules", C.ask("Modules", "database,storage"))
            self.cfg.set("schedule", sec, "days", C.ask("Days covered", "1"))
            self.cfg.set("schedule", sec, "formats", C.ask("Formats", "html,json"))
            self.cfg.set("schedule", sec, "email", "yes" if C.yes("Email", True) else "no")
            self.cfg.set("schedule", sec, "send_policy", C.ask("Send policy", "issues", choices=["always", "issues", "critical", "never"]))
            self.cfg.set("schedule", sec, "enabled", "yes")
        self.save("schedule")
        if C.yes("Install/refresh the schedules on this system now", True):
            self.app.reload()
            rep = scheduler.install(self.app.cfg, self.app.runner, self.app.launcher(), self.app.global_args())
            self.app.audit("schedule-install", installed=rep["installed"], errors=rep["errors"])
            for i in rep["installed"]:
                C.out(C.c("  installed " + i, "green"))
            for e in rep["errors"]:
                C.out(C.c("  error: " + e, "red"))
            self.cfg = self.app.cfg

    def step_retention(self):
        self.title("Retention")
        num = C.num_validator(0, 36500)
        for key, label in (("daily_days", "Keep daily reports (days)"), ("weekly_days", "Keep weekly reports (days)"),
                           ("monthly_days", "Keep monthly reports (days)"), ("adhoc_days", "Keep manual reports (days)"),
                           ("logs_days", "Keep execution logs (days)"), ("error_logs_days", "Keep error logs (days)"),
                           ("email_log_days", "Keep email delivery log (days)"), ("history_days", "Keep historical metrics (days)"),
                           ("config_backups_keep", "Configuration backups to keep (count)"), ("compress_after_days", "Compress reports older than (days, 0=never)")):
            self.cfg.set("retention", "retention", key, C.ask(label, self.cfg.get("retention", "retention", key, ""), validate=num))
        C.out("Space optimisation: old reports are also removed when disk space runs low or the report folder exceeds a size cap.")
        self.cfg.set("retention", "retention", "space_cleanup", "yes" if C.yes("Enable space-based report cleanup", True) else "no")
        for key, label in (("min_free_percent", "Start cleanup below this % free on the report filesystem"),
                           ("free_target_percent", "Clean up until this % is free"),
                           ("max_report_dir_gb", "Maximum size of the report folder in GB (0 = no cap)"),
                           ("space_min_keep_days", "Never delete reports newer than (days)")):
            self.cfg.set("retention", "retention", key, C.ask(label, self.cfg.get("retention", "retention", key, ""), validate=num))
        self.cfg.set("retention", "retention", "archive_dir", C.ask("Archive expired reports to (blank = delete)", self.cfg.get("retention", "retention", "archive_dir", "")))
        self.cfg.set("retention", "retention", "auto_apply", "yes" if C.yes("Apply retention automatically after scheduled runs", True) else "no")
        self.save("retention")

    def step_backup(self):
        self.title("Backup monitoring")
        C.out("NetBackup, Backup Exec and Veeam are detected automatically; Oracle RMAN is checked per database.")
        while C.yes("Add a backup job to verify (directory or log file)", False):
            name = C.ask("Job name", "daily-export", validate=lambda x: None if re.match(r"^[\w.-]+$", x) else "letters/digits only")
            sec = "job:" + name
            kind = C.ask("Type", "directory", choices=["directory", "logfile"])
            self.cfg.set("backup", sec, "type", kind)
            self.cfg.set("backup", sec, "path", C.ask("Path", "/backup" if kind == "directory" else "/var/log/backup.log"))
            if kind == "directory":
                self.cfg.set("backup", sec, "pattern", C.ask("File pattern", "*.dmp*"))
                self.cfg.set("backup", sec, "min_size_mb", C.ask("Minimum size of newest file (MB)", "1"))
            else:
                self.cfg.set("backup", sec, "success_pattern", C.ask("Success line regex", "completed successfully"))
            self.cfg.set("backup", sec, "max_age_hours", C.ask("Maximum age (hours)", "26"))
        self.save("backup")

    def step_fleet(self):
        self.title("Multi-server (fleet)")
        C.out("The central server connects with SSH keys (BatchMode) and runs 'healthcheck agent' on each server.")
        while C.yes("Add a server", True):
            name = C.ask("Name", "db01", validate=lambda x: None if re.match(r"^[\w.-]+$", x) else "letters/digits only")
            sec = "server:" + name
            self.cfg.set("servers", sec, "host", C.ask("Host/IP", name))
            self.cfg.set("servers", sec, "user", C.ask("SSH user", self.cfg.get("servers", "defaults", "user", "root")))
            self.cfg.set("servers", sec, "group", C.ask("Group", "Production"))
            self.cfg.set("servers", sec, "environment", C.ask("Environment", "Production"))
        self.save("servers")

    def step_validate(self):
        self.title("Validation")
        from lshme.ops import doctor
        self.app.reload()
        self.cfg = self.app.cfg
        rows = doctor.run(self.cfg, self.app.paths, self.app.runner)
        C.table(["Area", "Check", "Status", "Detail"], rows)


def _fmt_err(x):
    try:
        reports.parse_formats(x)
        return None
    except ValueError as e:
        return str(e)
