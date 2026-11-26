# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Reports for a date range you choose (From .. To), from the history database.

Only data inside the range is used: every history query is capped at the end of the To day, so a report for
1-15 November shows nothing from 16 November onwards. The layout follows the length: up to a day looks like the
daily checklist, up to a week like the weekly report, longer like the monthly report.

    vitals report-range --from 2026-11-01 --to 2026-11-15 --formats pdf,html
    web: Reports > Custom date range"""
import re
import time

DAY = 86400
MAX_DAYS = 400                       # history keeps about a year (main.conf [history] keep_days)
_TS_GE = re.compile(r"\bts\s*>=\s*\?")


class _Con(object):
    """The history connection with an upper time limit added to every 'ts >= ?' condition."""

    def __init__(self, con, until):
        self._con, self._until = con, int(until)

    def execute(self, q, args=()):
        if _TS_GE.search(q):
            q = _TS_GE.sub("ts>=? AND ts<=%d" % self._until, q)
        return self._con.execute(q, args)

    def __getattr__(self, name):
        return getattr(self._con, name)


class Bounded(object):
    """A read-only view of History that ends at `until` (the end of the report range)."""

    def __init__(self, hist, until):
        self._hist, self.until = hist, until
        self.con = _Con(hist.con, until)

    def runs(self, host=None, days=30, limit=500):
        cutoff = self.until - days * DAY
        q = "SELECT id, host, ts, schedule, score, status, critical, warning, unknown, duration, report_dir FROM runs WHERE ts >= ?"
        args = [cutoff]
        if host:
            q += " AND host = ?"
            args.append(host)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        cols = ("id", "host", "ts", "schedule", "score", "status", "critical", "warning", "unknown", "duration", "report_dir")
        return [dict(zip(cols, r)) for r in self.con.execute(q, args).fetchall()]

    def __getattr__(self, name):
        """Every other History method runs against the capped connection."""
        fn = getattr(type(self._hist), name, None)
        if callable(fn):
            return lambda *a, **k: fn(self, *a, **k)
        return getattr(self._hist, name)

    def close(self):
        self._hist.close()


def day_start(text):
    try:
        t = time.strptime(str(text).strip()[:10], "%Y-%m-%d")
    except ValueError:
        raise ValueError("dates are YYYY-MM-DD, e.g. 2026-11-01 (got %r)" % text)
    return time.mktime(t[:3] + (0, 0, 0, 0, 0, -1))


def window(start, end, now=None):
    """(since, until) for From .. To, whole days in local time; a To of today ends now."""
    now = now or time.time()
    since = day_start(start)
    until = min(day_start(end or start) + DAY - 1, now)
    if since > now:
        raise ValueError("the From date is in the future")
    if until < since:
        raise ValueError("the To date is before the From date")
    if (until - since) / DAY > MAX_DAYS:
        raise ValueError("choose at most %d days" % MAX_DAYS)
    return since, until


def layout(since, until):
    days = (until - since) / float(DAY)
    return "daily" if days <= 1.01 else "weekly" if days <= 7.01 else "monthly"


def build(run, cfg, paths, start, end, variant="summary", now=None):
    from lshme.reports import periodic
    since, until = window(start, end, now)
    return periodic.build(run, cfg, paths, layout(since, until), variant, now=until, since=since)


def generate(run, cfg, paths, start, end, formats, comprehensive=False, now=None):
    """Write the report files under <reports>/<host>/custom/. -> (doc, outdir, files, notes)"""
    from lshme import reports
    since, until = window(start, end, now)
    docs = [build(run, cfg, paths, start, end, "summary", now)]
    if comprehensive or layout(since, until) == "monthly":
        docs.append(build(run, cfg, paths, start, end, "comprehensive", now))
    outdir = reports.output_dir(paths.report_dir, run.host, "custom")
    base = "%s_custom_%s_to_%s" % (reports.safe(run.host), time.strftime("%Y%m%d", time.localtime(since)), time.strftime("%Y%m%d", time.localtime(until)))
    logo = cfg.get("main", "general", "logo", "")
    files, notes = reports.write_periodic(docs, formats, outdir, base, logo)
    reports.keep_source(notes, outdir, base, "periodic", docs, files, logo)
    return docs[0], outdir, files, notes


def latest_run(app):
    from lshme.reports import templates
    return templates.latest_run(app)

