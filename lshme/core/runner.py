# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Command execution and file access.

Every module talks to the operating system only through a Runner, so tests can
substitute canned output and so every command is time-limited and read-only by
construction (modules never pass a shell string).
"""
import glob as _glob
import os
import shutil
import signal
import subprocess
import time

EXTRA_PATHS = (
    "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin",
    "/usr/openv/netbackup/bin", "/usr/openv/netbackup/bin/admincmd",
    "/opt/MegaRAID/storcli", "/opt/MegaRAID/perccli", "/opt/MegaRAID/MegaCli",
    "/opt/smartmontools/sbin", "/opt/mssql-tools/bin", "/opt/mssql-tools18/bin",
)


def _dec(b):
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    return b.decode("utf-8", "replace")


class Result(object):
    __slots__ = ("args", "rc", "out", "err", "missing", "timed_out", "duration")

    def __init__(self, args, rc=0, out="", err="", missing=False, timed_out=False, duration=0.0):
        self.args = list(args) if args else []
        self.rc = rc
        self.out = out or ""
        self.err = err or ""
        self.missing = missing
        self.timed_out = timed_out
        self.duration = duration

    @property
    def ok(self):
        return self.rc == 0 and not self.missing and not self.timed_out

    def lines(self):
        return [x for x in self.out.splitlines() if x.strip()]

    def brief_error(self):
        if self.missing:
            return "command not available: %s" % (self.args[0] if self.args else "?")
        if self.timed_out:
            return "timed out: %s" % " ".join(self.args[:3])
        return (self.err or self.out or "exit code %s" % self.rc).strip()[:500]

    def to_dict(self):
        return {"command": " ".join(self.args), "rc": self.rc, "stdout": self.out,
                "stderr": self.err, "missing": self.missing, "timed_out": self.timed_out}


class Runner(object):
    """Runs commands with a timeout, C locale and an extended PATH."""

    def __init__(self, timeout=30):
        self.timeout = timeout
        self._which = {}
        self.history = []

    # -- commands ---------------------------------------------------------
    def which(self, name):
        if name in self._which:
            return self._which[name]
        if os.path.isabs(name):
            found = name if os.access(name, os.X_OK) else None
        else:
            path = os.environ.get("PATH", "") + os.pathsep + os.pathsep.join(EXTRA_PATHS)
            found = shutil.which(name, path=path)
        self._which[name] = found
        return found

    def has(self, name):
        return self.which(name) is not None

    def run(self, args, timeout=None, input_text=None, env=None):
        args = [str(a) for a in args]
        exe = self.which(args[0])
        if not exe:
            r = Result(args, 127, "", "command not found: %s" % args[0], missing=True)
            self.history.append(r)
            return r
        full_env = dict(os.environ)
        full_env["LC_ALL"] = "C"
        full_env["LANG"] = "C"
        full_env["PATH"] = full_env.get("PATH", "") + os.pathsep + os.pathsep.join(EXTRA_PATHS)
        if env:
            full_env.update(env)
        limit = timeout or self.timeout
        start = time.time()
        # Bytes in/out, decoded as UTF-8 with replacement: Python 3.6 under a C locale
        # (common for cron on RHEL/OL 7) would otherwise crash on non-ASCII output.
        try:
            proc = subprocess.Popen(
                [exe] + args[1:], stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env, start_new_session=True)
        except Exception as e:  # permission denied, exec format error, ...
            r = Result(args, 126, "", str(e))
            self.history.append(r)
            return r
        data = input_text.encode("utf-8") if input_text is not None else None
        try:
            out, err = proc.communicate(input=data, timeout=limit)
            out, err = _dec(out), _dec(err)
            r = Result(args, proc.returncode, out.rstrip("\n"), err.strip(), duration=time.time() - start)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:
                out, err = b"", b""
            r = Result(args, 124, _dec(out).rstrip("\n"), "timed out after %ss" % limit,
                       timed_out=True, duration=time.time() - start)
        except Exception as e:
            r = Result(args, 1, "", str(e), duration=time.time() - start)
        self.history.append(r)
        return r

    # -- files ------------------------------------------------------------
    def read(self, path, default="", max_bytes=8 * 1024 * 1024):
        try:
            with open(path, "r", errors="replace", encoding="utf-8") as f:
                return f.read(max_bytes).rstrip("\n")
        except Exception:
            return default

    def tail(self, path, max_bytes=4 * 1024 * 1024):
        """Return the last max_bytes of a (possibly huge) text file."""
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                if size > max_bytes:
                    f.seek(size - max_bytes)
                    f.readline()
                return f.read().decode("utf-8", "replace")
        except Exception:
            return ""

    def exists(self, path):
        return os.path.exists(path)

    def isdir(self, path):
        return os.path.isdir(path)

    def listdir(self, path):
        try:
            return sorted(os.listdir(path))
        except Exception:
            return []

    def glob(self, pattern):
        return sorted(_glob.glob(pattern))

    def mtime(self, path):
        try:
            return os.path.getmtime(path)
        except Exception:
            return None

    def size(self, path):
        try:
            return os.path.getsize(path)
        except Exception:
            return None

    def is_root(self):
        return hasattr(os, "geteuid") and os.geteuid() == 0

    def now(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)
