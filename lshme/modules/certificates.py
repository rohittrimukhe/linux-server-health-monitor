"""TLS certificate expiry for configured files, directories and network endpoints."""
import re
import ssl
import time

from lshme.core.util import parse_openssl_date

NAME = "certificates"
TITLE = "TLS Certificates"
WEIGHT = 4


def inspect_pem(ctx, pem=None, path=None):
    args = ["openssl", "x509", "-noout", "-enddate", "-subject", "-issuer"]
    r = ctx.run(args + (["-in", path] if path else []), input_text=pem)
    if not r.ok:
        return None
    end = re.search(r"notAfter=(.+)", r.out)
    subj = re.search(r"subject=\s*(.+)", r.out)
    iss = re.search(r"issuer=\s*(.+)", r.out)
    exp = parse_openssl_date(end.group(1)) if end else None
    return {"expires": exp, "subject": subj.group(1).strip() if subj else "", "issuer": iss.group(1).strip() if iss else ""}


def run(ctx, res):
    files = ctx.cfg.getlist("certificates", "certificates", "files", "")
    for d in ctx.cfg.getlist("certificates", "certificates", "scan_dirs", ""):
        for ext in ("crt", "pem", "cer", "cert"):
            files += ctx.runner.glob("%s/*.%s" % (d.rstrip("/"), ext))
    endpoints = ctx.cfg.getlist("certificates", "certificates", "endpoints", "")
    if not files and not endpoints:
        res.status = "SKIPPED"
        res.summary = "no certificates configured"
        res.skip("configure files/endpoints in certificates.conf")
        return
    if not ctx.has("openssl"):
        res.status = "ERROR"
        res.add("UNKNOWN", "OPENSSL_MISSING", "openssl", "openssl is required for certificate checks")
        return
    cw, cc = ctx.t("certificates", "warning_days", 30), ctx.t("certificates", "critical_days", 7)
    rows = []
    items = [("file", f) for f in files] + [("endpoint", e) for e in endpoints]
    for kind, target in items:
        info, err = None, ""
        if kind == "file":
            if not ctx.runner.exists(target):
                err = "file not found"
            else:
                info = inspect_pem(ctx, path=target)
        else:
            host, _, port = target.rpartition(":")
            try:
                pem = ssl.get_server_certificate((host or target, int(port or 443)))
                info = inspect_pem(ctx, pem=pem)
            except Exception as e:
                err = str(e)[:200]
        if not info or info["expires"] is None:
            rows.append([target, kind, "", "", "", "ERROR: %s" % (err or "unreadable")])
            res.add("WARNING", "CERT_UNREADABLE", target, "Certificate %s could not be read" % target, evidence=err)
            continue
        days = (info["expires"] - time.time()) / 86400
        status = "OK"
        if days < 0:
            status = "EXPIRED"
            res.add("CRITICAL", "CERT_EXPIRED", target, "Certificate %s expired %.0f days ago" % (target, -days),
                    evidence=info["subject"], recommendation="Renew and deploy the certificate immediately.")
        elif days <= cc:
            status = "CRITICAL"
            res.add("CRITICAL", "CERT_EXPIRING", target, "Certificate %s expires in %.0f days" % (target, days), evidence=info["subject"],
                    recommendation="Renew the certificate now.")
        elif days <= cw:
            status = "WARNING"
            res.add("WARNING", "CERT_EXPIRING", target, "Certificate %s expires in %.0f days" % (target, days), evidence=info["subject"],
                    recommendation="Plan certificate renewal.")
        res.metric("cert.days_left:" + target, days)
        rows.append([target, kind, info["subject"][:80], time.strftime("%Y-%m-%d", time.gmtime(info["expires"])), "%.0f" % days, status])
    res.table("Certificates", ["Target", "Type", "Subject", "Expires", "Days left", "Status"], rows, kind="summary")
    res.data = {"checked": len(rows)}
    res.summary = "%d certificate(s) checked" % len(rows)
