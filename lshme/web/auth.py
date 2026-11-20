# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Accounts, sessions and sign-in rate limiting for the web interface.

Passwords are stored only as salted PBKDF2-HMAC-SHA256 hashes in web.conf [users]. Sessions are random
tokens kept in memory (they end when the server restarts), expire after a period of inactivity and carry a
CSRF token that every change must present."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

SHADOW = "/etc/shadow"
ITERATIONS = 200000
ROLES = ("admin", "viewer")
USER_RX = re.compile(r"^[A-Za-z0-9_.@-]{1,40}$")
MIN_PASSWORD = 10


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def hash_password(password, salt=None, iterations=None):
    iterations = iterations or ITERATIONS
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, _b64(salt), _b64(digest))


def verify_password(password, stored):
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def system_users(cfg):
    """Linux accounts allowed to sign in with their own Linux password (web.conf [web] system_users, default root)."""
    return [u.strip() for u in (cfg.get("web", "web", "system_users", "root") or "").replace(";", ",").split(",") if u.strip()]


def system_password_ok(user, password, shadow=SHADOW):
    """True when the Linux account exists, is not locked and the password is right. Reads /etc/shadow, so the
    application must run as root. The password is never written anywhere."""
    try:
        with open(shadow, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    stored = None
    for line in lines:
        parts = line.split(":")
        if len(parts) > 1 and parts[0] == user:
            stored = parts[1]
            break
    if not stored or stored[0] in "!*" or not stored.startswith("$"):     # no such account, locked, or no password
        return False
    try:
        import crypt
        calc = crypt.crypt(password, stored)
        return bool(calc) and hmac.compare_digest(calc, stored)
    except ImportError:                                                  # newer Pythons dropped the crypt module: use openssl
        import subprocess
        parts = stored.split("$")
        algo = {"1": "-1", "5": "-5", "6": "-6"}.get(parts[1] if len(parts) > 1 else "")
        if not algo or len(parts) < 4 or parts[2].startswith("rounds="):
            return False
        try:
            out = subprocess.run(["openssl", "passwd", algo, "-salt", parts[2], "-stdin"], input=(password + chr(10)).encode("utf-8"), stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=10).stdout.decode("utf-8", "replace").strip()
        except (OSError, subprocess.SubprocessError):
            return False
        return bool(out) and hmac.compare_digest(out, stored)


def password_problem(password):
    """Why a password is not acceptable, or None."""
    if len(password) < MIN_PASSWORD:
        return "use at least %d characters" % MIN_PASSWORD
    if password.lower() == password or password.upper() == password or not re.search(r"\d", password):
        return "mix upper and lower case letters and at least one digit"
    return None


_dummy = []


def _dummy_hash():
    if not _dummy:
        _dummy.append(hash_password("not-a-real-password", b"0" * 16))
    return _dummy[0]


class Users(object):
    """The accounts in web.conf. Every change is saved through the application (backup + audit)."""

    def __init__(self, app):
        self.app = app

    def _p(self):
        return self.app.cfg.parsers["web"]

    def names(self):
        p = self._p()
        return sorted(p.options("users")) if p.has_section("users") else []

    def role(self, name):
        p = self._p()
        role = p.get("roles", name).strip() if p.has_section("roles") and p.has_option("roles", name) else "admin"
        return role if role in ROLES else "viewer"

    def listing(self):
        return [{"user": n, "role": self.role(n)} for n in self.names()]

    def verify(self, name, password):
        """The role when the name and password are right, else None. Unknown names cost the same time as wrong passwords."""
        if name in system_users(self.app.cfg):         # a Linux account: its own Linux password, full access
            return "admin" if system_password_ok(name, password) else None
        p = self._p()
        stored = p.get("users", name) if p.has_section("users") and p.has_option("users", name) else None
        if stored is None:
            verify_password(password, _dummy_hash())
            return None
        return self.role(name) if verify_password(password, stored) else None

    def set_password(self, name, password, role=None):
        if not USER_RX.match(name):
            raise ValueError("a user name may contain letters, digits and . _ @ - (up to 40 characters)")
        problem = password_problem(password)
        if problem:
            raise ValueError("password: " + problem)
        if role is not None and role not in ROLES:
            raise ValueError("role must be admin or viewer")
        cfg = self.app.cfg
        cfg.set("web", "users", name, hash_password(password))
        if role or name not in self.names():
            cfg.set("web", "roles", name, role or "admin")
        self.app.save_config("web")
        self.app.reload()

    def remove(self, name):
        if name not in self.names():
            raise ValueError("no such user")
        admins = [n for n in self.names() if self.role(n) == "admin" and n != name]
        if self.role(name) == "admin" and not admins:
            raise ValueError("the last administrator cannot be removed")
        self.app.cfg.parsers["web"].remove_option("users", name)
        if self.app.cfg.parsers["web"].has_section("roles"):
            self.app.cfg.parsers["web"].remove_option("roles", name)
        self.app.save_config("web")
        self.app.reload()


class Sessions(object):
    def __init__(self, minutes=30):
        self.ttl = max(1, int(minutes)) * 60
        self.items = {}
        self.lock = threading.Lock()

    def create(self, user, role, ip):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self._purge()
            self.items[token] = {"user": user, "role": role, "ip": ip, "csrf": secrets.token_urlsafe(24), "seen": time.time(), "started": time.time()}
        return token, self.items[token]

    def get(self, token):
        if not token:
            return None
        with self.lock:
            s = self.items.get(token)
            if not s or time.time() - s["seen"] > self.ttl:
                self.items.pop(token, None)
                return None
            s["seen"] = time.time()  # sliding expiry
            return s

    def drop(self, token):
        with self.lock:
            self.items.pop(token, None)

    def drop_user(self, user):
        with self.lock:
            for t in [t for t, s in self.items.items() if s["user"] == user]:
                del self.items[t]

    def _purge(self):
        now = time.time()
        for t in [t for t, s in self.items.items() if now - s["seen"] > self.ttl]:
            del self.items[t]


class RateLimiter(object):
    """Too many wrong passwords from one address in a short time: refuse for a while."""

    def __init__(self, max_failures=5, window=300):
        self.max, self.window = max(1, int(max_failures)), window
        self.fails = {}
        self.lock = threading.Lock()

    def blocked(self, ip):
        with self.lock:
            now = time.time()
            recent = [t for t in self.fails.get(ip, []) if now - t < self.window]
            self.fails[ip] = recent
            return len(recent) >= self.max

    def failure(self, ip):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())

    def success(self, ip):
        with self.lock:
            self.fails.pop(ip, None)
