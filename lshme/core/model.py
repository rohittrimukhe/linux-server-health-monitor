# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Result data model shared by modules, the engine, reports and the fleet collector."""
import datetime
import time

SEVERITIES = ("CRITICAL", "WARNING", "UNKNOWN", "INFO")
SEV_RANK = {"CRITICAL": 3, "WARNING": 2, "UNKNOWN": 1, "INFO": 0}
STATUS_ORDER = ("CRITICAL", "WARNING", "UNKNOWN", "HEALTHY")


def iso(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class Finding(object):
    __slots__ = ("severity", "code", "component", "message", "evidence", "recommendation", "module")

    def __init__(self, severity, code, component, message, evidence="", recommendation="", module=""):
        if severity not in SEV_RANK:
            raise ValueError("bad severity %r" % severity)
        self.severity = severity
        self.code = code
        self.component = str(component)
        self.message = str(message)
        self.evidence = str(evidence or "")[:4000]
        self.recommendation = recommendation or ""
        self.module = module

    def to_dict(self):
        return dict((k, getattr(self, k)) for k in self.__slots__)

    @classmethod
    def from_dict(cls, d):
        return cls(d["severity"], d["code"], d.get("component", ""), d.get("message", ""),
                   d.get("evidence", ""), d.get("recommendation", ""), d.get("module", ""))


class Table(object):
    __slots__ = ("title", "headers", "rows", "note", "kind")

    def __init__(self, title, headers, rows, note="", kind="detail"):
        self.title = title
        self.headers = list(headers)
        self.rows = [["" if c is None else c for c in r] for r in rows]
        self.note = note
        self.kind = kind  # "summary" tables also appear in executive reports

    def to_dict(self):
        return {"title": self.title, "headers": self.headers, "rows": self.rows,
                "note": self.note, "kind": self.kind}

    @classmethod
    def from_dict(cls, d):
        return cls(d["title"], d["headers"], d["rows"], d.get("note", ""), d.get("kind", "detail"))


class ModuleResult(object):
    """What one monitoring module produced during a run."""

    def __init__(self, name, title, weight=10):
        self.name = name
        self.title = title
        self.weight = weight
        self.status = "OK"          # OK | PARTIAL | SKIPPED | ERROR
        self.summary = ""
        self.data = {}
        self.findings = []
        self.tables = []
        self.metrics = {}
        self.errors = []
        self.skipped = []
        self.duration = 0.0
        self.score = None

    def add(self, severity, code, component, message, evidence="", recommendation=""):
        f = Finding(severity, code, component, message, evidence, recommendation, self.name)
        self.findings.append(f)
        return f

    def table(self, title, headers, rows, note="", kind="detail"):
        t = Table(title, headers, rows, note, kind)
        self.tables.append(t)
        return t

    def metric(self, key, value):
        try:
            if value is not None:
                self.metrics[key] = float(value)
        except (TypeError, ValueError):
            pass

    def skip(self, reason):
        self.skipped.append(reason)

    def error(self, message):
        self.errors.append(str(message)[:1000])

    def worst(self):
        if not self.findings:
            return "INFO"
        return max(self.findings, key=lambda f: SEV_RANK[f.severity]).severity

    def to_dict(self):
        return {"name": self.name, "title": self.title, "weight": self.weight, "status": self.status,
                "summary": self.summary, "data": self.data,
                "findings": [f.to_dict() for f in self.findings],
                "tables": [t.to_dict() for t in self.tables], "metrics": self.metrics,
                "errors": self.errors, "skipped": self.skipped,
                "duration": round(self.duration, 3), "score": self.score}

    @classmethod
    def from_dict(cls, d):
        m = cls(d["name"], d.get("title", d["name"]), d.get("weight", 10))
        m.status = d.get("status", "OK")
        m.summary = d.get("summary", "")
        m.data = d.get("data", {})
        m.findings = [Finding.from_dict(x) for x in d.get("findings", [])]
        m.tables = [Table.from_dict(x) for x in d.get("tables", [])]
        m.metrics = d.get("metrics", {})
        m.errors = d.get("errors", [])
        m.skipped = d.get("skipped", [])
        m.duration = d.get("duration", 0.0)
        m.score = d.get("score")
        return m


class RunResult(object):
    """A complete health-check run for one host."""

    def __init__(self, host="", days=1, schedule="adhoc"):
        self.host = host
        self.fqdn = host
        self.ip = ""
        self.collected_at = time.time()
        self.finished_at = None
        self.days = days
        self.schedule = schedule
        self.version = ""
        self.meta = {}
        self.modules = []
        self.health = {}
        self.maintenance = None
        self.trends = {}
        self.history = []
        self.excluded = []

    def findings(self, min_severity="INFO"):
        floor = SEV_RANK[min_severity]
        out = [f for m in self.modules for f in m.findings if SEV_RANK[f.severity] >= floor]
        return sorted(out, key=lambda f: (-SEV_RANK[f.severity], f.module, f.component))

    def module(self, name):
        for m in self.modules:
            if m.name == name:
                return m
        return None

    def counts(self):
        c = dict((s, 0) for s in SEVERITIES)
        for f in self.findings():
            c[f.severity] += 1
        return c

    def metrics(self):
        out = {}
        for m in self.modules:
            out.update(m.metrics)
        return out

    def to_dict(self):
        return {"host": self.host, "fqdn": self.fqdn, "ip": self.ip,
                "collected_at": self.collected_at, "collected_at_text": iso(self.collected_at),
                "finished_at": self.finished_at, "days": self.days, "schedule": self.schedule,
                "version": self.version, "meta": self.meta, "health": self.health,
                "maintenance": self.maintenance, "trends": self.trends, "history": self.history, "excluded": self.excluded,
                "modules": [m.to_dict() for m in self.modules]}

    @classmethod
    def from_dict(cls, d):
        r = cls(d.get("host", ""), d.get("days", 1), d.get("schedule", "adhoc"))
        r.fqdn = d.get("fqdn", r.host)
        r.ip = d.get("ip", "")
        r.collected_at = d.get("collected_at", time.time())
        r.finished_at = d.get("finished_at")
        r.version = d.get("version", "")
        r.meta = d.get("meta", {})
        r.health = d.get("health", {})
        r.maintenance = d.get("maintenance")
        r.trends = d.get("trends", {})
        r.history = d.get("history", [])
        r.excluded = d.get("excluded", [])
        r.modules = [ModuleResult.from_dict(x) for x in d.get("modules", [])]
        return r
