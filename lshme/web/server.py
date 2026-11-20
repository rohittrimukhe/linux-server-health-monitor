# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The web server: sign-in, a JSON API and the single-page interface (static files).

All request handling is in WebApp.handle(), which takes a plain description of a request and returns a plain
response, so every route and every security rule can be tested without sockets. Security: sign-in with salted
hashed passwords, rate limiting, expiring random session tokens in HttpOnly SameSite=Strict cookies, a CSRF token
and an Origin check on every change, roles (admin may change things, viewer may only look), strict
Content-Security-Policy, no directory listing and no path outside the report folder is ever served."""
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
from lshme.ops import discover, reportlist, sysstats
from lshme.web import auth, confmeta

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "application/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8")}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src 'self'; "
       "frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
REPORT_CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:; "
              "frame-ancestors 'self'; sandbox allow-scripts allow-popups")
INLINE_TYPES = {".html": "text/html; charset=utf-8", ".pdf": "application/pdf"}
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

    def security_headers(self, resp, ctype_is_report=False):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Cache-Control", "no-store")
        h.setdefault("Content-Security-Policy", CSP)
        if self.secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

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
            name, ctype = STATIC[path]
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                return Resp(200, f.read(), ctype)
        if req.method == "GET" and path == "/lrs_logo.png" and os.path.isfile(BRAND_LOGO):
            with open(BRAND_LOGO, "rb") as f:
                return Resp(200, f.read(), "image/png", {"Cache-Control": "max-age=86400"})
        if req.method == "GET" and path == "/favicon.ico":
            return Resp(204, b"", "image/x-icon")
        if not (path.startswith("/api/") or path.startswith("/reports/")):
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
        if path.startswith("/reports/"):
            return self.report_file(path)
        name = path[len("/api/"):]
        handler = getattr(self, "api_" + name.replace("/", "_"), None)
        if handler is None:
            return err("not found", 404)
        if req.method not in ("GET", "HEAD") and sess["role"] != "admin" and name != "password":
            self.audit("web-denied", sess, route=name)
            raise PermissionError("your account is read-only")
        if name in ("config", "users", "backups", "logs", "audit") and sess["role"] != "admin":
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
        self.audit("web-login", sess)
        return jr(self.me_data(sess), headers={"Set-Cookie": self.cookie_header(token, self.sessions.ttl)})

    def me_data(self, sess):
        import socket
        return {"user": sess["user"], "role": sess["role"], "csrf": sess["csrf"], "host": socket.gethostname(), "version": VERSION, "app": APP_NAME,
                "tls": self.secure}

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
            active.append({"severity": p.get("severity"), "component": e.get("component") or key, "message": e.get("message") or key,
                           "since": p.get("since"), "count": p.get("count"), "alerted": p.get("alerted")})
        active.sort(key=lambda a: (0 if a["severity"] == "CRITICAL" else 1, -(a["since"] or 0)))
        s = lambda k: W.setting(cfg, k)
        channels = {"email": as_bool(s("alert_email"), True), "to": s("alert_to"), "syslog": as_bool(s("syslog"), True), "webhook": bool(s("webhook_url")),
                    "min_severity": s("alert_min_severity"), "confirm": s("confirm"), "interval": s("interval")}
        return jr({"active": active, "events": list(reversed(events))[:40], "channels": channels})

    def fleet_view(self):
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
        return {"servers": rows, "configured": [{"name": s.name, "host": s.host} for s in fleet.servers(cfg)], "job": self.fleet_job}

    def api_fleet(self, req, sess):
        if req.method == "GET":
            return jr(self.fleet_view())
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
        out = [{"id": self.rel(r), "ts": r["ts"], "when": r["when"], "host": r["host"], "category": r["category"], "files": r["files"],
                "score": r["score"], "status": r["status"], "critical": r["critical"], "warning": r["warning"], "unknown": r["unknown"]} for r in items]
        return jr({"reports": out, "series": reportlist.score_series(self.app.paths, 365)})

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
        d = self.report_dir(rid)
        files = sorted(f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
        detail = {"id": rid, "files": [{"name": f, "url": "/reports/%s/%s" % (rid, f), "size": os.path.getsize(os.path.join(d, f))} for f in files]}
        j = next((f for f in files if f.endswith(".json")), None)
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

    def report_file(self, path):
        parts = path[len("/reports/"):].split("/")
        if len(parts) != 4:
            return err("not found", 404)
        d = self.report_dir("/".join(parts[:3]))
        name = parts[3]
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            return err("not found", 404)
        full = os.path.realpath(os.path.join(d, name))
        if os.path.dirname(full) != d or not os.path.isfile(full):
            return err("not found", 404)
        ext = os.path.splitext(name)[1].lower()
        with open(full, "rb") as f:
            body = f.read()
        if ext in INLINE_TYPES:
            return Resp(200, body, INLINE_TYPES[ext], {"Content-Security-Policy": REPORT_CSP})
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return Resp(200, body, ctype, {"Content-Disposition": 'attachment; filename="%s"' % re.sub(r'[^A-Za-z0-9._-]', "_", name),
                                       "Content-Security-Policy": "default-src 'none'; sandbox"})

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
    cert, key = cfg.get("web", "web", "cert", ""), cfg.get("web", "web", "key", "")
    if cert and key:
        return cert, key
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
        cert, key = ensure_certificate(app)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)
        ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    return server, web
