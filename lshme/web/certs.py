# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""A free Let's Encrypt (ACME) certificate for the web interface, and its renewal.

The certificate is used by the LRS Vitals web interface only. Nothing else on the server is touched: no system
trust store, no other service. It needs a public DNS name that points at this server and, for the default HTTP check,
port 80 reachable from the internet. It cannot work for a private or LAN-only name."""
import json
import os
import re
import socket
import threading
import time

from lshme.core import logs
from lshme.core.config import as_bool, as_list
from lshme.web import acme, dnsproviders

PRIVATE_SUFFIXES = ("local", "localdomain", "lan", "home", "internal", "intranet", "corp", "private", "localhost", "test", "example", "invalid")
NAME_RX = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_contexts = []
_lock = threading.Lock()


def settings(cfg):
    g = lambda k, d="": (cfg.get("web", "web", k, d) or "").strip()
    directory = g("acme_directory", "production")
    source = g("tls_source", "self-signed").lower()
    source = source if source in ("letsencrypt", "self-signed", "custom") else "self-signed"
    return {"source": source, "enabled": as_bool(g("acme", "no"), False) or source == "letsencrypt", "dns_provider": g("acme_dns_provider").lower(), "dns_api": g("acme_dns_api"),
            "domains": [d.lower() for d in as_list(g("acme_domains"))], "email": g("acme_email"),
            "directory": {"production": acme.PRODUCTION, "staging": acme.STAGING}.get(directory.lower(), directory), "directory_name": directory,
            "challenge": g("acme_challenge", "http").lower(), "port": int(g("acme_http_port", "80") or 80), "webroot": g("acme_webroot"),
            "dns_hook": g("acme_dns_hook"), "dns_wait": int(g("acme_dns_wait", "60") or 60), "renew_days": int(g("acme_renew_days", "30") or 30)}


def valid_public_name(name):
    """A name Let's Encrypt could issue for: a real domain, not an IP address and not a private or local suffix."""
    name = (name or "").strip().lower()
    if not NAME_RX.match(name) or re.match(r"^[0-9.]+$", name):
        return False
    return name.rsplit(".", 1)[-1] not in PRIVATE_SUFFIXES


def domains_for(cfg):
    """The names for the certificate: acme_domains, or this server's own FQDN when that is a public name."""
    s = settings(cfg)
    if s["domains"]:
        return s["domains"]
    fqdn = socket.getfqdn()
    return [fqdn.lower()] if valid_public_name(fqdn) else []


def folder(app):
    return os.path.join(app.paths.data_dir, "web", "acme")


def files(app):
    d = folder(app)
    return {"account": os.path.join(d, "account.key"), "key": os.path.join(d, "server.key"), "cert": os.path.join(d, "fullchain.pem"), "meta": os.path.join(d, "meta.json")}


