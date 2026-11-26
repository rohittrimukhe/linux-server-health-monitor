# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The web server: sign-in, a JSON API and the single-page interface (static files).

All request handling is in WebApp.handle(), which takes a plain description of a request and returns a plain
response, so every route and every security rule can be tested without sockets. Security: sign-in with salted
hashed passwords, rate limiting, expiring random session tokens in HttpOnly SameSite=Strict cookies, a CSRF token
and an Origin check on every change, roles (admin may change things, viewer may only look), strict
Content-Security-Policy, no directory listing and no path outside the report folder is ever served."""
import base64
import binascii
import hmac
import json
import mimetypes
import os
import re
import socketserver
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from lshme import APP_NAME, BRAND_LOGO, VERSION
from lshme.core.config import backup_config, restore_config
from lshme.core.lock import LockBusy
from lshme.ops import archive, branding, discover, reportlist, sysstats
from lshme.reports import templates as report_templates
from lshme.web import auth, confmeta

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "application/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/vitals.svg": ("vitals.svg", "image/svg+xml"), "/favicon.svg": ("vitals.svg", "image/svg+xml")}
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src 'self'; "
       "frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
REPORT_CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:; "
              "frame-ancestors 'self'; sandbox allow-scripts allow-popups")
INLINE_TYPES = {".html": "text/html; charset=utf-8", ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
                ".csv": "text/plain; charset=utf-8", ".json": "text/plain; charset=utf-8"}
DOWNLOAD_TYPES = {".csv": "text/csv; charset=utf-8", ".json": "application/json"}
MAX_BODY = 1 << 20
COOKIE = "lshme_session"
ACTIONS = ("schedule-install", "watch-install", "watch-remove", "email-test", "config-backup")


class Req(object):
    def __init__(self, method, path, query=None, body=b"", headers=None, ip="127.0.0.1"):
        self.method, self.path, self.query, self.body, self.ip = method, path, query or {}, body, ip
        self.headers = dict((k.lower(), v) for k, v in (headers or {}).items())

    def json(self):
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("the request is not valid JSON")
        if not isinstance(data, dict):
            raise ValueError("the request must be a JSON object")
        return data

    def cookie(self, name):
        for part in self.headers.get("cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""


class Resp(object):
    def __init__(self, status=200, body=b"", ctype="application/json", headers=None):
        self.status, self.body, self.ctype, self.headers = status, body, ctype, dict(headers or {})


def jr(obj, status=200, headers=None):
    return Resp(status, json.dumps(obj, default=str).encode("utf-8"), "application/json", headers)


def err(message, status=400):
    return jr({"error": message}, status)


class WebApp(object):
    def __init__(self, app, secure=False):
        self.app, self.secure = app, secure
        cfg = app.cfg
        self.users = auth.Users(app)
        self.sessions = auth.Sessions(cfg.getint("web", "web", "session_minutes", 30))
        self.limiter = auth.RateLimiter(cfg.getint("web", "web", "max_login_failures", 5))
        self.lock = threading.RLock()  # serialises configuration changes
        self.sampler = sysstats.Sampler()
        self.sample, self.sample_at, self.sample_lock = None, 0.0, threading.Lock()
        self.job = {"running": False, "progress": 0, "total": 0, "title": "", "started": 0, "finished": 0, "result": None, "error": ""}
        self.fleet_job = {"running": False, "title": "", "started": 0, "finished": 0, "result": None, "error": ""}
        self.cert_job = {"running": False, "started": 0, "finished": 0, "result": None}
        self.pending_profile = None

    # -- helpers ------------------------------------------------------------------------------------
    def audit(self, action, sess, **kw):
        self.app.audit(action, user=sess["user"] if sess else "-", ip=(sess or {}).get("ip", ""), **kw)

    def live_sample(self):
        with self.sample_lock:
            if self.sample is None or time.time() - self.sample_at > 1.0:
                self.sample = self.sampler.sample()
                self.sample_at = time.time()
            return self.sample

    def build_id(self):
        """Which program is installed (version and commit): an open dashboard tab compares it and offers a reload after an update."""
        try:
            st = os.stat(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"))
            return "%s-%d-%d" % (VERSION, int(st.st_mtime), st.st_size)
        except OSError:
            return VERSION

    def security_headers(self, resp, ctype_is_report=False):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Cache-Control", "no-store")
        h.setdefault("X-LRS-Build", self.build_id())
        h.setdefault("Content-Security-Policy", CSP)
        if self.secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    def static_file(self, req, name, ctype):
        """The page, its script and styles: compressed once (gzip), revalidated with an ETag so a reload costs a tiny 304."""
        import gzip as _gz
        p = os.path.join(STATIC_DIR, name)
        st = os.stat(p)
        tag = '"%x-%x"' % (int(st.st_mtime), st.st_size)
        cache = self.__dict__.setdefault("_static", {})
        hit = cache.get(name)
        if not hit or hit[0] != tag:
            with open(p, "rb") as f:
                raw = f.read()
            hit = (tag, raw, _gz.compress(raw, 6) if len(raw) > 1024 else None)
            cache[name] = hit
        headers = {"Cache-Control": "no-cache", "ETag": tag, "Vary": "Accept-Encoding"}
        if req.headers.get("if-none-match") == tag:
            return Resp(304, b"", ctype, headers)
        if hit[2] is not None and "gzip" in (req.headers.get("accept-encoding") or ""):
            headers["Content-Encoding"] = "gzip"
            return Resp(200, hit[2], ctype, headers)
        return Resp(200, hit[1], ctype, headers)

    # -- entry point -----------------------------------------------------------------------------------
    def handle(self, req):
        try:
            resp = self._route(req)
        except ValueError as e:
            resp = err(str(e), 400)
        except PermissionError as e:
            resp = err(str(e) or "not allowed", 403)
        except Exception as e:  # never leak a traceback to the browser
            self.app.log.exception("web request failed: %s %s", req.method, req.path)
            resp = err("internal error (see the LSHME log)", 500)
        return self.security_headers(resp)

    def _route(self, req):
        path = req.path
        if req.method in ("GET", "HEAD") and path in STATIC:
            return self.static_file(req, *STATIC[path])
        if req.method == "GET" and path == "/lrs_logo.png" and os.path.isfile(BRAND_LOGO):
            with open(BRAND_LOGO, "rb") as f:
                return Resp(200, f.read(), "image/png", {"Cache-Control": "max-age=86400"})
        if req.method == "GET" and path == "/favicon.ico":
            return Resp(204, b"", "image/x-icon")
        if not (path.startswith("/api/") or path.startswith("/reports/") or path == "/brand/logo"):
            return err("not found", 404)
        sess = self.sessions.get(req.cookie(COOKIE))
        if path == "/api/login" and req.method == "POST":
            return self.login(req)
        if path == "/api/me" and req.method == "GET":
            return self.me(sess)
        if not sess:
            return err("sign in required", 401)
        if req.method not in ("GET", "HEAD", "POST"):
            return err("method not allowed", 405)
        if req.method not in ("GET", "HEAD"):
            self.check_origin_and_csrf(req, sess)
        if path == "/api/logout" and req.method == "POST":
            self.sessions.drop(req.cookie(COOKIE))
            self.audit("web-logout", sess)
            return jr({"ok": True}, headers={"Set-Cookie": self.cookie_header("", 0)})
        if path == "/brand/logo":
            logo = self.branding(sess.get("group", "")).get("logo_path", "")
            if logo and os.path.isfile(logo) and os.path.getsize(logo) < 2 * 1024 * 1024:
                with open(logo, "rb") as f:
                    return Resp(200, f.read(), mimetypes.guess_type(logo)[0] or "image/png", {"Cache-Control": "max-age=3600"})
            return err("not found", 404)
        if sess.get("group"):                                  # a customer account: only its own servers and reports
            if path.startswith("/api/") and path[len("/api/"):] not in ("fleet", "reports", "report", "help", "password"):
                raise PermissionError("this account only sees its own servers and reports")
            if path.startswith("/reports/") and path[len("/reports/"):].split("/")[0] not in self.group_hosts(sess["group"]):
                return err("not found", 404)
        if path.startswith("/reports/"):
            return self.report_file(path, (req.query.get("download") or [""])[0] in ("1", "yes", "true"))
        name = path[len("/api/"):]
        handler = getattr(self, "api_" + name.replace("/", "_"), None)
        if handler is None:
            return err("not found", 404)
        if req.method not in ("GET", "HEAD") and sess["role"] != "admin" and name != "password":
            self.audit("web-denied", sess, route=name)
            raise PermissionError("your account is read-only")
        if name in ("config", "users", "backups", "logs", "audit", "raw", "raw/file", "branding", "branding/logo", "san", "bmc", "onboard") and sess["role"] != "admin":
            raise PermissionError("administrators only")
        return handler(req, sess)

    def check_origin_and_csrf(self, req, sess):
        origin = req.headers.get("origin")
        if origin and urlsplit(origin).netloc != req.headers.get("host", ""):
            raise PermissionError("cross-site request refused")
        token = req.headers.get("x-csrf-token", "")
        if not token or not hmac.compare_digest(token, sess["csrf"]):
            raise PermissionError("missing or wrong security token; reload the page")

    def cookie_header(self, token, max_age):
        return "%s=%s; Path=/; HttpOnly; SameSite=Strict%s; Max-Age=%d" % (COOKIE, token, "; Secure" if self.secure else "", max_age)

    # -- sign in ----------------------------------------------------------------------------------------
    def login(self, req):
        data = req.json()
        if self.limiter.blocked(req.ip):
            return err("Too many wrong passwords. Wait a few minutes and try again.", 429)
        user, password = str(data.get("user", ""))[:80], str(data.get("password", ""))[:200]
        role = self.users.verify(user, password)
        if not role:
            self.limiter.failure(req.ip)
            self.app.audit("web-login-failed", user=user[:40], ip=req.ip)
            return err("Wrong user name or password.", 401)
        self.limiter.success(req.ip)
        token, sess = self.sessions.create(user, role, req.ip)
        grp = self.users.group(user)
        if grp:
            sess["group"], sess["role"] = grp, "viewer"          # a customer account is always read-only
        self.audit("web-login", sess)
        return jr(self.me_data(sess), headers={"Set-Cookie": self.cookie_header(token, self.sessions.ttl)})

    def me_data(self, sess):
        import socket
        return {"user": sess["user"], "role": sess["role"], "csrf": sess["csrf"], "host": socket.gethostname(), "version": VERSION, "app": APP_NAME,
                "tls": self.secure, "group": sess.get("group", ""), "branding": self.branding(sess.get("group", ""), public=True),
                "server_role": self.server_role()}

    def server_role(self):
        from lshme.ops import role as roles
        return roles.get(self.app.cfg) or "central"

    def branding(self, group, public=False):
        """Customer name, colour and logo for a group ([branding:group] in servers.conf); without a group, the customer of this installation."""
        if not group:
            logo = self.app.cfg.get("main", "general", "logo", "")
            cust = self.app.cfg.get("main", "general", "customer", "")
            has = bool(logo and os.path.isfile(logo))
            out = {"customer": cust, "color": "", "logo": has}
            if not public:
                out["logo_path"] = logo
            return out if (cust or has) else {}
        s = self.app.cfg.section("servers", "branding:" + group)
        if not s:
            return {"customer": group}
        logo = s.get("logo", "")
        out = {"customer": s.get("customer") or group, "color": s.get("color", "") if re.match(r"^#[0-9a-fA-F]{6}$", s.get("color", "")) else "",
               "logo": bool(logo and os.path.isfile(logo))}
        if not public:
            out["logo_path"] = logo
        return out

    def group_hosts(self, group):
        """Names and host names of the servers in a customer group."""
        from lshme.ops import dashboard, fleet
        hosts = set()
        for sv in fleet.servers(self.app.cfg):
            if sv.group == group:
                hosts.update((sv.name, sv.host))
        runs, _ = dashboard.load_results(self.app.paths)
        for r in runs:
            if (r.meta or {}).get("group") == group:
                hosts.add(r.host)
        return hosts

    def me(self, sess):
        return jr(self.me_data(sess)) if sess else err("sign in required", 401)

    # -- live ------------------------------------------------------------------------------------------------
    def api_live(self, req, sess):
        th = self.app.cfg.threshold
        return jr({"sample": self.live_sample(), "health": reportlist.latest_summary(self.app.paths),
                   "limits": {"cpu": [th("cpu", "warning", 85), th("cpu", "critical", 95)], "mem": [th("memory", "warning", 85), th("memory", "critical", 95)],
                              "swap": [th("swap", "warning", 50), th("swap", "critical", 80)], "disk": [th("disk", "warning", 80), th("disk", "critical", 90)],
                              "inode": [th("inode", "warning", 80), th("inode", "critical", 90)]}})

    # -- alerts and central monitoring ---------------------------------------------------------------------------------
    def api_alerts(self, req, sess):
        from lshme.core.config import as_bool
        from lshme.ops import watch as W
        if req.method == "POST":                                 # acknowledge, mute or unmute one problem
            data = req.json()
            action = data.get("action")
            key = str(data.get("key", ""))[:300]
            if not key:
                raise ValueError("no problem given")
            if action == "ack":
                W.acknowledge(self.app.paths, key, sess["user"])
            elif action == "mute":
                hours = float(data.get("hours", 6))
                if hours not in W.MUTE_HOURS:
                    raise ValueError("mute for 1, 6, 24 or 168 hours, or 0 (until it ends)")
                W.mute(self.app.paths, key, sess["user"], hours)
            elif action == "unmute":
                W.unmute(self.app.paths, key)
            else:
                raise ValueError("unknown action")
            self.audit("web-alert-" + action, sess, key=key, hours=data.get("hours"))
            return jr({"ok": True})
        acks = W.load_acks(self.app.paths)
        d, cfg = self.app.paths.data_dir, self.app.cfg
        events, active = [], []
        try:
            with open(os.path.join(d, "watch_events.jsonl"), encoding="utf-8") as f:
                for line in f.read().splitlines()[-200:]:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
        last = {}
        for e in events:
            last[e.get("key")] = e
        try:
            with open(os.path.join(d, "watch_state.json"), encoding="utf-8") as f:
                problems = json.load(f).get("problems") or {}
        except (OSError, ValueError):
            problems = {}
        for key, p in problems.items():
            e = last.get(key) or {}
            active.append({"key": key, "severity": p.get("severity"), "component": e.get("component") or key, "message": e.get("message") or key,
                           "since": p.get("since"), "count": p.get("count"), "alerted": p.get("alerted"), "ack": acks.get(key),
                           "muted": W.is_muted(acks.get(key)), "mute_until": (acks.get(key) or {}).get("mute_until")})
        active.sort(key=lambda a: (0 if a["severity"] == "CRITICAL" else 1, -(a["since"] or 0)))
        s = lambda k: W.setting(cfg, k)
        channels = {"email": as_bool(s("alert_email"), True), "to": s("alert_to"), "syslog": as_bool(s("syslog"), True), "webhook": bool(s("webhook_url")),
                    "min_severity": s("alert_min_severity"), "confirm": s("confirm"), "interval": s("interval"),
                    "slack": bool(s("slack_webhook_url")), "teams": bool(s("teams_webhook_url")), "quiet_hours": s("quiet_hours"),
                    "min_minutes": s("min_minutes"), "digest": as_bool(s("alert_digest"), True) and s("alert_digest_time")}
        try:
            days = max(1, min(90, int((req.query.get("days") or ["30"])[0])))
        except ValueError:
            days = 30
        from lshme.ops import sla
        incidents, summary = sla.incident_log(sla.load_events(os.path.join(d, "watch_events.jsonl")), time.time(), days, 200, acks)
        return jr({"active": active, "events": list(reversed(events))[:40], "channels": channels, "incidents": incidents, "incident_summary": summary})

    def fleet_view(self, group=""):
        import socket as _socket
        from lshme.ops import dashboard, fleet
        cfg = self.app.cfg
        runs, errors = dashboard.load_results(self.app.paths)
        try:
            stale = float(cfg.get("main", "dashboard", "stale_hours", "26") or 26) * 3600
        except ValueError:
            stale = 26 * 3600
        now, rows, me = time.time(), [], _socket.gethostname()
        for r in runs:
            m, top, age = r.metrics(), r.findings("WARNING")[:1], now - r.collected_at
            real = r.health.get("status", "UNKNOWN")
            c = r.health.get("counts", {})
            rows.append({"name": r.meta.get("fleet_name") or r.host, "host": r.host, "ip": r.ip, "roles": r.meta.get("roles") or [], "group": r.meta.get("group", ""),
                         "environment": r.meta.get("environment", ""), "status": "STALE" if age > stale else real, "real_status": real, "score": r.health.get("score"),
                         "critical": c.get("CRITICAL", 0), "warning": c.get("WARNING", 0), "collected": r.collected_at, "age": age, "cpu": m.get("cpu.usage_percent"),
                         "mem": m.get("memory.used_percent"), "disk": m.get("fs.max_used_percent"), "top": top[0].message[:160] if top else "", "local": r.host == me})
        for name, e in errors.items():
            rows.append({"name": name, "host": name, "ip": "", "roles": [], "group": "", "environment": "", "status": "UNREACHABLE", "real_status": "UNREACHABLE", "score": None,
                         "critical": 0, "warning": 0, "collected": 0, "age": 0, "cpu": None, "mem": None, "disk": None, "top": str(e)[:200], "local": False})
        order = {"CRITICAL": 0, "UNREACHABLE": 1, "WARNING": 2, "STALE": 3, "UNKNOWN": 4, "HEALTHY": 5}
        rows.sort(key=lambda r: (order.get(r["status"], 4), r["name"]))
        if group:
            rows = [r for r in rows if r["group"] == group]
        return {"servers": rows, "configured": [] if group else [{"name": s.name, "host": s.host} for s in fleet.servers(cfg)], "job": self.fleet_job}

    def api_fleet(self, req, sess):
        if req.method == "GET":
            return jr(self.fleet_view(sess.get("group", "")))
        from lshme.ops import fleet
        if not fleet.servers(self.app.cfg):
            return err("No servers are configured yet. Add them in Settings > servers (host and user), with SSH key login from this server.", 400)
        with self.lock:
            if self.fleet_job["running"]:
                return err("The servers are already being checked.", 409)
            self.fleet_job.update(running=True, title="Starting", started=time.time(), finished=0, result=None, error="")
        self.audit("web-fleet-run", sess)

        def progress(name, e):
            self.fleet_job["title"] = name

        def work():
            try:
                runs, errors, files, notes, mail = self.app.fleet_run(None, "html", "executive", False, 1, None, progress)
                self.fleet_job["result"] = {"ok": len(runs), "failed": len(errors)}
            except Exception as e:
                self.app.log.exception("web-started fleet check failed")
                self.fleet_job["error"] = "The server check failed: %s" % e
            finally:
                self.fleet_job.update(running=False, finished=time.time())
        threading.Thread(target=work, daemon=True).start()
        return jr(self.fleet_job, 202)

    # -- web certificate (Let's Encrypt) -------------------------------------------------------------------------------
    def api_cert(self, req, sess):
        from lshme.web import certs
        if req.method == "GET":
            return jr({"status": certs.status(self.app), "job": self.cert_job})
        data = req.json()
        if data.get("action") != "issue":
            raise ValueError("unknown action")
        with self.lock:
            if self.cert_job["running"]:
                return err("A certificate request is already running.", 409)
            self.cert_job.update(running=True, started=time.time(), finished=0, result=None)
        self.audit("web-cert-request", sess, force=bool(data.get("force")))

        def work():
            try:
                self.cert_job["result"] = certs.issue(self.app, force=bool(data.get("force")))
            except Exception as e:
                self.app.log.exception("web certificate request failed")
                self.cert_job["result"] = {"ok": False, "message": "The request failed: %s" % e}
            finally:
                self.cert_job.update(running=False, finished=time.time())
        threading.Thread(target=work, daemon=True).start()
        return jr(self.cert_job, 202)

    # -- health of the tool, retention, audit -----------------------------------------------------------------------
    def api_toolhealth(self, req, sess):
        from lshme.ops import selfhealth
        return jr({"checks": selfhealth.check(self.app.cfg, self.app.paths, self.app.runner)})

    def api_retention(self, req, sess):
        from lshme.ops import selfhealth
        return jr(selfhealth.retention_view(self.app.cfg, self.app.paths))

    # -- reports -----------------------------------------------------------------------------------------------
    def rel(self, entry):
        return os.path.relpath(entry["dir"], self.app.paths.report_dir).replace(os.sep, "/")

    def api_reports(self, req, sess):
        items = reportlist.list_reports(self.app.paths, 300)
        if sess.get("group"):
            allowed = self.group_hosts(sess["group"])
            items = [r for r in items if r["host"] in allowed]
        out = [{"id": self.rel(r), "ts": r["ts"], "when": r["when"], "host": r["host"], "category": r["category"], "files": r["files"],
                "score": r["score"], "status": r["status"], "critical": r["critical"], "warning": r["warning"], "unknown": r["unknown"]} for r in items]
        return jr({"reports": out, "series": reportlist.score_series(self.app.paths, 365)})

    def api_raw(self, req, sess):
        from lshme.ops import rawstore
        out = []
        for e in rawstore.days()[:400]:
            m = e["manifest"] or {}
            out.append({"host": e["host"], "day": e["day"], "period": "00:00:00 - 23:59:59", "sealed": e["sealed"], "open": e["open"],
                        "bytes": e["bytes"], "records": m.get("records"), "coverage": m.get("coverage_pct"), "complete": m.get("complete"),
                        "gaps": m.get("gap_count"), "by_type": m.get("by_type")})
        return jr({"days": out, "root": rawstore.root()})

    def api_raw_file(self, req, sess):
        from lshme.ops import rawstore
        host = (req.query.get("host") or [""])[0]
        day = (req.query.get("day") or [""])[0]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day) or not host or rawstore.safe(host) != host:
            raise ValueError("unknown day")
        files = rawstore.day_files(host, day)
        if not files:
            return err("not found", 404)
        import gzip as _gz
        import io as _io
        buf = _io.BytesIO()
        for p in files:                                   # one download: the sealed day plus anything written after sealing
            with open(p, "rb") as f:
                data = f.read()
            buf.write(data if p.endswith(".gz") else _gz.compress(data))
        self.audit("raw-download", sess, host=host, day=day)
        name = rawstore.file_name(host, day) + ".gz"
        return Resp(200, buf.getvalue(), "application/gzip", {"Content-Disposition": 'attachment; filename="%s"' % name,
                                                              "Content-Security-Policy": "default-src 'none'; sandbox"})

    def livefleet(self):
        from lshme.ops import livefleet
        with self.lock:
            if getattr(self, "_livefleet", None) is None:
                self._livefleet = livefleet.LiveFleet(self.app)
        return self._livefleet

    def api_server(self, req, sess):
        """Everything about one server for its own page: live figures, the latest health check (modules, findings, disks, VMs) and its reports."""
        import socket as _socket
        from lshme.core.model import RunResult
        from lshme.ops import dashboard, fleet, livefleet
        name = (req.query.get("name") or [""])[0][:120]
        local = name in ("", "_local", _socket.gethostname(), self.app.cfg.get("main", "general", "hostname", ""))
        members = dict((s.name, s) for s in fleet.servers(self.app.cfg))
        if not local and name not in members:
            return err("not found", 404)
        if sess.get("group"):
            allowed = self.group_hosts(sess["group"])
            if local or (name not in allowed and members[name].host not in allowed):
                return err("not found", 404)
        live, series = {}, []
        if livefleet.enabled(self.app.cfg):
            lf = self.livefleet()
            if not lf.updated:
                lf.prime()
            lf.start()
            with lf.lock:
                live = dict(lf.latest.get("" if local else name) or {})
                series = [list(x) for x in lf.history.get("" if local else name, [])]
        run = None
        if local:
            try:
                with open(os.path.join(self.app.paths.data_dir, "latest.json"), encoding="utf-8") as f:
                    run = RunResult.from_dict(json.load(f))
            except (OSError, ValueError, KeyError):
                run = None
        else:
            runs, errors = dashboard.load_results(self.app.paths)
            sv = members[name]
            for r in runs:
                if r.meta.get("fleet_name") == name or r.host in (name, sv.host) or r.ip == sv.host:
                    run = r
                    break
        out = {"name": _socket.gethostname() if local else name, "local": local, "host": "" if local else members[name].host,
               "group": "" if local else members[name].group, "environment": "" if local else members[name].environment,
               "live": live, "series": series[-120:], "check": None}
        if run:
            sysm = run.module("system")
            sd = (sysm.data if sysm else {}) or {}
            st = run.module("storage")
            fs = [{"mount": f.get("mountpoint"), "pct": f.get("used_percent"), "size": f.get("size"), "free": f.get("free"), "fstype": f.get("fstype")}
                  for f in ((st.data if st else {}) or {}).get("filesystems", []) if f.get("fstype") not in ("tmpfs", "devtmpfs", "squashfs", "overlay")]
            kvm = run.module("kvm")
            kd = (kvm.data if kvm and kvm.status not in ("SKIPPED", "ERROR") else {}) or {}
            mods = []
            for ms in run.health.get("modules", []):
                m = run.module(ms["module"])
                mods.append({"title": ms["title"], "score": ms["score"], "critical": ms["critical"], "warning": ms["warning"], "summary": (m.summary if m else "")[:160],
                             "status": (m.status if m else "")})
            out["check"] = {
                "when": run.collected_at, "status": run.health.get("status"), "score": run.health.get("score"), "counts": run.health.get("counts", {}),
                "host": run.host, "ip": run.ip, "os": sd.get("distribution", ""), "kernel": sd.get("kernel", ""), "uptime": sd.get("uptime", ""),
                "hardware": sd.get("virtualization", "") or sd.get("product", ""), "roles": run.meta.get("roles") or [],
                "modules": mods, "filesystems": sorted(fs, key=lambda f: -(f["pct"] or 0))[:20],
                "findings": [{"severity": f.severity, "code": f.code, "module": f.module, "component": f.component, "message": f.message, "recommendation": f.recommendation}
                             for f in run.findings("WARNING")][:60],
                "advice": [{"severity": f.severity, "code": f.code, "module": f.module, "component": f.component, "message": f.message, "recommendation": f.recommendation}
                           for f in run.findings("INFO") if f.severity == "INFO" and f.recommendation][:20],
                "excluded": run.excluded[:30],
                "vms": [{"name": v.get("name"), "state": v.get("state"), "vcpus": v.get("vcpus"), "memory": v.get("memory"), "cpu": v.get("cpu_percent"),
                         "disk": sum(d.get("allocation") or 0 for d in v.get("disks") or [])} for v in kd.get("vms", [])],
                "pools": [{"name": p.get("name"), "pct": p.get("used_percent"), "free": p.get("available"), "state": p.get("state")} for p in kd.get("pools", [])]}
        try:
            days = max(1, min(90, int((req.query.get("days") or ["7"])[0])))
        except ValueError:
            days = 7
        out["days"] = days
        out["history"] = self.server_history(run.host if run else (_socket.gethostname() if local else name), days)
        return jr(out)

    def server_history(self, host, days):
        """CPU, memory, swap, load per core, health score and every filesystem over the period: the watcher's hourly figures
        where it runs (average and peak per hour), otherwise one point per health check."""
        from lshme.engine.history import History
        since = time.time() - days * 86400
        out = {"cpu": [], "cpu_max": [], "mem": [], "mem_max": [], "swap": [], "load": [], "score": [], "fs": {}, "source": "health checks"}

        def thin(pts, n=500):
            step = max(1, len(pts) // n)
            return [[int(t), round(v, 2)] for t, v in pts[::step] if v is not None]
        try:
            h = History(self.app.paths.history_db)
        except Exception:
            return out
        try:
            hourly = h.hourly(host, since)
            if len(hourly) >= 3:
                out["source"] = "real-time watcher, per hour"
                for k in ("cpu", "cpu_max", "mem", "mem_max", "swap"):
                    out[k] = thin([(r["ts"], r[k]) for r in hourly])
                out["load"] = thin([(r["ts"], r["load"]) for r in hourly])
            else:
                for k, key in (("cpu", "cpu.usage_percent"), ("mem", "memory.used_percent"), ("swap", "swap.used_percent"), ("load", "cpu.load5_per_core")):
                    out[k] = thin(h.series(host, key, since))
            out["score"] = thin([(r["ts"], r["score"]) for r in reversed(h.runs(host, days=days, limit=2000)) if r.get("score") is not None])
            for key in h.keys_like(host, "fs.used_percent:", since)[:12]:
                out["fs"][key.split(":", 1)[1]] = thin(h.series(host, key, since))
        except Exception:
            pass
        finally:
            h.close()
        return out

    def api_sanview(self, req, sess):
        """One SAN storage system for its own page: live state and I/O, every pool / volume / drive / controller / part from the latest
        health check, its open findings, and history (pool use, IOPS, MB/s, response time) over 1, 7 or 30 days. Never the password."""
        import socket as _socket
        from lshme.core.model import RunResult
        from lshme.engine.history import History
        from lshme.ops import livefleet, san
        name = (req.query.get("name") or [""])[0][:60]
        try:
            days = max(1, min(90, int((req.query.get("days") or ["7"])[0])))
        except ValueError:
            days = 7
        try:
            a = san.public(san.find(self.app.cfg, name))
        except san.SanError:
            return err("not found", 404)
        live, series = {}, []
        if livefleet.enabled(self.app.cfg):
            lf = self.livefleet()
            lf.start()
            with lf.lock:
                live = dict(lf.san.get(name) or {})
                series = [list(x) for x in lf.san_perf.get(name, [])]
                live["volumes_top"] = list(lf.san_vols.get(name, []))
        detail, findings, run = None, [], None
        try:
            with open(os.path.join(self.app.paths.data_dir, "latest.json"), encoding="utf-8") as f:
                run = RunResult.from_dict(json.load(f))
        except (OSError, ValueError, KeyError):
            run = None
        if run:
            m = run.module("san")
            for d in ((m.data if m else {}) or {}).get("arrays", []):
                if d.get("name") == name:
                    detail = d
            findings = [{"severity": f.severity, "component": f.component, "message": f.message, "recommendation": f.recommendation}
                        for f in run.findings("WARNING") if f.module == "san" and (f.component == name or f.component.startswith(name + "/"))][:60]
        hist = {}
        host = run.host if run else _socket.gethostname()
        since = time.time() - days * 86400

        def thin(pts, n=400):
            step = max(1, len(pts) // n)
            return [[int(t), round(v, 2)] for t, v in pts[::step]]
        try:
            h = History(self.app.paths.history_db)
            try:
                for k in ("read_iops", "write_iops", "read_mbps", "write_mbps", "latency_ms", "reachable", "drives_failed"):
                    hist[k] = thin(h.series(host, "san.%s:%s" % (k, name), since))
                hist["pools"] = {}
                for key in h.keys_like(host, "san.pool_used_percent:%s|" % name, since):
                    hist["pools"][key.split("|", 1)[1]] = thin(h.series(host, key, since))
            finally:
                h.close()
        except Exception:
            pass
        if detail:                                                # what the page needs, never anything secret
            detail = {k: detail.get(k) for k in ("model", "system_name", "firmware", "status", "serial", "wwn", "controllers", "drives", "pools", "volumes",
                                                 "hardware", "events", "perf", "unreachable", "failures", "reached", "error")}
        return jr({"array": a, "live": live, "series": series, "detail": detail, "checked": run.collected_at if run and detail else None,
                   "findings": findings, "history": hist, "days": days, "live_interval": self.livefleet().san_perf_interval if livefleet.enabled(self.app.cfg) else None})

    def api_topology(self, req, sess):
        """The dependency map (hardware, SAN, hosts, VMs, servers, databases) and what each current failure takes down."""
        from lshme.ops import topology
        if sess.get("group"):
            raise PermissionError("this account only sees its own servers and reports")
        return jr(topology.snapshot(self.app))

    def api_onboard(self, req, sess):
        """Add a Linux server and install LRS Vitals on it over SSH (password used once, never stored); GET ?id= follows the steps."""
        import re as _re
        from lshme.ops import onboard
        if req.method != "POST":
            st = onboard.status((req.query.get("id") or [""])[0])
            return jr(st) if st else err("not found", 404)
        from lshme.ops import role as roles
        if not roles.has_fleet(self.app.cfg):
            raise ValueError("this is a %s server: Linux servers are added on the central server (to make this one central: vitals role central)" % roles.get(self.app.cfg))
        d = req.json()
        name = _re.sub(r"[^\w.-]+", "-", str(d.get("name") or d.get("host") or "").strip())[:60].strip("-")
        host = str(d.get("host", "")).strip()
        if not name or not host or not _re.match(r"^[A-Za-z0-9.:\[\]-]+$", host):
            raise ValueError("give the server's IP address or host name")
        if ("server:" + name) in self.app.cfg.parsers["servers"].sections():
            raise ValueError("%s is already in the fleet" % name)
        user = str(d.get("user") or "root").strip()
        if not _re.match(r"^[a-z_][a-z0-9_.-]*$", user):
            raise ValueError("not a user name: %s" % user)
        port = int(d.get("port") or 22)
        job = onboard.start_job(self.app, name, host, port, user, str(d.get("password") or ""), str(d.get("group", ""))[:60],
                                str(d.get("environment", ""))[:60], bool(d.get("install", True)))
        self.audit("web-onboard", sess, server=name, host=host, user=user, install=bool(d.get("install", True)))
        return jr({"id": job, "name": name})

    def api_bmc(self, req, sess):
        """Server hardware (BMC): list (never the password), add or change, test, remove. Administrators only."""
        from lshme.ops import bmc
        if req.method != "POST":
            return jr({"items": [bmc.public(a) for a in bmc.bmcs(self.app.cfg)],
                       "types": [{"type": t, "label": l, "port": p, "howto": bmc.HOWTO[t]} for t, (l, p) in bmc.TYPES.items()]})
        d = req.json()
        action, name = d.get("action"), str(d.get("name", ""))[:40]
        if action == "remove":
            bmc.find(self.app.cfg, name)
            bmc.remove(self.app, name)
            self.audit("web-bmc-remove", sess, name=name)
            return jr({"ok": True})
        if action == "test" and d.get("saved"):
            return jr(bmc.test(bmc.find(self.app.cfg, name)))
        t = bmc.kind(d.get("type", ""))
        a = {"name": bmc.safe_name(name), "type": t, "address": str(d.get("address", "")).strip(), "port": int(d.get("port") or bmc.TYPES[t][1]),
             "username": str(d.get("username", ""))[:80], "password": str(d.get("password", "")), "verify_tls": bool(d.get("verify_tls")), "enabled": True,
             "server": str(d.get("server", ""))[:120]}
        if not a["password"]:
            try:
                a["password"] = bmc.find(self.app.cfg, a["name"])["password"]
            except bmc.BmcError:
                pass
        if action == "test":
            return jr(bmc.test(a))
        if action != "save":
            raise ValueError("unknown action")
        saved = bmc.save(self.app, a["name"], t, a["address"], a["username"], d.get("password") or None, a["port"], a["verify_tls"], True, a["server"])
        self.audit("web-bmc-save", sess, name=saved, type=t, address=a["address"], server=a["server"])
        return jr({"ok": True, "name": saved})

    def api_repair(self, req, sess):
        """Check and repair LRS Vitals itself on this server or, from the central server, on a member over SSH. Administrators only."""
        from lshme.ops import fleet, livefleet, selfrepair
        if req.method != "POST":
            raise ValueError("use POST")
        d = req.json()
        name = str(d.get("server", "_local"))[:120]
        if name in ("", "_local"):
            steps = selfrepair.repair(self.app, fix=not d.get("check"))
            self.audit("web-repair", sess, server="this server")
            return jr({"server": "this server", "steps": steps, "ok": all(s["ok"] for s in steps)})
        members = dict((s.name, s) for s in fleet.servers(self.app.cfg))
        if name not in members:
            return err("not found", 404)
        lf = self.livefleet()
        r = self.app.runner.run(livefleet.ssh_command(members[name], "vitals repair --json" + (" --check" if d.get("check") else ""), lf.sock_dir()), timeout=180)
        self.audit("web-repair", sess, server=name)
        text = r.out or ""
        if "{" not in text:
            msg = (r.err or text or "no answer").strip().splitlines()[-1][:300]
            if livefleet.outdated(msg) or "invalid choice: 'repair'" in msg:
                msg = "Its LRS Vitals is older and has no repair yet: upgrade it first (Copy upgrade command on its card)."
            return err("%s: %s" % (name, msg), 502)
        res = json.loads(text[text.index("{"):])
        res["server"] = name
        return jr(res)

    def api_exclusions(self, req, sess):
        """Open findings with a one-click Exclude, and the exclusion rules with Un-exclude. Writes exclusions.conf like 'vitals exclude add'."""
        import re as _re
        from lshme.engine import exclusions
        if req.method == "POST":
            d = req.json()
            action = d.get("action")
            if action == "remove":
                name = str(d.get("name", ""))
                if ("exclude:" + name) not in self.app.cfg.parsers["exclusions"].sections():
                    raise ValueError("no exclusion rule named %s" % name)
                self.app.cfg.remove_section("exclusions", "exclude:" + name)
                self.app.save_config("exclusions")
                self.audit("web-exclusion-remove", sess, rule=name)
                return jr({"ok": True})
            if action != "add":
                raise ValueError("unknown action")
            code, comp = str(d.get("code", "")).strip(), str(d.get("component", "")).strip() or "*"
            if not _re.match(r"^[A-Z0-9_*]+$", code):
                raise ValueError("not a finding code: %s" % code)
            until = str(d.get("until", "")).strip()
            fields = {"code": code, "component": comp, "module": str(d.get("module", "")).strip(), "hosts": str(d.get("hosts", "")).strip(),
                      "reason": str(d.get("reason", "")).strip()[:300], "owner": str(d.get("owner", "")).strip()[:80] or sess["user"], "until": until}
            base = _re.sub(r"[^\w.-]+", "-", ("%s-%s" % (code, comp if comp != "*" else "all")).lower()).strip("-")[:50] or "rule"
            name, n = base, 2
            while ("exclude:" + name) in self.app.cfg.parsers["exclusions"].sections():
                name, n = "%s-%d" % (base, n), n + 1
            problems = exclusions.Rule(name, fields).validate()
            if problems:
                raise ValueError("; ".join(problems))
            import shlex as _shlex
            import socket as _socket
            from lshme.ops import branding, fleet
            scope = "all" if d.get("scope") == "all" else "here"          # this server only, or every server in the estate
            server = str(d.get("server", "_local") or "_local")
            members = fleet.servers(self.app.cfg)
            local = server in ("_local", _socket.gethostname())
            if scope == "here" and not local and server not in [m.name for m in members]:
                raise ValueError("unknown server %s" % server)
            if scope == "here" and local:                               # the name in the health check, and this computer's own name
                names = [_socket.gethostname(), _socket.gethostname().split(".")[0]]
                try:
                    with open(os.path.join(self.app.paths.data_dir, "latest.json"), encoding="utf-8") as f:
                        names.insert(0, json.load(f).get("host", ""))
                except (OSError, ValueError):
                    pass
                fields["hosts"] = ",".join(sorted(set(n for n in names if n), key=names.index))
            saved = scope == "all" or local
            if saved:
                for k, v in fields.items():
                    if v:
                        self.app.cfg.set("exclusions", "exclude:" + name, k, v)
                self.app.save_config("exclusions")
            remote = []
            for m in members if scope == "all" else [m for m in members if not local and m.name == server]:
                cmd = "vitals exclude add %s --code %s --component %s --reason %s --owner %s%s" % (
                    _shlex.quote(name), _shlex.quote(code), _shlex.quote(comp), _shlex.quote(fields["reason"] or "accepted from the central dashboard"),
                    _shlex.quote(fields["owner"]), (" --until " + _shlex.quote(until)) if until else "")
                target = "%s@%s" % (m.user, m.host) if m.user else m.host
                r = self.app.runner.run(branding._ssh_base(m) + [target, cmd], timeout=60)
                remote.append({"server": m.name, "ok": bool(r.ok), "detail": "added" if r.ok else ((r.err or r.out or "failed").strip().splitlines() or ["failed"])[-1][:160]})
            self.audit("web-exclusion-add", sess, rule=name, scope=scope, server=server, remote=",".join("%s:%s" % (x["server"], "ok" if x["ok"] else "failed") for x in remote), **fields)
            return jr({"ok": True, "name": name, "scope": scope, "saved_here": saved, "remote": remote})
        now = time.time()
        rules = []
        for r in exclusions.load(self.app.cfg):
            st = "disabled" if not r.enabled else ("invalid" if r.validate() else "expired" if r.expired(now) else "active")
            rules.append({"name": r.name, "code": r.code, "component": r.component, "module": r.module, "hosts": ",".join(r.hosts),
                          "reason": r.reason, "owner": r.owner, "until": r.until, "status": st})
        findings, excluded = [], []
        try:
            with open(os.path.join(self.app.paths.data_dir, "latest.json"), encoding="utf-8") as f:
                latest = json.load(f)
            for m in latest.get("modules", []):
                for x in m.get("findings", []):
                    if x.get("severity") in ("CRITICAL", "WARNING"):
                        findings.append({"severity": x["severity"], "code": x.get("code", ""), "component": x.get("component", ""), "module": m.get("name", ""),
                                         "message": x.get("message", ""), "recommendation": x.get("recommendation", "")})
            excluded = latest.get("excluded", [])
            host, checked = latest.get("host", ""), latest.get("collected_at")
        except (OSError, ValueError):
            host, checked = "", None
        findings.sort(key=lambda x: (0 if x["severity"] == "CRITICAL" else 1, x["module"], x["component"]))
        return jr({"findings": findings, "excluded": excluded, "rules": rules, "host": host, "checked": checked})

    def api_update(self, req, sess):
        """Automatic update: status, check for a newer version, start a safe update now (runs outside this web service)."""
        from lshme.ops import autoupdate
        up = autoupdate.Updater(self.app)
        if req.method == "POST":
            action = req.json().get("action")
            if action == "check":
                try:
                    info = up.check()
                except autoupdate.UpdateError as e:
                    return err(str(e), 502)
                self.audit("web-update-check", sess)
                return jr({"ok": True, "available": info["available"], "latest": info["sha"], "message": info["message"]})
            if action == "now":
                autoupdate.start_background(self.app, self.app.launcher())
                self.audit("web-update-now", sess)
                return jr({"ok": True, "started": True}, 202)
            raise ValueError("unknown action")
        st = up.state()
        st.pop("log", None) if st.get("result") == "current" else None
        return jr({"settings": autoupdate.settings(self.app.cfg), "installed": up.installed(), "state": st})

    def api_san(self, req, sess):
        """SAN storage: list (never the password), add or change, test (saved or not yet saved), remove. Administrators only."""
        from lshme.ops import san
        if req.method != "POST":
            return jr({"arrays": [san.public(a) for a in san.arrays(self.app.cfg)],
                       "types": [{"type": t, "label": l, "port": p, "howto": san.HOWTO[t]} for t, (l, p) in san.TYPES.items()]})
        d = req.json()
        action = d.get("action")
        name = str(d.get("name", ""))[:40]
        if action == "remove":
            san.find(self.app.cfg, name)
            san.remove(self.app, name)
            self.audit("web-san-remove", sess, name=name)
            return jr({"ok": True})
        if action == "test" and d.get("saved"):
            return jr(san.test(san.find(self.app.cfg, name)))
        t = san.kind(d.get("type", ""))
        ctl = [str(x).strip() for x in (d.get("controllers") or []) if str(x).strip()][:4]
        a = {"name": san.safe_name(name), "type": t, "controllers": ctl, "port": int(d.get("port") or san.TYPES[t][1]), "username": str(d.get("username", ""))[:80],
             "password": str(d.get("password", "")), "verify_tls": bool(d.get("verify_tls")), "enabled": True}
        if not a["password"]:                                    # empty password: use the saved one
            try:
                a["password"] = san.find(self.app.cfg, a["name"])["password"]
            except san.SanError:
                pass
        if action == "test":
            return jr(san.test(a))
        if action == "save":
            san.save(self.app, a["name"], t, ctl, a["username"], d.get("password") or None, a["port"], a["verify_tls"], bool(d.get("enabled", True)))
            self.audit("web-san-save", sess, name=a["name"], type=t, controllers=",".join(ctl))
            return jr({"ok": True, "name": a["name"]})
        raise ValueError("unknown action")

    def api_live_fleet(self, req, sess):
        from lshme.ops import livefleet
        if not livefleet.enabled(self.app.cfg):
            return jr({"servers": [], "interval": 0, "updated": 0, "enabled": False})
        lf = self.livefleet()
        if not lf.updated:
            lf.prime()                                           # this server at once; the others arrive from the collector moments later
        lf.start()
        servers = lf.snapshot()
        if sess.get("group"):                                    # a customer account sees only its own servers
            allowed = self.group_hosts(sess["group"])
            servers = [s for s in servers if s.get("name") in allowed or s.get("host") in allowed]
        slim = []
        for s in servers:
            s = dict(s)
            s.pop("problems_detail", None)
            slim.append(s)
        san_rows = [] if sess.get("group") else lf.san_snapshot()
        return jr({"servers": slim, "interval": lf.interval, "updated": lf.updated, "enabled": True, "san": san_rows, "san_updated": lf.san_updated,
                   "san_interval": lf.san_interval})

    # -- report templates ----------------------------------------------------------------------------------------------------------
    def api_templates(self, req, sess):
        if sess.get("group"):
            return jr({"templates": []})
        return jr({"templates": report_templates.catalog()})

    def api_templates_preview(self, req, sess):
        if sess.get("group"):
            return err("not found", 404)
        tid = (req.query.get("id") or [""])[0]
        fmt = (req.query.get("fmt") or ["html"])[0]
        body, ctype = report_templates.preview(self.app, tid, fmt)
        extra = {"Content-Security-Policy": REPORT_CSP, "X-Content-Type-Options": "nosniff"}
        if fmt == "pdf":
            extra["Content-Disposition"] = 'inline; filename="%s.pdf"' % re.sub(r"[^A-Za-z0-9_-]", "_", tid)
        return Resp(200, body, ctype, extra)

    # -- report logos ------------------------------------------------------------------------------------------------------------
    def api_branding(self, req, sess):
        return jr(branding.listing(self.app))

    def api_branding_logo(self, req, sess):
        scope = branding.check_scope(self.app, (req.query.get("scope") or ["all"])[0])
        path = branding.find(self.app.paths, scope) or (branding.find(self.app.paths, "all") if scope != "all" else "")
        if not path:
            return err("no logo", 404)
        with open(path, "rb") as f:
            body = f.read()
        return Resp(200, body, "image/png" if path.endswith(".png") else "image/jpeg",
                    {"Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"})

    def api_branding_upload(self, req, sess):
        data = req.json()
        try:
            raw = base64.b64decode(str(data.get("data", "")), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("The file could not be read. Choose a PNG or JPEG image.")
        info = branding.save(self.app, raw, str(data.get("scope", "all")))
        self.audit("logo-upload", sess, scope=str(data.get("scope", "all")), bytes=info["bytes"])
        return jr(branding.listing(self.app))

    def api_branding_remove(self, req, sess):
        data = req.json()
        branding.remove(self.app, str(data.get("scope", "all")))
        return jr(branding.listing(self.app))

    def api_branding_push(self, req, sess):
        data = req.json()
        names = [str(x) for x in (data.get("servers") or [])] or None
        res = branding.push(self.app, names)
        self.audit("logo-push", sess, servers=",".join(x["server"] for x in res))
        return jr({"results": res})

    def api_reports_search(self, req, sess):
        text = (req.query.get("q") or [""])[0]
        host = (req.query.get("host") or [""])[0]
        cat = (req.query.get("category") or [""])[0]
        if sess.get("group"):
            allowed = self.group_hosts(sess["group"])
            if host and host not in allowed:
                return jr({"results": [], "stats": {}})
        ix = archive.Index(self.app.paths)
        try:
            rows = ix.search(text, 60, host, cat)
            if sess.get("group"):
                rows = [r for r in rows if r["rid"].split("/")[0] in allowed]
            for r in rows:
                r["when"] = reportlist.iso(r["ts"])
            return jr({"results": rows, "stats": ix.stats()})
        finally:
            ix.close()

    def api_reports_email(self, req, sess):
        data = req.json()
        rid = str(data.get("id", ""))
        if sess.get("group") and rid.split("/")[0] not in self.group_hosts(sess["group"]):
            raise ValueError("unknown report")
        d = self.report_dir(rid)
        to = [x.strip() for x in re.split(r"[,; ]+", str(data.get("to", ""))) if x.strip()]
        names = data.get("files") or None
        res = archive.email_report(self.app, d, to or None, names)
        self.audit("report-email", sess, report=rid, to=",".join(to))
        return jr(res)

    def report_dir(self, rid):
        root = os.path.realpath(self.app.paths.report_dir)
        parts = [p for p in rid.split("/") if p]
        if len(parts) != 3 or any(p in (".", "..") or "\\" in p or "\x00" in p for p in parts):
            raise ValueError("unknown report")
        full = os.path.realpath(os.path.join(root, *parts))
        if not full.startswith(root + os.sep) or not os.path.isdir(full):
            raise ValueError("unknown report")
        return full

    def api_report(self, req, sess):
        rid = (req.query.get("id") or [""])[0]
        if sess.get("group") and rid.split("/")[0] not in self.group_hosts(sess["group"]):
            raise ValueError("unknown report")
        d = self.report_dir(rid)
        listing = archive.catalog(d)
        files = [f["name"] for f in listing]
        detail = {"id": rid, "files": [dict(f, url="/reports/%s/%s" % (rid, f["name"])) for f in listing]}
        j = next((f for f in files if f.endswith(".json") and os.path.isfile(archive.real_path(os.path.join(d, f)))), None)
        if j:
            try:
                run = reportlist.load_run(os.path.join(d, j))
                detail.update(host=run.host, when=reportlist.iso(run.collected_at), health=run.health, counts=run.counts(),
                              modules=[{"name": m.name, "title": m.title, "status": m.status, "score": m.score, "summary": m.summary,
                                        "findings": len(m.findings)} for m in run.modules],
                              findings=[{"severity": f.severity, "module": f.module, "component": f.component, "message": f.message,
                                         "recommendation": f.recommendation} for f in run.findings()])
            except (OSError, ValueError, KeyError):
                detail["error"] = "The JSON result in this report could not be read."
        return jr(detail)

    def report_file(self, path, download=False):
        """A report file: the stored one when it is there (plain or compressed), else rebuilt exactly from the report's source."""
        parts = path[len("/reports/"):].split("/")
        if len(parts) != 4:
            return err("not found", 404)
        d = self.report_dir("/".join(parts[:3]))
        name = parts[3]
        if not name or name in (".", "..") or "/" in name or "\\" in name or name.startswith("."):
            return err("not found", 404)
        if name.endswith(".gz"):                          # old links to the stored name still work
            name = name[:-3]
        full = os.path.realpath(archive.real_path(os.path.join(d, name)))
        if os.path.lexists(archive.real_path(os.path.join(d, name))) and (os.path.dirname(full) != d or not os.path.isfile(full)):
            return err("not found", 404)                  # a link out of the report folder
        try:
            body, how = archive.materialize(d, name)
        except ValueError:
            return err("not found", 404)
        ext = os.path.splitext(name)[1].lower()
        extra = {"X-Report-Source": how}
        safe_name = re.sub(r'[^A-Za-z0-9._-]', "_", name)
        if ext in INLINE_TYPES and not download:
            extra["Content-Security-Policy"] = REPORT_CSP
            return Resp(200, body, INLINE_TYPES[ext], extra)
        ctype = DOWNLOAD_TYPES.get(ext) or INLINE_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        extra.update({"Content-Disposition": 'attachment; filename="%s"' % safe_name, "Content-Security-Policy": "default-src 'none'; sandbox"})
        return Resp(200, body, ctype, extra)

    def api_run(self, req, sess):
        if req.method == "GET":
            return jr(self.job)
        with self.lock:
            if self.job["running"]:
                return err("A health check is already running.", 409)
            self.job.update(running=True, progress=0, total=0, title="Starting", started=time.time(), finished=0, result=None, error="")
        self.audit("web-run", sess)

        def progress(i, n, title):
            self.job.update(progress=i, total=n, title=title)

        def work():
            try:
                run, result = self.app.full_run(None, 1, None, None, None, progress=progress)
                c = run.counts()
                self.job["result"] = {"score": run.health.get("score"), "status": run.health.get("status"), "critical": c["CRITICAL"],
                                      "warning": c["WARNING"], "report_dir": os.path.relpath(result["report_dir"], self.app.paths.report_dir).replace(os.sep, "/")
                                      if result.get("report_dir") else ""}
            except LockBusy:
                self.job["error"] = "Another health check is already running."
            except Exception as e:
                self.app.log.exception("web-started run failed")
                self.job["error"] = "The health check failed: %s" % e
            finally:
                self.job.update(running=False, finished=time.time())
        threading.Thread(target=work, daemon=True).start()
        return jr(self.job, 202)

    # -- server scan ------------------------------------------------------------------------------------------------
    def scan_view(self):
        from lshme.core.config import as_list
        cfg = self.app.cfg
        profile = self.pending_profile or discover.load_profile(self.app.paths)
        return {"profile": profile, "order": list(confmeta.modreg.MODULE_ORDER), "pending": bool(self.pending_profile), "forced": as_list(cfg.get("main", "modules", "force_enabled", "")),
                "excluded": as_list(cfg.get("main", "modules", "auto_excluded", "")),
                "schedule": {"enabled": cfg.getbool("main", "scan", "enabled", True), "weekday": cfg.get("main", "scan", "weekday", "sun"),
                             "time": cfg.get("main", "scan", "time", "02:00")}}

    def api_scan(self, req, sess):
        if req.method == "GET":
            return jr(self.scan_view())
        from lshme.core.config import as_list
        from lshme.core.context import Context
        data = req.json()
        action = data.get("action")
        with self.lock:
            if action == "scan":
                self.pending_profile = discover.scan(Context(self.app.cfg, self.app.paths, self.app.runner))
                self.audit("web-scan", sess)
            elif action == "apply":
                profile = self.pending_profile or discover.load_profile(self.app.paths)
                if not profile:
                    raise ValueError("scan first")
                changes, excluded = discover.apply(self.app, profile)
                self.pending_profile = None
                self.audit("web-scan-apply", sess, excluded=excluded)
                out = self.scan_view()
                out["changes"] = changes
                return jr(out)
            elif action == "force":
                name = str(data.get("module", ""))
                if name not in confmeta.modreg.MODULE_ORDER:
                    raise ValueError("unknown module")
                forced = [m for m in as_list(self.app.cfg.get("main", "modules", "force_enabled", "")) if m != name]
                if data.get("on"):
                    forced.append(name)
                confmeta.apply(self.app, "main", "modules", {"force_enabled": ",".join(forced)})
                self.audit("web-scan-force", sess, module=name, on=bool(data.get("on")))
            else:
                raise ValueError("unknown action")
        return jr(self.scan_view())

    # -- configuration ------------------------------------------------------------------------------------------------
    def api_config(self, req, sess):
        if req.method == "GET":
            return jr(confmeta.describe(self.app))
        data = req.json()
        with self.lock:
            written = confmeta.apply(self.app, str(data.get("file", "")), str(data.get("section", "")), data.get("values") or {}, data.get("remove") or [])
        keys = [k for k in written if confmeta.kind_of(data.get("file"), data.get("section"), k, None) != "secret"]
        self.audit("web-config-change", sess, file=data.get("file"), section=data.get("section"), keys=keys)
        return jr({"ok": True, "written": written})

    def api_config_section(self, req, sess):
        data = req.json()
        file, name = str(data.get("file", "")), str(data.get("name", ""))
        with self.lock:
            if data.get("action") == "add":
                confmeta.add_section(self.app, file, name, data.get("values") or None)
            elif data.get("action") == "delete":
                confmeta.delete_section(self.app, file, name)
            else:
                raise ValueError("unknown action")
        self.audit("web-config-section", sess, file=file, section=name, op=data.get("action"))
        return jr({"ok": True})

    # -- operations ---------------------------------------------------------------------------------------------------
    def api_watch(self, req, sess):
        import os as _os
        state = None
        try:
            with open(_os.path.join(self.app.paths.data_dir, "watch_state.json"), encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            pass
        unit = _os.path.join(self.app.cfg.get("schedule", "scheduler", "systemd_dir", "/etc/systemd/system"), "lshme-watch.service")
        active = self.app.runner.run(["systemctl", "is-active", "lshme-watch.service"]).out.strip() if self.app.runner.has("systemctl") else "unknown"
        return jr({"state": state, "installed": _os.path.exists(unit), "service": active or "unknown"})

    def api_schedules(self, req, sess):
        from lshme.ops import scheduler
        backend, rows = scheduler.status(self.app.cfg, self.app.runner)
        return jr({"backend": backend, "schedules": rows})

    def api_action(self, req, sess):
        name = str(req.json().get("name", ""))
        if name not in ACTIONS:
            raise ValueError("unknown action")
        app = self.app
        with self.lock:
            if name == "schedule-install":
                from lshme.ops import scheduler
                rep = scheduler.install(app.cfg, app.runner, app.launcher(), app.global_args())
                app.audit("schedule-install", installed=rep["installed"], errors=rep["errors"])
                out = {"installed": rep["installed"], "errors": rep["errors"]}
            elif name == "watch-install":
                from lshme.ops import watch
                how, path, ok, error = watch.install(app)
                out = {"ok": ok, "how": how, "error": error}
            elif name == "watch-remove":
                from lshme.ops import watch
                out = {"removed": watch.uninstall(app)}
            elif name == "email-test":
                from lshme.delivery import mailer
                res = mailer.send_test(mailer.MailSettings(app.cfg), app.paths.log_dir)
                out = {"ok": bool(res["ok"]), "recipients": res.get("recipients"), "error": res.get("error")}
            else:
                out = {"archive": os.path.basename(backup_config(app.paths.config_dir, app.paths.config_backup_dir, "manual"))}
        self.audit("web-action", sess, name=name)
        return jr(out)

    def api_backups(self, req, sess):
        d = self.app.paths.config_backup_dir
        found = sorted((f for f in os.listdir(d) if f.startswith("config_") and f.endswith(".tar.gz")), reverse=True) if os.path.isdir(d) else []
        if req.method == "GET":
            dl = (req.query.get("download") or [""])[0]
            if dl:
                if dl not in found:
                    raise ValueError("unknown backup")
                with open(os.path.join(d, dl), "rb") as f:
                    body = f.read()
                self.audit("web-config-download", sess, archive=dl)
                return Resp(200, body, "application/gzip", {"Content-Disposition": 'attachment; filename="%s"' % dl, "Content-Security-Policy": "default-src 'none'; sandbox"})
            details = []
            for f in found:
                st = os.stat(os.path.join(d, f))
                details.append({"name": f, "size": st.st_size, "time": st.st_mtime})
            return jr({"backups": found, "details": details})
        archive = str(req.json().get("archive", ""))
        if archive not in found:
            raise ValueError("unknown backup")
        with self.lock:
            restore_config(os.path.join(d, archive), self.app.paths.config_dir, d)
            self.app.reload()
        self.audit("web-config-restore", sess, archive=archive)
        return jr({"ok": True})

    def api_logs(self, req, sess):
        d = self.app.paths.log_dir
        names = sorted(f for f in os.listdir(d) if f.endswith(".log")) if os.path.isdir(d) else []
        name = (req.query.get("name") or [""])[0]
        if not name:
            return jr({"logs": names})
        if name not in names:
            raise ValueError("unknown log")
        n = min(1000, max(10, int((req.query.get("lines") or ["200"])[0] or 200)))
        with open(os.path.join(d, name), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()[-n:]
        return jr({"name": name, "lines": lines})

    def api_audit(self, req, sess):
        path = os.path.join(self.app.paths.log_dir, "audit.log")
        lines = []
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()[-600:]
        records = []
        for line in reversed(lines):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            extra = ", ".join("%s=%s" % (k, v) for k, v in r.items() if k not in ("time", "user", "action"))
            records.append({"time": r.get("time", ""), "user": r.get("user", ""), "action": r.get("action", ""), "detail": extra[:300]})
        return jr({"lines": lines[-300:], "records": records[:400]})

    # -- accounts ------------------------------------------------------------------------------------------------------
    def api_users(self, req, sess):
        if req.method == "GET":
            return jr({"users": self.users.listing()})
        data = req.json()
        action, user = data.get("action"), str(data.get("user", ""))
        with self.lock:
            if action == "add":
                if user in self.users.names():
                    raise ValueError("that user already exists")
                self.users.set_password(user, str(data.get("password", "")), str(data.get("role", "viewer")))
            elif action == "passwd":
                self.users.set_password(user, str(data.get("password", "")))
                self.sessions.drop_user(user)
            elif action == "role":
                if str(data.get("role")) not in auth.ROLES:
                    raise ValueError("role must be admin or viewer")
                if user == sess["user"] and data.get("role") != "admin":
                    raise ValueError("you cannot remove your own administrator role")
                self.app.cfg.set("web", "roles", user, str(data.get("role")))
                self.app.save_config("web")
                self.app.reload()
                self.sessions.drop_user(user)
            elif action == "remove":
                if user == sess["user"]:
                    raise ValueError("you cannot remove your own account")
                self.users.remove(user)
                self.sessions.drop_user(user)
            else:
                raise ValueError("unknown action")
        self.audit("web-users", sess, op=action, target=user)
        return jr({"users": self.users.listing()})

    def api_password(self, req, sess):
        data = req.json()
        if sess["user"] not in self.users.names():
            raise ValueError("this is a Linux account: change its password with the passwd command on the server")
        if not self.users.verify(sess["user"], str(data.get("old", ""))):
            raise ValueError("the current password is wrong")
        with self.lock:
            self.users.set_password(sess["user"], str(data.get("new", "")))
        self.audit("web-password-change", sess)
        return jr({"ok": True})

    def api_help(self, req, sess):
        from lshme.ui import helptext
        return jr({"lines": helptext.full_help()})


# ---------------------------------------------------------------------------------------------------------
# sockets
# ---------------------------------------------------------------------------------------------------------
class Threaded(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(web):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LSHME-web"
        sys_version = ""

        def _serve(self):
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True  # the body is not read
                body, resp = b"", err("request too large", 413)
            else:
                body = self.rfile.read(length) if length else b""
                resp = web.handle(Req(self.command, unquote(parts.path), parse_qs(parts.query), body, dict(self.headers.items()), self.client_address[0]))
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.ctype)
            self.send_header("Content-Length", str(len(resp.body)))
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD" and resp.status != 204:
                self.wfile.write(resp.body)

        do_GET = do_POST = do_HEAD = _serve

        def do_PUT(self):
            self.send_error(405)

        do_DELETE = do_PATCH = do_PUT

        def log_message(self, *a):
            pass
    return Handler


def ensure_certificate(app):
    """The certificate and key to use: the configured pair, or a self-signed one created once."""
    cfg = app.cfg
    from lshme.web import certs
    source = certs.settings(cfg)["source"]
    if source == "letsencrypt":                           # genuine certificate only: never a self-signed one
        pair = certs.active_pair(app)
        if pair:
            return pair
        raise ValueError("a genuine Let's Encrypt certificate is required (tls_source = letsencrypt) but none has been issued yet: run 'vitals web cert issue'")
    pair = certs.active_pair(app)                         # acme = yes next to your own choice: use it once it exists
    if pair:
        return pair
    cert, key = cfg.get("web", "web", "cert", ""), cfg.get("web", "web", "key", "")
    if cert and key:
        return cert, key
    if source == "custom":
        raise ValueError("tls_source = custom needs cert and key in web.conf")
    d = os.path.join(app.paths.data_dir, "web")
    cert, key = os.path.join(d, "server.crt"), os.path.join(d, "server.key")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    import socket
    os.makedirs(d, exist_ok=True)
    os.chmod(d, 0o700)
    host = socket.gethostname()
    base = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "825", "-subj", "/CN=%s/O=LRS Services" % host,
            "-keyout", key, "-out", cert]
    r = app.runner.run(base + ["-addext", "subjectAltName=DNS:%s,DNS:localhost,IP:127.0.0.1" % host], timeout=60)
    if not r.ok:  # older OpenSSL (1.0.x) has no -addext
        r = app.runner.run(base, timeout=60)
    if not r.ok or not os.path.exists(cert):
        raise ValueError("could not create a TLS certificate (is openssl installed?): %s" % r.brief_error())
    os.chmod(key, 0o600)
    return cert, key


def make_server(app, bind=None, port=None):
    """Returns (server, WebApp). Refuses unsafe combinations instead of starting."""
    cfg = app.cfg
    bind = bind or cfg.get("web", "web", "bind", "127.0.0.1")
    port = int(cfg.getint("web", "web", "port", 8443) if port is None else port)
    tls = cfg.getbool("web", "web", "tls", True)
    loopback = bind in ("127.0.0.1", "::1", "localhost")
    if not tls and not loopback and not cfg.getbool("web", "web", "allow_plain_http", False):
        raise ValueError("refusing to serve passwords over plain HTTP on the network: turn TLS on, or bind to 127.0.0.1")
    web = WebApp(app, secure=tls)
    if not web.users.names() and not auth.system_users(app.cfg):
        raise ValueError("there are no web users yet: run  vitals web passwd admin (or allow a Linux account with system_users in web.conf)")
    server = Threaded((bind, port), make_handler(web))
    if tls:
        from lshme.web import certs as _certs
        if _certs.settings(cfg)["source"] == "letsencrypt":
            try:
                cert, key = _certs.obtain(app)
            except ValueError:
                server.server_close()
                raise
        else:
            cert, key = ensure_certificate(app)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)
        ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        from lshme.web import certs
        certs.register_context(ctx)                       # a renewed certificate is loaded into it without a restart
        if certs.settings(cfg)["enabled"] and port:
            certs.start_renewal(app, first_after=3600)    # renewals are checked about hourly after the first check
    return server, web
