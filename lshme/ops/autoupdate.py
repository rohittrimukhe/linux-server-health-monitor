# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Safe automatic update of LRS Vitals itself (default every day at 21:00, [update] in main.conf).

1. Ask GitHub for the newest commit of the release branch; nothing to do when it is the installed one.
2. Wait while a health check, report or fleet check is running (up to [update] wait_minutes); if still busy, postpone to the next night.
3. Hold the run lock for the whole update, so no run starts half-way.
4. Download and unpack the new code (paths checked), compile it with this server's Python: a failure here changes nothing.
5. Back up the installed program (/opt/lshme.pre-upgrade.<time>; the newest [update] keep_backups are kept).
6. Replace only the program files (lshme, docs, tools). Settings in /etc/lshme, history, reports and raw data are never touched.
7. Self-test the new code, restart the watcher and the web service that were running and confirm they come back.
8. If any step fails: restore the backup, restart the services, and report what happened.
9. Record the result (shown in the dashboard) and e-mail it when e-mail is set up."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen

from lshme.core.config import as_bool

DEFAULT_REPO = "rohittrimukhe/linux-server-health-monitor"
SERVICES = ("lshme-watch", "lshme-web")
PROGRAM_DIRS = ("lshme", "docs", "tools")
UA = "lrs-vitals-updater"


class UpdateError(ValueError):              # a plain message, never a traceback
    pass


def settings(cfg):
    g = lambda k, d: cfg.get("main", "update", k, d)
    try:
        wait = max(0, int(float(g("wait_minutes", "120") or 120)))
    except ValueError:
        wait = 120
    try:
        keep = max(1, int(float(g("keep_backups", "3") or 3)))
    except ValueError:
        keep = 3
    return {"auto": as_bool(g("auto", "yes"), True), "time": g("time", "21:00") or "21:00", "ref": g("ref", "main") or "main",
            "repo": g("repo", DEFAULT_REPO) or DEFAULT_REPO, "wait_minutes": wait, "keep_backups": keep, "notify": as_bool(g("notify", "yes"), True)}


def _get(url, timeout=30):
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


