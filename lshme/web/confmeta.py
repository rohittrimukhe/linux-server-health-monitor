# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The settings editor behind the web interface: every setting of every configuration file.

The type of each setting (yes/no, number, choice, list, time, path, secret...) is worked out from its default
and a table of known names, and every change is validated before it is written, then saved through the
application (comments kept, mode 600, backup first, audit log)."""
import re

from lshme import modules as modreg
from lshme import reports
from lshme.core.config import CONFIG_FILES, DEFAULTS, as_bool, as_list

FILE_INFO = {
    "main": ("General, modules, policy", "Company details, which modules run, security policy, network checks, real-time monitoring, dashboard."),
    "thresholds": ("Thresholds", "When CPU, memory, disks, databases and other values count as warnings or critical."),
    "services": ("Services & processes", "Services that must be running, ports that must be listening, processes that must exist."),
    "database": ("Databases", "Database auto-discovery and instances that need a login or non-standard location."),
    "smtp": ("Email", "Mail server, addresses and when reports are emailed."),
    "reports": ("Reports", "Default formats and report type."),
    "schedule": ("Schedules", "Daily, weekly, monthly and custom report runs."),
    "retention": ("Retention", "How long reports, logs and history are kept; cleanup when space runs low."),
    "servers": ("Fleet servers", "Other servers monitored from this one over SSH."),
    "backup": ("Backup jobs", "Backups to verify by age and size."),
    "certificates": ("TLS certificates", "Certificate files, folders and endpoints to watch for expiry."),
    "custom": ("Custom checks", "Your own check scripts."),
    "exclusions": ("Exclusions", "Accepted findings that no longer affect the score."),
    "credentials": ("Passwords", "Passwords for email and databases. Values are never shown; type a new one to replace."),
    "web": ("Web interface", "Address, port and encryption of this web interface. Accounts are managed on the Account page."),
}
HIDDEN_SECTIONS = {"web": ("users", "roles")}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SEVERITIES = ["INFO", "WARNING", "CRITICAL"]
CHOICES = {
    "method": ["smtp", "sendmail"], "security": ["starttls", "ssl", "none"], "send_policy": ["", "always", "issues", "critical", "never"],
    "report_type": ["daily", "weekly", "monthly", "executive", "technical", "full"], "alert_min_severity": SEVERITIES, "expected_selinux": ["enforcing", "permissive", "disabled"],
    "weekday": WEEKDAYS, "frequency": ["hourly", "daily", "weekly", "monthly", "custom"], "backend": ["auto", "systemd", "cron"],
    "type": ["directory", "logfile"],
}
MULTI = {"formats": list(reports.FORMATS), "attach_formats": list(reports.FORMATS)}
EMAIL_KEYS = {"to", "cc", "bcc", "from", "critical_to", "alert_to"}
PATH_KEYS = {"path", "logo", "archive_dir", "identity_file", "oracle_home", "defaults_file", "socket", "cert", "key", "data_dir", "report_dir",
             "log_dir", "checks_dir", "sendmail_path", "systemd_dir", "cron_file", "prometheus_textfile", "agent_dropdir"}
BOOL_KEYS = {"enabled", "email", "sla_report", "inventory_report", "include_appendix"}
NUMBER_RANGES = {"port": (1, 65535), "days": (1, 3650), "monthday": (1, 28), "interval": (10, 3600)}
SECRET_KEYS = {"password"}
SECTION_LABELS = {"general": "General", "paths": "Folders", "modules": "Modules", "security": "Security policy", "logs": "Logs", "network": "Network checks",
                  "kvm": "KVM", "cockpit": "Cockpit", "applications": "Applications", "history": "History", "integrations": "Integrations",
                  "watch": "Real-time monitoring", "performance": "Run priority", "sla": "Uptime target", "dashboard": "Dashboard", "scan": "Server scan",
                  "smtp": "Email", "scheduler": "Scheduler", "critical": "Critical services", "ports": "Expected ports", "processes": "Required processes",
                  "ignore": "Ignored units", "disk_overrides": "Per-mount filesystem limits", "discovery": "Auto-discovery", "defaults": "Defaults",
                  "retention": "Retention", "reports": "Reports", "web": "Web interface"}
# what a new section of each kind starts with, and the keys that make sense in it
SECTION_KINDS = {
    "schedule": [{"prefix": "schedule:", "label": "Schedule", "keys": ["frequency", "time", "weekday", "monthday", "calendar", "days", "modules", "formats",
                                                                   "report_type", "email", "to", "cc", "send_policy", "enabled"]}],
    "database": [{"prefix": "oracle:", "label": "Oracle instance", "keys": ["sid", "oracle_home", "os_user", "username", "connect_identifier", "backup_max_age_hours"]},
                 {"prefix": "mysql:", "label": "MySQL / MariaDB", "keys": ["defaults_file", "socket"]},
                 {"prefix": "postgresql:", "label": "PostgreSQL", "keys": ["os_user", "port"]},
                 {"prefix": "mongodb:", "label": "MongoDB", "keys": ["uri", "username"]},
                 {"prefix": "sqlserver:", "label": "SQL Server", "keys": ["server", "username"]}],
    "backup": [{"prefix": "job:", "label": "Backup job", "keys": ["type", "path", "pattern", "min_size_mb", "success_pattern", "max_age_hours"]}],
    "servers": [{"prefix": "server:", "label": "Server", "keys": ["host", "user", "port", "group", "environment", "identity_file", "enabled"]}],
    "custom": [{"prefix": "check:", "label": "Custom check", "keys": ["command", "timeout", "description"]}],
    "exclusions": [{"prefix": "exclude:", "label": "Exclusion rule", "keys": ["code", "module", "component", "hosts", "reason", "owner", "until"]}],
    "credentials": [{"prefix": "", "label": "Password entry", "keys": ["password"]}],
}
HELP = {
    "company": "Shown on the cover of every report.", "customer": "Who the reports are prepared for.", "environment": "For example Production, DR, UAT.",
    "prepared_by": "Your name or team.", "location": "Data centre or site.", "logo": "Extra customer logo (PNG/JPG, full path). The LRS logo is always included.",
    "command_timeout": "Longest any single system command may run (seconds).", "run_timeout": "Longest a complete health check may run (seconds).",
    "disabled": "Comma-separated modules never run.", "auto_excluded": "Managed by the server scan: modules with nothing to check on this server.",
    "force_enabled": "Modules to check even though the scan found nothing.", "hide_not_present": "Leave modules with nothing to check out of reports.",
    "expected_selinux": "A different SELinux mode raises a finding.", "require_firewall": "Raise a finding when no firewall is active.",
    "permit_root_login_allowed": "Yes: root SSH login is accepted policy.", "password_authentication_allowed": "Yes: SSH password login is accepted policy.",
    "require_auditd": "Raise a finding when auditd is not running.", "check_updates": "Count pending package updates.",
    "dns_test_names": "Names that must resolve (comma-separated).", "ping_targets": "Hosts that must answer ping (comma-separated).",
    "tcp_checks": "host:port pairs that must accept connections (comma-separated).", "expected_interfaces": "Interfaces that must be up.",
    "http_checks": "URL|expected status|text that must appear, comma-separated.", "interval": "Seconds between real-time checks.",
    "confirm": "Consecutive bad checks before alerting.", "repeat_minutes": "Remind about an unresolved alert this often (0 = never).",
    "alert_min_severity": "Lowest severity that sends an alert.", "alert_to": "Alert recipients; blank uses the email settings.",
    "webhook_url": "POST each alert as JSON to this URL.", "max_rss_mb": "The watcher restarts itself above this memory use.",
    "host": "Server name or address.", "port": "TCP port.", "method": "smtp: a mail server. sendmail: the local MTA.", "security": "Encryption for SMTP.",
    "username": "Login name.", "from": "Sender address.", "to": "Recipients, comma-separated.", "cc": "Copy recipients, comma-separated.",
    "bcc": "Blind-copy recipients.", "critical_to": "Extra recipients told only about CRITICAL reports.", "send_policy": "always, issues (warnings or worse), critical, never.",
    "attach_formats": "Report files attached to emails.", "max_attachment_mb": "Total attachment size per email.", "formats": "Report files written.",
    "time": "24-hour HH:MM.", "weekday": "Day of the week.", "monthday": "Day of the month, 1-28.", "days": "Days of data a report covers.",
    "calendar": "systemd OnCalendar expression, for custom schedules.", "modules": "all, or a comma list of modules.", "frequency": "How often it runs.",
    "enabled": "Switch off to keep the settings but stop using them.", "password": "Stored write-only in credentials.conf.",
    "services": "Comma-separated service names that must be running.", "failed_units": "Units whose failure is expected.",
    "retention_note": "", "space_cleanup": "Delete the oldest reports when disk space runs low.", "archive_dir": "Move expired reports here instead of deleting.",
    "sid": "Oracle instance name.", "oracle_home": "Oracle software folder.", "os_user": "OS user that owns the instance (OS authentication).",
    "connect_identifier": "TNS alias or //host:port/service.", "defaults_file": "A my.cnf with the login (mode 600).", "uri": "Connection URI without a password.",
    "path": "Folder or log file.", "pattern": "File pattern, e.g. *.dmp*", "type": "directory, or logfile with a success line.", "max_age_hours": "Newest backup must be younger than this.",
    "min_size_mb": "A newest file smaller than this means a failed backup.", "identity_file": "SSH private key for this server.", "group": "Grouping in the fleet dashboard.",
    "command": "Script to run (must be executable by root).", "files": "Comma-separated PEM files.", "endpoints": "Comma-separated host:port TLS endpoints.",
    "scan_dirs": "Comma-separated folders searched for certificates.", "bind": "Address to listen on. 127.0.0.1 = this server only; 0.0.0.0 = the network.",
    "tls": "Encrypt with HTTPS (a self-signed certificate is created if none is given).", "cert": "TLS certificate file (PEM).", "key": "TLS private key file (PEM).",
    "session_minutes": "Minutes of inactivity before signing out.", "max_login_failures": "Wrong passwords allowed per address in 5 minutes.",
    "allow_plain_http": "Allow HTTP (no encryption) on a network address. Not recommended.", "code": "Finding code, wildcards allowed.", "reason": "Why this is accepted.",
    "until": "Expiry date YYYY-MM-DD.", "target_percent": "Availability target used by the SLA report.", "title": "Title shown on pages and reports.",
}
KEY_RX = re.compile(r"^[A-Za-z0-9_.:/@*\- ]{1,100}$")
SECTION_RX = re.compile(r"^[A-Za-z0-9_.:@\-]{1,80}$")


def kind_of(file, section, key, default):
    if file == "credentials" or key in SECRET_KEYS:
        return "secret"
    if file == "backup" and key == "enabled":
        return "choice"
    if key in MULTI:
        return "multi"
    if key in CHOICES:
        return "choice"
    d = str(default).strip().lower() if default is not None else ""
    if d in ("yes", "no") or key in BOOL_KEYS:
        return "bool"
    if re.match(r"^-?\d+$", d):
        return "int"
    if re.match(r"^-?\d+\.\d+$", d):
        return "float"
    if key in EMAIL_KEYS:
        return "emails"
    if key == "time":
        return "time"
    if key in PATH_KEYS:
        return "path"
    return "text"


def choices_of(file, section, key):
    if file == "backup" and key == "enabled":
        return ["auto", "yes", "no"]
    if key in MULTI:
        return MULTI[key]
    return CHOICES.get(key)


def validate_value(kind, key, value, choices=None, optional=True):
    """The reason a value is not acceptable, or None."""
    v = value
    if "\n" in v or "\r" in v or any(ord(c) < 32 for c in v):
        return "must be a single line of text"
    if len(v) > 2000:
        return "is too long"
    if v == "" and kind not in ("bool",):
        return None if optional or kind in ("text", "path", "emails", "multi", "secret", "choice") else "is required"
    if kind == "bool":
        return None if v in ("yes", "no") else "must be yes or no"
    if kind in ("int", "float"):
        try:
            f = float(v)
        except ValueError:
            return "must be a number"
        if kind == "int" and f != int(f):
            return "must be a whole number"
        lo, hi = NUMBER_RANGES.get(key, (None, None))
        if (lo is not None and f < lo) or (hi is not None and f > hi):
            return "must be between %s and %s" % (lo, hi)
    elif kind == "choice":
        return None if v in (choices or []) else "must be one of: %s" % ", ".join(c or "(blank)" for c in (choices or []))
    elif kind == "multi":
        bad = [x for x in as_list(v) if x not in (choices or [])]
        return "unknown: %s" % ", ".join(bad) if bad else None
    elif kind == "emails":
        return None if all(re.match(r"^[^@\s]+@[^@\s]+$", a) for a in as_list(v)) else "must be email addresses separated by commas"
    elif kind == "time":
        return None if re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", v) else "must be a time like 07:00"
    elif kind == "path":
        return None if v.startswith("/") else "must be a full path starting with /"
    return None


def section_defaults(file, section):
    """The default keys and values a section is typed from. Your own schedules use the daily schedule's keys."""
    d = DEFAULTS.get(file, {})
    if section in d:
        return d[section]
    if file == "schedule" and section.startswith("schedule:"):
        return d.get("schedule:daily", {})
    if ":" in section and section.split(":", 1)[0] in d:
        return d[section.split(":", 1)[0]]
    return {}


