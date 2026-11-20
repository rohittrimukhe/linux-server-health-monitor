# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""A small ACME (RFC 8555) client for Let's Encrypt and other ACME certificate authorities.

Standard library plus the `openssl` command only. It proves control of a domain with HTTP-01 (a temporary web server
on port 80, or a webroot folder) or DNS-01 (a hook script you provide) and returns the signed certificate chain.
It never changes anything outside the files it is asked to write."""
import base64
import binascii
import hashlib
import http.server
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"
STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"


class AcmeError(Exception):
    pass


def b64(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def openssl(args, data=None):
    try:
        p = subprocess.run(["openssl"] + list(args), input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise AcmeError("openssl failed: %s" % e)
    if p.returncode:
        raise AcmeError("openssl %s failed: %s" % (args[0], p.stderr.decode("utf-8", "replace").strip()[:300]))
    return p.stdout


def new_key(path, bits=2048):
    key = openssl(["genrsa", str(bits)])
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    os.replace(tmp, path)


def rsa_jwk(key_path):
    text = openssl(["rsa", "-in", key_path, "-noout", "-text"]).decode("utf-8", "replace")
    m = re.search(r"modulus:\s+00:([a-f0-9:\s]+?)\n\s*(?:public)?exponent:\s*([0-9]+)", text, re.I | re.S)
    if not m:
        raise AcmeError("could not read the account key")
    exp = "{0:x}".format(int(m.group(2)))
    exp = "0" + exp if len(exp) % 2 else exp
    n = re.sub(r"[\s:]", "", m.group(1))
    return {"e": b64(binascii.unhexlify(exp)), "kty": "RSA", "n": b64(binascii.unhexlify(n))}


def thumbprint(jwk):
    return b64(hashlib.sha256(json.dumps(jwk, sort_keys=True, separators=(",", ":")).encode("utf-8")).digest())


def make_csr(domain_key, domains):
    """DER certificate request for the domains (written with a temporary config so old OpenSSL versions work too)."""
    conf = "[req]\ndistinguished_name=dn\nreq_extensions=san\nprompt=no\n[dn]\nCN=%s\n[san]\nsubjectAltName=%s\n" % (
        domains[0], ",".join("DNS:" + d for d in domains))
    fd, path = tempfile.mkstemp(suffix=".cnf")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(conf)
        return openssl(["req", "-new", "-sha256", "-key", domain_key, "-config", path, "-outform", "DER"])
    finally:
        os.unlink(path)


class Standalone(object):
    """A temporary web server that answers /.well-known/acme-challenge/<token> for the HTTP-01 check."""

    def __init__(self, port):
        self.port, self.tokens, self.server, self.thread = port, {}, None, None

    def start(self):
        tokens = self.tokens

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                prefix = "/.well-known/acme-challenge/"
                token = self.path[len(prefix):] if self.path.startswith(prefix) else None
                if token in tokens:
                    body = tokens[token].encode("ascii")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *a):
                pass
        try:
            self.server = http.server.HTTPServer(("", self.port), Handler)
        except OSError as e:
            raise AcmeError("port %d is not available (%s). Stop the program using it, or set acme_webroot to the folder that program serves." % (self.port, e))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


class Client(object):
    def __init__(self, directory_url, account_key, email="", log=None, timeout=30):
        self.directory_url, self.account_key, self.email = directory_url, account_key, email
        self.log = log or (lambda m: None)
        self.timeout = timeout
        self.jwk = rsa_jwk(account_key)
        self.directory, self.nonce, self.kid = None, None, None

    # -- HTTP -----------------------------------------------------------------------------------------
    def http(self, url, data=None, headers=None):
        req = Request(url, data=data, headers=dict({"User-Agent": "lrs-vitals-acme"}, **(headers or {})))
        try:
            resp = urlopen(req, timeout=self.timeout)
            return resp.getcode(), resp.read(), resp.info()
        except HTTPError as e:
            return e.code, e.read(), e.info()
        except (URLError, socket.timeout, OSError) as e:
            raise AcmeError("cannot reach the certificate authority at %s: %s" % (url, getattr(e, "reason", e)))

    def get_nonce(self):
        code, _, hdr = self.http(self.directory["newNonce"], headers={})
        nonce = hdr.get("Replay-Nonce")
        if not nonce:
            raise AcmeError("the certificate authority sent no nonce")
        return nonce

    def signed(self, url, payload, use_kid=True, tries=3):
        """POST a JWS. payload None means POST-as-GET. Returns (status, parsed or raw bytes, headers)."""
        for _ in range(tries):
            if self.nonce is None:
                self.nonce = self.get_nonce()
            payload64 = "" if payload is None else b64(json.dumps(payload))
            protected = {"url": url, "alg": "RS256", "nonce": self.nonce}
            protected["kid" if (use_kid and self.kid) else "jwk"] = self.kid if (use_kid and self.kid) else self.jwk
            protected64 = b64(json.dumps(protected))
            sig = openssl(["dgst", "-sha256", "-sign", self.account_key], ("%s.%s" % (protected64, payload64)).encode("ascii"))
            body = json.dumps({"protected": protected64, "payload": payload64, "signature": b64(sig)}).encode("utf-8")
            code, raw, hdr = self.http(url, body, {"Content-Type": "application/jose+json"})
            self.nonce = hdr.get("Replay-Nonce")
            data = raw
            if "json" in (hdr.get("Content-Type") or ""):
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError:
                    pass
            if code >= 400 and isinstance(data, dict) and data.get("type", "").endswith("badNonce"):
                self.nonce = None
                continue
            if code >= 400:
                detail = data.get("detail") if isinstance(data, dict) else raw[:200]
                raise AcmeError("the certificate authority refused the request (%s): %s" % (code, detail))
            return code, data, hdr
        raise AcmeError("the certificate authority kept rejecting the request nonce")

    # -- account and order ------------------------------------------------------------------------------
    def start(self):
        code, raw, _ = self.http(self.directory_url)
        if code != 200:
            raise AcmeError("the certificate authority returned %s for its directory" % code)
        self.directory = json.loads(raw.decode("utf-8"))
        reg = {"termsOfServiceAgreed": True}
        if self.email:
            reg["contact"] = ["mailto:" + self.email]
        _, _, hdr = self.signed(self.directory["newAccount"], reg, use_kid=False)
        self.kid = hdr.get("Location")
        if not self.kid:
            raise AcmeError("the certificate authority did not return an account address")

    def poll(self, url, done=("valid",), bad=("invalid",), what="the request", wait=3, rounds=40):
        for _ in range(rounds):
            _, data, _ = self.signed(url, None)
            status = data.get("status") if isinstance(data, dict) else None
            if status in done:
                return data
            if status in bad:
                err = ""
                if isinstance(data, dict):
                    ch = [c for c in data.get("challenges", []) if c.get("error")]
                    err = (ch[0]["error"].get("detail") if ch else (data.get("error") or {}).get("detail", "")) or ""
                raise AcmeError("%s failed: %s" % (what, err or status))
            time.sleep(wait)
        raise AcmeError("%s did not finish in time" % what)

    def issue(self, domains, domain_key, challenge="http", port=80, webroot="", dns_hook="", dns_wait=60, poll_wait=3):
        """Returns the certificate chain (PEM text) for the domains."""
        self.start()
        _, order, hdr = self.signed(self.directory["newOrder"], {"identifiers": [{"type": "dns", "value": d} for d in domains]})
        order_url = hdr.get("Location")
        if not isinstance(order, dict) or not order_url:
            raise AcmeError("the certificate authority did not accept the order")
        standalone = None
        if challenge == "http" and not webroot:
            standalone = Standalone(port)
            standalone.start()
        try:
            for authz_url in order["authorizations"]:
                _, authz, _ = self.signed(authz_url, None)
                domain = authz["identifier"]["value"]
                if authz.get("status") == "valid":
                    continue
                want = "http-01" if challenge == "http" else "dns-01"
                ch = [c for c in authz["challenges"] if c["type"] == want]
                if not ch:
                    raise AcmeError("the certificate authority does not offer %s for %s" % (want, domain))
                ch = ch[0]
                keyauth = "%s.%s" % (ch["token"], thumbprint(self.jwk))
                cleanup = self.provision(challenge, domain, ch["token"], keyauth, standalone, webroot, dns_hook, dns_wait)
                try:
                    self.log("asking the certificate authority to check %s" % domain)
                    self.signed(ch["url"], {})
                    self.poll(authz_url, what="the check of %s" % domain, wait=poll_wait)
                finally:
                    cleanup()
            _, _, _ = self.signed(order["finalize"], {"csr": b64(make_csr(domain_key, domains))})
            done = self.poll(order_url, done=("valid",), bad=("invalid",), what="the certificate order", wait=poll_wait)
            if not done.get("certificate"):
                raise AcmeError("the order finished without a certificate")
            code, pem, _ = self.signed(done["certificate"], None)
            pem = pem.decode("utf-8") if isinstance(pem, bytes) else str(pem)
            if "BEGIN CERTIFICATE" not in pem:
                raise AcmeError("the certificate authority returned something that is not a certificate")
            return pem
        finally:
            if standalone:
                standalone.stop()

    def provision(self, challenge, domain, token, keyauth, standalone, webroot, dns_hook, dns_wait):
        if challenge == "http":
            if webroot:
                d = os.path.join(webroot, ".well-known", "acme-challenge")
                os.makedirs(d, exist_ok=True)
                path = os.path.join(d, token)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(keyauth)
                return lambda: os.path.exists(path) and os.unlink(path)
            standalone.tokens[token] = keyauth
            return lambda: standalone.tokens.pop(token, None)
        if not dns_hook:
            raise AcmeError("DNS validation needs acme_dns_hook: a script that adds and removes the TXT record")
        value = b64(hashlib.sha256(keyauth.encode("ascii")).digest())
        self.run_hook(dns_hook, "add", domain, value)
        self.log("waiting %d s for the DNS record to spread" % dns_wait)
        time.sleep(dns_wait)
        return lambda: self.run_hook(dns_hook, "remove", domain, value, quiet=True)

    def run_hook(self, hook, action, domain, value, quiet=False):
        try:
            p = subprocess.run([hook, action, "_acme-challenge." + domain, value], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            if quiet:
                return
            raise AcmeError("the DNS hook could not run: %s" % e)
        if p.returncode and not quiet:
            raise AcmeError("the DNS hook failed (%d): %s" % (p.returncode, p.stderr.decode("utf-8", "replace")[:200]))