def load_meta(app):
    try:
        with open(files(app)["meta"], encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return {}


def save_meta(app, meta):
    p = files(app)["meta"]
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    os.replace(tmp, p)


def inspect(app, cert_path):
    """{'subject', 'issuer', 'not_after'} of a certificate file, or None."""
    from lshme.core.util import parse_openssl_date
    r = app.runner.run(["openssl", "x509", "-noout", "-subject", "-issuer", "-enddate", "-in", cert_path], timeout=20)
    if not r.ok:
        return None
    end = re.search(r"notAfter=(.+)", r.out)
    subj = re.search(r"subject=\s*(.+)", r.out)
    iss = re.search(r"issuer=\s*(.+)", r.out)
    return {"subject": subj.group(1).strip() if subj else "", "issuer": iss.group(1).strip() if iss else "",
            "not_after": parse_openssl_date(end.group(1)) if end else None}


def active_pair(app):
    """(certificate, key) of a valid ACME certificate when the feature is on, else None."""
    if not settings(app.cfg)["enabled"]:
        return None
    f = files(app)
    if not (os.path.exists(f["cert"]) and os.path.exists(f["key"])):
        return None
    info = inspect(app, f["cert"])
    if not info or not info["not_after"] or info["not_after"] < time.time():
        return None
    return f["cert"], f["key"]


def status(app):
    s = settings(app.cfg)
    f = files(app)
    meta = load_meta(app)
    domains = domains_for(app.cfg)
    out = {"source": s["source"], "enabled": s["enabled"], "domains": domains, "dns_provider": s["dns_provider"], "directory": s["directory_name"], "challenge": s["challenge"], "problem": "", "certificate": None,
           "last_error": meta.get("last_error", ""), "last_try": meta.get("last_try"), "renew_days": s["renew_days"]}
    if s["enabled"] and not domains:
        out["problem"] = "No public name to use. Give this server a public FQDN, or list the names in acme_domains (web.conf)."
    info = inspect(app, f["cert"]) if os.path.exists(f["cert"]) else None
    if info and info["not_after"]:
        out["certificate"] = {"subject": info["subject"], "issuer": info["issuer"], "expires": info["not_after"], "days_left": (info["not_after"] - time.time()) / 86400.0,
                              "domains": meta.get("domains", []), "in_use": bool(active_pair(app))}
        out["next_renewal"] = info["not_after"] - s["renew_days"] * 86400
    return out


def register_context(ctx):
    with _lock:
        _contexts.append(ctx)


def reload_contexts(app):
    """Make the running web server use the newest certificate for new connections, without a restart."""
    pair = active_pair(app)
    if not pair:
        return 0
    n = 0
    with _lock:
        for ctx in list(_contexts):
            try:
                ctx.load_cert_chain(*pair)
                n += 1
            except Exception:
                logs.get().exception("could not load the new web certificate")
    return n


def check_domains(domains, resolve=True):
    for d in domains:
        if not valid_public_name(d):
            raise acme.AcmeError("%s is not a public domain name (no IP addresses, .local, .lan, .internal and similar)" % d)
        if not resolve:                                  # the DNS check does not need the name to point at this server
            continue
        try:
            socket.getaddrinfo(d, None)
        except socket.gaierror:
            raise acme.AcmeError("%s does not resolve in DNS yet; create the DNS record that points it at this server" % d)


def issue(app, force=False, log=None):
    """Get or renew the certificate. Returns {'ok', 'message', 'renewed'}; never raises for expected problems."""
    log = log or (lambda m: None)
    s = settings(app.cfg)
    meta = load_meta(app)
    f = files(app)
    domains = domains_for(app.cfg)
    try:
        if not s["enabled"]:
            raise acme.AcmeError("the Let's Encrypt certificate is switched off (acme = no in web.conf)")
        if not domains:
            raise acme.AcmeError("no public name to use: set acme_domains in web.conf, or give this server a public FQDN")
        if os.path.exists(f["cert"]) and not force:
            info = inspect(app, f["cert"])
            if info and info["not_after"] and info["not_after"] - time.time() > s["renew_days"] * 86400 and sorted(meta.get("domains", [])) == sorted(domains):
                return {"ok": True, "renewed": False, "message": "The certificate is valid for %d more days; nothing to do." % ((info["not_after"] - time.time()) // 86400)}
        check_domains(domains, resolve=(s["challenge"] == "http"))
        dns = None
        if s["challenge"] == "dns":
            dns = dnsproviders.provider(s["dns_provider"], app.cfg.secret("acme", "token"), s["dns_hook"], s["dns_api"])
        os.makedirs(folder(app), exist_ok=True)
        os.chmod(folder(app), 0o700)
        if not os.path.exists(f["account"]):
            acme.new_key(f["account"])
        new_key = f["key"] + ".new"
        acme.new_key(new_key)
        client = acme.Client(s["directory"], f["account"], s["email"], log)
        pem = client.issue(domains, new_key, s["challenge"], s["port"], s["webroot"], dns, s["dns_wait"])
        tmp = f["cert"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(pem)
        os.replace(new_key, f["key"])                 # key and certificate are replaced together, only after a successful order
        os.replace(tmp, f["cert"])
        meta.update(domains=domains, issued=time.time(), last_error="", last_try=time.time(), directory=s["directory_name"])
        save_meta(app, meta)
        reload_contexts(app)
        app.audit("web-cert-issued", domains=",".join(domains), directory=s["directory_name"])
        return {"ok": True, "renewed": True, "message": "A certificate for %s was installed in the web interface." % ", ".join(domains)}
    except acme.AcmeError as e:
        meta.update(last_error=str(e), last_try=time.time())
        try:
            save_meta(app, meta)
        except OSError:
            pass
        logs.get().warning("web certificate: %s", e)
        return {"ok": False, "renewed": False, "message": str(e)}


def obtain(app, retry_after=900):
    """The (certificate, key) of a genuine certificate, getting one first if needed. Raises ValueError with the reason when it cannot:
    with tls_source = letsencrypt a self-signed certificate is never used instead."""
    pair = active_pair(app)
    if pair:
        return pair
    meta = load_meta(app)
    if not meta.get("last_error") or time.time() - meta.get("last_try", 0) > retry_after:     # do not hammer the authority after a failure
        issue(app)
        pair = active_pair(app)
        if pair:
            return pair
        meta = load_meta(app)
    raise ValueError("a genuine Let's Encrypt certificate is required (tls_source = letsencrypt) but none could be obtained: %s. "
                     "Self-signed certificates are switched off. See 'vitals web cert status'." % (meta.get("last_error") or "no public name is set (acme_domains)"))


def start_renewal(app, check_every=6 * 3600, first_after=30):
    """A background thread in the web server: gets the first certificate and renews before it expires."""
    def loop():
        time.sleep(first_after)
        while True:
            try:
                if settings(app.cfg)["enabled"]:
                    issue(app)
            except Exception:
                logs.get().exception("web certificate renewal failed")
            time.sleep(check_every)
    t = threading.Thread(target=loop, name="web-cert-renewal", daemon=True)
    t.start()
    return t