def _template_help(app):
    """Comments written above a key in the shipped template files, used when no built-in help exists."""
    import os
    out = {}
    for base in (os.path.join(app.paths.app_dir, "config-templates"), os.path.join(app.paths.app_dir, "config")):
        if not os.path.isdir(base):
            continue
        for name in CONFIG_FILES:
            path = os.path.join(base, name + ".conf")
            if not os.path.exists(path):
                continue
            section, notes = "", []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#"):
                        notes.append(line.lstrip("# ").strip())
                    elif line.startswith("["):
                        section, notes = line.strip("[]"), []
                    elif "=" in line and not line.startswith(";"):
                        if notes:
                            out.setdefault((name, section, line.split("=", 1)[0].strip()), " ".join(notes))
                        notes = []
                    else:
                        notes = []
        break
    return out


def _entry(app, file, section, key, value, default, tpl):
    kind = kind_of(file, section, key, default)
    e = {"key": key, "kind": kind, "default": "" if default is None else default,
         "help": HELP.get(key) or tpl.get((file, section, key), ""), "choices": choices_of(file, section, key)}
    if kind == "secret":
        e["set"] = bool(value)
        e["value"] = ""
    else:
        e["value"] = value
    return e


def describe(app):
    """Everything the editor needs: files > sections > keys with value, default, type, choices and help."""
    cfg = app.cfg
    tpl = _template_help(app)
    files = []
    for file in CONFIG_FILES:
        title, about = FILE_INFO[file]
        hidden = HIDDEN_SECTIONS.get(file, ())
        names = [s for s in cfg.sections(file) if s not in hidden]
        sections = []
        for sec in names:
            defaults = section_defaults(file, sec)
            current = cfg.section(file, sec)
            keys = list(defaults) + [k for k in current if k not in defaults]
            builtin = sec in DEFAULTS.get(file, {})
            sections.append({"name": sec, "label": SECTION_LABELS.get(sec, sec), "builtin": builtin, "free": not defaults,
                             "keys": [_entry(app, file, sec, k, current.get(k, defaults.get(k, "")), defaults.get(k), tpl) for k in keys]})
        files.append({"file": file, "title": title, "about": about, "sections": sections, "kinds": SECTION_KINDS.get(file, [])})
    return {"files": files, "modules": list(modreg.MODULE_ORDER)}


