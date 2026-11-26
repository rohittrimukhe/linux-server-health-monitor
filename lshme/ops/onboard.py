# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Add a Linux server from the central server and install LRS Vitals on it (web: Add > Linux server).

Steps, each reported with its result:
  1. Connect      - with the password given once, put the central server's SSH key on the server; the password is used only
                    for this login, never stored or written anywhere. Without a password the key must already work.
  2. Check        - key login works, it is root, Python 3 is there, which Linux it is.
  3. Install      - copy this LRS Vitals (the same version as the central server) to /opt/lshme, add the vitals command,
                    create /etc/lshme and /var/lib/lshme. An existing installation is kept as /opt/lshme.pre-install.<time>.
  4. Settings     - e-mail, schedules, reports, retention, thresholds and exceptions as on the central server; customer name,
                    short name and e-mail sender; this server's group and environment. SAN, hardware and fleet settings stay central.
  5. Start        - scan what is installed, install the schedules and the real-time watcher, start the first health check.
  6. Add to fleet - servers.conf on the central server, the customer logo, and the live dashboard.
Nothing else on the server is changed."""
import base64
import io
import os
import shlex
import stat
import tarfile
import tempfile
import threading
import time
import uuid

from lshme.ops import branding

SHARED = ("smtp.conf", "schedule.conf", "reports.conf", "retention.conf", "thresholds.conf", "exclusions.conf")
JOBS = {}
_LOCK = threading.Lock()


class OnboardError(ValueError):
    pass


def _ssh(host, port, user, extra=None):
    return ["ssh", "-p", str(port), "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=15"] + (extra or []) + ["%s@%s" % (user, host)]


def ensure_key(app):
    """The central server's SSH key (made once if there is none); returns the public key text."""
    home = os.path.expanduser("~")
    key = os.path.join(home, ".ssh", "id_ed25519")
    if not os.path.exists(key + ".pub"):
        os.makedirs(os.path.dirname(key), mode=0o700, exist_ok=True)
        r = app.runner.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "lrs-vitals-central", "-f", key], timeout=60)
        if not r.ok:
            raise OnboardError("could not create an SSH key on the central server: %s" % (r.err or r.out))
    with open(key + ".pub", encoding="utf-8") as f:
        return f.read().strip()


def push_key(app, host, port, user, password, pub):
    """One password login (OpenSSH asks the helper for it; the password only lives in this process' memory) that appends the key."""
    fd, helper = tempfile.mkstemp(prefix="lrs-askpass-", suffix=".sh")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nprintf '%s\\n' \"$LRS_ONBOARD_PW\"\n")
        os.chmod(helper, stat.S_IRWXU)
        remote = "umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && (grep -qxF %s ~/.ssh/authorized_keys || cat >> ~/.ssh/authorized_keys); " \
                 "chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; restorecon -R ~/.ssh >/dev/null 2>&1; true" % shlex.quote(pub)
        r = app.runner.run(_ssh(host, port, user, ["-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive",
                                                   "-o", "NumberOfPasswordPrompts=1"]) + [remote],
                           timeout=60, input_text=pub + "\n",
                           env={"SSH_ASKPASS": helper, "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": "none", "LRS_ONBOARD_PW": password})
    finally:
        try:
            os.remove(helper)
        except OSError:
            pass
    if not r.ok:
        msg = (r.err or r.out or "failed").strip().splitlines()[-1:] or ["failed"]
        raise OnboardError("password login to %s failed: %s" % (host, msg[0][:200]))


