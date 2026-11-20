# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The web interface: accounts, sign-in security, every API route, the settings editor and a real HTTP server."""
import http.client
import json
import os
import ssl
import threading
import time
import unittest

from fakes import ROOT, FakeRunner, TempHome
from lshme.app import App
from lshme.core.model import ModuleResult, RunResult
from lshme.web import auth, confmeta, server
from lshme.web.server import Req, WebApp

auth.ITERATIONS = 2000  # fast hashing for the tests; production uses 200000
GOOD = "Str0ngPassw0rd"


def fake_run():
    run = RunResult("h1")
    run.health = {"score": 69, "status": "CRITICAL", "counts": {"CRITICAL": 1, "WARNING": 1}}
    m = ModuleResult("storage", "Storage", 10)
    m.score = 90
    m.add("CRITICAL", "DISK_FULL", "/data", "Filesystem /data is 93% full", recommendation="Free space.")
    run.modules = [m]
    return run


class WebCase(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.app = App(ROOT, home=self.home.dir)
        auth.Users(self.app).set_password("admin", GOOD, "admin")
        auth.Users(self.app).set_password("viewer", GOOD + "1", "viewer")
        self.web = WebApp(self.app)
        self.sessions = {}

    def tearDown(self):
        self.home.cleanup()

    def call(self, method, path, body=None, user=None, csrf=True, headers=None, query=None, ip="10.0.0.5"):
        h = dict(headers or {"host": "testhost"})
        if user:
            token, sess = self.session(user)
            h["cookie"] = "%s=%s" % (server.COOKIE, token)
            if csrf and method != "GET":
                h["x-csrf-token"] = sess["csrf"]
        data = json.dumps(body).encode() if body is not None else b""
        resp = self.web.handle(Req(method, path, query or {}, data, h, ip))
        try:
            resp.json = json.loads(resp.body.decode()) if resp.ctype == "application/json" and resp.body else None
        except ValueError:
            resp.json = None
        return resp

    def session(self, user):
        if user not in self.sessions:
            role = self.web.users.verify(user, GOOD if user == "admin" else GOOD + "1")
            self.sessions[user] = self.web.sessions.create(user, role, "10.0.0.5")
        return self.sessions[user]

    def write_report(self, stamp="20261120_134824", category="adhoc"):
        d = os.path.join(self.app.paths.report_dir, "h1", category, stamp)
        os.makedirs(d)
        for name, text in (("h1_x.html", "<html>hi</html>"), ("h1_x.json", json.dumps(fake_run().to_dict())), ("h1_x.xlsx", "x")):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(text)
        return "h1/%s/%s" % (category, stamp)


class AuthUnitTests(unittest.TestCase):
    def test_hashing(self):
        stored = auth.hash_password("Correct Horse 9")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn("Correct", stored)
        self.assertTrue(auth.verify_password("Correct Horse 9", stored))
        self.assertFalse(auth.verify_password("correct horse 9", stored))
        self.assertFalse(auth.verify_password("x", "garbage"))
        self.assertFalse(auth.verify_password("x", "md5$1$a$b"))
        self.assertNotEqual(auth.hash_password("same"), auth.hash_password("same"))  # a fresh salt each time

    def test_password_rules(self):
        self.assertIsNotNone(auth.password_problem("short1A"))
        self.assertIsNotNone(auth.password_problem("alllowercase123"))
        self.assertIsNotNone(auth.password_problem("NoDigitsHereAtAll"))
        self.assertIsNone(auth.password_problem("Str0ngPassw0rd"))

    def test_rate_limiter_blocks_then_releases(self):
        r = auth.RateLimiter(3, window=0.3)
        for _ in range(3):
            self.assertFalse(r.blocked("a"))
            r.failure("a")
        self.assertTrue(r.blocked("a"))
        self.assertFalse(r.blocked("b"))  # per address
        time.sleep(0.35)
        self.assertFalse(r.blocked("a"))
        r.failure("a")
        r.success("a")
        self.assertFalse(r.blocked("a"))

    def test_sessions_expire_and_slide(self):
        s = auth.Sessions(1)
        token, sess = s.create("u", "admin", "ip")
        self.assertIs(s.get(token), sess)
        self.assertIsNone(s.get("nope"))
        sess["seen"] -= 61
        self.assertIsNone(s.get(token))
        token2, sess2 = s.create("u", "admin", "ip")
        s.drop_user("u")
        self.assertIsNone(s.get(token2))


class SystemLoginTests(WebCase):
    def test_shadow_password_check(self):
        import crypt
        import tempfile
        h = crypt.crypt("Sup3r Secret!", crypt.mksalt(crypt.METHOD_SHA512))
        with tempfile.NamedTemporaryFile("w", suffix=".shadow", delete=False) as f:
            f.write(chr(10).join(["root:%s:19000:0:99999:7:::" % h, "locked:!%s:19000::::::" % h, "nologin:*:19000::::::", "empty::19000::::::", ""]))
            path = f.name
        try:
            self.assertTrue(auth.system_password_ok("root", "Sup3r Secret!", path))
            self.assertFalse(auth.system_password_ok("root", "wrong", path))
            self.assertFalse(auth.system_password_ok("locked", "Sup3r Secret!", path))      # a locked account never signs in
            self.assertFalse(auth.system_password_ok("nologin", "x", path))
            self.assertFalse(auth.system_password_ok("empty", "", path))
            self.assertFalse(auth.system_password_ok("nobody-here", "x", path))
            self.assertFalse(auth.system_password_ok("root", "x", path + ".missing"))
        finally:
            os.unlink(path)

    ACCOUNTS = {"root": {"uid": 0, "gid": 0, "shell": "/bin/bash"}, "alice": {"uid": 1001, "gid": 1001, "shell": "/bin/bash"},
                "bob": {"uid": 1002, "gid": 1002, "shell": "/bin/bash"}, "svc": {"uid": 998, "gid": 998, "shell": "/sbin/nologin"},
                "carol": {"uid": 1003, "gid": 1003, "shell": "/sbin/nologin"}}
    GROUPS = {"wheel": (10, ["alice"]), "sudo": (27, [])}

    def with_linux(self, fn):
        real = (auth.account_info, auth.group_info, auth.system_password_ok)
        auth.account_info, auth.group_info = self.ACCOUNTS.get, self.GROUPS.get
        auth.system_password_ok = lambda user, password, shadow=None: password == "LinuxPassw0rd"
        try:
            fn()
        finally:
            auth.account_info, auth.group_info, auth.system_password_ok = real

    def test_rights_follow_linux_groups(self):
        def check():
            cfg = self.app.cfg
            self.assertEqual(auth.system_users(cfg), ["*"])
            role = lambda n: auth.system_role(cfg, n)
            self.assertEqual(role("root"), "admin")
            self.assertEqual(role("alice"), "admin")          # member of wheel
            self.assertEqual(role("bob"), "viewer")           # an ordinary user: read-only
            self.assertIsNone(role("svc"))                    # service account
            self.assertIsNone(role("carol"))                  # no login shell
            self.assertIsNone(role("ghost"))                  # not a Linux account
            cfg.set("web", "web", "system_users", "bob")
            self.assertEqual(role("bob"), "viewer")
            self.assertIsNone(role("alice"))                  # not on the list
            cfg.set("web", "web", "system_users", "*")
            cfg.set("web", "web", "system_default_role", "none")
            self.assertIsNone(role("bob"))
            self.assertEqual(role("alice"), "admin")
            cfg.set("web", "web", "system_users", "")
            self.assertIsNone(role("root"))                   # switched off
        self.with_linux(check)

    def test_any_linux_user_can_sign_in_with_the_linux_password(self):
        def check():
            v = self.web.users.verify
            self.assertEqual(v("alice", "LinuxPassw0rd"), "admin")
            self.assertEqual(v("bob", "LinuxPassw0rd"), "viewer")
            self.assertIsNone(v("bob", "wrong"))
            self.assertIsNone(v("svc", "LinuxPassw0rd"))
            r = self.call("POST", "/api/login", {"user": "bob", "password": "LinuxPassw0rd"})
            self.assertEqual(r.status, 200, r.json)
            self.assertEqual(r.json["role"], "viewer")
            self.assertEqual(self.web.users.verify("admin", GOOD), "admin")     # web accounts still work
        self.with_linux(check)

    def test_a_viewer_from_linux_is_read_only_and_cannot_set_a_web_password(self):
        def check():
            token, sess = self.web.sessions.create("alice", "admin", "10.0.0.5")
            self.sessions["alice"] = (token, sess)
            r = self.call("POST", "/api/password", {"old": "x", "new": "NewPassw0rd1"}, user="alice")
            self.assertEqual(r.status, 400)
            self.assertIn("Linux account", r.json["error"])
            token, sess = self.web.sessions.create("bob", "viewer", "10.0.0.5")
            self.sessions["bob"] = (token, sess)
            self.assertEqual(self.call("POST", "/api/run", {}, user="bob").status, 403)
        self.with_linux(check)


class SignInTests(WebCase):
    def test_static_files_and_headers(self):
        r = self.call("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"/app.js", r.body)
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertIn("javascript", self.call("GET", "/app.js").ctype)
        self.assertEqual(self.call("GET", "/lrs_logo.png").ctype, "image/png")
        self.assertEqual(self.call("GET", "/nothing").status, 404)
        self.assertEqual(self.call("GET", "/../etc/passwd").status, 404)

    def test_api_needs_a_session(self):
        for path in ("/api/live", "/api/reports", "/api/config", "/api/users", "/reports/h1/a/b/c.html"):
            self.assertEqual(self.call("GET", path).status, 401, path)
        self.assertEqual(self.call("GET", "/api/me").status, 401)

    def test_wrong_password_and_unknown_user_are_refused_alike(self):
        a = self.call("POST", "/api/login", {"user": "admin", "password": "wrong"})
        b = self.call("POST", "/api/login", {"user": "ghost", "password": "wrong"})
        self.assertEqual((a.status, b.status), (401, 401))
        self.assertEqual(a.json, b.json)  # no hint which part was wrong
        with open(os.path.join(self.app.paths.log_dir, "audit.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("web-login-failed", log)
        self.assertNotIn("wrong", log)  # the password is never logged

    def test_successful_login_sets_a_safe_cookie(self):
        r = self.call("POST", "/api/login", {"user": "admin", "password": GOOD})
        self.assertEqual(r.status, 200)
        cookie = r.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Secure", cookie)  # this test server is not TLS
        self.assertEqual(r.json["role"], "admin")
        self.assertTrue(r.json["csrf"])
        token = cookie.split(";")[0].split("=", 1)[1]
        me = self.call("GET", "/api/me", headers={"host": "t", "cookie": "%s=%s" % (server.COOKIE, token)})
        self.assertEqual(me.json["user"], "admin")

    def test_secure_cookie_and_hsts_with_tls(self):
        self.web.secure = True
        r = self.call("POST", "/api/login", {"user": "admin", "password": GOOD})
        self.assertIn("Secure", r.headers["Set-Cookie"])
        self.assertIn("Strict-Transport-Security", r.headers)

    def test_too_many_failures_lock_the_address_out(self):
        self.web.limiter = auth.RateLimiter(3)
        for _ in range(3):
            self.assertEqual(self.call("POST", "/api/login", {"user": "admin", "password": "x"}).status, 401)
        self.assertEqual(self.call("POST", "/api/login", {"user": "admin", "password": GOOD}).status, 429)  # even the right one
        self.assertEqual(self.call("POST", "/api/login", {"user": "admin", "password": GOOD}, ip="10.0.0.9").status, 200)

    def test_bad_login_bodies(self):
        self.assertEqual(self.call("POST", "/api/login").status, 401)
        r = self.web.handle(Req("POST", "/api/login", {}, b"not json", {"host": "h"}))
        self.assertEqual(r.status, 400)
        r = self.web.handle(Req("POST", "/api/login", {}, b"[1,2]", {"host": "h"}))
        self.assertEqual(r.status, 400)

    def test_logout_ends_the_session(self):
        token, _ = self.session("admin")
        r = self.call("POST", "/api/logout", {}, user="admin")
        self.assertEqual(r.status, 200)
        self.assertIn("Max-Age=0", r.headers["Set-Cookie"])
        self.assertEqual(self.call("GET", "/api/live", headers={"host": "h", "cookie": "%s=%s" % (server.COOKIE, token)}).status, 401)


class ProtectionTests(WebCase):
    def test_changes_need_the_csrf_token(self):
        self.assertEqual(self.call("POST", "/api/scan", {"action": "scan"}, user="admin", csrf=False).status, 403)
        token, sess = self.session("admin")
        bad = {"host": "h", "cookie": "%s=%s" % (server.COOKIE, token), "x-csrf-token": "wrong"}
        self.assertEqual(self.call("POST", "/api/scan", {"action": "scan"}, headers=bad).status, 403)

    def test_cross_site_requests_are_refused(self):
        token, sess = self.session("admin")
        h = {"host": "myserver:8443", "origin": "https://evil.example", "cookie": "%s=%s" % (server.COOKIE, token), "x-csrf-token": sess["csrf"]}
        r = self.call("POST", "/api/scan", {"action": "scan"}, headers=h)
        self.assertEqual(r.status, 403)
        self.assertIn("cross-site", r.json["error"])
        h["origin"] = "https://myserver:8443"
        self.assertNotEqual(self.call("POST", "/api/scan", {"action": "bogus"}, headers=h).status, 403)

    def test_a_viewer_can_look_but_not_change(self):
        self.write_report()
        self.assertEqual(self.call("GET", "/api/reports", user="viewer").status, 200)
        self.assertEqual(self.call("GET", "/api/live", user="viewer").status, 200)
        self.assertEqual(self.call("GET", "/api/scan", user="viewer").status, 200)
        for method, path, body in (("POST", "/api/run", {}), ("POST", "/api/scan", {"action": "scan"}), ("POST", "/api/config", {"file": "main"}),
                                   ("POST", "/api/action", {"name": "config-backup"}), ("POST", "/api/users", {"action": "add"})):
            r = self.call(method, path, body, user="viewer")
            self.assertEqual(r.status, 403, path)
            self.assertIn("read-only", r.json["error"])
        for path in ("/api/config", "/api/users", "/api/backups", "/api/logs", "/api/audit"):
            self.assertEqual(self.call("GET", path, user="viewer").status, 403, path)  # settings and logs: administrators only
        self.assertEqual(self.call("POST", "/api/password", {"old": GOOD + "1", "new": "An0therStrongOne"}, user="viewer").status, 200)

    def test_unknown_routes_and_methods(self):
        self.assertEqual(self.call("GET", "/api/nothing", user="admin").status, 404)
        self.assertEqual(self.call("DELETE", "/api/config", user="admin").status, 405)

    def test_errors_do_not_leak_tracebacks(self):
        self.web.api_live = lambda req, sess: 1 / 0
        r = self.call("GET", "/api/live", user="admin")
        self.assertEqual(r.status, 500)
        self.assertNotIn("ZeroDivision", r.body.decode())


class ReportTests(WebCase):
    def test_list_detail_and_files(self):
        rid = self.write_report()
        d = self.call("GET", "/api/reports", user="viewer").json
        self.assertEqual(d["reports"][0]["id"], rid)
        self.assertEqual(d["reports"][0]["score"], 69)
        detail = self.call("GET", "/api/report", user="viewer", query={"id": [rid]}).json
        self.assertEqual(detail["health"]["status"], "CRITICAL")
        self.assertEqual(detail["modules"][0]["title"], "Storage")
        self.assertEqual(detail["findings"][0]["recommendation"], "Free space.")
        urls = dict((f["name"], f["url"]) for f in detail["files"])
        html = self.call("GET", urls["h1_x.html"], user="viewer")
        self.assertEqual(html.status, 200)
        self.assertIn("sandbox", html.headers["Content-Security-Policy"])  # a report cannot reach the interface's cookies
        self.assertEqual(html.ctype, "text/html; charset=utf-8")
        xlsx = self.call("GET", urls["h1_x.xlsx"], user="viewer")
        self.assertIn("attachment", xlsx.headers["Content-Disposition"])

    def test_nothing_outside_the_report_folder_is_served(self):
        self.write_report()
        outside = os.path.join(self.home.dir, "secret.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("top secret")
        os.symlink(outside, os.path.join(self.app.paths.report_dir, "h1", "adhoc", "20261120_134824", "link.txt"))
        for path in ("/reports/h1/adhoc/20261120_134824/../../../../secret.txt", "/reports/h1/adhoc/20261120_134824/link.txt",
                     "/reports/../../etc/passwd", "/reports/h1/adhoc/20261120_134824/", "/reports/h1/adhoc/nonexistent/x.html"):
            r = self.call("GET", path, user="admin")
            self.assertIn(r.status, (400, 404), path)
            self.assertNotIn(b"top secret", r.body)
        for rid in ("../../etc", "h1/adhoc", "h1/adhoc/20261120_134824/extra", "..", ""):
            self.assertEqual(self.call("GET", "/api/report", user="admin", query={"id": [rid]}).status, 400, rid)

    def test_a_broken_json_still_lists_the_files(self):
        rid = self.write_report()
        with open(os.path.join(self.app.paths.report_dir, rid, "h1_x.json"), "w", encoding="utf-8") as f:
            f.write("{ nope")
        detail = self.call("GET", "/api/report", user="admin", query={"id": [rid]}).json
        self.assertIn("could not be read", detail["error"])
        self.assertEqual(len(detail["files"]), 3)


class RunAndScanTests(WebCase):
    def test_run_job_progress_and_single_flight(self):
        gate = threading.Event()

        def fake_full_run(*a, **kw):
            kw["progress"](1, 4, "Storage")
            gate.wait(5)
            return fake_run(), {"files": [], "report_dir": ""}
        self.app.full_run = fake_full_run
        r = self.call("POST", "/api/run", {}, user="admin")
        self.assertEqual(r.status, 202)
        time.sleep(0.2)
        state = self.call("GET", "/api/run", user="viewer").json
        self.assertTrue(state["running"])
        self.assertEqual((state["progress"], state["total"], state["title"]), (1, 4, "Storage"))
        self.assertEqual(self.call("POST", "/api/run", {}, user="admin").status, 409)  # one run at a time
        gate.set()
        for _ in range(50):
            state = self.call("GET", "/api/run", user="admin").json
            if not state["running"]:
                break
            time.sleep(0.1)
        self.assertFalse(state["running"])
        self.assertEqual(state["result"]["score"], 69)

    def test_run_failures_are_reported(self):
        def boom(*a, **kw):
            raise OSError("disk full")
        self.app.full_run = boom
        self.call("POST", "/api/run", {}, user="admin")
        for _ in range(50):
            state = self.call("GET", "/api/run", user="admin").json
            if not state["running"]:
                break
            time.sleep(0.1)
        self.assertIn("disk full", state["error"])

    def test_scan_apply_and_force(self):
        self.app.runner = FakeRunner()
        self.assertIsNone(self.call("GET", "/api/scan", user="admin").json["profile"])
        r = self.call("POST", "/api/scan", {"action": "scan"}, user="admin").json
        self.assertTrue(r["pending"])
        self.assertFalse(r["profile"]["modules"]["kvm"]["present"])
        r = self.call("POST", "/api/scan", {"action": "apply"}, user="admin").json
        self.assertIn("kvm", r["excluded"])
        self.assertFalse(r["pending"])
        r = self.call("POST", "/api/scan", {"action": "force", "module": "kvm", "on": True}, user="admin").json
        self.assertEqual(r["forced"], ["kvm"])
        r = self.call("POST", "/api/scan", {"action": "force", "module": "kvm", "on": False}, user="admin").json
        self.assertEqual(r["forced"], [])
        self.assertEqual(self.call("POST", "/api/scan", {"action": "force", "module": "nonsense", "on": True}, user="admin").status, 400)
        self.assertEqual(self.call("POST", "/api/scan", {"action": "apply"}, user="admin").status, 200)


class AlertsAndFleetTests(WebCase):
    def test_alerts_show_active_problems_and_how_they_are_sent(self):
        d = self.app.paths.data_dir
        with open(os.path.join(d, "watch_events.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "open", "key": "cpu", "component": "cpu", "message": "CPU utilisation 97%", "severity": "CRITICAL", "ts": time.time()}) + chr(10))
        with open(os.path.join(d, "watch_state.json"), "w", encoding="utf-8") as f:
            json.dump({"problems": {"cpu": {"severity": "CRITICAL", "count": 3, "since": time.time() - 300, "alerted": "CRITICAL", "last_alert": 0}}}, f)
        r = self.call("GET", "/api/alerts", user="admin")
        self.assertEqual(r.status, 200, r.json)
        self.assertEqual(r.json["active"][0]["severity"], "CRITICAL")
        self.assertEqual(r.json["active"][0]["message"], "CPU utilisation 97%")
        self.assertEqual(r.json["events"][0]["event"], "open")
        self.assertIn("syslog", r.json["channels"])

    def test_fleet_lists_servers_and_refuses_to_run_without_any(self):
        from lshme.ops import dashboard
        run = fake_run()
        run.host, run.ip = "db02", "10.0.0.2"
        run.meta["roles"] = ["KVM host"]
        dashboard.save_results(self.app.paths, [run], {"web9": "ssh: connection refused"})
        r = self.call("GET", "/api/fleet", user="viewer")
        self.assertEqual(r.status, 200, r.json)
        by = dict((s["name"], s) for s in r.json["servers"])
        self.assertEqual(by["db02"]["roles"], ["KVM host"])
        self.assertEqual(by["web9"]["status"], "UNREACHABLE")
        self.assertEqual([x["status"] for x in r.json["servers"]], ["CRITICAL", "UNREACHABLE"])      # worst first
        self.assertEqual(self.call("POST", "/api/fleet", {"action": "run"}, user="admin").status, 400)   # none configured
        self.assertEqual(self.call("POST", "/api/fleet", {"action": "run"}, user="viewer").status, 403)  # read-only account


class ToolHealthWebTests(WebCase):
    def test_tool_health_retention_audit_and_backup_download(self):
        r = self.call("GET", "/api/toolhealth", user="admin")
        self.assertEqual(r.status, 200, r.json)
        self.assertTrue(any(c["name"] == "Real-time watcher" for c in r.json["checks"]))
        r = self.call("GET", "/api/retention", user="admin")
        self.assertEqual(r.status, 200)
        self.assertEqual([t["type"] for t in r.json["types"]][:3], ["daily", "weekly", "monthly"])
        self.app.audit("test-action", detail="x")
        r = self.call("GET", "/api/audit", user="admin")
        self.assertEqual(r.json["records"][0]["action"], "test-action")
        self.assertEqual(self.call("GET", "/api/toolhealth", user="viewer").status, 200)
        self.call("POST", "/api/action", {"name": "config-backup"}, user="admin")
        r = self.call("GET", "/api/backups", user="admin")
        self.assertTrue(r.json["details"] and r.json["details"][0]["size"] > 0)
        dl = self.call("GET", "/api/backups", user="admin", query={"download": [r.json["backups"][0]]})
        self.assertEqual(dl.status, 200)
        self.assertIn("gzip", dl.ctype)
        self.assertEqual(self.call("GET", "/api/backups", user="admin", query={"download": ["../../etc/passwd"]}).status, 400)


class ConfigApiTests(WebCase):
    def section(self, file, name):
        cat = self.call("GET", "/api/config", user="admin").json
        f = [x for x in cat["files"] if x["file"] == file][0]
        return [s for s in f["sections"] if s["name"] == name][0]

    def test_describe_types_defaults_and_help(self):
        cat = self.call("GET", "/api/config", user="admin").json
        self.assertEqual([f["file"] for f in cat["files"]][:3], ["main", "thresholds", "services"])
        smtp = dict((e["key"], e) for e in self.section("smtp", "smtp")["keys"])
        self.assertEqual(smtp["port"]["kind"], "int")
        self.assertEqual(smtp["security"]["choices"], ["starttls", "ssl", "none"])
        self.assertEqual(smtp["to"]["kind"], "emails")
        self.assertEqual(smtp["attach_formats"]["kind"], "multi")
        self.assertTrue(smtp["to"]["help"])
        self.assertEqual(smtp["port"]["default"], "587")
        general = dict((e["key"], e) for e in self.section("main", "security")["keys"])
        self.assertEqual(general["require_firewall"]["kind"], "bool")
        users = [s["name"] for f in cat["files"] if f["file"] == "web" for s in f["sections"]]
        self.assertEqual(users, ["web"])  # the account hashes are never offered for editing

    def test_valid_change_is_saved_with_backup_and_audit(self):
        r = self.call("POST", "/api/config", {"file": "smtp", "section": "smtp", "values": {"host": "smtp.example.com", "port": "465", "security": "ssl"}}, user="admin")
        self.assertEqual(r.status, 200, r.json)
        self.app.reload()
        self.assertEqual(self.app.cfg.get("smtp", "smtp", "host"), "smtp.example.com")
        self.assertEqual(self.app.cfg.get("smtp", "smtp", "port"), "465")
        self.assertEqual(oct(os.stat(self.app.cfg.path("smtp")).st_mode & 0o777), "0o600")
        self.assertTrue(os.listdir(self.app.paths.config_backup_dir))
        with open(os.path.join(self.app.paths.log_dir, "audit.log"), encoding="utf-8") as f:
            self.assertIn("web-config-change", f.read())

    def test_invalid_values_are_refused_and_nothing_is_written(self):
        for values, word in (({"port": "abc"}, "number"), ({"port": "99999"}, "between"), ({"security": "rot13"}, "one of"),
                             ({"to": "not an address"}, "email"), ({"attach_formats": "html,exe"}, "unknown"), ({"host": "a\nb"}, "single line")):
            r = self.call("POST", "/api/config", {"file": "smtp", "section": "smtp", "values": values}, user="admin")
            self.assertEqual(r.status, 400, values)
            self.assertIn(word, r.json["error"], values)
        self.assertFalse(os.path.exists(self.app.cfg.path("smtp")))

    def test_threshold_pairs_and_schedule_rules(self):
        r = self.call("POST", "/api/config", {"file": "thresholds", "section": "cpu", "values": {"warning": "99"}}, user="admin")
        self.assertEqual(r.status, 400)
        self.assertIn("must not be above critical", r.json["error"])
        r = self.call("POST", "/api/config", {"file": "schedule", "section": "schedule:daily", "values": {"time": "25:99"}}, user="admin")
        self.assertEqual(r.status, 400)
        ok = self.call("POST", "/api/config", {"file": "schedule", "section": "schedule:daily", "values": {"time": "06:30"}}, user="admin")
        self.assertEqual(ok.status, 200)

    def test_passwords_are_write_only(self):
        # credentials.conf has no [smtp] section until now, so create it the way the editor does
        r = self.call("POST", "/api/config/section", {"action": "add", "file": "credentials", "name": "smtp", "values": {"password": "hunter2hunter2"}}, user="admin")
        self.assertEqual(r.status, 200, r.json)
        cat = self.call("GET", "/api/config", user="admin")
        self.assertNotIn("hunter2", cat.body.decode())
        entry = self.section("credentials", "smtp")["keys"][0]
        self.assertEqual((entry["kind"], entry["set"], entry["value"]), ("secret", True, ""))
        with open(self.app.cfg.path("credentials"), encoding="utf-8") as f:
            self.assertIn("hunter2hunter2", f.read())
        self.call("POST", "/api/config", {"file": "credentials", "section": "smtp", "values": {"password": ""}}, user="admin")
        self.app.reload()
        self.assertEqual(self.app.cfg.secret("smtp"), "hunter2hunter2")  # an empty box leaves it alone
        with open(os.path.join(self.app.paths.log_dir, "audit.log"), encoding="utf-8") as f:
            self.assertNotIn("hunter2", f.read())

    def test_sections_can_be_added_and_deleted(self):
        r = self.call("POST", "/api/config/section", {"action": "add", "file": "schedule", "name": "schedule:nightly"}, user="admin")
        self.assertEqual(r.status, 200, r.json)
        sec = self.section("schedule", "schedule:nightly")
        self.assertIn("frequency", [e["key"] for e in sec["keys"]])  # typed from the schedule defaults
        self.assertFalse(sec["builtin"])
        self.assertEqual(self.call("POST", "/api/config/section", {"action": "add", "file": "schedule", "name": "schedule:nightly"}, user="admin").status, 400)
        self.assertEqual(self.call("POST", "/api/config/section", {"action": "delete", "file": "schedule", "name": "schedule:daily"}, user="admin").status, 400)
        self.assertEqual(self.call("POST", "/api/config/section", {"action": "delete", "file": "schedule", "name": "schedule:nightly"}, user="admin").status, 200)
        self.app.reload()
        self.assertNotIn("schedule:nightly", self.app.cfg.sections("schedule"))

    def test_free_sections_take_any_key_and_keys_can_be_removed(self):
        r = self.call("POST", "/api/config", {"file": "services", "section": "ports", "values": {"oracle-listener": "1521"}}, user="admin")
        self.assertEqual(r.status, 200, r.json)
        self.assertEqual([e["key"] for e in self.section("services", "ports")["keys"]], ["oracle-listener"])
        r = self.call("POST", "/api/config", {"file": "services", "section": "ports", "values": {}, "remove": ["oracle-listener"]}, user="admin")
        self.assertEqual(r.status, 200)
        self.assertEqual(self.section("services", "ports")["keys"], [])

    def test_hostile_input(self):
        for body in ({"file": "../etc/passwd", "section": "x", "values": {}}, {"file": "main", "section": "nope", "values": {}},
                     {"file": "web", "section": "users", "values": {"x": "y"}}, {"file": "main", "section": "general", "values": {"bad\nkey": "v"}},
                     {"file": "main", "section": "general", "values": {"company": ["list"]}}):
            self.assertEqual(self.call("POST", "/api/config", body, user="admin").status, 400, body)
        for name in ("a b", "x]\n[evil", "", "../x"):
            self.assertEqual(self.call("POST", "/api/config/section", {"action": "add", "file": "custom", "name": name}, user="admin").status, 400, name)
        self.assertEqual(self.call("POST", "/api/config/section", {"action": "add", "file": "web", "name": "x"}, user="admin").status, 400)


class OperationsTests(WebCase):
    def test_actions_backups_logs_and_audit(self):
        self.app.runner = FakeRunner({"systemctl": (0, "inactive\n")})
        r = self.call("POST", "/api/action", {"name": "config-backup"}, user="admin")
        self.assertEqual(r.status, 200)
        archive = r.json["archive"]
        self.assertIn(archive, self.call("GET", "/api/backups", user="admin").json["backups"])
        self.assertEqual(self.call("POST", "/api/backups", {"archive": archive}, user="admin").status, 200)
        self.assertEqual(self.call("POST", "/api/backups", {"archive": "../../etc/passwd"}, user="admin").status, 400)
        self.assertEqual(self.call("POST", "/api/action", {"name": "rm -rf"}, user="admin").status, 400)
        w = self.call("GET", "/api/watch", user="viewer").json
        self.assertEqual(w["service"], "inactive")
        self.assertIn("backend", self.call("GET", "/api/schedules", user="viewer").json)
        self.assertIn("web-action", "\n".join(self.call("GET", "/api/audit", user="admin").json["lines"]))
        names = self.call("GET", "/api/logs", user="admin").json["logs"]
        self.assertTrue(names)
        self.assertEqual(self.call("GET", "/api/logs", user="admin", query={"name": ["../audit.log"]}).status, 400)
        self.assertIn("lines", self.call("GET", "/api/logs", user="admin", query={"name": [names[0]]}).json)

    def test_help(self):
        self.assertIn("vitals", "\n".join(self.call("GET", "/api/help", user="viewer").json["lines"]))


class AccountTests(WebCase):
    def test_user_management_rules(self):
        users = lambda: dict((u["user"], u["role"]) for u in self.call("GET", "/api/users", user="admin").json["users"])
        self.assertEqual(users(), {"admin": "admin", "viewer": "viewer"})
        self.assertEqual(self.call("POST", "/api/users", {"action": "add", "user": "ops", "password": "weak", "role": "viewer"}, user="admin").status, 400)
        self.assertEqual(self.call("POST", "/api/users", {"action": "add", "user": "bad name!", "password": GOOD, "role": "viewer"}, user="admin").status, 400)
        self.assertEqual(self.call("POST", "/api/users", {"action": "add", "user": "ops", "password": GOOD, "role": "viewer"}, user="admin").status, 200)
        self.assertEqual(self.call("POST", "/api/users", {"action": "add", "user": "ops", "password": GOOD, "role": "viewer"}, user="admin").status, 400)
        self.assertEqual(users()["ops"], "viewer")
        self.assertEqual(self.call("POST", "/api/users", {"action": "remove", "user": "admin"}, user="admin").status, 400)  # not yourself
        self.assertEqual(self.call("POST", "/api/users", {"action": "role", "user": "admin", "role": "viewer"}, user="admin").status, 400)
        self.assertEqual(self.call("POST", "/api/users", {"action": "role", "user": "ops", "role": "admin"}, user="admin").status, 200)
        self.assertEqual(self.call("POST", "/api/users", {"action": "remove", "user": "ops"}, user="admin").status, 200)
        self.assertNotIn("ops", users())
        with open(self.app.cfg.path("web"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(GOOD, text)
        self.assertIn("pbkdf2_sha256", text)
        self.assertEqual(oct(os.stat(self.app.cfg.path("web")).st_mode & 0o777), "0o600")

    def test_the_last_administrator_cannot_be_removed(self):
        with self.assertRaises(ValueError):
            self.web.users.remove("admin")
        self.web.users.remove("viewer")

    def test_password_change_and_reset_end_sessions(self):
        self.assertEqual(self.call("POST", "/api/password", {"old": "wrong", "new": "An0therStrongOne"}, user="admin").status, 400)
        self.assertEqual(self.call("POST", "/api/password", {"old": GOOD, "new": "short"}, user="admin").status, 400)
        self.assertEqual(self.call("POST", "/api/password", {"old": GOOD, "new": "An0therStrongOne"}, user="admin").status, 200)
        self.assertEqual(self.web.users.verify("admin", "An0therStrongOne"), "admin")
        self.assertIsNone(self.web.users.verify("admin", GOOD))
        token, _ = self.session("viewer")
        self.call("POST", "/api/users", {"action": "passwd", "user": "viewer", "password": "Fresh1PasswordX"}, user="admin")
        self.assertEqual(self.call("GET", "/api/live", headers={"host": "h", "cookie": "%s=%s" % (server.COOKIE, token)}).status, 401)  # signed out


class RealServerTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.app = App(ROOT, home=self.home.dir)
        auth.Users(self.app).set_password("admin", GOOD, "admin")

    def tearDown(self):
        self.home.cleanup()

    def start(self, **cfg):
        for k, v in cfg.items():
            self.app.cfg.set("web", "web", k, v)
        self.app.cfg.save("web")
        self.app.reload()
        srv, web = server.make_server(self.app, port=0)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv.server_address[1]

    def test_plain_http_on_loopback_end_to_end(self):
        port = self.start(tls="no")
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        r.read()
        c.request("POST", "/api/login", json.dumps({"user": "admin", "password": GOOD}), {"Content-Type": "application/json"})
        r = c.getresponse()
        me = json.loads(r.read().decode())
        cookie = r.getheader("Set-Cookie").split(";")[0]
        self.assertEqual(me["user"], "admin")
        c.request("GET", "/api/live", headers={"Cookie": cookie})
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertIn("sample", json.loads(r.read().decode()))
        c.request("PUT", "/api/config", "{}", {"Cookie": cookie})
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 405)
        c.request("GET", "/reports/%2e%2e/%2e%2e/%2e%2e/etc/passwd", headers={"Cookie": cookie})
        r = c.getresponse()
        self.assertNotIn(b"root:", r.read())
        c.putrequest("POST", "/api/login")
        c.putheader("Content-Length", str(server.MAX_BODY + 10))
        c.endheaders()
        self.assertEqual(c.getresponse().status, 413)  # refused before the body is read

    def test_refuses_unsafe_or_incomplete_setups(self):
        self.app.cfg.set("web", "web", "tls", "no")
        self.app.cfg.set("web", "web", "bind", "0.0.0.0")
        self.app.cfg.save("web")
        self.app.reload()
        with self.assertRaises(ValueError) as cm:
            server.make_server(self.app, port=0)
        self.assertIn("plain HTTP", str(cm.exception))
        self.app.cfg.set("web", "web", "bind", "127.0.0.1")
        self.app.cfg.parsers["web"].remove_option("users", "admin")
        self.app.cfg.set("web", "web", "system_users", "")      # no Linux account may sign in either
        self.app.cfg.save("web")
        self.app.reload()
        with self.assertRaises(ValueError) as cm:
            server.make_server(self.app, port=0)
        self.assertIn("no web users", str(cm.exception))

    def test_https_with_a_generated_certificate(self):
        if not self.app.runner.has("openssl"):
            self.skipTest("openssl is not installed")
        port = self.start(tls="yes")
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # the certificate is self-signed
        c = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=ctx)
        c.request("POST", "/api/login", json.dumps({"user": "admin", "password": GOOD}), {"Content-Type": "application/json"})
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 200)
        self.assertIn("Secure", r.getheader("Set-Cookie"))
        self.assertIsNotNone(r.getheader("Strict-Transport-Security"))
        key = os.path.join(self.app.paths.data_dir, "web", "server.key")
        self.assertEqual(oct(os.stat(key).st_mode & 0o777), "0o600")


class ValidationTests(unittest.TestCase):
    def test_validate_value_by_kind(self):
        v = confmeta.validate_value
        self.assertIsNone(v("bool", "x", "yes"))
        self.assertIsNotNone(v("bool", "x", "maybe"))
        self.assertIsNone(v("int", "port", "8443"))
        self.assertIsNotNone(v("int", "port", "0"))
        self.assertIsNotNone(v("int", "x", "1.5"))
        self.assertIsNone(v("float", "x", "1.5"))
        self.assertIsNone(v("time", "time", "07:00"))
        self.assertIsNotNone(v("time", "time", "7pm"))
        self.assertIsNone(v("path", "path", "/a/b"))
        self.assertIsNotNone(v("path", "path", "relative"))
        self.assertIsNone(v("emails", "to", "a@b.c, d@e.f"))
        self.assertIsNone(v("emails", "to", ""))
        self.assertIsNotNone(v("choice", "x", "z", ["a", "b"]))
        self.assertIsNotNone(v("text", "x", "a\x00b"))

    def test_kinds_are_worked_out_from_defaults_and_names(self):
        k = confmeta.kind_of
        self.assertEqual(k("main", "watch", "interval", "60"), "int")
        self.assertEqual(k("thresholds", "load", "per_core_warning", "1.5"), "float")
        self.assertEqual(k("main", "security", "require_firewall", "yes"), "bool")
        self.assertEqual(k("smtp", "smtp", "password", None), "secret")
        self.assertEqual(k("credentials", "x", "anything", None), "secret")
        self.assertEqual(k("backup", "netbackup", "enabled", "auto"), "choice")


if __name__ == "__main__":
    unittest.main()
