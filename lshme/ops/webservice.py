# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Run the web interface as a systemd service (lshme-web.service).

Like the scheduler, this module manages only its own unit and only when asked: it never touches any other
service. The unit is limited in CPU, memory and tasks, and starts after the network is up."""
import os
import shlex
import socket

UNIT_NAME = "lshme-web.service"
UNIT = """[Unit]
Description=LSHME web interface
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={cmd} web start
Restart=on-failure
RestartSec=10
Nice=10
IOSchedulingClass=idle
MemoryMax=256M
TasksMax=64
PrivateTmp=true
# LSHME_MANAGED

[Install]
WantedBy=multi-user.target
"""


def unit_path(app):
    return os.path.join(app.cfg.get("schedule", "scheduler", "systemd_dir", "/etc/systemd/system"), UNIT_NAME)


def url(app):
    cfg = app.cfg
    tls = cfg.getbool("web", "web", "tls", True)
    bind = cfg.get("web", "web", "bind", "127.0.0.1")
    host = socket.getfqdn() if bind in ("0.0.0.0", "::") else bind
    return "%s://%s:%s/" % ("https" if tls else "http", host, cfg.get("web", "web", "port", "8443"))


def installed(app):
    return os.path.exists(unit_path(app))


def state(app):
    if not (app.runner.has("systemctl") and installed(app)):
        return "not installed"
    return app.runner.run(["systemctl", "is-active", UNIT_NAME]).out.strip() or "unknown"


def install(app):
    """Write the unit and (re)start it so a changed address or port takes effect. Returns (ok, message)."""
    if not (app.runner.has("systemctl") and os.path.isdir("/run/systemd/system")):
        return False, "systemd is needed to run the web interface as a service; use 'vitals web start' instead"
    cmd = " ".join(shlex.quote(a) for a in [app.launcher()] + app.global_args())
    with open(unit_path(app), "w", encoding="utf-8") as f:
        f.write(UNIT.format(cmd=cmd))
    os.chmod(unit_path(app), 0o644)
    app.runner.run(["systemctl", "daemon-reload"])
    r = app.runner.run(["systemctl", "enable", UNIT_NAME])
    if r.ok:
        r = app.runner.run(["systemctl", "restart", UNIT_NAME])
    return r.ok, r.brief_error() if not r.ok else "The web interface is running at %s" % url(app)


def uninstall(app):
    """Stop and remove the service, but only if it runs this installation. Returns True when removed."""
    from lshme.core.util import owned_by
    path = unit_path(app)
    if not os.path.exists(path) or not owned_by(path, app.global_args()):
        return False
    app.runner.run(["systemctl", "disable", "--now", UNIT_NAME])
    os.remove(path)
    app.runner.run(["systemctl", "daemon-reload"])
    return True
