# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Configuration: a directory of INI files with built-in defaults.

Every setting has a default here, so a missing or partial file never breaks a
run. Files are written 0600 and the previous version is kept in the config
backup area before every change.
"""
import configparser
import re
import os
import shutil
import tarfile
import time

CONFIG_FILES = ("main", "thresholds", "services", "database", "smtp", "reports", "schedule",
                "retention", "servers", "backup", "certificates", "custom", "exclusions", "credentials")

DEFAULTS = {
    "main": {
        "general": {"company": "", "customer": "", "environment": "Production", "prepared_by": "",
                    "location": "", "logo": "", "command_timeout": "30", "run_timeout": "1800"},
        "paths": {"data_dir": "", "report_dir": "", "log_dir": ""},
        "modules": {"enabled": "all", "disabled": ""},
        "security": {"expected_selinux": "enforcing", "require_firewall": "yes",
                     "permit_root_login_allowed": "no", "password_authentication_allowed": "yes",
                     "require_auditd": "no", "check_updates": "yes", "updates_cache_hours": "12",
                     "updates_timeout": "120"},
        "logs": {"max_lines": "200000", "extra_files": "", "extra_patterns": ""},
        "network": {"dns_test_names": "", "ping_targets": "", "tcp_checks": "",
                    "expected_interfaces": "", "ping_gateway": "yes"},
        "kvm": {"uri": "qemu:///system", "sample_seconds": "2"},
        "cockpit": {"required": "no", "probe_https": "yes"},
        "applications": {"http_checks": "", "check_mail_queue": "yes"},
        "history": {"enabled": "yes"},
        "integrations": {"prometheus_textfile": "", "agent_dropdir": ""},
        "watch": {"interval": "60", "confirm": "2", "repeat_minutes": "60", "recovery_alerts": "yes",
                  "rotation_modules": "services,database,kvm,cockpit,network,backup,cluster,certificates",
                  "rotation_minutes": "15", "alert_min_severity": "WARNING", "alert_email": "yes", "alert_to": "",
                  "syslog": "yes", "webhook_url": "", "max_rss_mb": "100", "netfs_timeout": "5",
                  "metrics_hours": "24"},
        "performance": {"nice": "10", "idle_io": "yes"},
        "sla": {"target_percent": "99.9"},
        "dashboard": {"title": "", "stale_hours": "26", "refresh_seconds": "300", "auto_build": "yes"},
    },
    "thresholds": {
        "cpu": {"warning": "85", "critical": "95", "iowait_warning": "20", "steal_warning": "10",
                "sample_seconds": "1"},
        "load": {"per_core_warning": "1.5", "per_core_critical": "3.0"},
        "memory": {"warning": "85", "critical": "95"},
        "swap": {"warning": "50", "critical": "80"},
        "disk": {"warning": "80", "critical": "90"},
        "disk_overrides": {},
        "inode": {"warning": "80", "critical": "90"},
        "disk_io": {"util_warning": "90", "await_warning_ms": "50", "sample_seconds": "1"},
        "network": {"error_ratio_warning": "0.1", "packet_loss_warning": "10",
                    "packet_loss_critical": "50", "latency_warning_ms": "100"},
        "services": {"restart_warning": "3"},
        "kvm": {"pool_warning": "80", "pool_critical": "90", "vcpu_overcommit_warning": "4.0",
                "memory_overcommit_warning": "1.0", "snapshot_age_warning_days": "7",
                "vm_cpu_warning": "90"},
        "certificates": {"warning_days": "30", "critical_days": "7"},
        "logs": {"ssh_fail_warning": "20", "ssh_fail_critical": "200"},
        "oracle": {"tablespace_warning": "85", "tablespace_critical": "95", "fra_warning": "80",
                   "fra_critical": "90", "limits_warning": "80", "limits_critical": "90",
                   "blocking_seconds": "300", "backup_max_age_hours": "26",
                   "archivelog_backup_max_age_hours": "24", "dg_lag_warning_seconds": "300",
                   "dg_lag_critical_seconds": "1800", "invalid_objects_warning": "50",
                   "long_running_seconds": "3600", "include_undo_temp": "no",
                   "check_rman": "yes", "alert_log_max_mb": "16"},
        "mysql": {"connections_warning": "80", "connections_critical": "90",
                  "replication_lag_warning": "300", "replication_lag_critical": "1800"},
        "postgresql": {"connections_warning": "80", "connections_critical": "90",
                       "long_query_minutes": "30", "xid_age_warning": "1000000000",
                       "xid_age_critical": "1500000000", "replication_lag_warning_mb": "512",
                       "replication_lag_critical_mb": "4096"},
        "mongodb": {"connections_warning": "80", "connections_critical": "90"},
        "sqlserver": {"backup_max_age_hours": "26"},
        "backup": {"default_max_age_hours": "26"},
        "hardware": {"temperature_warning_c": "75", "temperature_critical_c": "90"},
        "mail": {"queue_warning": "100", "queue_critical": "1000"},
        "history": {"forecast_warning_days": "30", "forecast_critical_days": "7",
                    "forecast_min_samples": "3", "forecast_min_span_hours": "24"},
    },
    "services": {"critical": {"services": "sshd, crond"}, "ports": {}, "processes": {},
                 "ignore": {"failed_units": ""}},
    "database": {"discovery": {"oracle_autodiscover": "yes", "postgresql_autodiscover": "yes",
                               "mysql_autodiscover": "yes", "mongodb_autodiscover": "no"}},
    "smtp": {"smtp": {"method": "smtp", "host": "", "port": "587", "security": "starttls",
                      "username": "", "from": "", "to": "", "cc": "", "bcc": "", "critical_to": "",
                      "subject_prefix": "[LSHME]", "send_policy": "always", "max_attachment_mb": "15",
                      "attach_formats": "pdf,xlsx,html", "retries": "3", "retry_delay": "20",
                      "timeout": "30", "sendmail_path": "/usr/sbin/sendmail"}},
    "reports": {"reports": {"formats": "html,pdf,json", "report_type": "technical",
                            "include_appendix": "yes", "title": "", "max_evidence_chars": "3000"}},
    "schedule": {"scheduler": {"backend": "auto", "retries": "2", "retry_delay": "300",
                               "systemd_dir": "/etc/systemd/system", "cron_file": "/etc/cron.d/lshme"},
                 "schedule:daily": {"enabled": "yes", "frequency": "daily", "time": "07:00", "days": "1",
                                    "modules": "all", "formats": "html,pdf,json", "report_type": "technical",
                                    "email": "yes", "send_policy": "", "to": "", "cc": ""},
                 "schedule:weekly": {"enabled": "yes", "frequency": "weekly", "weekday": "mon", "time": "08:00",
                                     "days": "7", "modules": "all", "formats": "html,pdf,xlsx,json",
                                     "report_type": "technical", "email": "yes", "send_policy": "", "to": "", "cc": ""},
                 "schedule:monthly": {"enabled": "yes", "frequency": "monthly", "monthday": "1", "time": "09:00",
                                      "days": "30", "modules": "all", "formats": "html,pdf,xlsx,pptx,json",
                                      "report_type": "full", "email": "yes", "send_policy": "always", "to": "", "cc": "",
                                      "sla_report": "yes", "inventory_report": "yes"}},
    "retention": {"retention": {"daily_days": "30", "weekly_days": "90", "monthly_days": "365",
                                "adhoc_days": "30", "fleet_days": "90", "custom_days": "60",
                                "logs_days": "30", "error_logs_days": "90", "audit_logs_days": "365",
                                "auto_apply": "yes",
                                "email_log_days": "90", "history_days": "365",
                                "config_backups_keep": "30", "compress_after_days": "7",
                                "keep_latest": "yes", "archive_dir": "", "min_free_percent": "10",
                                "space_cleanup": "yes", "free_target_percent": "15", "max_report_dir_gb": "0",
                                "space_min_keep_days": "1"}},
    "servers": {"defaults": {"user": "root", "port": "22", "parallel": "8", "timeout": "900",
                             "remote_command": "/usr/local/bin/healthcheck agent --days {days}",
                             "inventory_command": "/usr/local/bin/healthcheck inventory --json",
                             "ssh_options": "-o BatchMode=yes -o ConnectTimeout=15",
                             "identity_file": ""}},
    "backup": {"netbackup": {"enabled": "auto", "max_age_hours": "26"},
               "backupexec": {"enabled": "auto"}, "veeam": {"enabled": "auto", "max_age_hours": "26"}},
    "certificates": {"certificates": {"files": "", "endpoints": "", "scan_dirs": ""}},
    "custom": {"general": {"checks_dir": "", "default_timeout": "60"}},
    "exclusions": {},
    "credentials": {},
}


def as_bool(value, default=False):
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "yes", "y", "true", "on", "enabled")


def as_list(value):
    if not value:
        return []
    out = []
    for part in str(value).replace("\n", ",").split(","):
        part = part.strip()
        if part:
            out.append(part)
    return out


class Paths(object):
    """Resolves where config, data, reports and logs live.

    Installed layout: /etc/lshme, /var/lib/lshme, /var/log/lshme.
    Portable layout (--home DIR or a non-/etc config dir): DIR/{config,data,reports,logs}.
    """

    def __init__(self, app_dir, config_dir=None, home=None):
        self.app_dir = os.path.abspath(app_dir)
        home = home or os.environ.get("LSHME_HOME")
        if home:
            home = os.path.abspath(home)
            self.config_dir = config_dir or os.path.join(home, "config")
            base = home
            self.data_dir = os.path.join(base, "data")
            self.report_dir = os.path.join(base, "reports")
            self.log_dir = os.path.join(base, "logs")
        else:
            if not config_dir:
                config_dir = os.environ.get("LSHME_CONFIG_DIR")
            if not config_dir:
                config_dir = "/etc/lshme" if os.path.isdir("/etc/lshme") else os.path.join(self.app_dir, "config")
            self.config_dir = os.path.abspath(config_dir)
            if self.config_dir.startswith("/etc/"):
                self.data_dir, self.log_dir = "/var/lib/lshme", "/var/log/lshme"
                self.report_dir = "/var/lib/lshme/reports"
            else:
                base = os.path.dirname(self.config_dir)
                self.data_dir = os.path.join(base, "data")
                self.report_dir = os.path.join(base, "reports")
                self.log_dir = os.path.join(base, "logs")

    def apply_overrides(self, cfg):
        for key in ("data_dir", "report_dir", "log_dir"):
            v = cfg.get("main", "paths", key)
            if v:
                setattr(self, key, os.path.abspath(v))

    def ensure(self):
        """Create directories; never loosen existing permissions (config stays 0700)."""
        for d, mode in ((self.config_dir, 0o700), (self.data_dir, 0o750), (self.report_dir, 0o750), (self.log_dir, 0o750)):
            try:
                if not os.path.isdir(d):
                    os.makedirs(d, exist_ok=True)
                    os.chmod(d, mode)
                elif os.stat(d).st_mode & 0o777 & ~mode:
                    os.chmod(d, os.stat(d).st_mode & 0o777 & mode)
            except OSError:
                pass

    @property
    def history_db(self):
        return os.path.join(self.data_dir, "history.sqlite3")

    @property
    def lock_file(self):
        return os.path.join(self.data_dir, "lshme.lock")

    @property
    def config_backup_dir(self):
        return os.path.join(self.data_dir, "config-backups")


_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KEY_RE = re.compile(r"^([^#;\s][^=:]*?)\s*[=:]")


def merge_ini(lines, parser):
    """Return the lines of an INI file updated to match parser, keeping every comment, blank line and
    the order of what is already there: changed values are rewritten in place, new keys go under
    their section, new sections at the end, and keys/sections no longer in parser are dropped."""
    def render(key, value):
        return "%s = %s" % (key, value.replace("\n", "\n    "))

    out, section, keep, seen, last_key_at, drop_cont = [], None, False, {}, {}, False
    for line in (l.rstrip("\n") for l in lines):
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            keep, drop_cont = parser.has_section(section), False
            if keep:
                seen.setdefault(section, set())
                out.append(line)
                last_key_at[section] = len(out)
            continue
        if not line.strip() or line.lstrip().startswith(("#", ";")) or section is None:
            out.append(line)  # comments, blank lines and the file header are always kept
            continue
        if line[:1].isspace():  # continuation of a multi-line value
            if keep and not drop_cont:
                out.append(line)
                last_key_at[section] = len(out)
            continue
        drop_cont = True
        km = _KEY_RE.match(line)
        if not keep or not km:
            continue
        key = km.group(1).strip()
        if not parser.has_option(section, key) or key in seen[section]:
            continue
        seen[section].add(key)
        value = parser.get(section, key)
        old = line[km.end():].strip()
        if old == value.split("\n")[0].strip():
            out.append(line)
            drop_cont = False  # unchanged: keep its original continuation lines too
        else:
            out.append(render(key, value))
        last_key_at[section] = len(out)
    inserts = []
    for sec in parser.sections():
        missing = [k for k in parser.options(sec) if k not in seen.get(sec, set())]
        if sec not in seen:
            inserts.append((len(out), [""] + ["[%s]" % sec] + [render(k, parser.get(sec, k)) for k in missing]))
        elif missing:
            inserts.append((last_key_at[sec], [render(k, parser.get(sec, k)) for k in missing]))
    for at, new in sorted(inserts, key=lambda x: -x[0]):  # bottom first so earlier positions stay valid
        out[at:at] = new
    return [l + "\n" for l in out]


class Config(object):
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.parsers = {}
        self.load_errors = []
        for name in CONFIG_FILES:
            p = configparser.ConfigParser(interpolation=None, strict=False)
            p.optionxform = str  # keep case (mount points, service names)
            path = self.path(name)
            if os.path.exists(path):
                try:
                    p.read(path, encoding="utf-8")
                except configparser.Error as e:
                    self.load_errors.append("%s: %s" % (path, e))
            self.parsers[name] = p

    def path(self, name):
        return os.path.join(self.config_dir, name + ".conf")

    # -- reading ----------------------------------------------------------
    def get(self, name, section, key, default=None):
        p = self.parsers.get(name)
        if p is not None and p.has_section(section) and p.has_option(section, key):
            return p.get(section, key).strip()
        d = DEFAULTS.get(name, {}).get(section, {})
        if key in d:
            return d[key]
        # pattern sections like [oracle:ORCL] fall back to the generic section
        if ":" in section:
            base = section.split(":", 1)[0]
            d = DEFAULTS.get(name, {}).get(base, {})
            if key in d:
                return d[key]
        return default

    def getfloat(self, name, section, key, default=0.0):
        try:
            return float(self.get(name, section, key, default))
        except (TypeError, ValueError):
            return float(default)

    def getint(self, name, section, key, default=0):
        try:
            return int(float(self.get(name, section, key, default)))
        except (TypeError, ValueError):
            return int(default)

    def getbool(self, name, section, key, default=False):
        return as_bool(self.get(name, section, key, None), default)

    def getlist(self, name, section, key, default=""):
        return as_list(self.get(name, section, key, default))

    def section(self, name, section):
        out = dict(DEFAULTS.get(name, {}).get(section, {}))
        p = self.parsers.get(name)
        if p is not None and p.has_section(section):
            for k, v in p.items(section):
                out[k] = v.strip()
        return out

    def sections(self, name, prefix=None):
        p = self.parsers.get(name)
        secs = list(p.sections()) if p is not None else []
        for s in DEFAULTS.get(name, {}):
            if s not in secs:
                secs.append(s)
        if prefix:
            secs = [s for s in secs if s.startswith(prefix)]
        return secs

    def threshold(self, section, key, default=0.0):
        return self.getfloat("thresholds", section, key, default)

    def secret(self, section, key="password"):
        """Read a secret from credentials.conf (root-only) or the environment."""
        env_key = "LSHME_%s_%s" % (section.upper().replace(":", "_").replace("-", "_"), key.upper())
        if os.environ.get(env_key):
            return os.environ[env_key]
        return self.get("credentials", section, key, "") or ""

    # -- writing ----------------------------------------------------------
    def set(self, name, section, key, value):
        p = self.parsers[name]
        if not p.has_section(section):
            p.add_section(section)
        p.set(section, key, str(value))

    def remove_section(self, name, section):
        self.parsers[name].remove_section(section)

    def save(self, name, backup_dir=None):
        path = self.path(name)
        os.makedirs(self.config_dir, exist_ok=True)
        if backup_dir and os.path.exists(path):
            backup_config(self.config_dir, backup_dir, label="before-%s" % name)
        tmp = path + ".tmp"
        old_umask = os.umask(0o077)
        try:
            lines = []
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    lines = f.readlines()
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(merge_ini(lines, self.parsers[name]))  # keeps the explanatory comments
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            os.umask(old_umask)

    def permission_problems(self):
        """Files holding secrets must not be readable by group/others."""
        problems = []
        for name in ("credentials", "smtp", "database"):
            path = self.path(name)
            if os.path.exists(path):
                mode = os.stat(path).st_mode & 0o777
                if mode & 0o077:
                    problems.append("%s has mode %o; expected 600" % (path, mode))
        return problems


def backup_config(config_dir, backup_dir, label="manual", keep=None):
    os.makedirs(backup_dir, exist_ok=True)
    try:
        os.chmod(backup_dir, 0o700)
    except OSError:
        pass
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_dir, "config_%s_%s.tar.gz" % (stamp, label))
    n = 1
    while os.path.exists(path):
        path = os.path.join(backup_dir, "config_%s_%s_%d.tar.gz" % (stamp, label, n))
        n += 1
    with tarfile.open(path, "w:gz") as tar:
        for f in sorted(os.listdir(config_dir)):
            if f.endswith(".conf"):
                tar.add(os.path.join(config_dir, f), arcname=f)
    os.chmod(path, 0o600)
    if keep:
        backups = sorted(x for x in os.listdir(backup_dir) if x.startswith("config_"))
        for old in backups[:-keep]:
            os.remove(os.path.join(backup_dir, old))
    return path


def restore_config(archive, config_dir, backup_dir):
    """Restore a config archive; the current config is backed up first."""
    backup_config(config_dir, backup_dir, label="before-restore")
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = os.path.basename(member.name)
            if not member.isfile() or not name.endswith(".conf") or name != member.name:
                continue  # refuse paths, links and anything unexpected
            src = tar.extractfile(member)
            dest = os.path.join(config_dir, name)
            with open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(dest, 0o600)
    return True
