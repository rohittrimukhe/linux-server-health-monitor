# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Customer logos for the reports.

A logo is uploaded once (web dashboard: Reports > Report logos, or `vitals logo set FILE`) and stored on the server in
<data>/branding/. The default logo (`customer_logo.png` or `.jpg`) is written to [main] [general] logo, which every report
(HTML, PDF, Excel, PowerPoint) already uses, so it appears on all reports from then on. With several servers each can have a
logo of its own (<data>/branding/servers/<name>.png): the central server keeps them all and sends the right one to every server
(`vitals logo push`, or "Send to servers" in the dashboard), because each server writes its own reports.

Accepted: PNG (8 bits per channel, not interlaced, up to 700,000 pixels) and baseline/progressive RGB or grey JPEG, up to 700 KB.
The check uses the same decoder as the PDF writer, so a logo that is accepted here always shows in the PDF."""
import os
import re
import shlex
import struct

from lshme.ops import fleet
from lshme.reports import minipdf

MAX_BYTES = 700 * 1024
PNG_SIG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
EXTS = (".png", ".jpg")
REMOTE_DIR = "/var/lib/lshme/branding"


def safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name or "")


def inspect(raw):
    """{'kind', 'ext', 'width', 'height', 'bytes'} for a usable logo, else ValueError with a message for the person uploading it."""
    n = len(raw or b"")
    if not n:
        raise ValueError("The file is empty.")
    if n > MAX_BYTES:
        raise ValueError("The logo is %d KB; the limit is %d KB. Use a smaller image (about 600 x 200 pixels is plenty)." % (n // 1024, MAX_BYTES // 1024))
    if raw[:8] == PNG_SIG:
        kind, ext = "png", ".png"
        try:
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", raw[16:29])
        except struct.error:
            raise ValueError("The PNG file is damaged.")
        if depth != 8:
            raise ValueError("The PNG uses %d bits per channel. Save it as a normal 8-bit PNG (PNG-24 or PNG-32), or use a JPEG." % depth)
        if interlace:
            raise ValueError("The PNG is interlaced. Save it without interlacing, or use a JPEG.")
        if w * h > 700000:
            raise ValueError("The PNG is %d x %d pixels; the limit is 700,000 pixels (for example 1000 x 500). Resize it first." % (w, h))
    elif raw[:2] == bytes([0xFF, 0xD8]):
        kind, ext = "jpeg", ".jpg"
    else:
        raise ValueError("Use a PNG or JPEG image.")
    try:
        img = minipdf.load_image_bytes(raw)
    except Exception:
        img = None
    if not img:
        raise ValueError("The image could not be read. Save it again as a standard PNG or JPEG.")
    if kind == "jpeg":
        comps = jpeg_components(raw)
        if comps not in (1, 3):
            raise ValueError("The JPEG uses an unsupported colour mode (CMYK). Save it as an RGB JPEG or a PNG.")
    return {"kind": kind, "ext": ext, "width": img[0], "height": img[1], "bytes": n}


def jpeg_components(raw):
    i = 2
    while i < len(raw) - 9:
        if raw[i] != 0xFF:
            i += 1
            continue
        if raw[i + 1] in (0xC0, 0xC1, 0xC2):
            return raw[i + 9]
        i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
    return 0


# -- storage -------------------------------------------------------------------------------------------------------------------
def base_dir(paths):
    return os.path.join(paths.data_dir, "branding")


def find(paths, scope="all"):
    """Path of the stored logo for 'all' (the default) or a server name, or ''."""
    stem = os.path.join(base_dir(paths), "customer_logo") if scope in ("", "all") else os.path.join(base_dir(paths), "servers", safe(scope))
    for ext in EXTS:
        if os.path.isfile(stem + ext):
            return stem + ext
    return ""


def describe(path):
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
        d = inspect(raw)
        d["path"] = path
        return d
    except (OSError, ValueError):
        return {"path": path, "kind": "unreadable", "ext": os.path.splitext(path)[1], "width": 0, "height": 0, "bytes": 0}


def server_names(cfg):
    return [sv.name for sv in fleet.servers(cfg)]


def check_scope(app, scope):
    scope = (scope or "all").strip()
    if scope != "all" and scope not in server_names(app.cfg):
        raise ValueError("unknown server '%s' (servers are listed in servers.conf)" % scope)
    return scope


def save(app, raw, scope="all"):
    """Store a logo. The default logo is also written to [main] [general] logo. Returns its description."""
    scope = check_scope(app, scope)
    info = inspect(raw)
    if scope == "all":
        stem = os.path.join(base_dir(app.paths), "customer_logo")
    else:
        stem = os.path.join(base_dir(app.paths), "servers", safe(scope))
    os.makedirs(os.path.dirname(stem), exist_ok=True)
    try:
        os.chmod(os.path.dirname(stem), 0o750)
    except OSError:
        pass
    target = stem + info["ext"]
    tmp = target + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.chmod(tmp, 0o640)
    os.replace(tmp, target)
    for ext in EXTS:                                        # a new PNG replaces an old JPEG and the other way round
        if ext != info["ext"] and os.path.isfile(stem + ext):
            os.remove(stem + ext)
    if scope == "all" and app.cfg.get("main", "general", "logo", "") != target:
        app.cfg.set("main", "general", "logo", target)
        app.save_config("main")
    app.audit("logo-upload", scope=scope, bytes=info["bytes"], size="%dx%d" % (info["width"], info["height"]))
    info["path"] = target
    return info


def remove(app, scope="all"):
    scope = check_scope(app, scope)
    path = find(app.paths, scope)
    if path:
        os.remove(path)
    if scope == "all" and path and app.cfg.get("main", "general", "logo", "") == path:
        app.cfg.set("main", "general", "logo", "")
        app.save_config("main")
    app.audit("logo-remove", scope=scope)
    return bool(path)


def listing(app):
    """What is stored: the default logo and, for every server in servers.conf, its own logo or the default."""
    default = find(app.paths, "all")
    out = {"default": describe(default), "servers": []}
    for name in server_names(app.cfg):
        own = find(app.paths, name)
        out["servers"].append({"name": name, "logo": describe(own or default), "own": bool(own)})
    return out


# -- sending the right logo to every server ---------------------------------------------------------------------------------------
def _ssh_base(sv, scp=False):
    cmd = ["scp" if scp else "ssh", "-P" if scp else "-p", str(sv.port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    cmd += shlex.split(sv.ssh_options or "")
    if sv.identity:
        cmd += ["-i", sv.identity]
    return cmd


def push(app, names=None):
    """Copy each server's logo (its own, else the default) to it and point its [general] logo at it. Returns [{'server','ok','detail'}]."""
    results = []
    for sv in fleet.servers(app.cfg):
        if names and sv.name not in names:
            continue
        src = find(app.paths, sv.name) or find(app.paths, "all")
        if not src:
            results.append({"server": sv.name, "ok": False, "detail": "no logo uploaded yet"})
            continue
        ext = os.path.splitext(src)[1]
        remote = "%s/customer_logo%s" % (REMOTE_DIR, ext)
        target = "%s@%s" % (sv.user, sv.host) if sv.user else sv.host
        r = app.runner.run(_ssh_base(sv) + [target, "mkdir -p %s && chmod 750 %s" % (REMOTE_DIR, REMOTE_DIR)], timeout=60)
        if r.ok:
            r = app.runner.run(_ssh_base(sv, True) + [src, "%s:%s" % (target, remote)], timeout=120)
        if r.ok:
            r = app.runner.run(_ssh_base(sv) + [target, "chmod 640 %s && vitals config set main general logo %s" % (remote, remote)], timeout=60)
        results.append({"server": sv.name, "ok": bool(r.ok), "detail": "sent" if r.ok else ((r.err or r.out or "failed").strip().splitlines() or ["failed"])[-1][:200]})
    app.audit("logo-push", servers=",".join(x["server"] for x in results), ok=sum(1 for x in results if x["ok"]))
    return results
