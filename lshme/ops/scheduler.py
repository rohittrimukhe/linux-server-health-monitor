# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Scheduling: daily / weekly / monthly / hourly / custom runs via systemd timers
(preferred) or /etc/cron.d. Each schedule has its own modules, lookback days,
report formats, report type and recipients."""
import os
import re
import shlex

from lshme.core.config import as_bool, as_list

WEEKDAYS = {"mon": (1, "Mon"), "tue": (2, "Tue"), "wed": (3, "Wed"), "thu": (4, "Thu"), "fri": (5, "Fri"),
            "sat": (6, "Sat"), "sun": (0, "Sun"), "1": (1, "Mon"), "2": (2, "Tue"), "3": (3, "Wed"),
            "4": (4, "Thu"), "5": (5, "Fri"), "6": (6, "Sat"), "7": (0, "Sun"), "0": (0, "Sun")}


class Schedule(object):
    def __init__(self, name, s):
        self.name = name
        self.enabled = as_bool(s.get("enabled", "yes"), True)
        self.frequency = s.get("frequency", "daily").lower()
        self.time = s.get("time", "07:00")
        self.weekday = s.get("weekday", "mon").lower()[:3]
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

    def validate(self):
        errors = []
        if self.frequency not in ("hourly", "daily", "weekly", "monthly", "custom"):
            errors.append("frequency must be hourly, daily, weekly, monthly or custom")
        if self.frequency != "custom" and not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", self.time):
            errors.append("time must be HH:MM")
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
            return "*-*-* %02d:%02d:00" % (h, m)
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
            return "%d %d * * *" % (m, h)
        if self.frequency == "weekly":
            return "%d %d * * %d" % (m, h, WEEKDAYS[self.weekday][0])
        return "%d %d %d * *" % (m, h, int(self.monthday))

    def describe(self):
        if self.frequency == "custom":
            return "custom: %s" % self.calendar
        if self.frequency == "hourly":
            return "every hour at :%02d" % self.hm()[1]
        if self.frequency == "daily":
            return "every day at %s" % self.time
        if self.frequency == "weekly":
            return "every %s at %s" % (WEEKDAYS[self.weekday][1], self.time)
        return "day %s of every month at %s" % (self.monthday, self.time)


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
    args = [launcher] + list(global_args) + ["run", "--schedule", sched.name, "--quiet", "--retries", str(retries)]
    return " ".join(shlex.quote(a) for a in args)


def render_systemd(sched, cmdline):
    service = ("[Unit]\nDescription=LSHME health check (%s)\nAfter=network-online.target\nWants=network-online.target\n\n"
               "[Service]\nType=oneshot\nExecStart=%s\nNice=10\nIOSchedulingClass=idle\nCPUQuota=50%%\nMemoryMax=512M\n"
               "TimeoutStartSec=3h\nPrivateTmp=true\n# LSHME_MANAGED\n" % (sched.name, cmdline))
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
    all_s = schedules(cfg)
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


def uninstall(cfg, runner):
    removed = []
    d = cfg.get("schedule", "scheduler", "systemd_dir", "/etc/systemd/system")
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.startswith("lshme-") and f.endswith((".timer", ".service")):
                if f.endswith(".timer"):
                    runner.run(["systemctl", "disable", "--now", f])
                os.remove(os.path.join(d, f))
                removed.append(os.path.join(d, f))
        if removed and runner.has("systemctl"):
            runner.run(["systemctl", "daemon-reload"])
    cron = cfg.get("schedule", "scheduler", "cron_file", "/etc/cron.d/lshme")
    if os.path.exists(cron):
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
