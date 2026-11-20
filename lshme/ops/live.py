# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Standalone live monitoring page (`healthcheck live`) for servers without Cockpit.

Serves the same page as the Cockpit plugin plus a tiny read-only JSON API:
  /api/latest   latest full health check (secrets already masked)
  /api/watch    real-time watcher state (active problems)
  /api/metrics  rolling metric history for the live graphs
Only these paths and the page's static files are served; nothing can be
changed through it. It listens on 127.0.0.1 by default.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from lshme import BRAND_LOGO
from lshme.ops.cockpit_page import SRC

STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/lshme.js": ("lshme.js", "application/javascript"), "/charts.js": ("charts.js", "application/javascript"),
          "/lshme.css": ("lshme.css", "text/css")}
API = {"/api/latest": "latest.json", "/api/watch": "watch_state.json", "/api/metrics": "watch_metrics.json"}
CSP = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"


def make_server(data_dir, bind="127.0.0.1", port=8089, poll_seconds=15):
    config = json.dumps({"mode": "http", "poll_seconds": poll_seconds}).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "LSHME-live"
        sys_version = ""

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            try:
                if path in STATIC:
                    name, ctype = STATIC[path]
                    with open(os.path.join(SRC, name), "rb") as f:
                        return self._send(200, f.read(), ctype)
                if path == "/base1/cockpit.js":  # the page probes for Cockpit; answer with an empty script
                    return self._send(200, b"/* not running inside Cockpit */", "application/javascript")
                if path == "/config.json":
                    return self._send(200, config, "application/json")
                if path == "/lrs_logo.png" and os.path.isfile(BRAND_LOGO):
                    with open(BRAND_LOGO, "rb") as f:
                        return self._send(200, f.read(), "image/png")
                if path in API:
                    fpath = os.path.join(data_dir, API[path])
                    if not os.path.exists(fpath):
                        return self._send(200, b"null", "application/json")
                    with open(fpath, "rb") as f:
                        return self._send(200, f.read(), "application/json")
            except (IOError, OSError):
                return self._send(500, b"read error", "text/plain")
            return self._send(404, b"not found", "text/plain")

        do_HEAD = do_GET

        def log_message(self, *a):
            pass

    return HTTPServer((bind, int(port)), Handler)
