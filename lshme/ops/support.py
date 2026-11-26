# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Support export: one file with everything needed to look at an installation somewhere else, without any secret.

    vitals support export [--days 30] [--reports 5] [--output FILE]
    vitals support import FILE --home DIR          (on a test server only: a separate folder, never /etc/lshme)

What goes in: the settings (every password, token and key blanked; credentials.conf and the web passwords left out),
the latest health check of every server, the real-time watcher's state and events, the history database (the last
--days days), the newest reports, the tail of the program logs, a live snapshot taken at export time (all servers,
SAN storage, server hardware) and the version, role and self-check output. Nothing is changed on the server."""
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import tarfile
import tempfile
import time

SECRET_KEY = re.compile(r"(pass|secret|token|apikey|api_key|community|private|_key$|^key$)", re.I)
SKIP_CONF = ("credentials.conf",)
LOG_TAIL = 4 * 1024 * 1024


def redact_conf(text):
    """Blank every value whose name looks like a secret, and every web password hash ([users])."""
    out, section = [], ""
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^\[(.+)\]\s*$", s)
        if m:
            section = m.group(1).strip().lower()
            out.append(line)
            continue
        kv = re.match(r"^(\s*)([^#;=\s][^=]*?)\s*=\s*(.*)$", line)
        if kv and kv.group(3).strip() and (section == "users" or SECRET_KEY.search(kv.group(2).strip())):
            out.append("%s%s = <removed>" % (kv.group(1), kv.group(2).strip()))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _add_bytes(tar, name, data, mtime=None):
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(mtime or time.time()), 0o600
    tar.addfile(info, io.BytesIO(data))


def _add_text(tar, name, text):
    _add_bytes(tar, name, text.encode("utf-8", "replace"))


def _tail(path, limit=LOG_TAIL):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - limit))
        return f.read()


def _history_copy(src, days, tmpdir):
    """A consistent copy of the history database with only the last `days` days (online backup, the live file is never touched)."""
    dst = os.path.join(tmpdir, "history.db")
    con = sqlite3.connect(src, timeout=30)
    try:
        out = sqlite3.connect(dst)
        try:
            con.backup(out)
            cut = time.time() - days * 86400
            for (table,) in out.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                cols = [r[1] for r in out.execute("PRAGMA table_info(%s)" % table).fetchall()]
                if "ts" in cols:
                    out.execute("DELETE FROM %s WHERE ts < ?" % table, (cut,))
            out.commit()
            out.execute("VACUUM")
        finally:
            out.close()
    finally:
        con.close()
    return dst


def live_snapshot(app, notes):
    """What the dashboard shows right now: every server (live), SAN storage and server hardware. Never a password."""
    snap = {"taken": time.time()}
    try:
        from lshme.ops import livefleet
        if livefleet.enabled(app.cfg):
            lf = livefleet.LiveFleet(app)
            snap["servers"] = lf.poll_once()
            try:
                lf.poll_san()
                snap["san"] = lf.san_snapshot()
            except Exception as e:
                notes.append("SAN snapshot: %s" % e)
    except Exception as e:
        notes.append("live servers: %s" % e)
    try:
        from lshme.ops import bmc
        hw = []
        for a in bmc.bmcs(app.cfg):
            try:
                d = bmc.collect(a, detail=True)
                d.pop("password", None)
                hw.append(d)
            except Exception as e:
                hw.append({"name": a["name"], "error": str(e)[:300]})
        snap["hardware"] = hw
    except Exception as e:
        notes.append("hardware: %s" % e)
    return snap


def export(app, output=None, days=30, reports=5, run=None):
    """Write the support file. Returns (path, notes)."""
    from lshme import VERSION
    paths, notes = app.paths, []
    host = socket.gethostname()
    stamp = time.strftime("%Y%m%d_%H%M")
    output = output or "/tmp/lrs-vitals-export-%s-%s.tar.gz" % (re.sub(r"[^\w.-]", "_", host), stamp)
    tmp = tempfile.mkdtemp(prefix="lrs-export-")
    old_umask = os.umask(0o077)
    try:
        with tarfile.open(output, "w:gz") as tar:
            for name in sorted(os.listdir(paths.config_dir)) if os.path.isdir(paths.config_dir) else []:
                p = os.path.join(paths.config_dir, name)
                if name in SKIP_CONF or not os.path.isfile(p):
                    continue
                if name.endswith(".conf"):
                    with open(p, encoding="utf-8", errors="replace") as f:
                        _add_text(tar, "config/" + name, redact_conf(f.read()))
                elif re.search(r"\.(png|jpe?g|svg|gif)$", name, re.I):          # customer logo
                    with open(p, "rb") as f:
                        _add_bytes(tar, "config/" + name, f.read())
            dd = paths.data_dir
            for root, dirs, files in os.walk(dd):
                rel = os.path.relpath(root, dd)
                if rel.split(os.sep)[0] in ("raw", "reports", "archive", "backups"):
                    dirs[:] = []
                    continue
                for fn in files:
                    if fn.endswith((".json", ".jsonl")) or fn in ("profile.json",):
                        p = os.path.join(root, fn)
                        if os.path.getsize(p) > 200 * 1024 * 1024:
                            notes.append("skipped (too big): %s" % fn)
                            continue
                        tar.add(p, arcname="data/" + os.path.normpath(os.path.join(rel, fn)).replace(os.sep, "/"))
            if os.path.exists(paths.history_db):
                try:
                    tar.add(_history_copy(paths.history_db, days, tmp), arcname="data/history.db")
                except Exception as e:
                    notes.append("history database: %s" % e)
            rd = paths.report_dir
            if os.path.isdir(rd) and reports:
                dirs = []
                for root, sub, files in os.walk(rd):
                    if files and root.count(os.sep) - rd.count(os.sep) == 3:
                        dirs.append((os.path.getmtime(root), root))
                for _, d in sorted(dirs, reverse=True)[:reports * 6]:
                    for fn in os.listdir(d):
                        p = os.path.join(d, fn)
                        if os.path.isfile(p) and os.path.getsize(p) < 30 * 1024 * 1024:
                            tar.add(p, arcname="reports/" + os.path.relpath(p, rd).replace(os.sep, "/"))
            for fn in sorted(os.listdir(paths.log_dir)) if os.path.isdir(paths.log_dir) else []:
                p = os.path.join(paths.log_dir, fn)
                if os.path.isfile(p) and fn.endswith((".log", ".jsonl")):
                    _add_bytes(tar, "logs/" + fn, _tail(p))
            info = {"host": host, "version": VERSION, "exported": time.time(), "days": days, "notes": notes}
            try:
                from lshme.ops import role
                info["role"] = role.get(app.cfg) or "central"
            except Exception:
                pass
            for label, cmd in (("os", ["cat", "/etc/os-release"]), ("python", ["python3", "-V"]), ("uptime", ["uptime"]),
                               ("units", ["systemctl", "list-units", "--all", "--no-pager", "lshme*"]),
                               ("timers", ["systemctl", "list-timers", "--all", "--no-pager", "lshme*"])):
                try:
                    r = app.runner.run(cmd, timeout=20)
                    info[label] = (r.out or r.err or "")[-20000:]
                except Exception as e:
                    info[label] = "error: %s" % e
            try:
                from lshme.ops import doctor
                info["doctor"] = doctor.run(app.cfg, app.paths, app.runner)
            except Exception as e:
                info["doctor"] = "error: %s" % e
            _add_text(tar, "live_snapshot.json", json.dumps(live_snapshot(app, notes), default=str, indent=1))
            info["notes"] = notes
            _add_text(tar, "manifest.json", json.dumps(info, default=str, indent=1))
        os.chmod(output, 0o600)
    finally:
        os.umask(old_umask)
        shutil.rmtree(tmp, ignore_errors=True)
    return output, notes


def import_to(path, home):
    """Unpack a support file into a separate folder (DIR/config, data, reports, logs) for a look on a test server.
    Refuses the live configuration folders. Live polling of servers, SAN and hardware is switched off there."""
    home = os.path.abspath(home)
    if home.startswith(("/etc", "/var/lib/lshme", "/opt/lshme")) or home in ("/", "/root", "/home"):
        raise ValueError("import into an empty folder of its own, e.g. --into /root/lrs-import (never the live folders)")
    if os.path.isdir(home) and os.listdir(home):
        raise ValueError("%s is not empty" % home)
    os.makedirs(home, mode=0o700, exist_ok=True)
    with tarfile.open(path, "r:gz") as tar:
        for m in tar.getmembers():
            name = os.path.normpath(m.name)
            if name.startswith(("..", "/")) or not (m.isfile() or m.isdir()):
                raise ValueError("unexpected entry in the file: %s" % m.name)
        if hasattr(tarfile, "data_filter"):                     # Python 3.12+ (and patched older ones): the safe extraction filter
            tar.extractall(home, filter="data")
        else:
            tar.extractall(home)
    main = os.path.join(home, "config", "main.conf")
    if os.path.exists(main):                                     # no live polling of the customer's servers from a test server
        import configparser
        cp = configparser.RawConfigParser(strict=False)
        cp.read(main, encoding="utf-8")
        if not cp.has_section("fleet"):
            cp.add_section("fleet")
        cp.set("fleet", "live", "no")
        cp.set("fleet", "enabled", "no")
        with open(main, "w", encoding="utf-8") as f:
            cp.write(f)
    with open(os.path.join(home, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)
