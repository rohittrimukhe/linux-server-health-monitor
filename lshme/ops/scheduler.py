# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Scheduling: daily / weekly / monthly / hourly / custom runs via systemd timers
(preferred) or /etc/cron.d. Each schedule has its own modules, lookback days,
report formats, report type and recipients."""
import os
import re
import shlex

from lshme.core.config import as_bool, as_list
from lshme.core.util import owned_by

WEEKDAYS = {"mon": (1, "Mon"), "tue": (2, "Tue"), "wed": (3, "Wed"), "thu": (4, "Thu"), "fri": (5, "Fri"),
            "sat": (6, "Sat"), "sun": (0, "Sun"), "1": (1, "Mon"), "2": (2, "Tue"), "3": (3, "Wed"),
            "4": (4, "Thu"), "5": (5, "Fri"), "6": (6, "Sat"), "7": (0, "Sun"), "0": (0, "Sun")}


DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}


def parse_days(spec):
    """Week days from mon-sat, mon,wed,fri or empty (every day), in week order; None when the text is not valid."""
    spec = (spec or "").strip().lower().replace(" ", "")
    if spec in ("", "all", "daily", "every", "everyday"):
        return list(DAY_ORDER)
    days = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a[:3] not in DAY_ORDER or b[:3] not in DAY_ORDER:
                return None
            i, j = DAY_ORDER.index(a[:3]), DAY_ORDER.index(b[:3])
            days.update(DAY_ORDER[i:j + 1] if i <= j else DAY_ORDER[i:] + DAY_ORDER[:j + 1])
        elif part[:3] in DAY_ORDER:
            days.add(part[:3])
        else:
            return None
    return [d for d in DAY_ORDER if d in days]


def _is_run(days):
    idx = [DAY_ORDER.index(d) for d in days]
    return idx == list(range(idx[0], idx[0] + len(idx)))


def days_systemd(days):
    if len(days) == 7:
        return ""
    # a comma list, never a range such as Mon..Sat: systemd 219 (Oracle Linux 7 / RHEL 7) rejects ranges and the timer is not loaded
    return ",".join(DAY_NAMES[d] for d in days) + " "


def days_cron(days):
    if len(days) == 7:
        return "*"
    num = lambda d: (DAY_ORDER.index(d) + 1) % 7      # Monday 1 ... Saturday 6, Sunday 0
    if len(days) > 1 and _is_run(days) and "sun" not in days:
        return "%d-%d" % (num(days[0]), num(days[-1]))
    return ",".join(str(num(d)) for d in days)


def days_text(days):
    if len(days) == 7:
        return "every day"
    if len(days) > 1 and _is_run(days):
        return "%s-%s" % (DAY_NAMES[days[0]], DAY_NAMES[days[-1]])
    return ", ".join(DAY_NAMES[d] for d in days)


class Schedule(object):
    def __init__(self, name, s):
        self.name = name
        self.enabled = as_bool(s.get("enabled", "yes"), True)
        self.frequency = s.get("frequency", "daily").lower()
        self.time = s.get("time", "07:00")
        self.weekday = s.get("weekday", "mon").lower()[:3]
        self.weekdays = s.get("weekdays", "")           # daily schedules only: mon-sat runs Monday to Saturday, blank means every day
        self.monthday = s.get("monthday", "1")
        self.calendar = s.get("calendar", "")  # systemd OnCalendar or 5-field cron for frequency=custom
        self.days = int(s.get("days") or {"daily": 1, "weekly": 7, "monthly": 30, "hourly": 1}.get(self.frequency, 1))
        self.modules = s.get("modules", "all") or "all"
        self.formats = s.get("formats", "html,pdf,json")
        self.report_type = s.get("report_type", "technical")
        self.email = as_bool(s.get("email", "yes"), True)
        self.send_policy = s.get("send_policy", "")
        self.to = as_list(s.get("to"))
        self.cc = as_list(s.get("cc"))
        self.to_summary = as_list(s.get("to_summary"))      # management: only the summary report, not the comprehensive one
        self.to_database = as_list(s.get("to_database"))    # the DBA team: a database-only report
        self.sla_report = as_bool(s.get("sla_report", "no"), False)          # attach last month's SLA report
        self.inventory_report = as_bool(s.get("inventory_report", "no"), False)  # attach the asset inventory

    def validate(self):
        errors = []
        if self.frequency not in ("hourly", "daily", "weekly", "monthly", "custom"):
            errors.append("frequency must be hourly, daily, weekly, monthly or custom")
        if self.frequency != "custom" and not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", self.time):
            errors.append("time must be HH:MM")
        if self.frequency == "daily" and parse_days(self.weekdays) is None:
            errors.append("weekdays must look like mon-sat or mon,wed,fri (or be empty for every day)")
        if self.frequency == "weekly" and self.weekday not in WEEKDAYS:
            errors.append("weekday must be mon..sun")
        if self.frequency == "monthly" and not (self.monthday.isdigit() and 1 <= int(self.monthday) <= 28):
            errors.append("monthday must be 1-28 (so it exists in every month)")
        if self.frequency == "custom" and not self.calendar:
            errors.append("custom schedules need 'calendar'")
        if not re.match(r"^[A-Za-z0-9_-]+$", self.name):
            errors.append("schedule name may only contain letters, digits, - and _")
        return errors

    def hm(self):
        h, m = self.time.split(":")
        return int(h), int(m)

    def on_calendar(self):
        if self.frequency == "custom":
            return self.calendar if not re.match(r"^\S+ \S+ \S+ \S+ \S+$", self.calendar) else None
        h, m = self.hm()
        if self.frequency == "hourly":
            return "*-*-* *:%02d:00" % m
        if self.frequency == "daily":
            return "%s*-*-* %02d:%02d:00" % (days_systemd(parse_days(self.weekdays) or list(DAY_ORDER)), h, m)
        if self.frequency == "weekly":
            return "%s *-*-* %02d:%02d:00" % (WEEKDAYS[self.weekday][1], h, m)
        return "*-*-%02d %02d:%02d:00" % (int(self.monthday), h, m)

    def cron(self):
        if self.frequency == "custom":
            return self.calendar if re.match(r"^\S+ \S+ \S+ \S+ \S+$", self.calendar) else None
        h, m = self.hm()
        if self.frequency == "hourly":
            return "%d * * * *" % m
        if self.frequency == "daily":
            return "%d %d * * %s" % (m, h, days_cron(parse_days(self.weekdays) or list(DAY_ORDER)))
        if self.frequency == "weekly":
            return "%d %d * * %d" % (m, h, WEEKDAYS[self.weekday][0])
        return "%d %d %d * *" % (m, h, int(self.monthday))

    def describe(self):
        if self.frequency == "custom":
            return "custom: %s" % self.calendar
        if self.frequency == "hourly":
            return "every hour at :%02d" % self.hm()[1]
        if self.frequency == "daily":
            return "%s at %s" % (days_text(parse_days(self.weekdays) or list(DAY_ORDER)), self.time)
        if self.frequency == "weekly":
            return "every %s at %s" % (WEEKDAYS[self.weekday][1], self.time)
        return "day %s of every month at %s" % (self.monthday, self.time)


class ScanJob(object):
    """The weekly server rescan (default Sunday 02:00), scheduled with the same timers or cron as the reports so it
    notices new software (a database, KVM, a backup product...) and brings the matching module back."""
    name = "scan"
    label = "server scan"
    frequency = "weekly"

    def __init__(self, cfg):
        g = lambda key, default: cfg.get("main", "scan", key, default)
        self.enabled = as_bool(g("enabled", "yes"), True)
        self.weekday = g("weekday", "sun").lower()[:3]
        self.time = g("time", "02:00")

    def validate(self):
        errors = []
        if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", self.time):
            errors.append("scan time must be HH:MM")
        if self.weekday not in WEEKDAYS:
            errors.append("scan weekday must be mon..sun")
        return errors

    def hm(self):
        h, m = self.time.split(":")
        return int(h), int(m)

    def on_calendar(self):
        h, m = self.hm()
        return "%s *-*-* %02d:%02d:00" % (WEEKDAYS[self.weekday][1], h, m)

    def cron(self):
        h, m = self.hm()
        return "%d %d * * %d" % (m, h, WEEKDAYS[self.weekday][0])

    def describe(self):
        return "server scan every %s at %s" % (WEEKDAYS[self.weekday][1], self.time)


class UpdateJob(object):
    """The safe automatic update of LRS Vitals itself (default every day at 21:00; [update] in main.conf)."""
    name = "update"
    label = "automatic update"
    frequency = "daily"

    def __init__(self, cfg):
        g = lambda key, default: cfg.get("main", "update", key, default)
        self.enabled = as_bool(g("auto", "yes"), True)
        self.time = g("time", "21:00") or "21:00"

    def validate(self):
        return [] if re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", self.time) else ["update time must be HH:MM"]

    def hm(self):
        h, m = self.time.split(":")
        return int(h), int(m)

    def on_calendar(self):
        return "*-*-* %02d:%02d:00" % self.hm()

    def cron(self):
        h, m = self.hm()
        return "%d %d * * *" % (m, h)

    def describe(self):
        return "automatic update every day at %s" % self.time


class FleetJob(object):
    """Central monitoring: ask every server in servers.conf for its health every few minutes, without writing report files,
    so the Servers page of the web dashboard stays fresh. Off until [main] [fleet] enabled = yes."""
    name = "fleet"
    label = "fleet collection"
    frequency = "minutes"

    def __init__(self, cfg):
        self.enabled = as_bool(cfg.get("main", "fleet", "enabled", "no"), False)
        self.minutes = str(cfg.get("main", "fleet", "minutes", "10"))

    def validate(self):
        if not (self.minutes.isdigit() and 1 <= int(self.minutes) <= 59):
            return ["fleet minutes must be 1-59"]
        return []

    def on_calendar(self):
        return "*:0/%d" % int(self.minutes)

    def cron(self):
        return "*/%d * * * *" % int(self.minutes)

    def describe(self):
        return "ask every server in servers.conf every %s minutes" % self.minutes


def schedules(cfg):
    out = []
    for section in cfg.sections("schedule", "schedule:"):
        out.append(Schedule(section.split(":", 1)[1], cfg.section("schedule", section)))
    return out


def get(cfg, name):
    for s in schedules(cfg):
        if s.name == name:
            return s
    return None


def backend(cfg, runner):
    b = cfg.get("schedule", "scheduler", "backend", "auto").lower()
    if b in ("systemd", "cron"):
        return b
    return "systemd" if runner.has("systemctl") and os.path.isdir("/run/systemd/system") else "cron"


def command_line(launcher, global_args, sched, retries):
    if isinstance(sched, ScanJob):
        args = [launcher] + list(global_args) + ["scan", "--apply", "--quiet"]
    elif isinstance(sched, FleetJob):
        args = [launcher] + list(global_args) + ["fleet", "run", "--formats", "none"]
    elif isinstance(sched, UpdateJob):
        args = [launcher] + list(global_args) + ["update", "auto", "--quiet"]
    else:
        args = [launcher] + list(global_args) + ["run", "--schedule", sched.name, "--quiet", "--retries", str(retries)]
    return " ".join(shlex.quote(a) for a in args)


def render_systemd(sched, cmdline):
    service = ("[Unit]\nDescription=LSHME %s (%s)\nAfter=network-online.target\nWants=network-online.target\n\n"
               "[Service]\nType=oneshot\nExecStart=%s\nNice=10\nIOSchedulingClass=idle\nCPUQuota=50%%\nMemoryMax=512M\n"
               "TimeoutStartSec=3h\nPrivateTmp=true\n# LSHME_MANAGED\n" % (getattr(sched, "label", "health check"), sched.name, cmdline))
    timer = ("[Unit]\nDescription=LSHME schedule %s (%s)\n\n[Timer]\nOnCalendar=%s\nPersistent=true\nRandomizedDelaySec=120\n"
             "Unit=lshme-%s.service\n\n[Install]\nWantedBy=timers.target\n# LSHME_MANAGED\n" % (
                 sched.name, sched.describe(), sched.on_calendar(), sched.name))
    return service, timer


def render_cron(entries):
    lines = ["# Managed by LSHME (Linux Server Health Monitor Enterprise). Changes are overwritten.",
             "SHELL=/bin/sh", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", ""]
    for sched, cmdline in entries:
        lines.append("# %s: %s" % (sched.name, sched.describe()))
        lines.append("%s root %s >/dev/null 2>&1" % (sched.cron(), cmdline))
    return "\n".join(lines) + "\n"


def install(cfg, runner, launcher, global_args, dry_run=False):
    """Install all enabled schedules; remove disabled ones. Returns a report dict."""
    be = backend(cfg, runner)
    retries = cfg.getint("schedule", "scheduler", "retries", 2)
    report = {"backend": be, "installed": [], "removed": [], "errors": [], "files": {}}
    all_s = schedules(cfg) + [ScanJob(cfg), FleetJob(cfg), UpdateJob(cfg)]  # the weekly server scan and central collection are scheduled alongside the reports
    for s in all_s:
        for e in s.validate():
            report["errors"].append("%s: %s" % (s.name, e))
    if report["errors"]:
        return report
    enabled = [s for s in all_s if s.enabled]
    if be == "systemd":
        d = cfg.get("schedule", "scheduler", "systemd_dir", "/etc/systemd/system")
        for s in all_s:
            svc, tmr = os.path.join(d, "lshme-%s.service" % s.name), os.path.join(d, "lshme-%s.timer" % s.name)
            if s.enabled:
                cal = s.on_calendar()
                if not cal:
                    report["errors"].append("%s: calendar is a cron expression; use systemd OnCalendar syntax or backend=cron" % s.name)
                    continue
                a, b = render_systemd(s, command_line(launcher, global_args, s, retries))
                report["files"][svc], report["files"][tmr] = a, b
                if not dry_run:
                    for path, text in ((svc, a), (tmr, b)):
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(text)
                        os.chmod(path, 0o644)
                report["installed"].append("%s (%s)" % (s.name, s.describe()))
            elif os.path.exists(tmr) and not dry_run:
                runner.run(["systemctl", "disable", "--now", "lshme-%s.timer" % s.name])
                for path in (svc, tmr):
                    if os.path.exists(path):
                        os.remove(path)
                report["removed"].append(s.name)
        if not dry_run:
            runner.run(["systemctl", "daemon-reload"])
            for s in enabled:
                if s.on_calendar():
                    r = runner.run(["systemctl", "enable", "--now", "lshme-%s.timer" % s.name])
                    if not r.ok:
                        report["errors"].append("%s: %s" % (s.name, r.brief_error()))
    else:
        path = cfg.get("schedule", "scheduler", "cron_file", "/etc/cron.d/lshme")
        entries = []
        for s in enabled:
            if not s.cron():
                report["errors"].append("%s: calendar is not a 5-field cron expression" % s.name)
                continue
            entries.append((s, command_line(launcher, global_args, s, retries)))
            report["installed"].append("%s (%s)" % (s.name, s.describe()))
        text = render_cron(entries)
        report["files"][path] = text
        if not dry_run:
            d = os.path.dirname(path)
            if not os.path.isdir(d):
                report["errors"].append("%s does not exist (is cron installed?)" % d)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                os.chmod(path, 0o644)
    return report


def uninstall(cfg, runner, owner_args=None):
    """Remove installed schedules. With owner_args (App.global_args()), only files that run this
    installation are touched."""
    removed = []
    d = cfg.get("schedule", "scheduler", "systemd_dir", "/etc/systemd/system")
    if os.path.isdir(d):
        # timers first: whether a timer is ours is read from its service file, which must still exist
        for f in sorted(os.listdir(d), key=lambda n: (not n.endswith(".timer"), n)):
            if f.startswith("lshme-") and f.endswith((".timer", ".service")) and f not in ("lshme-watch.service", "lshme-web.service"):
                if not owned_by(os.path.join(d, f[:-6] + ".service" if f.endswith(".timer") else f), owner_args):
                    continue
                if f.endswith(".timer"):
                    runner.run(["systemctl", "disable", "--now", f])
                os.remove(os.path.join(d, f))
                removed.append(os.path.join(d, f))
        if removed and runner.has("systemctl"):
            runner.run(["systemctl", "daemon-reload"])
    cron = cfg.get("schedule", "scheduler", "cron_file", "/etc/cron.d/lshme")
    if os.path.exists(cron) and owned_by(cron, owner_args):
        os.remove(cron)
        removed.append(cron)
    return removed


def status(cfg, runner):
    be = backend(cfg, runner)
    timers = {}
    if be == "systemd":
        r = runner.run(["systemctl", "list-timers", "--all", "--no-pager", "--no-legend", "lshme-*"])
        for line in r.lines():
            m = re.search(r"(lshme-[\w-]+)\.timer", line)
            if m:
                timers[m.group(1)[6:]] = line.split("  ")[0].strip()
    rows = []
    cron_text = ""
    if be == "cron":
        cron_text = runner.read(cfg.get("schedule", "scheduler", "cron_file", "/etc/cron.d/lshme"))
    for s in schedules(cfg):
        if be == "systemd":
            installed = os.path.exists(os.path.join(cfg.get("schedule", "scheduler", "systemd_dir", "/etc/systemd/system"), "lshme-%s.timer" % s.name))
        else:
            installed = ("--schedule %s " % s.name) in cron_text
        rows.append({"name": s.name, "enabled": s.enabled, "when": s.describe(), "days": s.days, "modules": s.modules,
                     "formats": s.formats, "report_type": s.report_type, "email": s.email,
                     "recipients": ", ".join(s.to) or "(smtp.conf default)", "installed": installed,
                     "next": timers.get(s.name, "")})
    return be, rows
