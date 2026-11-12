# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Test doubles: a scripted Runner and helpers to build a Context."""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lshme.core.config import Config, Paths  # noqa: E402
from lshme.core.context import Context  # noqa: E402
from lshme.core.model import ModuleResult  # noqa: E402
from lshme.core.runner import Result  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class FakeRunner(object):
    """commands: {"cmd arg1 arg2": (rc, out[, err]) or callable(args, input_text) -> tuple}

    Matching is by longest command-string prefix. Unknown commands behave as
    'not installed' unless the program is listed in `tools`.
    """

    def __init__(self, commands=None, files=None, tools=None, root=True, dirs=None, mtimes=None):
        self.commands = commands or {}
        self.files = files or {}
        self.tools = set(tools or [])
        for k in self.commands:
            self.tools.add(k.split()[0])
        self.root = root
        self.dirs = set(dirs or [])
        self.mtimes = mtimes or {}
        self.calls = []
        self.inputs = []

    def which(self, name):
        return "/usr/bin/" + os.path.basename(name) if (name in self.tools or os.path.basename(name) in self.tools) else None

    def has(self, name):
        return self.which(name) is not None

    def run(self, args, timeout=None, input_text=None, env=None):
        args = [str(a) for a in args]
        line = " ".join(args)
        self.calls.append(line)
        self.inputs.append(input_text)
        best = None
        for k in self.commands:
            if line == k or line.startswith(k + " "):
                if best is None or len(k) > len(best):
                    best = k
        if best is None:
            if not self.has(args[0]):
                return Result(args, 127, "", "command not found", missing=True)
            return Result(args, 1, "", "no fake output for: " + line)
        v = self.commands[best]
        if callable(v):
            v = v(args, input_text)
        rc, out = v[0], v[1]
        err = v[2] if len(v) > 2 else ""
        return Result(args, rc, out, err, timed_out=(rc == 124))

    def read(self, path, default=""):
        return self.files.get(path, default)

    def tail(self, path, max_bytes=0):
        return self.files.get(path, "")

    def exists(self, path):
        return path in self.files or path in self.dirs

    def isdir(self, path):
        return path in self.dirs

    def listdir(self, path):
        pre = path.rstrip("/") + "/"
        names = set()
        for p in list(self.files) + list(self.dirs):
            if p.startswith(pre):
                names.add(p[len(pre):].split("/")[0])
        return sorted(names)

    def glob(self, pattern):
        import fnmatch
        return sorted(p for p in list(self.files) + list(self.dirs) if fnmatch.fnmatch(p, pattern))

    def mtime(self, path):
        return self.mtimes.get(path)

    def size(self, path):
        return len(self.files.get(path, ""))

    def is_root(self):
        return self.root

    def now(self):
        import time
        return time.time()

    def sleep(self, seconds):
        pass


class TempHome(object):
    """A throw-away portable LSHME home with optional config file contents."""

    def __init__(self, configs=None):
        self.dir = tempfile.mkdtemp(prefix="lshme-test-")
        os.makedirs(os.path.join(self.dir, "config"))
        for name, text in (configs or {}).items():
            path = os.path.join(self.dir, "config", name + ".conf")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(path, 0o600)
        self.paths = Paths(ROOT, home=self.dir)
        self.paths.ensure()
        self.cfg = Config(self.paths.config_dir)

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def make_ctx(runner, configs=None, days=1, home=None):
    home = home or TempHome(configs)
    ctx = Context(home.cfg, home.paths, runner, days)
    ctx._home = home
    return ctx


def run_module(mod, runner, configs=None, days=1):
    ctx = make_ctx(runner, configs, days)
    res = ModuleResult(mod.NAME, mod.TITLE, mod.WEIGHT)
    mod.run(ctx, res)
    ctx._home.cleanup()
    return res


def codes(res):
    """code -> worst severity seen for that code."""
    from lshme.core.model import SEV_RANK
    out = {}
    for f in res.findings:
        if f.code not in out or SEV_RANK[f.severity] > SEV_RANK[out[f.code]]:
            out[f.code] = f.severity
    return out
