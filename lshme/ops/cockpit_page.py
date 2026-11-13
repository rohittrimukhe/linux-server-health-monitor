# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Install/remove the 'LSHME Health' page in Cockpit (static files only, no service)."""
import json
import os
import shutil

from lshme import BRAND_LOGO

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cockpit")
FILES = ("manifest.json", "index.html", "lshme.js", "lshme.css", "charts.js")


def target(user=False):
    if user:
        return os.path.join(os.path.expanduser("~"), ".local", "share", "cockpit", "lshme")
    return "/usr/share/cockpit/lshme"


def install(app, user=False, dest=None):
    dest = dest or target(user)
    os.makedirs(dest, exist_ok=True)
    for f in FILES:
        shutil.copyfile(os.path.join(SRC, f), os.path.join(dest, f))
    if os.path.isfile(BRAND_LOGO):
        shutil.copyfile(BRAND_LOGO, os.path.join(dest, "lrs_logo.png"))
    with open(os.path.join(dest, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"data_dir": app.paths.data_dir, "command": [app.launcher()] + app.global_args()}, f, indent=1)
    for f in os.listdir(dest):
        os.chmod(os.path.join(dest, f), 0o644)
    os.chmod(dest, 0o755)
    return dest


def owner(dest):
    """Global arguments of the installation that installed the page at dest, or None."""
    try:
        with open(os.path.join(dest, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("command", [])[1:]
    except (IOError, OSError, ValueError, AttributeError):
        return None


def remove(user=False, dest=None, owner_args=None):
    """Remove the page. With owner_args, only a page installed by that installation is removed."""
    dest = dest or target(user)
    if owner_args is not None and owner(dest) not in (None, list(owner_args)):
        return None
    if os.path.isdir(dest) and os.path.exists(os.path.join(dest, "lshme.js")):
        shutil.rmtree(dest)
        return dest
    return None
