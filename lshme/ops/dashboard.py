# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Fleet web dashboard.

A single self-contained HTML page (no external assets, auto-refresh) showing
every server's latest status, with filters, search and a drill-down HTML report
per server. Built from the latest results the central server holds:
data/fleet/<host>.json (fleet collection) and data/latest.json (this host).
An optional, minimal read-only HTTP server can publish it.
"""
import base64
import html
import json
import os
import time

from lshme import APP_NAME, BRAND_LOGO, COPYRIGHT, VERSION
from lshme.core.model import RunResult

E = lambda s: html.escape(str(s if s is not None else ""))
COLORS = {"CRITICAL": "#c62828", "WARNING": "#ef8f00", "HEALTHY": "#2e7d32", "UNREACHABLE": "#6a1b9a", "STALE": "#6d6d6d"}


def fleet_dir(paths):
    return os.path.join(paths.data_dir, "fleet")


def save_results(paths, runs, errors):
    """Keep the latest result (or error) per server for the dashboard."""
    d = fleet_dir(paths)
    os.makedirs(d, exist_ok=True)
    for r in runs:
        with open(os.path.join(d, "%s.json" % _safe(r.host)), "w", encoding="utf-8") as f:
            json.dump(r.to_dict(), f, default=str)
    if errors is not None:
        with open(os.path.join(d, "_errors.json"), "w", encoding="utf-8") as f:
            json.dump({"time": time.time(), "errors": errors}, f)


def _safe(s):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s or "host")


def load_results(paths):
    runs, errors = {}, {}
    candidates = [os.path.join(paths.data_dir, "latest.json")]
    d = fleet_dir(paths)
    if os.path.isdir(d):
        candidates += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".json") and not f.startswith("_")]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                r = RunResult.from_dict(json.load(f))
        except (IOError, OSError, ValueError, KeyError):
            continue
        if r.host not in runs or r.collected_at > runs[r.host].collected_at:
            runs[r.host] = r
    try:
        with open(os.path.join(d, "_errors.json"), encoding="utf-8") as f:
            errors = json.load(f).get("errors", {})
    except (IOError, OSError, ValueError):
        pass
    for host in list(errors):
        if host in runs:
            errors.pop(host)  # a later successful result supersedes an old error
    return list(runs.values()), errors


def _logo():
    try:
        with open(BRAND_LOGO, "rb") as f:
            return '<img class="logo" alt="LRS Services" src="data:image/png;base64,%s">' % base64.b64encode(f.read()).decode("ascii")
    except (IOError, OSError):
        return ""


CSS = """
:root{--bg:#f4f6f9;--card:#fff;--ink:#1d2733;--muted:#5b6876;--line:#d9dee5}
@media (prefers-color-scheme:dark){:root{--bg:#11161c;--card:#1b222b;--ink:#e6ebf1;--muted:#9aa7b4;--line:#2c3540}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{background:linear-gradient(120deg,#0b3d6e,#0b5cad);color:#fff;padding:18px 16px}
.inner{max-width:1400px;margin:0 auto;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
h1{margin:0;font-size:22px}header p{margin:2px 0 0;opacity:.85}.logo{margin-left:auto;max-height:46px;background:#fff;border-radius:8px;padding:5px 9px}
.wrap{max-width:1400px;margin:0 auto;padding:16px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.chip{border:0;border-radius:999px;padding:6px 12px;color:#fff;font-weight:600;cursor:pointer;opacity:.9}.chip.on{outline:3px solid var(--ink);opacity:1}
input,select{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font:inherit}
input{flex:1;min-width:160px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-left:6px solid;border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:6px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.host{font-weight:700;font-size:15px;word-break:break-all}
.score{font-size:24px;font-weight:800}.st{font-size:11px;font-weight:800;letter-spacing:.04em}
.meta{color:var(--muted);font-size:12px}.tags span{display:inline-block;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:0 6px;margin-right:4px;font-size:11px}
ul{margin:2px 0 0;padding-left:18px;font-size:12.5px}a{color:#0b5cad}@media (prefers-color-scheme:dark){a{color:#7fb3ff}}
footer{color:var(--muted);text-align:center;font-size:12px;margin:24px 0 8px}
.empty{color:var(--muted);padding:40px;text-align:center}
.charts{display:grid;grid-template-columns:minmax(280px,1fr) minmax(300px,2fr);gap:12px;margin-bottom:14px}
@media (max-width:760px){.charts{grid-template-columns:1fr}}
.cardc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;overflow:hidden}.cardc h3{margin:0 0 8px;font-size:14px}
.pie{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
"""

JS = """
(function(){var f='ALL';var q=document.getElementById('q'),g=document.getElementById('g');
function apply(){var t=q.value.toLowerCase(),gv=g.value;document.querySelectorAll('.card').forEach(function(c){
var ok=(f==='ALL'||c.dataset.status===f)&&(!gv||c.dataset.group===gv)&&(!t||c.textContent.toLowerCase().indexOf(t)>=0);
c.style.display=ok?'':'none';});}
document.querySelectorAll('.chip').forEach(function(b){b.addEventListener('click',function(){f=b.dataset.f;
document.querySelectorAll('.chip').forEach(function(x){x.classList.toggle('on',x===b);});apply();});});
q.addEventListener('input',apply);g.addEventListener('change',apply);})();
"""


def build(cfg, paths, outdir=None, stale_hours=None):
    """Write index.html (+ per-server reports) and return its path."""
    from lshme.reports import doc as docmod
    from lshme.reports import html as htmlrep
    outdir = outdir or os.path.join(paths.report_dir, "_dashboard")
    os.makedirs(os.path.join(outdir, "servers"), exist_ok=True)
    stale_hours = stale_hours or cfg.getfloat("main", "dashboard", "stale_hours", 26)
    refresh = cfg.getint("main", "dashboard", "refresh_seconds", 300)
    runs, errors = load_results(paths)
    now = time.time()
    cards, counts, groups = [], {}, set()
    order = {"UNREACHABLE": 0, "CRITICAL": 1, "STALE": 2, "WARNING": 3, "HEALTHY": 4}
    items = []
    for r in runs:
        status = r.health.get("status", "UNKNOWN")
        if now - r.collected_at > stale_hours * 3600:
            status = "STALE"
        items.append((status, r))
    for host, err in errors.items():
        items.append(("UNREACHABLE", host if isinstance(host, str) else str(host)))
    for status, r in sorted(items, key=lambda x: (order.get(x[0], 9), (x[1].health.get("score", 0) if isinstance(x[1], RunResult) else -1))):
        counts[status] = counts.get(status, 0) + 1
        color = COLORS.get(status, "#555")
        if isinstance(r, RunResult):
            page = "servers/%s.html" % _safe(r.host)
            try:
                htmlrep.render(docmod.build(r, cfg, "technical"), os.path.join(outdir, page))
            except Exception:
                page = ""
            group = r.meta.get("group", "")
            groups.add(group)
            c = r.health.get("counts", {})
            issues = "".join("<li>%s: %s</li>" % (E(f.component[:40]), E(f.message[:110])) for f in r.findings("WARNING")[:3])
            cards.append(
                '<div class="card" style="border-left-color:%s" data-status="%s" data-group="%s"><div class="top"><span class="host">%s</span>'
                '<span class="score" style="color:%s">%s</span></div><div class="st" style="color:%s">%s</div>'
                '<div class="tags">%s%s</div><div class="meta">critical %d · warning %d · %s</div>%s%s</div>' % (
                    color, status, E(group), E(r.fqdn or r.host), color, r.health.get("score", ""), color, status,
                    ("<span>%s</span>" % E(group)) if group else "", ("<span>%s</span>" % E(r.meta.get("environment"))) if r.meta.get("environment") and r.meta.get("environment") != group else "",
                    c.get("CRITICAL", 0), c.get("WARNING", 0), time.strftime("%Y-%m-%d %H:%M", time.localtime(r.collected_at)),
                    ("<ul>%s</ul>" % issues) if issues else "", ('<a href="%s">Full report &rarr;</a>' % page) if page else ""))
        else:
            cards.append('<div class="card" style="border-left-color:%s" data-status="UNREACHABLE" data-group=""><div class="top"><span class="host">%s</span></div>'
                         '<div class="st" style="color:%s">UNREACHABLE</div><div class="meta">%s</div></div>' % (
                             color, E(r), color, E(errors.get(r, ""))[:200]))
    chips = '<button class="chip on" data-f="ALL" style="background:#0b5cad">All %d</button>' % len(items)
    for st in ("CRITICAL", "WARNING", "HEALTHY", "STALE", "UNREACHABLE"):
        if counts.get(st):
            chips += '<button class="chip" data-f="%s" style="background:%s">%s %d</button>' % (st, COLORS[st], st.title(), counts[st])
    from lshme.reports import charts as ch
    pie_items = [(st.title(), counts.get(st, 0), COLORS[st]) for st in ("CRITICAL", "WARNING", "HEALTHY", "STALE", "UNREACHABLE")]
    scores = [(r.host.split(".")[0], r.health.get("score", 0)) for st, r in items if isinstance(r, RunResult)]
    scores.sort(key=lambda x: x[1])
    charts_html = ""
    if items:
        charts_html = ('<div class="charts"><div class="cardc"><h3>Server status</h3><div class="pie">%s%s</div></div>'
                       '<div class="cardc"><h3>Health score by server (lowest first)</h3>%s</div></div>') % (
            ch.donut(pie_items, "servers", 170), ch.legend(pie_items), ch.columns(scores[:30], 100, "", height=230))
    opts = "".join('<option value="%s">%s</option>' % (E(g), E(g)) for g in sorted(x for x in groups if x))
    title = cfg.get("main", "dashboard", "title", "") or "Infrastructure Health Dashboard"
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="refresh" content="%d"><title>%s</title><style>%s</style></head><body>'
            '<header><div class="inner"><div><h1>%s</h1><p>%d servers · updated %s · refreshes every %d min</p></div>%s</div></header>'
            '<div class="wrap"><div class="bar">%s<input id="q" placeholder="Search server, issue, group..." aria-label="Search">'
            '<select id="g" aria-label="Group"><option value="">All groups</option>%s</select></div>'
            '%s<div class="grid">%s</div>%s<footer>%s v%s · read-only monitoring<br>%s</footer></div><script>%s</script></body></html>') % (
        refresh, E(title), CSS, E(title), len(items), time.strftime("%Y-%m-%d %H:%M"), max(1, refresh // 60), _logo(),
        chips, opts, charts_html, "".join(cards), '' if cards else '<div class="empty">No results yet. Run "healthcheck fleet run" or "healthcheck run".</div>',
        E(APP_NAME), E(VERSION), E(COPYRIGHT), JS)
    path = os.path.join(outdir, "index.html")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, path)
    return path, len(items), counts


def serve(directory, bind="127.0.0.1", port=8088):
    """Read-only static server for the dashboard (GET/HEAD only, directory listing off)."""
    import posixpath
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    from urllib.parse import unquote, urlsplit

    root = os.path.abspath(directory)

    class Handler(SimpleHTTPRequestHandler):
        server_version = "LSHME-dashboard"
        sys_version = ""

        def translate_path(self, path):  # Python 3.6 has no 'directory' argument
            path = posixpath.normpath(unquote(urlsplit(path).path)).lstrip("/")
            full = os.path.abspath(os.path.join(root, path))
            return full if full.startswith(root) else root

        def list_directory(self, path):
            self.send_error(403, "Directory listing disabled")
            return None

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-cache")
            SimpleHTTPRequestHandler.end_headers(self)

        def log_message(self, *a):
            pass

    httpd = HTTPServer((bind, int(port)), Handler)
    return httpd