def program_tar(app):
    """This installation's program as a .tar.gz (no caches, no backups), base64 for the SSH channel."""
    root = getattr(app.paths, "app_dir", "") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        from lshme.ops.role import MEMBER_REMOVE
        skip = tuple(MEMBER_REMOVE)                                  # a member gets only what it uses

        def keep(ti):
            if "__pycache__" in ti.name or ti.name.endswith(".pyc") or ti.name.startswith(skip):
                return None
            return ti
        for name in ("lshme", "healthcheck", "config-templates", "README.md", "CHANGELOG.md", ".lrs_commit", "tools"):
            p = os.path.join(root, name)
            if os.path.exists(p):
                t.add(p, arcname=name, filter=keep)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def settings_tar(app):
    """The shared settings, plus only the e-mail password from credentials.conf (no SAN, hardware or database passwords)."""
    cdir = app.paths.config_dir
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name in SHARED:
            p = os.path.join(cdir, name)
            if os.path.isfile(p):
                t.add(p, arcname=name)
        smtp_pw = app.cfg.get("credentials", "smtp", "password", "")
        if smtp_pw:
            data = ("[smtp]\npassword = %s\n" % smtp_pw).encode("utf-8")
            ti = tarfile.TarInfo("credentials.smtp")
            ti.size, ti.mode = len(data), 0o600
            t.addfile(ti, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")


INSTALL = r"""set -e
STAMP=$(date +%Y%m%d%H%M%S)
[ -d /opt/lshme ] && cp -a /opt/lshme /opt/lshme.pre-install.$STAMP
mkdir -p /opt/lshme
base64 -d | tar -C /opt/lshme -xzf -
chmod 0755 /opt/lshme/healthcheck
for n in vitals systemhealth healthcheck; do ln -sf /opt/lshme/healthcheck /usr/bin/$n; done
install -d -m 0700 /etc/lshme
install -d -m 0750 /var/lib/lshme /var/log/lshme
if [ -d /opt/lshme/config-templates ]; then for f in /opt/lshme/config-templates/*.conf; do [ -f "/etc/lshme/$(basename "$f")" ] || install -m 0600 "$f" /etc/lshme/; done; fi
restorecon -R /opt/lshme /etc/lshme /var/lib/lshme /var/log/lshme >/dev/null 2>&1 || true
/opt/lshme/healthcheck version | head -1
"""

SETTINGS = r"""set -e
T=$(mktemp -d)
base64 -d | tar -C "$T" -xzf -
cp -a /etc/lshme /etc/lshme.pre-install.$(date +%Y%m%d%H%M%S) 2>/dev/null || true
for f in "$T"/*.conf; do [ -f "$f" ] && install -m 0600 "$f" /etc/lshme/; done
if [ -f "$T/credentials.smtp" ]; then
  touch /etc/lshme/credentials.conf; chmod 600 /etc/lshme/credentials.conf
  python3 - "$T/credentials.smtp" <<'PY'
import configparser, sys
src = configparser.RawConfigParser(); src.read(sys.argv[1], encoding="utf-8")
dst = configparser.RawConfigParser(); dst.read("/etc/lshme/credentials.conf", encoding="utf-8")
if not dst.has_section("smtp"):
    dst.add_section("smtp")
dst.set("smtp", "password", src.get("smtp", "password"))
with open("/etc/lshme/credentials.conf", "w", encoding="utf-8") as f:
    dst.write(f)
PY
fi
rm -rf "$T"
"""


def start_job(app, name, host, port=22, user="root", password="", group="", environment="", install=True):
    job = {"id": uuid.uuid4().hex[:12], "name": name, "host": host, "started": time.time(), "steps": [], "done": False, "ok": None}
    with _LOCK:
        JOBS[job["id"]] = job
    t = threading.Thread(target=_run, args=(app, job, name, host, port, user, password, group, environment, install), name="lrs-onboard", daemon=True)
    t.start()
    return job["id"]


def status(job_id):
    with _LOCK:
        j = JOBS.get(job_id)
        return dict(j, steps=[dict(s) for s in j["steps"]]) if j else None


def _run(app, job, name, host, port, user, password, group, environment, install):
    def step(title, fn):
        s = {"step": title, "state": "running", "detail": ""}
        job["steps"].append(s)
        try:
            s["detail"] = fn() or "done"
            s["state"] = "ok"
            return True
        except Exception as e:
            s["state"], s["detail"] = "failed", str(e)[:400]
            return False

    target = _ssh(host, port, user, ["-o", "BatchMode=yes"])

    def run(cmd, text=None, timeout=300):
        r = app.runner.run(target + [cmd], timeout=timeout, input_text=text)
        if not r.ok:
            raise OnboardError(((r.err or r.out or "failed").strip().splitlines() or ["failed"])[-1][:300])
        return r.out.strip()

    ok = True
    if password:
        ok = step("Connect with the password once and add the central server's key", lambda: (push_key(app, host, port, user, password, ensure_key(app)), "key added; the password was not stored")[1])
    password = None                                                    # never kept after the first login
    ok = ok and step("Check the key login, root and Python 3", lambda: _check(run, user))
    if ok and install:
        ok = step("Install LRS Vitals (same version as this server)", lambda: run(INSTALL, program_tar(app), timeout=600))
        ok = ok and step("Copy settings: e-mail, schedules, reports, thresholds, exceptions", lambda: (run(SETTINGS, settings_tar(app)), "copied")[1])
        sets = ["vitals role member >/dev/null"]                      # installed from the central server: always a member
        for f, sec, k, v in branding.identity(app):
            if v:
                sets.append("vitals config set %s %s %s %s" % (f, sec, k, shlex.quote(v)))
        if environment:
            sets.append("vitals config set main general environment %s" % shlex.quote(environment))
        ok = ok and step("Customer name, e-mail sender and environment", lambda: (run(" && ".join(sets)), "set")[1])
        ok = ok and step("Scan the server, install schedules and the real-time watcher",
                         lambda: run("vitals scan --apply --quiet && vitals schedule install >/dev/null && vitals watch install >/dev/null && "
                                     "systemctl is-active lshme-watch.service", timeout=600))
        ok = ok and step("Start the first health check (runs in the background)",
                         lambda: (run("nohup vitals run --report-type daily --formats html --quiet >/var/log/lshme/first-run.log 2>&1 &"), "started")[1])
    if ok:
        ok = step("Add to the fleet (servers.conf), send the logo", lambda: _add(app, name, host, port, user, group, environment))
    job["ok"], job["done"] = ok, True
    try:
        app.audit("onboard", server=name, host=host, ok=ok, steps=";".join("%s:%s" % (s["step"][:20], s["state"]) for s in job["steps"]))
    except Exception:
        pass


def _check(run, user):
    out = run("id -u; . /etc/os-release 2>/dev/null; echo \"$PRETTY_NAME\"; command -v python3 >/dev/null && python3 -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])' || echo no-python3; hostname")
    lines = (out.splitlines() + ["", "", "", ""])[:4]
    if lines[0].strip() != "0":
        raise OnboardError("logged in as %s, but installing needs root (use root, or a user with the same rights)" % user)
    if lines[2].strip() == "no-python3":
        raise OnboardError("Python 3 is not installed on %s (dnf install python3)" % lines[3])
    return "%s, %s, Python %s" % (lines[3].strip(), lines[1].strip() or "Linux", lines[2].strip())


def _add(app, name, host, port, user, group, environment):
    sec = "server:" + name
    app.cfg.set("servers", sec, "host", host)
    if user and user != "root":
        app.cfg.set("servers", sec, "user", user)
    if str(port) != "22":
        app.cfg.set("servers", sec, "port", str(port))
    if group:
        app.cfg.set("servers", sec, "group", group)
    if environment:
        app.cfg.set("servers", sec, "environment", environment)
    app.save_config("servers")
    app.reload()
    res = branding.push(app, [name])
    sent = res and res[0].get("ok")
    return "added to servers.conf%s" % ("; logo and customer name sent" if sent else "")
