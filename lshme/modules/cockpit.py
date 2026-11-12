# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Cockpit web console: packages, socket/service, listener, HTTPS reachability,
TLS certificate and security-relevant configuration."""
import re
import ssl
import time

try:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
except ImportError:  # pragma: no cover
    Request = urlopen = HTTPError = None

from lshme.core.util import listening_ports, parse_openssl_date

NAME = "cockpit"
TITLE = "Cockpit Web Console"
WEIGHT = 4


def packages(ctx):
    pk = []
    if ctx.has("rpm"):
        r = ctx.run(["rpm", "-qa", "--qf", "%{NAME} %{VERSION}-%{RELEASE}\n", "cockpit*"])
        pk = [l.split(None, 1) for l in r.lines()]
    elif ctx.has("dpkg-query"):
        r = ctx.run(["dpkg-query", "-W", "-f", "${Package} ${Version} ${Status}\n", "cockpit*"])
        pk = [l.split(None, 2)[:2] for l in r.lines() if "install ok installed" in l]
    return [p for p in pk if len(p) == 2]


def unit_state(ctx, unit):
    r = ctx.run(["systemctl", "show", unit, "-p", "LoadState,ActiveState,SubState,UnitFileState,Listen", "--no-pager"])
    d = {}
    for line in r.lines():
        if "=" in line:
            k, val = line.split("=", 1)
            d[k] = val
    return d


def probe(port, timeout=8):
    if urlopen is None:
        return None, "urllib unavailable"
    # Local liveness probe only: Cockpit commonly uses a self-signed certificate,
    # so verification is disabled for this localhost request. Expiry is checked separately.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT) if hasattr(ssl, "PROTOCOL_TLS_CLIENT") else ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        resp = urlopen(Request("https://127.0.0.1:%d/" % port, headers={"User-Agent": "lshme"}), timeout=timeout, context=context)
        return resp.getcode(), "ok"
    except HTTPError as e:
        return e.code, "http %s" % e.code
    except Exception as e:
        return None, str(e)[:300]


def run(ctx, res):
    pk = packages(ctx)
    required = ctx.cfg.getbool("main", "cockpit", "required", False)
    if not pk and not ctx.runner.isdir("/usr/share/cockpit"):
        res.status = "SKIPPED"
        res.summary = "Cockpit is not installed"
        res.skip("Cockpit not installed")
        if required:
            res.add("CRITICAL", "COCKPIT_NOT_INSTALLED", "cockpit", "Cockpit is required but not installed",
                    recommendation="Install cockpit and enable cockpit.socket.")
        return
    sock = unit_state(ctx, "cockpit.socket") if ctx.has("systemctl") else {}
    svc = unit_state(ctx, "cockpit.service") if ctx.has("systemctl") else {}
    port = 9090
    m = re.search(r":?(\d+)\s*\(Stream\)", sock.get("Listen", ""))
    if m:
        port = int(m.group(1))
    ports = set(p["port"] for p in (ctx.shared.get("listening_ports") or listening_ports(ctx.runner)))
    listening = port in ports
    code, detail = (None, "skipped")
    if listening and ctx.cfg.getbool("main", "cockpit", "probe_https", True):
        code, detail = probe(port)
    certs = []
    for path in ctx.runner.glob("/etc/cockpit/ws-certs.d/*.cert") + ctx.runner.glob("/etc/cockpit/ws-certs.d/*.crt"):
        r = ctx.run(["openssl", "x509", "-noout", "-enddate", "-subject", "-issuer", "-in", path])
        if r.ok:
            end = re.search(r"notAfter=(.+)", r.out)
            subj = re.search(r"subject=\s*(.+)", r.out)
            issuer = re.search(r"issuer=\s*(.+)", r.out)
            exp = parse_openssl_date(end.group(1)) if end else None
            certs.append({"path": path, "expires": exp, "days_left": round((exp - time.time()) / 86400, 1) if exp else None,
                          "subject": subj.group(1).strip() if subj else "", "issuer": issuer.group(1).strip() if issuer else "",
                          "self_signed": bool(subj and issuer and subj.group(1).strip() == issuer.group(1).strip())})
    conf = ctx.read("/etc/cockpit/cockpit.conf")
    allow_unencrypted = bool(re.search(r"^\s*AllowUnencrypted\s*=\s*true", conf, re.M | re.I))
    disallowed = ctx.read("/etc/cockpit/disallowed-users")
    res.data = {"packages": pk, "socket": sock, "service": svc, "port": port, "listening": listening,
                "https_status": code, "https_detail": detail, "certificates": certs,
                "allow_unencrypted": allow_unencrypted, "disallowed_users": disallowed.split()}
    res.table("Cockpit status", ["Item", "Value"], [
        ["Packages", ", ".join("%s %s" % tuple(p) for p in pk) or "web assets present"],
        ["cockpit.socket", "%s / %s (%s)" % (sock.get("ActiveState", "?"), sock.get("SubState", "?"), sock.get("UnitFileState", "?"))],
        ["cockpit.service", "%s / %s" % (svc.get("ActiveState", "?"), svc.get("SubState", "?"))],
        ["Port", "%d (%s)" % (port, "listening" if listening else "not listening")],
        ["HTTPS probe", "%s %s" % (code or "", detail)],
        ["Certificates", "; ".join("%s: %s days" % (c["path"].rsplit("/", 1)[-1], c["days_left"]) for c in certs) or "none found"],
        ["AllowUnencrypted", "true" if allow_unencrypted else "false"],
        ["Disallowed users", disallowed.replace("\n", ", ") or "none"],
    ], kind="summary")
    sev = "CRITICAL" if required else "WARNING"
    if sock and sock.get("ActiveState") != "active" and svc.get("ActiveState") != "active":
        res.add(sev, "COCKPIT_INACTIVE", "cockpit.socket", "Cockpit socket is %s" % sock.get("ActiveState", "unknown"),
                recommendation="Enable it with 'systemctl enable --now cockpit.socket' if the console should be available.")
    elif sock and sock.get("UnitFileState") not in ("enabled", "static", "enabled-runtime"):
        res.add("WARNING", "COCKPIT_NOT_ENABLED", "cockpit.socket", "cockpit.socket is not enabled at boot")
    if sock.get("ActiveState") == "active" and not listening:
        res.add(sev, "COCKPIT_PORT_CLOSED", "port %d" % port, "Cockpit socket is active but port %d is not listening" % port)
    if listening and code is None and detail != "skipped":
        res.add("WARNING", "COCKPIT_HTTPS_FAILED", "https", "Cockpit HTTPS probe failed: %s" % detail)
    cw, cc = ctx.t("certificates", "warning_days", 30), ctx.t("certificates", "critical_days", 7)
    for c in certs:
        if c["days_left"] is None:
            continue
        if c["days_left"] < 0:
            res.add("CRITICAL", "COCKPIT_CERT_EXPIRED", c["path"], "Cockpit certificate expired %d days ago" % -c["days_left"])
        elif c["days_left"] <= cc:
            res.add("CRITICAL", "COCKPIT_CERT_EXPIRING", c["path"], "Cockpit certificate expires in %.0f days" % c["days_left"])
        elif c["days_left"] <= cw:
            res.add("WARNING", "COCKPIT_CERT_EXPIRING", c["path"], "Cockpit certificate expires in %.0f days" % c["days_left"],
                    recommendation="Renew the certificate in /etc/cockpit/ws-certs.d/.")
        if c["self_signed"]:
            res.add("INFO", "COCKPIT_SELF_SIGNED", c["path"], "Cockpit uses a self-signed certificate",
                    recommendation="Install a CA-signed certificate for production consoles.")
    if allow_unencrypted:
        res.add("WARNING", "COCKPIT_UNENCRYPTED", "cockpit.conf", "Cockpit allows unencrypted HTTP (AllowUnencrypted=true)",
                recommendation="Disable AllowUnencrypted unless a TLS-terminating proxy is in front.")
    if "root" not in disallowed.split() and listening:
        res.add("INFO", "COCKPIT_ROOT_ALLOWED", "disallowed-users", "root may log in to Cockpit",
                recommendation="Add root to /etc/cockpit/disallowed-users if policy requires named admin accounts.")
    state = sock.get("ActiveState") or ("listening" if listening else "not listening")
    res.summary = "Cockpit %s on port %d%s" % (state, port, (", HTTPS %s" % code) if code else "")
