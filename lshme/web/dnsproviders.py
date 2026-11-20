# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""DNS providers for the Let's Encrypt DNS check (DNS-01).

With the DNS check the certificate authority only asks for a TXT record, so a server on a private network
(for example 192.168.1.3) can still get a genuine, publicly trusted certificate for a domain you control.
Built in: Cloudflare (API token) and DuckDNS (token; free sub-domains), plus your own hook script."""
import json
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from lshme.web.acme import AcmeError


def call(url, method="GET", headers=None, data=None, timeout=30):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    h = dict({"User-Agent": "lrs-vitals-acme"}, **(headers or {}))
    if body is not None:
        h["Content-Type"] = "application/json"
    try:
        resp = urlopen(Request(url, data=body, headers=h, method=method), timeout=timeout)
        return resp.getcode(), resp.read().decode("utf-8", "replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (URLError, OSError) as e:
        raise AcmeError("cannot reach the DNS provider at %s: %s" % (url.split("?")[0], getattr(e, "reason", e)))


class Hook(object):
    """Your own script, called as: script add|remove _acme-challenge.NAME VALUE"""
    name = "hook"

    def __init__(self, path):
        self.path = path

    def add(self, domain, value):
        try:
            p = subprocess.run([self.path, "add", "_acme-challenge." + domain, value], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            raise AcmeError("the DNS hook could not run: %s" % e)
        if p.returncode:
            raise AcmeError("the DNS hook failed (%d): %s" % (p.returncode, p.stderr.decode("utf-8", "replace")[:200]))

    def remove(self, domain, value):
        try:
            subprocess.run([self.path, "remove", "_acme-challenge." + domain, value], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except (OSError, subprocess.SubprocessError):
            pass


class Cloudflare(object):
    name = "cloudflare"

    def __init__(self, token, base="https://api.cloudflare.com/client/v4"):
        if not token:
            raise AcmeError("Cloudflare needs an API token (Zone:DNS:Edit): put it in credentials.conf under [acme] as token = ...")
        self.base, self.headers, self.created = base.rstrip("/"), {"Authorization": "Bearer " + token}, {}

    def api(self, method, path, data=None):
        code, text = call(self.base + path, method, self.headers, data)
        try:
            body = json.loads(text)
        except ValueError:
            body = {}
        if code >= 400 or not body.get("success", False):
            msg = "; ".join(e.get("message", "") for e in body.get("errors", [])) or text[:150]
            raise AcmeError("Cloudflare refused the request (%s): %s" % (code, msg))
        return body

    def zone_for(self, domain):
        parts = domain.split(".")
        for i in range(len(parts) - 1):
            cand = ".".join(parts[i:])
            res = self.api("GET", "/zones?name=%s" % quote(cand)).get("result") or []
            if res:
                return res[0]["id"]
        raise AcmeError("no Cloudflare zone found for %s: is the domain in this Cloudflare account and does the token cover it?" % domain)

    def add(self, domain, value):
        zone = self.zone_for(domain)
        rec = self.api("POST", "/zones/%s/dns_records" % zone, {"type": "TXT", "name": "_acme-challenge." + domain, "content": value, "ttl": 60})
        self.created[(domain, value)] = (zone, rec["result"]["id"])

    def remove(self, domain, value):
        found = self.created.pop((domain, value), None)
        if found:
            try:
                self.api("DELETE", "/zones/%s/dns_records/%s" % found)
            except AcmeError:
                pass


class DuckDNS(object):
    """Free sub-domains such as lrsvitals.duckdns.org. The domain may point at a private address."""
    name = "duckdns"

    def __init__(self, token, base="https://www.duckdns.org"):
        if not token:
            raise AcmeError("DuckDNS needs your token: put it in credentials.conf under [acme] as token = ...")
        self.token, self.base = token, base.rstrip("/")

    def sub(self, domain):
        if not domain.endswith(".duckdns.org"):
            raise AcmeError("the DuckDNS provider only works for names ending in .duckdns.org (not %s)" % domain)
        return domain[:-len(".duckdns.org")].split(".")[-1]

    def update(self, domain, extra):
        code, text = call("%s/update?%s" % (self.base, urlencode(dict({"domains": self.sub(domain), "token": self.token, "verbose": "true"}, **extra))))
        if code != 200 or not text.strip().startswith("OK"):
            raise AcmeError("DuckDNS refused the update (%s): %s" % (code, text.strip()[:100]))

    def add(self, domain, value):
        self.update(domain, {"txt": value})

    def remove(self, domain, value):
        try:
            self.update(domain, {"txt": value, "clear": "true"})
        except AcmeError:
            pass


def provider(name, token="", hook="", api=""):
    name = (name or "hook").lower()
    if name == "cloudflare":
        return Cloudflare(token, api) if api else Cloudflare(token)
    if name == "duckdns":
        return DuckDNS(token, api) if api else DuckDNS(token)
    if name == "hook":
        if not hook:
            raise AcmeError("the DNS check needs acme_dns_provider (cloudflare or duckdns) with a token, or acme_dns_hook with your own script")
        return Hook(hook)
    raise AcmeError("unknown DNS provider '%s' (use cloudflare, duckdns or hook)" % name)
