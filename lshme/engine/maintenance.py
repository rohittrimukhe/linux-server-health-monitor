"""Maintenance windows: checks still run and are recorded, but alert emails are
suppressed and reports are marked, so planned work does not page anyone."""
import json
import os
import time


def _path(paths):
    return os.path.join(paths.data_dir, "maintenance.json")


def start(paths, minutes, reason, user=""):
    if not user:
        user = os.environ.get("SUDO_USER") or os.environ.get("USER")
    if not user:
        try:
            import pwd
            user = pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError):
            user = str(os.getuid())
    rec = {"start": time.time(), "until": time.time() + minutes * 60, "reason": reason, "by": user}
    os.makedirs(paths.data_dir, exist_ok=True)
    with open(_path(paths), "w", encoding="utf-8") as f:
        json.dump(rec, f)
    return rec


def stop(paths):
    try:
        os.remove(_path(paths))
        return True
    except OSError:
        return False


def active(paths):
    try:
        with open(_path(paths), encoding="utf-8") as f:
            rec = json.load(f)
    except (IOError, OSError, ValueError):
        return None
    if rec.get("until", 0) < time.time():
        stop(paths)
        return None
    return rec
