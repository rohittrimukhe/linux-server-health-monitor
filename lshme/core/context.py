"""Per-run context handed to every module."""
import json
import os
import socket
import time

from lshme.core.runner import Runner


class Context(object):
    def __init__(self, cfg, paths=None, runner=None, days=1, schedule="adhoc", options=None):
        self.cfg = cfg
        self.paths = paths
        self.runner = runner or Runner(cfg.getint("main", "general", "command_timeout", 30))
        self.days = max(1, int(days))
        self.schedule = schedule
        self.options = options or {}
        self.started = time.time()
        self.hostname = socket.gethostname()
        self.shared = {}  # results modules share (e.g. log lines, listening ports)

    # convenience accessors ---------------------------------------------------
    def t(self, section, key, default=0.0):
        return self.cfg.threshold(section, key, default)

    def run(self, args, **kw):
        return self.runner.run(args, **kw)

    def has(self, name):
        return self.runner.has(name)

    def read(self, path, default=""):
        return self.runner.read(path, default)

    @property
    def since_seconds(self):
        return self.days * 86400

    # small JSON cache for slow checks (e.g. package update metadata) -------
    def cache_get(self, key, max_age_seconds):
        if not self.paths:
            return None
        path = os.path.join(self.paths.data_dir, "cache", key + ".json")
        try:
            if time.time() - os.path.getmtime(path) <= max_age_seconds:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    def cache_put(self, key, value):
        if not self.paths:
            return
        d = os.path.join(self.paths.data_dir, "cache")
        try:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, key + ".json"), "w", encoding="utf-8") as f:
                json.dump(value, f)
        except Exception:
            pass