def _pairs_problem(values):
    """warning must not exceed critical (for *_days settings: critical must not exceed warning)."""
    for key, v in values.items():
        if "warning" not in key:
            continue
        other = values.get(key.replace("warning", "critical"))
        if other in (None, "") or v == "":
            continue
        try:
            w, c = float(v), float(other)
        except ValueError:
            continue
        if key.endswith("_days"):
            if c > w:
                return "%s: critical (%s) must not exceed warning (%s)" % (key.replace("warning", "critical"), other, v)
        elif w > c:
            return "%s: warning (%s) must not be above critical (%s)" % (key, v, other)
    return None


def apply(app, file, section, values, remove=()):
    """Validate and save changes to one section. Returns the list of keys written. Raises ValueError."""
    if file not in CONFIG_FILES or file == "web" and section in HIDDEN_SECTIONS.get("web", ()):
        raise ValueError("unknown setting")
    cfg = app.cfg
    if section not in cfg.sections(file):
        raise ValueError("unknown section")
    default_section = section_defaults(file, section)
    current = cfg.section(file, section)
    problems, merged = [], dict(current)
    for key, value in values.items():
        if not KEY_RX.match(key):
            problems.append("%s: not a valid setting name" % key)
            continue
        if not isinstance(value, str):
            problems.append("%s: must be text" % key)
            continue
        value = value.strip()
        kind = kind_of(file, section, key, default_section.get(key))
        if kind == "secret" and value == "":
            continue  # an empty password field means "leave it as it is"
        err = validate_value(kind, key, value, choices_of(file, section, key), optional=default_section.get(key, "") in ("", None))
        if err:
            problems.append("%s %s" % (key, err))
        merged[key] = value
    if section.startswith("schedule:"):
        from lshme.ops import scheduler
        problems += scheduler.Schedule(section.split(":", 1)[1], merged).validate()
    if file == "thresholds":
        pair = _pairs_problem(merged)
        if pair:
            problems.append(pair)
    if problems:
        raise ValueError("; ".join(problems))
    written = []
    for key, value in values.items():
        kind = kind_of(file, section, key, default_section.get(key))
        if kind == "secret" and value.strip() == "":
            continue
        cfg.set(file, section, key, value.strip())
        written.append(key)
    parser = cfg.parsers[file]
    for key in remove:
        if parser.has_section(section) and parser.has_option(section, key):
            parser.remove_option(section, key)
            written.append(key)
    app.save_config(file)
    app.reload()
    return written


def add_section(app, file, name, values=None):
    if file not in CONFIG_FILES or file == "web":
        raise ValueError("sections cannot be added here")
    if not SECTION_RX.match(name):
        raise ValueError("a section name may contain letters, digits and . _ : @ -")
    if name in app.cfg.sections(file):
        raise ValueError("'%s' already exists" % name)
    app.cfg.parsers[file].add_section(name)
    app.save_config(file)
    app.reload()
    if values:
        apply(app, file, name, values)


def delete_section(app, file, name):
    if file not in CONFIG_FILES or file == "web":
        raise ValueError("sections cannot be removed here")
    if name in DEFAULTS.get(file, {}):
        raise ValueError("'%s' is built in and cannot be removed; switch it off instead" % name)
    if not app.cfg.parsers[file].has_section(name):
        raise ValueError("no such section")
    app.cfg.remove_section(file, name)
    app.save_config(file)
    app.reload()
