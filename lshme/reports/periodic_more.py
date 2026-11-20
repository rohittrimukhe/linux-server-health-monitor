# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Extra sections of the weekly and monthly reports: this period against the last one, repeating log errors,
patch and compliance, backup verification, and the certificate and licence calendar."""
import re
import time

from lshme.core.util import fmt_bytes
from lshme.reports import periodic as P
from lshme.reports.periodic import Cell, DAY, MB, chip, day_key, table

BAD_WHEN_UP = True


# -- this period against the last one ---------------------------------------------------------------
def window_stats(rows, lo, hi, key):
    vals = [r[key] for r in rows if lo <= r["ts"] < hi]
    mx = [r[key + "_max"] for r in rows if lo <= r["ts"] < hi]
    if not vals:
        return None
    return sum(vals) / len(vals), max(mx)


def change_cell(now_v, before_v, unit="", up_is_bad=True, rel_floor=0.05, fmt="%.1f"):
    if now_v is None or before_v is None:
        return Cell("-", "fl")
    d = now_v - before_v
    base = abs(before_v) or 1.0
    if d == 0:
        return Cell("no change", "fl")
    arrow = "up" if d > 0 else "down"
    txt = "%s %s%s (%s)" % (arrow, fmt % abs(d), unit, ("%+.0f%%" % (100.0 * d / before_v)) if before_v else "was none")
    if abs(d) / base < rel_floor:
        return Cell(txt, "fl")
    bad = (d > 0) == up_is_bad
    return Cell(txt, "up" if bad else "dn")


def compare(data, ptype):
    hist = data.hist
    if not hist:
        return None
    days = data.days
    lo_now, lo_prev = data.now - days * DAY, data.now - 2 * days * DAY
    rows_h = hist.hourly(data.host, lo_prev)
    runs = [r for r in hist.runs(data.host, days * 2 + 1, 3000)]
    label = {"weekly": ("This week", "Last week"), "monthly": ("This month", "Last month")}[ptype]
    out = []
    for key, name, unit in (("cpu", "CPU used (average)", " %"), ("mem", "Memory used (average)", " %"), ("swap", "Swap used (average)", " %"), ("load", "Load per core (average)", "")):
        a, b = window_stats(rows_h, lo_now, data.now + 1, key), window_stats(rows_h, lo_prev, lo_now, key)
        f = "%.2f" if key == "load" else "%.1f"
        out.append([name, (f % a[0] + unit) if a else "-", (f % b[0] + unit) if b else "-", change_cell(a[0] if a else None, b[0] if b else None, unit, True, 0.08, f)])
        out.append([name.replace("average", "peak"), (f % a[1] + unit) if a else "-", (f % b[1] + unit) if b else "-", change_cell(a[1] if a else None, b[1] if b else None, unit, True, 0.08, f)])
    cur = [r for r in runs if r["ts"] >= lo_now]
    prev = [r for r in runs if lo_prev <= r["ts"] < lo_now]

    def avg(rs, k):
        vals = [r[k] for r in rs if r[k] is not None]
        return sum(vals) / len(vals) if vals else None
    for k, name, up_bad in (("score", "Health score (average)", False), ("critical", "Critical findings per check", True), ("warning", "Warnings per check", True)):
        a, b = avg(cur, k), avg(prev, k)
        out.append([name, "%.1f" % a if a is not None else "-", "%.1f" % b if b is not None else "-", change_cell(a, b, "", up_bad, 0.05, "%.1f")])
    out.append(["Days with a critical finding", str(len(set(day_key(r["ts"]) for r in cur if r["critical"]))), str(len(set(day_key(r["ts"]) for r in prev if r["critical"]))),
                change_cell(len(set(day_key(r["ts"]) for r in cur if r["critical"])), len(set(day_key(r["ts"]) for r in prev if r["critical"])), "", True, 0.0, "%.0f")])
    # data growth: how much more space was used during each period (sum over filesystems and over databases)
    for prefix, name in (("fs.used_bytes:", "Filesystem space added"), ("oracle.", "Oracle data added"), ("postgresql.", "PostgreSQL data added"), ("mysql.", "MySQL data added")):
        keys = [k for k in data.keys(prefix) if (prefix == "fs.used_bytes:" or k.endswith((".datafile_bytes", ".total_bytes")))]
        if not keys:
            continue
        g_now = g_prev = 0.0
        have = False
        for k in keys:
            pts = hist.series(data.host, k, lo_prev)
            now_pts = [p for p in pts if p[0] >= lo_now]
            prev_pts = [p for p in pts if lo_prev <= p[0] < lo_now]
            if len(now_pts) >= 2:
                g_now += now_pts[-1][1] - now_pts[0][1]
                have = True
            if len(prev_pts) >= 2:
                g_prev += prev_pts[-1][1] - prev_pts[0][1]
        if have:
            out.append([name, fmt_bytes(g_now), fmt_bytes(g_prev), change_cell(g_now / MB, g_prev / MB, " MB", True, 0.1, "%.0f")])
    if not out:
        return None
    return table("%s compared with %s" % label, ["", label[0], label[1], "Change"], out, align=["", "r", "r", ""],
                 legend="Red = moved in the worse direction, green = better, grey = about the same (less than 5-8 % difference).")


# -- repeating log errors ---------------------------------------------------------------------------
def log_digest(data):
    out = []
    hist = data.hist
    if hist:
        q = ("SELECT module, code, component, COUNT(DISTINCT run_id) n, COUNT(DISTINCT date(ts, 'unixepoch', 'localtime')) d, MAX(severity), MAX(message), MIN(ts), MAX(ts) "
             "FROM findings WHERE host=? AND ts>=? GROUP BY module, code, component ORDER BY (module='logs') DESC, n DESC LIMIT 12")
        rows = []
        for module, code, comp, n, d, sev, msg, first, last in hist.con.execute(q, (data.host, data.since)).fetchall():
            rows.append([Cell("CRIT" if sev == "CRITICAL" else "WARN", "chip-cr" if sev == "CRITICAL" else "chip-wn"), (comp or code)[:40], (msg or "")[:110], n, d,
                         P.stamp(first, "%d %b"), P.stamp(last, "%d %b")])
        if rows:
            out.append(table("Problems that repeated most often", ["Level", "Where", "What", "Checks", "Days", "First seen", "Last seen"], rows, align=["", "", "", "r", "r", "", ""],
                             legend="'Checks' is the number of health checks that found it; 'Days' is on how many different days."))
    cats = {}
    for k in data.keys("logs.count:"):
        pts = data.points(k, 31)
        pts = [p for p in pts if p[0] >= data.since]
        if not pts:
            continue
        perday = data.per_day(pts)
        total = sum(perday.values())
        if total <= 0:
            continue
        worst = max(perday.items(), key=lambda kv: kv[1])
        cats[k.split(":", 1)[1]] = (total, len(perday), worst)
    if cats:
        rows = [[Cell(c, "b"), int(t), n, "%s (%d)" % (w[0], w[1])] for c, (t, n, w) in sorted(cats.items(), key=lambda kv: -kv[1][0])[:12]]
        out.append(table("Log events by type", ["Type of event", "Events in the period", "Days with events", "Worst day"], rows, align=["", "r", "r", ""],
                         legend="Counted from the system logs by each health check (OOM kills, I/O errors, failed logins, service failures ...)."))
    return out


# -- patch and compliance ---------------------------------------------------------------------------
def parse_patch_age_days(text, now):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text or "") or None
    if m:
        try:
            return (now - time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, 0, 0, 0, 0, -1))) / DAY
        except (ValueError, OverflowError):
            return None
    for fmt in ("%a %d %b %Y %I:%M:%S %p %Z", "%a %d %b %Y %H:%M:%S %Z", "%a %b %d %H:%M:%S %Y"):
        try:
            return (now - time.mktime(time.strptime((text or "").strip(), fmt))) / DAY
        except ValueError:
            continue
    return None


def compliance(data):
    run = data.run
    sec = run.module("security") if run else None
    if sec is None or not sec.data:
        return [P.note("The security checks did not run, so there is no patch and compliance information.")]
    d, cfg = sec.data, data.cfg
    pol = lambda k, dflt="": cfg.get("main", "security", k, dflt)
    rows = []

    def add(control, expected, actual, ok):
        rows.append([control, expected, str(actual), chip("ok" if ok else "wn")])
    exp_se = pol("expected_selinux", "enforcing").lower()
    add("SELinux", exp_se, d.get("selinux") or "unknown", (d.get("selinux") or "").lower() in (exp_se, "apparmor"))
    fw = d.get("firewall") or {}
    add("Firewall", "active", ("%s %s" % (fw.get("tool", "none"), "active" if fw.get("active") else "inactive")).strip(), bool(fw.get("active")) or not cfg.getbool("main", "security", "require_firewall", True))
    ssh = d.get("sshd") or {}
    root_ok = ssh.get("permitrootlogin", "default") in ("no", "prohibit-password", "without-password", "forced-commands-only")
    add("SSH root login", "not allowed", ssh.get("permitrootlogin", "default"), root_ok or cfg.getbool("main", "security", "permit_root_login_allowed", False))
    add("Accounts with UID 0", "only root", ", ".join(d.get("uid0_accounts") or []) or "none", (d.get("uid0_accounts") or ["root"]) == ["root"])
    add("Accounts without a password", "none", len(d.get("empty_passwords") or []), not d.get("empty_passwords"))
    add("sudo without a password", "none", len(d.get("sudo_nopasswd") or []), not d.get("sudo_nopasswd"))
    add("Audit service (auditd)", "active", d.get("auditd") or "unknown", d.get("auditd") == "active" or not cfg.getbool("main", "security", "require_auditd", False))
    upd = d.get("updates") or {}
    sec_n = upd.get("security")
    add("Pending security updates", "0", "unknown" if sec_n is None else sec_n, sec_n == 0 if sec_n is not None else False)
    age = parse_patch_age_days(d.get("last_patch") or "", data.now)
    add("Last package change", "within 30 days", ("%d days ago" % age) if age is not None else (d.get("last_patch") or "unknown"), age is not None and age <= 30)
    passed = sum(1 for r in rows if r[3].style == "chip-ok")
    out = [table("Patch and compliance checks", ["Control", "Expected", "Found", "Result"], rows,
                 legend="%d of %d checks pass. The expected values come from the [security] settings in main.conf." % (passed, len(rows)))]
    pts = [p for p in data.points("security.pending_security_updates", 31) if p[0] >= data.since]
    if len(pts) >= 2:
        out.append(P.note("Pending security updates: %d at the start of the period, %d now." % (pts[0][1], pts[-1][1])))
    return out


# -- backup verification ----------------------------------------------------------------------------
def backup_keys(data):
    names = {}
    for k in data.keys("backup."):
        if k.startswith("backup.age_hours:"):
            names[k.split(":", 1)[1]] = ("backup.age_hours:" + k.split(":", 1)[1], "backup.size_bytes:" + k.split(":", 1)[1])
        elif k in ("backup.netbackup_age_hours", "backup.veeam_age_hours"):
            names[k.split(".", 1)[1].replace("_age_hours", "")] = (k, None)
    for k in data.keys("oracle."):
        if k.endswith(".backup_age_hours"):
            names["oracle " + k.split(".")[1] + " (RMAN)"] = (k, None)
    return names


def backups(data):
    names = backup_keys(data)
    if not names:
        return []
    limit = data.cfg.threshold("backup", "default_max_age_hours", 26)
    rows, matrix_cols = [], []
    for name, (age_key, size_key) in sorted(names.items()):
        ages = [p for p in data.points(age_key, 31) if p[0] >= data.since]
        if not ages:
            continue
        sizes = [p for p in (data.points(size_key, 31) if size_key else []) if p[0] >= data.since]
        age_now = ages[-1][1]
        worst = max(v for _, v in ages)
        size_txt, size_cell, verdict = "-", Cell("-", "fl"), "ok"
        if len(sizes) >= 2:
            first, last = sizes[0][1], sizes[-1][1]
            size_txt = fmt_bytes(last)
            if first > 0 and last < 0.7 * first:
                size_cell, verdict = Cell("%s (smaller by %d %%)" % (fmt_bytes(last), 100 - 100 * last / first), "cr"), "cr"
            else:
                size_cell = Cell("%s (%s)" % (fmt_bytes(last), ("+" if last >= first else "-") + fmt_bytes(abs(last - first))), "fl")
        elif sizes:
            size_cell = Cell(fmt_bytes(sizes[-1][1]), "fl")
        if age_now > limit * 2:
            verdict = "cr"
        elif age_now > limit and verdict == "ok":
            verdict = "wn"
        rows.append([Cell(name, "b"), "%.0f h" % age_now, Cell("%.0f h" % worst, P.level_for(worst, limit, limit * 2)), size_cell, chip(verdict)])
        matrix_cols.append((name, data.per_day(ages)))
    out = [table("Backups: how old, how big", ["Backup", "Newest now", "Oldest it got", "Size of newest (change)", "Result"], rows, align=["", "r", "r", "", ""],
                 legend="Age limit %.0f hours (amber above, red above twice that). A backup that became more than 30 %% smaller than at the start of the period is flagged, because that often means the job failed early." % limit)]
    if matrix_cols and data.days > 1:
        dates = data.dates()
        mrows = []
        for d in dates:
            row = [Cell(d, "b")]
            for _, pd in matrix_cols[:6]:
                v = pd.get(d)
                row.append(Cell("-", "fl") if v is None else Cell("%.0f h" % v, P.level_for(v, limit, limit * 2)))
            mrows.append(row)
        out.append(table("Age of the newest backup on each day", ["Date"] + [n for n, _ in matrix_cols[:6]], mrows))
    return out


# -- certificate and licence calendar -------------------------------------------------------------------
def calendar(data):
    items = []
    for prefix, kind in (("cert.days_left:", "Certificate"), ("licence.days_left:", "Licence")):
        for k in data.keys(prefix):
            v = data.cur.get(k)
            if v is None:
                pts = data.points(k, 31)
                v = pts[-1][1] if pts else None
            if v is not None:
                items.append((v, kind, k.split(":", 1)[1]))
    if not items:
        return []
    items.sort()
    rows = []
    bands = {"expired": 0, "30": 0, "60": 0, "90": 0}
    for days, kind, name in items:
        if days < 0:
            band, lv, bands["expired"] = "Expired", "cr", bands["expired"] + 1
        elif days <= 30:
            band, lv, bands["30"] = "Within 30 days", "cr", bands["30"] + 1
        elif days <= 60:
            band, lv, bands["60"] = "Within 60 days", "wn", bands["60"] + 1
        elif days <= 90:
            band, lv, bands["90"] = "Within 90 days", "wn", bands["90"] + 1
        else:
            band, lv = "Later", "ok"
        rows.append([Cell(name[:70], "b"), kind, P.stamp(data.now + days * DAY, "%d %b %Y"), Cell("%d" % days, lv), chip(lv), band])
    return [P.note("%d expired, %d within 30 days, %d within 31-60 days, %d within 61-90 days." % (bands["expired"], bands["30"], bands["60"], bands["90"])),
            table("Certificate and licence expiry calendar", ["Name", "Type", "Expires on", "Days left", "Result", "Band"], rows, align=["", "", "", "r", "", ""],
                  legend="Certificates come from [certificates] in certificates.conf. Licences are listed in its [licences] section as name = YYYY-MM-DD.")]


def sections(data, ptype):
    """[(title, blocks)] for the weekly and monthly reports."""
    out = []
    if ptype in ("weekly", "monthly"):
        c = compare(data, ptype)
        if c:
            out.append(("This period against the last one" if ptype == "weekly" else "This month against last month", [c]))
    ld = log_digest(data)
    if ld:
        out.append(("Repeating problems and log errors", ld))
    out.append(("Patch and compliance", compliance(data)))
    bk = backups(data)
    if bk:
        out.append(("Backup verification", bk))
    cal = calendar(data)
    if cal:
        out.append(("Certificate and licence calendar", cal))
    return out
