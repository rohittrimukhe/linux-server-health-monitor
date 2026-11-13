# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Exclusions (accepted risks / known exceptions).

A rule in exclusions.conf removes matching findings from scoring, status and
alerts. Excluded findings are still listed in reports under "Excluded
findings" with the rule and reason, so nothing is hidden silently. Rules can
expire; an expired rule stops applying and is reported once per run.

    [exclude:cockpit-self-signed]
    code = COCKPIT_SELF_SIGNED          ; finding code, wildcards allowed (ORACLE_*)
    component = *                       ; optional component glob(s), comma-separated
    module = cockpit                    ; optional module name
    hosts = *                           ; optional host glob(s), comma-separated (fleet)
    reason = Internal console, CA certificate not required
    until = 2027-12-31                  ; optional expiry date (YYYY-MM-DD)
    owner = infra-team                  ; optional, shown in reports
"""
import fnmatch
import socket
import time

from lshme.core.config import as_list


class Rule(object):
    def __init__(self, name, s):
        self.name = name
        self.code = s.get("code", "").strip()
        self.component = s.get("component", "*").strip() or "*"
        self.components = as_list(self.component) or ["*"]
        self.module = s.get("module", "").strip()
        self.hosts = as_list(s.get("hosts", "")) or ["*"]
        self.reason = s.get("reason", "").strip()
        self.owner = s.get("owner", "").strip()
        self.until = s.get("until", "").strip()
        self.enabled = s.get("enabled", "yes").strip().lower() not in ("no", "false", "0", "off")

    def expires_at(self):
        if not self.until:
            return None
        try:
            return time.mktime(time.strptime(self.until, "%Y-%m-%d")) + 86400  # valid through that day
        except ValueError:
            return 0  # an invalid date counts as expired, so a typo never hides findings forever

    def expired(self, now):
        e = self.expires_at()
        return e is not None and now >= e

    def validate(self):
        errors = []
        if not self.code:
            errors.append("code is required (use * deliberately if you really mean every finding)")
        if self.until and self.expires_at() == 0:
            errors.append("until must be YYYY-MM-DD")
        if not self.reason:
            errors.append("reason is required so reports explain the exception")
        return errors

    def matches(self, code, component, module, host):
        if not self.enabled or not self.code:
            return False
        if not fnmatch.fnmatchcase(code, self.code):
            return False
        if self.module and self.module != module:
            return False
        if not any(fnmatch.fnmatch(component, c) for c in self.components):
            return False
        return any(fnmatch.fnmatch(host, h) for h in self.hosts)


def load(cfg):
    return [Rule(sec.split(":", 1)[1], cfg.section("exclusions", sec)) for sec in cfg.sections("exclusions", "exclude:")]


def active_rules(rules, now=None):
    now = now or time.time()
    return [r for r in rules if r.enabled and not r.expired(now) and not r.validate()]


def match(rules, code, component, module, host=None):
    host = host or socket.gethostname()
    for r in rules:
        if r.matches(code, component, module, host):
            return r
    return None


def apply(run, rules, now=None):
    """Move excluded findings out of each module into run.excluded; report expired/invalid rules."""
    now = now or time.time()
    active = active_rules(rules, now)
    excluded = []
    used = set()
    for m in run.modules:
        keep = []
        for f in m.findings:
            r = match(active, f.code, f.component, m.name, run.host)
            if r is None:
                keep.append(f)
                continue
            used.add(r.name)
            d = f.to_dict()
            d.update(rule=r.name, reason=r.reason, owner=r.owner, until=r.until or "never")
            excluded.append(d)
        m.findings = keep
    run.excluded = excluded
    notes = []
    for r in rules:
        if not r.enabled:
            continue
        problems = r.validate()
        if problems:
            notes.append(("WARNING", "EXCLUSION_INVALID", r.name, "Exclusion rule '%s' is ignored: %s" % (r.name, "; ".join(problems))))
        elif r.expired(now):
            notes.append(("WARNING", "EXCLUSION_EXPIRED", r.name,
                          "Exclusion rule '%s' expired on %s and no longer applies (%s)" % (r.name, r.until, r.code)))
    if notes:
        from lshme.core.model import ModuleResult
        res = ModuleResult("exclusions", "Exclusions", 1)
        for sev, code, comp, msg in notes:
            res.add(sev, code, comp, msg, recommendation="Renew the exception with a new 'until' date, fix the rule, or remove it.")
        res.summary = "%d excluded finding(s), %d rule problem(s)" % (len(excluded), len(notes))
        run.modules.append(res)
    return excluded
