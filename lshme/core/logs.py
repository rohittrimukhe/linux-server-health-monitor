# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Application logging: daily execution log, error log and JSONL audit log."""
import json
import logging
import os
import time

_LOGGER = None


def setup(log_dir, verbose=False):
    global _LOGGER
    logger = logging.getLogger("lshme")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(log_dir, exist_ok=True)
        day = time.strftime("%Y%m%d")
        fh = logging.FileHandler(os.path.join(log_dir, "lshme-%s.log" % day), encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        eh = logging.FileHandler(os.path.join(log_dir, "error-%s.log" % day), encoding="utf-8")
        eh.setLevel(logging.WARNING)
        eh.setFormatter(fmt)
        logger.addHandler(eh)
    except OSError:
        pass
    if verbose:
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    _LOGGER = logger
    return logger


def get():
    return _LOGGER or logging.getLogger("lshme")


def audit(log_dir, action, **details):
    """Record an administrative action (config change, deletion, schedule...)."""
    try:
        os.makedirs(log_dir, exist_ok=True)
        user = os.environ.get("SUDO_USER") or os.environ.get("USER")
        if not user:
            try:
                import pwd
                user = pwd.getpwuid(os.getuid()).pw_name
            except (ImportError, KeyError):
                user = str(os.getuid())
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "user": user, "action": action}
        rec.update(details)
        with open(os.path.join(log_dir, "audit.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
    except OSError:
        pass