class Updater(object):
    """All outside effects (network, commands, the clock) can be replaced, so every step is tested."""

    def __init__(self, app, app_dir=None, fetch=None, run=None, sleep=None, now=None, python=None):
        self.app = app
        self.cfg = app.cfg
        self.s = settings(app.cfg)
        self.app_dir = app_dir or app.paths.app_dir
        self.fetch = fetch or _get
        self.run = run or (lambda args, timeout=120: subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout))
        self.sleep = sleep or time.sleep
        self.now = now or time.time
        self.python = python or sys.executable
        self.log = []

    # -- state ---------------------------------------------------------------------------------------
    @property
    def state_path(self):
        return os.path.join(self.app.paths.data_dir, "update_state.json")

    def state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_state(self, **kw):
        st = self.state()
        hist = st.get("history", [])
        st.update(kw)
        if "result" in kw:
            hist.insert(0, {"time": self.now(), "result": kw["result"], "message": kw.get("message", ""), "commit": kw.get("latest", "")})
            st["history"] = hist[:20]
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, self.state_path)
        return st

    def installed(self):
        try:
            with open(os.path.join(self.app_dir, ".lrs_commit"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def note(self, text):
        self.log.append("%s %s" % (time.strftime("%H:%M:%S", time.localtime(self.now())), text))
        try:
            self.app.log.info("update: %s", text)
        except Exception:
            pass

    # -- 1. what is new -----------------------------------------------------------------------------
    def latest(self):
        try:
            d = json.loads(self.fetch("https://api.github.com/repos/%s/commits/%s" % (self.s["repo"], self.s["ref"])).decode("utf-8"))
        except Exception as e:
            raise UpdateError("cannot ask GitHub for the newest version: %s" % e)
        sha = d.get("sha", "")
        if len(sha) < 7:
            raise UpdateError("GitHub gave no commit for %s" % self.s["ref"])
        c = d.get("commit") or {}
        return {"sha": sha, "date": (c.get("committer") or {}).get("date", ""), "message": (c.get("message") or "").splitlines()[0][:200] if c.get("message") else ""}

    def check(self):
        lat = self.latest()
        cur = self.installed()
        avail = lat["sha"] != cur
        self.save_state(checked_at=self.now(), installed=cur, latest=lat["sha"], latest_date=lat["date"], latest_message=lat["message"], available=avail)
        return dict(lat, installed=cur, available=avail)

    # -- 2. is anything running ---------------------------------------------------------------------
    def busy(self):
        """'' when nothing is running, else what is running."""
        from lshme.core.lock import LockBusy, RunLock
        try:
            with RunLock(self.app.paths.lock_file, wait_seconds=0):
                pass
        except LockBusy as e:
            return str(e)
        except Exception:
            pass
        try:
            r = self.run(["systemctl", "list-units", "lshme-*.service", "--state=activating,active", "--no-legend", "--plain"], timeout=20)
            for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
                unit = line.split()[0] if line.split() else ""
                name = unit.replace(".service", "")
                if unit and name not in SERVICES and not name.startswith("lshme-update"):      # the update itself does not count
                    return "%s is running" % unit
        except Exception:
            pass
        return ""

    def wait_until_free(self):
        deadline = self.now() + self.s["wait_minutes"] * 60
        while True:
            b = self.busy()
            if not b:
                return True
            if self.now() >= deadline:
                self.note("still busy after %d min (%s): postponed to the next run" % (self.s["wait_minutes"], b))
                return False
            self.note("waiting: %s" % b)
            self.sleep(60)

    # -- 4. download and check ----------------------------------------------------------------------
    def download(self, sha, work):
        data = self.fetch("https://codeload.github.com/%s/tar.gz/%s" % (self.s["repo"], sha), timeout=180)
        path = os.path.join(work, "src.tar.gz")
        with open(path, "wb") as f:
            f.write(data)
        out = os.path.join(work, "src")
        os.makedirs(out)
        with tarfile.open(path, "r:gz") as tf:
            root = os.path.realpath(out)
            for m in tf.getmembers():                              # only plain folders and files, every path checked; nothing else is created
                target = os.path.realpath(os.path.join(out, m.name))
                if target != root and not target.startswith(root + os.sep):
                    raise UpdateError("unsafe path in the download: %s" % m.name)
                if m.isdir():
                    os.makedirs(target, exist_ok=True)
                elif m.isfile():
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    src_f = tf.extractfile(m)
                    with open(target, "wb") as f:
                        shutil.copyfileobj(src_f, f)
                    os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)
        tops = [os.path.join(out, x) for x in os.listdir(out)]
        src = tops[0] if len(tops) == 1 and os.path.isdir(tops[0]) else out
        if not os.path.isfile(os.path.join(src, "lshme", "__init__.py")):
            raise UpdateError("the download does not look like LRS Vitals")
        r = self.run([self.python, "-m", "compileall", "-q", os.path.join(src, "lshme")], timeout=300)
        if r.returncode != 0:
            raise UpdateError("the new code does not compile with this Python: %s" % (r.stdout or b"").decode("utf-8", "replace")[-300:])
        for root, dirs, files in os.walk(os.path.join(src, "lshme")):
            for d in list(dirs):
                if d == "__pycache__":
                    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        return src

    # -- 5-8. back up, replace, test, restart or roll back -----------------------------------------------
    def backup(self):
        dst = "%s.pre-upgrade.%s" % (self.app_dir.rstrip("/\\"), time.strftime("%Y%m%d%H%M%S", time.localtime(self.now())))
        shutil.copytree(self.app_dir, dst, symlinks=True)
        return dst

    def replace(self, src):
        for d in PROGRAM_DIRS:
            new = os.path.join(src, d)
            if not os.path.isdir(new):
                continue
            cur = os.path.join(self.app_dir, d)
            if os.path.isdir(cur):
                shutil.rmtree(cur)
            shutil.copytree(new, cur, symlinks=True)
        for f in ("healthcheck", "CHANGELOG.md"):                     # the launcher and the change list sit next to the folders
            if os.path.isfile(os.path.join(src, f)):
                shutil.copy2(os.path.join(src, f), os.path.join(self.app_dir, f))
        try:
            os.chmod(os.path.join(self.app_dir, "healthcheck"), 0o755)
        except OSError:
            pass
        try:                                                         # member / standalone keep only what they use
            from lshme.ops import role as roles
            r = roles.get(self.app.cfg)
            if r:
                roles.prune(self.app_dir, r)
        except Exception:
            pass
        if shutil.which("restorecon"):                              # SELinux labels like the RPM sets them
            self.run(["restorecon", "-R", self.app_dir], timeout=120)

    def selftest(self):
        code = "import lshme, lshme.app, lshme.web.server, lshme.reports.periodic, lshme.reports.mailbody, lshme.ops.san, lshme.ops.livefleet; print(lshme.VERSION)"
        env_cmd = [self.python, "-c", "import sys; sys.path.insert(0, %r); %s" % (self.app_dir, code)]
        r = self.run(env_cmd, timeout=120)
        if r.returncode != 0:
            raise UpdateError("the new code does not start: %s" % (r.stdout or b"").decode("utf-8", "replace")[-400:])
        return (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1] if r.stdout else ""

    def active_services(self):
        out = []
        for s in SERVICES:
            try:
                if self.run(["systemctl", "is-active", "--quiet", s], timeout=20).returncode == 0:
                    out.append(s)
            except Exception:
                pass
        return out

    def restart(self, services):
        bad = []
        for s in services:
            self.run(["systemctl", "restart", s], timeout=120)
        if services:
            self.sleep(8)
        for s in services:
            if self.run(["systemctl", "is-active", "--quiet", s], timeout=20).returncode != 0:
                bad.append(s)
        return bad

    def restore(self, backup):
        tmp = self.app_dir.rstrip("/\\") + ".failed-update"
        if os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        if os.path.exists(self.app_dir):
            os.rename(self.app_dir, tmp)
        shutil.copytree(backup, self.app_dir, symlinks=True)
        shutil.rmtree(tmp, ignore_errors=True)

    def prune(self):
        parent, base = os.path.split(self.app_dir.rstrip("/\\"))
        olds = sorted(x for x in os.listdir(parent or ".") if x.startswith(base + ".pre-upgrade."))
        for x in olds[:-self.s["keep_backups"]]:
            shutil.rmtree(os.path.join(parent, x), ignore_errors=True)

    def update(self, auto=False, force=False):
        """The whole update. Returns the result dict (also saved for the dashboard)."""
        from lshme.core.lock import LockBusy, RunLock
        self.log = []
        if auto and not self.s["auto"]:
            return self.finish("off", "automatic update is switched off ([update] auto = no)")
        try:
            info = self.check()
        except UpdateError as e:
            return self.finish("failed", str(e))
        if not info["available"] and not force:
            return self.finish("current", "already the newest version (%s)" % info["sha"][:7], latest=info["sha"])
        self.note("new version %s: %s" % (info["sha"][:7], info["message"]))
        if not self.wait_until_free():
            return self.finish("postponed", "a health check or report kept running; trying again at the next run", latest=info["sha"])
        try:
            lock = RunLock(self.app.paths.lock_file, wait_seconds=300)
            lock.__enter__()
        except LockBusy as e:
            return self.finish("postponed", "could not start: %s" % e, latest=info["sha"])
        backup = None
        services = self.active_services()
        try:
            work = tempfile.mkdtemp(prefix="lrs-update-")
            try:
                src = self.download(info["sha"], work)
                self.note("downloaded and compiled")
                backup = self.backup()
                self.note("backed up to %s" % backup)
                self.replace(src)
                version = self.selftest()
                self.note("self-test passed (version %s)" % version)
                bad = self.restart(services)
                if bad:
                    raise UpdateError("%s did not start again after the update" % ", ".join(bad))
                with open(os.path.join(self.app_dir, ".lrs_commit"), "w", encoding="utf-8") as f:
                    f.write(info["sha"] + "\n")
                self.prune()
                return self.finish("updated", "updated to %s (%s); old version kept as %s" % (info["sha"][:7], info["message"], backup),
                                   latest=info["sha"], installed=info["sha"], backup=backup)
            finally:
                shutil.rmtree(work, ignore_errors=True)
        except Exception as e:
            if backup:
                try:
                    self.restore(backup)
                    bad = self.restart(services)
                    self.note("old version restored from %s" % backup)
                    return self.finish("rolled back", "update failed (%s); the previous version was restored%s" % (
                        e, (" but %s did not start: check it" % ", ".join(bad)) if bad else " and is running"), latest=info["sha"], backup=backup)
                except Exception as e2:
                    return self.finish("failed", "update failed (%s) and restoring %s also failed (%s): restore it by hand" % (e, backup, e2), latest=info["sha"], backup=backup)
            return self.finish("failed", "update not applied: %s (nothing was changed)" % e, latest=info["sha"])
        finally:
            lock.__exit__(None, None, None)

    def finish(self, result, message, **kw):
        st = self.save_state(result=result, message=message, finished_at=self.now(), log=self.log[-40:], **kw)
        if result in ("updated", "rolled back", "failed") and self.s["notify"]:
            try:
                self.mail(result, message)
            except Exception:
                pass
        return {"result": result, "message": message, "log": list(self.log), "state": st}

    def mail(self, result, message):
        from lshme.delivery import mailer
        ms = mailer.MailSettings(self.cfg)
        if ms.validate():
            return
        host = socket.gethostname()
        subject = "%s LRS Vitals on %s: %s" % (ms.prefix, host, {"updated": "updated", "rolled back": "update failed, previous version restored",
                                                                 "failed": "update failed"}[result])
        text = "%s\n\n%s\n\nSteps:\n%s\n" % (subject, message, "\n".join(self.log))
        msg, rcpts, _, _ = mailer.build_message(ms, subject, text, None, [], "WARNING" if result != "updated" else "HEALTHY")
        mailer.send(ms, msg, rcpts, self.app.paths.log_dir)


def start_background(app, launcher):
    """Start 'vitals update now' outside the web service (the update restarts the web service, so it must not run inside it)."""
    cmd = [launcher, "update", "now", "--quiet"]
    if shutil.which("systemd-run"):
        r = subprocess.run(["systemd-run", "--no-block", "--collect", "--unit", "lshme-update-manual-%d" % int(time.time())] + cmd,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        if r.returncode == 0:
            return True
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    return True
