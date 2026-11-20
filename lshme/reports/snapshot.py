# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The source of a report, kept so that any report file can be rebuilt exactly as it was sent.

Next to every report a hidden file .source.json.gz holds the complete report data as it was when the report was written
(the report document with every table, chart, colour and note, the health-check result, the logos as bytes) together with
a SHA-256 fingerprint of each file that was produced. When retention has removed a PDF, Excel, HTML, PowerPoint, CSV or text
file, it is rebuilt from this source with the same code, the same time stamps and the same logos, and its fingerprint is
checked against the original. The history database is not used for this: it is thinned and expires, the source does not."""
import base64
import gzip
import hashlib
import importlib
import json
import os
import shutil
import tempfile

SOURCE = ".source.json.gz"
FORMAT_VERSION = 1


# -- lossless encoding of report objects -------------------------------------------------------------------------------
def encode(o):
    if o is None or isinstance(o, (bool, int, float, str)):
        return o
    if isinstance(o, (list,)):
        return [encode(x) for x in o]
    if isinstance(o, tuple):
        return {"__t": [encode(x) for x in o]}
    if isinstance(o, (set, frozenset)):
        return {"__s": [encode(x) for x in sorted(o, key=repr)]}
    if isinstance(o, bytes):
        return {"__b": base64.b64encode(o).decode("ascii")}
    if isinstance(o, dict):
        if all(isinstance(k, str) for k in o) and not ("__t" in o or "__s" in o or "__b" in o or "__c" in o or "__d" in o):
            return {k: encode(v) for k, v in o.items()}
        return {"__d": [[encode(k), encode(v)] for k, v in o.items()]}
    cls = type(o)
    if not cls.__module__.startswith("lshme."):
        raise TypeError("cannot keep %r in a report source" % cls)
    state = {}
    for k in getattr(cls, "__slots__", ()) or ():
        if hasattr(o, k):
            state[k] = getattr(o, k)
    state.update(getattr(o, "__dict__", {}))
    return {"__c": "%s:%s" % (cls.__module__, cls.__qualname__), "s": {k: encode(v) for k, v in state.items()}}


def decode(o):
    if isinstance(o, list):
        return [decode(x) for x in o]
    if not isinstance(o, dict):
        return o
    if "__t" in o:
        return tuple(decode(x) for x in o["__t"])
    if "__s" in o:
        return set(decode(x) for x in o["__s"])
    if "__b" in o:
        return base64.b64decode(o["__b"])
    if "__d" in o:
        return {decode(k): decode(v) for k, v in o["__d"]}
    if "__c" in o:
        mod, name = o["__c"].split(":", 1)
        if not mod.startswith("lshme."):
            raise ValueError("unexpected class in report source")
        cls = importlib.import_module(mod)
        for part in name.split("."):
            cls = getattr(cls, part)
        obj = cls.__new__(cls)
        for k, v in o["s"].items():
            object.__setattr__(obj, k, decode(v))
        return obj
    return {k: decode(v) for k, v in o.items()}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _owner(name, base, docs):
    """Index of the document that produced a file (monthly has a summary and a comprehensive document)."""
    best, best_len = 0, -1
    for i, d in enumerate(docs):
        suffix = d.basename_suffix() if hasattr(d, "basename_suffix") else ""
        if name.startswith(base + suffix) and len(suffix) > best_len:
            best, best_len = i, len(suffix)
    return best


# -- writing ----------------------------------------------------------------------------------------------------------------
def save(outdir, base, kind, docs, files, logo="", run=None, runs=None, extra=None):
    """Store the source of the report files just written. kind: 'periodic' or 'standard'. Returns the path or ''."""
    from lshme import VERSION
    from lshme.core.util import redact_obj          # passwords never go into the source; a file that would differ is simply kept
    logos = {}
    for d in docs:
        for p in (getattr(d, "brand_logo", ""), getattr(d, "customer_logo", "")):
            if p and os.path.isfile(p) and p not in logos:
                with open(p, "rb") as f:
                    logos[p] = base64.b64encode(f.read()).decode("ascii")
    if logo and os.path.isfile(logo) and logo not in logos:
        with open(logo, "rb") as f:
            logos[logo] = base64.b64encode(f.read()).decode("ascii")
    entries = {}
    for p in files:
        name = os.path.basename(p)
        if not os.path.isfile(p) or name.startswith("."):
            continue
        fmt = name.rsplit(".", 1)[-1].lower()
        entries[name] = {"format": fmt, "doc": _owner(name, base, docs), "sha256": sha256(p), "size": os.path.getsize(p)}
    data = {"format_version": FORMAT_VERSION, "app_version": VERSION, "kind": kind, "base": base,
            "created": docs[0].generated if docs else 0, "tz": os.environ.get("TZ", ""), "logo": logo or "", "logos": logos,
            "docs": [encode(d) for d in docs], "run": redact_obj(encode(run)) if run is not None else None,
            "runs": redact_obj(encode(runs)) if runs is not None else None, "files": entries, "extra": extra or {}}
    path = os.path.join(outdir, SOURCE)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(data, f, separators=(",", ":"))
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)
    return path


def load(d):
    p = os.path.join(d, SOURCE)
    if not os.path.isfile(p):
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def entries(d):
    """{file name: {'format', 'sha256', 'size', ...}} of the files this report can rebuild, or {}."""
    try:
        src = load(d)
    except (OSError, ValueError, EOFError):
        return {}
    return (src or {}).get("files", {})


# -- rebuilding --------------------------------------------------------------------------------------------------------------
def rebuild(d, name, src=None):
    """Rebuild one report file from the source. Returns (bytes, identical) where identical tells whether the fingerprint
    matches the file that was originally produced. Raises ValueError when the file cannot be rebuilt."""
    from lshme import reports
    src = src or load(d)
    if not src:
        raise ValueError("this report has no stored source, so a removed file cannot be rebuilt")
    if src.get("format_version", 0) > FORMAT_VERSION:
        raise ValueError("this report was written by a newer version of LRS Vitals")
    entry = src["files"].get(name)
    if not entry:
        raise ValueError("'%s' was not part of this report" % name)
    tmp = tempfile.mkdtemp(prefix="lrs-rebuild-")
    try:
        moved = {}
        for i, (orig, b64) in enumerate(sorted(src.get("logos", {}).items())):
            ld = os.path.join(tmp, "logo%d" % i)
            os.makedirs(ld)
            target = os.path.join(ld, os.path.basename(orig))
            with open(target, "wb") as f:
                f.write(base64.b64decode(b64))
            moved[orig] = target
        docs = [decode(x) for x in src["docs"]]
        for doc in docs:
            for attr in ("brand_logo", "customer_logo"):
                v = getattr(doc, attr, "")
                if v in moved:
                    setattr(doc, attr, moved[v])
        logo = moved.get(src.get("logo", ""), src.get("logo", ""))
        doc = docs[entry.get("doc", 0)]
        out = os.path.join(tmp, "out")
        os.makedirs(out)
        if src["kind"] == "periodic":
            files, notes = reports.write_periodic([doc], [entry["format"]], out, src["base"], logo)
        else:
            run = decode(src["run"]) if src.get("run") is not None else None
            runs = decode(src["runs"]) if src.get("runs") is not None else None
            files, notes = reports.write_all(doc, [entry["format"]], out, src["base"], run=run, runs=runs, logo=logo)
        path = os.path.join(out, name)
        if not os.path.isfile(path):
            raise ValueError("'%s' could not be rebuilt%s" % (name, (": " + "; ".join(notes)) if notes else ""))
        with open(path, "rb") as f:
            data = f.read()
        return data, hashlib.sha256(data).hexdigest() == entry.get("sha256")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
