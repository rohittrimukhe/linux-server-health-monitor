# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Folder sizes for the growth reports.

Measures the configured top-level folders and their biggest sub-folders with
`du` (one filesystem only, low priority, cached) and records every size as a
metric so the daily, weekly and monthly reports can show growth by date.
"""
import json
import os
import time

from lshme.core.util import fmt_bytes, to_int

NAME = "folders"
TITLE = "Folder Sizes"
WEIGHT = 0

DEFAULT_ROOTS = "/var/log, /var/lib, /var/www, /home, /opt, /u01, /data, /backup, /srv, /tmp"


def settings(cfg):
    g = lambda key, default: cfg.get("main", "folders", key, default)
    roots = [r.strip().rstrip("/") or "/" for r in g("paths", DEFAULT_ROOTS).split(",") if r.strip()]
    return {"enabled": g("enabled", "yes").lower() in ("yes", "true", "1", "on"), "roots": roots,
            "top": to_int(g("top_per_root", "8"), 8), "timeout": to_int(g("timeout", "120"), 120),
            "cache_minutes": to_int(g("cache_minutes", "60"), 60)}


def du(ctx, root, timeout):
    """{path: bytes} for root and its direct children; empty when du fails entirely."""
    r = ctx.run(["du", "-x", "-B1", "-d1", root], timeout=timeout)
    out = {}
    for line in (r.out or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            out[parts[1].rstrip("/") or "/"] = int(parts[0])
    return out


def run(ctx, res):
    st = settings(ctx.cfg)
    if not st["enabled"]:
        res.skip("folder sizes are disabled ([folders] enabled = no)")
        return
    cache_path = os.path.join(ctx.paths.data_dir, "folders_cache.json")
    cache = {}
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except (IOError, OSError, ValueError):
        cache = {}
    now = time.time()
    rows, total_seen = [], 0
    for root in st["roots"]:
        if not os.path.isdir(root):
            continue
        hit = cache.get(root)
        if hit and now - hit.get("t", 0) < st["cache_minutes"] * 60:
            sizes = dict((k, int(v)) for k, v in hit["sizes"].items())
        else:
            sizes = du(ctx, root, st["timeout"])
            if sizes:
                cache[root] = {"t": now, "sizes": sizes}
        if not sizes:
            res.skipped.append("could not measure %s" % root)
            continue
        children = sorted(((p, b) for p, b in sizes.items() if p != root), key=lambda kv: -kv[1])[:st["top"]]
        top = [(root, sizes.get(root, sum(b for _, b in children)))] + children
        for path, size in top:
            res.metric("folder.bytes:" + path, size)
            rows.append([path, fmt_bytes(size), "root" if path == root else "sub-folder"])
            total_seen += 1
    try:
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, cache_path)
    except OSError:
        pass
    if not rows:
        res.skip("none of the configured folders exist ([folders] paths)")
        return
    res.table("Folder sizes", ["Folder", "Size", "Level"], rows, kind="summary")
    res.summary = "%d folders measured under %d roots" % (total_seen, len(set(r[0].split("/")[1] if r[0] != "/" else "" for r in rows)))
