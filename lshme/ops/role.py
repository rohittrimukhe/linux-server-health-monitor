# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""How this installation is used (main.conf [general] role).

  central     everything: the web dashboard, the fleet (members), SAN storage, server hardware, the dependency map and
              estate reports. Installed once, on the server that watches the others.
  standalone  one server on its own: its own web dashboard, health checks, reports, alerts and history, and the SAN
              storage and hardware it uses. No members and no fleet, so the tools that install other servers are not kept.
  member      only what a member needs: its own health checks, reports, the real-time watcher and alerts; the central
              server collects its results. The dashboard files, deploy tools and documentation are not kept.
              A server added from the central server (web: Add > Linux server) is always a member.

The install (install.sh --role), the setup wizard and `vitals role` store the role and keep only the files it uses;
every update (manual or automatic) keeps it that way."""
import os
import shutil

ROLES = ("central", "standalone", "member")
FLEET_TOOLS = ("tools/deploy_fleet.sh",)
MEMBER_REMOVE = ("lshme/web/static", "docs", "tools/demo_reports.py", "tools/release_check.sh", "packaging") + FLEET_TOOLS
REMOVE = {"central": (), "standalone": FLEET_TOOLS, "member": MEMBER_REMOVE}
LABEL = {"central": "Central server", "standalone": "Standalone server", "member": "Member server"}


def get(cfg):
    r = (cfg.get("main", "general", "role", "") or "").strip().lower()
    return r if r in ROLES else ""


def is_member(cfg):
    return get(cfg) == "member"


def is_standalone(cfg):
    return get(cfg) == "standalone"


def has_fleet(cfg):
    """Only a central server (or one with no role set, as before roles existed) watches other servers."""
    return get(cfg) in ("central", "")


def prune(app_dir, role="member"):
    """Remove what this role does not use from the installed program. Returns the paths removed."""
    gone = []
    for rel in REMOVE.get(role, ()):
        p = os.path.join(app_dir, rel)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
                gone.append(rel)
            elif os.path.isfile(p):
                os.remove(p)
                gone.append(rel)
        except OSError:
            pass
    return gone


def installed_tree(app_dir):
    """Prune only a real installation (under /opt), never a source checkout."""
    return os.path.isdir(os.path.join(app_dir, "lshme")) and app_dir.startswith("/opt/")


def keep_only_needed(app):
    """After an update: remove again what this role does not use."""
    r = get(app.cfg)
    return prune(app.paths.app_dir, r) if r and installed_tree(app.paths.app_dir) else []


def apply(app, role):
    """Store the role. Member and standalone get no fleet of their own; a member also no dashboard and no SAN /
    hardware polling of its own. Returns the paths removed from the installed program."""
    role = (role or "").strip().lower()
    if role not in ROLES:
        raise ValueError("role must be one of: " + ", ".join(ROLES))
    app.cfg.set("main", "general", "role", role)
    if role in ("member", "standalone"):
        app.cfg.set("main", "fleet", "enabled", "no")
        app.cfg.set("main", "fleet", "live", "no")
    else:
        app.cfg.set("main", "fleet", "live", "yes")                 # back to central: the live view of the members again
    removed = prune(app.paths.app_dir, role) if installed_tree(app.paths.app_dir) else []
    app.save_config("main")
    app.reload()
    try:
        app.audit("role", role=role, removed=",".join(removed))
    except Exception:
        pass
    return removed
