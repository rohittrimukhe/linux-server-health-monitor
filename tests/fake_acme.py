# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""A tiny pretend certificate authority that speaks ACME, for tests. It checks the HTTP-01 / DNS-01 answer the way a
real one does (the key authorization from the account key), and signs the request with a throw-away CA."""
import base64
import hashlib
import http.server
import json
import os
import subprocess
import tempfile
import threading
from urllib.request import urlopen


def b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sh(args, data=None):
    p = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeError(p.stderr.decode())
    return p.stdout


class FakeCA(object):
    def __init__(self, challenge_url=None, webroot=None, dns_file=None, reject=False, days=90):
        self.challenge_url, self.webroot, self.dns_file, self.reject, self.days = challenge_url, webroot, dns_file, reject, days
        self.dir = tempfile.mkdtemp(prefix="fakeca-")
        sh(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", os.path.join(self.dir, "ca.key"), "-out", os.path.join(self.dir, "ca.crt"),
            "-subj", "/CN=Fake Test CA", "-days", "30"])
        self.jwk, self.authz, self.order_done, self.requests, self.orders = None, {}, False, [], 0
        self.nonce_n = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, ctype="application/json", extra=None):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                self.send_response(code)
                outer.nonce_n += 1
                self.send_header("Replay-Nonce", "nonce-%d" % outer.nonce_n)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(raw)

            def do_HEAD(self):
                self.send(200, b"")

            def do_GET(self):
                if self.path == "/directory":
                    b = outer.base
                    self.send(200, {"newNonce": b + "/nonce", "newAccount": b + "/acct", "newOrder": b + "/order"})
                else:
                    self.send(204, b"")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
                protected = json.loads(unb64(body["protected"]).decode("utf-8"))
                payload = json.loads(unb64(body["payload"]).decode("utf-8")) if body["payload"] else None
                outer.requests.append((self.path, "jwk" in protected, "kid" in protected, payload))
                b = outer.base
                if self.path == "/acct":
                    outer.jwk = protected["jwk"]
                    return self.send(201, {"status": "valid"}, extra={"Location": b + "/acct/1"})
                if "kid" not in protected:
                    return self.send(400, {"type": "urn:ietf:params:acme:error:malformed", "detail": "this request must use the account address (kid)"})
                if self.path == "/order":
                    outer.orders += 1
                    names = [i["value"] for i in payload["identifiers"]]
                    outer.authz = dict((str(i), {"domain": d, "status": "pending"}) for i, d in enumerate(names))
                    return self.send(201, {"status": "pending", "authorizations": [b + "/authz/%d" % i for i in range(len(names))], "finalize": b + "/finalize/1",
                                           "identifiers": payload["identifiers"]}, extra={"Location": b + "/order/1"})
                if self.path.startswith("/authz/"):
                    i = self.path.rsplit("/", 1)[1]
                    a = outer.authz[i]
                    return self.send(200, {"status": a["status"], "identifier": {"type": "dns", "value": a["domain"]},
                                           "challenges": [{"type": "http-01", "url": b + "/chall/http/" + i, "token": "tok-" + i, "status": "pending"},
                                                          {"type": "dns-01", "url": b + "/chall/dns/" + i, "token": "tok-" + i, "status": "pending"}] +
                                          ([{"error": {"detail": "rejected by the test CA"}}] if a["status"] == "invalid" else [])})
                if self.path.startswith("/chall/"):
                    _, _, kind, i = self.path.split("/")
                    keyauth = "tok-%s.%s" % (i, b64(hashlib.sha256(json.dumps(outer.jwk, sort_keys=True, separators=(",", ":")).encode()).digest()))
                    ok = False
                    try:
                        if outer.reject:
                            ok = False
                        elif kind == "http":
                            if outer.webroot:
                                with open(os.path.join(outer.webroot, ".well-known", "acme-challenge", "tok-" + i)) as f:
                                    ok = f.read() == keyauth
                            else:
                                ok = urlopen(outer.challenge_url + "/.well-known/acme-challenge/tok-" + i, timeout=5).read().decode() == keyauth
                        else:
                            with open(outer.dns_file) as f:
                                ok = b64(hashlib.sha256(keyauth.encode()).digest()) in f.read().split()
                    except Exception:
                        ok = False
                    outer.authz[i]["status"] = "valid" if ok else "invalid"
                    return self.send(200, {"status": "processing"})
                if self.path == "/finalize/1":
                    with open(os.path.join(outer.dir, "req.der"), "wb") as f:
                        f.write(unb64(payload["csr"]))
                    sh(["openssl", "req", "-inform", "DER", "-in", os.path.join(outer.dir, "req.der"), "-out", os.path.join(outer.dir, "req.pem")])
                    sh(["openssl", "x509", "-req", "-in", os.path.join(outer.dir, "req.pem"), "-CA", os.path.join(outer.dir, "ca.crt"), "-CAkey", os.path.join(outer.dir, "ca.key"),
                        "-CAcreateserial", "-days", str(outer.days), "-sha256", "-out", os.path.join(outer.dir, "leaf.pem")])
                    outer.order_done = True
                    return self.send(200, {"status": "valid", "certificate": b + "/cert/1"})
                if self.path == "/order/1":
                    return self.send(200, {"status": "valid" if outer.order_done else "ready", "certificate": b + "/cert/1", "finalize": b + "/finalize/1"})
                if self.path == "/cert/1":
                    with open(os.path.join(outer.dir, "leaf.pem"), "rb") as f1, open(os.path.join(outer.dir, "ca.crt"), "rb") as f2:
                        return self.send(200, f1.read() + f2.read(), "application/pem-certificate-chain")
                self.send(404, {"type": "x", "detail": "unknown"})

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.directory = self.base + "/directory"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class FakeDnsApi(object):
    """A pretend DNS provider API (Cloudflare shaped and DuckDNS shaped). It records what was asked and writes TXT values to a file
    that the FakeCA reads, like a real DNS server would answer the authority."""

    def __init__(self, dns_file, token="secret-token"):
        self.dns_file, self.token, self.log, self.records = dns_file, token, [], {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, obj, raw=False):
                body = obj.encode() if raw else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorised(self):
                return self.headers.get("Authorization") == "Bearer " + outer.token

            def do_GET(self):
                from urllib.parse import parse_qs, urlsplit
                u = urlsplit(self.path)
                q = parse_qs(u.query)
                outer.log.append(("GET", u.path, q))
                if u.path == "/update":                                   # DuckDNS
                    if q.get("token", [""])[0] != outer.token:
                        return self.reply(200, "KO", raw=True)
                    value = q.get("txt", [""])[0]
                    if q.get("clear"):
                        outer.write(remove=value)
                    else:
                        outer.write(add=value)
                    return self.reply(200, "OK\n" + value, raw=True)
                if u.path == "/zones":                                    # Cloudflare: zone lookup
                    if not self.authorised():
                        return self.reply(403, {"success": False, "errors": [{"message": "bad token"}]})
                    name = q.get("name", [""])[0]
                    return self.reply(200, {"success": True, "result": [{"id": "zone-1", "name": name}] if name == "example.com" else []})
                self.reply(404, {"success": False, "errors": []})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
                outer.log.append(("POST", self.path, body))
                if not self.authorised():
                    return self.reply(403, {"success": False, "errors": [{"message": "bad token"}]})
                outer.write(add=body["content"])
                self.reply(200, {"success": True, "result": {"id": "rec-1"}})

            def do_DELETE(self):
                outer.log.append(("DELETE", self.path, None))
                outer.write(clear=True)
                self.reply(200, {"success": True, "result": {"id": "rec-1"}})

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def write(self, add=None, remove=None, clear=False):
        if clear or remove:
            open(self.dns_file, "w").close()
        if add:
            with open(self.dns_file, "a") as f:
                f.write(add + "\n")

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
