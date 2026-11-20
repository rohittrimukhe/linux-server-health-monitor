# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Compressed, indexed report archive.

Old reports are compressed one file at a time (report.html -> report.html.gz), exactly like rotated Linux logs, so that
`zcat`, `zless`, `zgrep` and `gunzip` work on them directly and nothing has to be unpacked first. LRS Vitals itself reads
the compressed files transparently: the web dashboard, the report list, e-mail attachments and the `vitals archive` commands
(cat, tail, grep, search, email) show and send the original file without leaving an unpacked copy behind. A small SQLite
index (with full-text search when SQLite has it) lists every report file and its text so searching is instant."""
import gzip
import io
import os
import re
import shutil
import sqlite3
import tarfile
import time

SKIP_EXT = (".gz", ".xlsx", ".pptx", ".zip", ".bz2", ".xz", ".zst", ".png", ".jpg", ".jpeg", ".tgz")
TEXT_EXT = (".txt", ".csv", ".json", ".md", ".log", ".html", ".htm")
MAX_INDEX_BYTES = 600 * 1024
STAMP = re.compile(r"^(\d{8})_(\d{6})")


# -- compressed or plain, transparently --------------------------------------------------------------------
def real_path(path):
    """The file that exists for this name: the plain file, or the same name with .gz."""
    if os.path.isfile(path):
        return path
    return path + ".gz" if os.path.isfile(path + ".gz") else path


def display_name(name):
    return name[:-3] if name.endswith(".gz") else name


def open_binary(path):
    real = real_path(path)
    return gzip.open(real, "rb") if real.endswith(".gz") else open(real, "rb")


def read_bytes(path, limit=None):
    with open_binary(path) as f:
        return f.read() if limit is None else f.read(limit)


def read_text(path, limit=None):
    return read_bytes(path, limit).decode("utf-8", "replace")


def original_size(path):
    """Size of the file when unpacked (from the gzip trailer; exact below 4 GB)."""
    real = real_path(path)
    if not real.endswith(".gz"):
        return os.path.getsize(real)
    with open(real, "rb") as f:
        f.seek(-4, 2)
        return int.from_bytes(f.read(4), "little")


def list_files(d):
    """[(original name, stored size, original size, compressed?)] of a report folder."""
    seen, out = set(), []
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if not os.path.isfile(p) or os.path.islink(p) or f.startswith(".") or f.endswith(".tmp"):
            continue
        name = display_name(f)
        if name in seen:
            continue
        seen.add(name)
        out.append((name, os.path.getsize(p), original_size(p), f.endswith(".gz")))
    return out


def catalog(d):
    """Every file of a report, on disk or rebuildable from the stored source:
    [{'name', 'format', 'size', 'stored', 'compressed', 'on_disk', 'rebuild'}] sorted by name."""
    from lshme.reports import snapshot
    src = snapshot.entries(d)
    out = {}
    for name, stored, size, gz in list_files(d):
        out[name] = {"name": name, "format": name.rsplit(".", 1)[-1].lower(), "size": size, "stored": stored, "compressed": gz,
                     "on_disk": True, "rebuild": name in src}
    for name, e in src.items():
        if name not in out:
            out[name] = {"name": name, "format": e.get("format", ""), "size": e.get("size", 0), "stored": 0, "compressed": False,
                         "on_disk": False, "rebuild": True}
    return [out[k] for k in sorted(out)]


def materialize(d, name):
    """The bytes of a report file: the stored file when it is still there (plain or .gz), otherwise rebuilt from the report's
    source exactly as it was produced. Returns (bytes, 'stored' | 'rebuilt'). Raises ValueError when neither is possible."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("unknown file")
    name = display_name(name)
    p = os.path.join(d, name)
    real = real_path(p)
    if os.path.isfile(real) and not os.path.islink(real):
        return read_bytes(real), "stored"
    from lshme.reports import snapshot
    data, identical = snapshot.rebuild(d, name)
    # 'rebuilt': byte for byte the file that was sent (fingerprint checked). 'rebuilt-changed': same data, but a later version
    # of LRS Vitals lays the file out differently; retention never removes a file unless it rebuilds identically.
    return data, "rebuilt" if identical else "rebuilt-changed"


# -- compression ---------------------------------------------------------------------------------------------
def compressible(name):
    return not name.lower().endswith(SKIP_EXT) and not name.startswith(".")


def compress_file(path):
    """name -> name.gz with the same modification time; returns the bytes saved. The original is removed only after the new file is verified."""
    st = os.stat(path)
    gz, tmp = path + ".gz", path + ".gz.tmp"
    with open(path, "rb") as src, open(tmp, "wb") as raw:
        with gzip.GzipFile(filename=os.path.basename(path), mode="wb", fileobj=raw, compresslevel=6, mtime=int(st.st_mtime)) as out:
            shutil.copyfileobj(src, out, 1 << 20)
    with gzip.open(tmp, "rb") as chk:                           # read it back: the number of bytes must match
        total = 0
        while True:
            block = chk.read(1 << 20)
            if not block:
                break
            total += len(block)
    if total != st.st_size:
        os.remove(tmp)
        raise IOError("verification of %s failed" % path)
    os.chmod(tmp, st.st_mode & 0o777)
    os.utime(tmp, (st.st_atime, st.st_mtime))
    os.replace(tmp, gz)
    os.remove(path)
    return max(0, st.st_size - os.path.getsize(gz))


def needs_compress(d):
    return any(os.path.isfile(os.path.join(d, f)) and compressible(f) for f in os.listdir(d))


def compress_dir(d):
    """Compress every compressible file of a report folder. Returns (files compressed, bytes saved)."""
    n, saved = 0, 0
    st = os.stat(d)
    try:
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if os.path.isfile(p) and compressible(f):
                saved += compress_file(p)
                n += 1
    finally:
        os.utime(d, (st.st_atime, st.st_mtime))           # the folder keeps its age, so retention still deletes it on time
    return n, saved


def convert_tarball(tgz):
    """An old folder-wide <stamp>.tar.gz becomes a folder of individually compressed files again (easy to read, grep and mail)."""
    d = tgz[:-len(".tar.gz")]
    mt = os.path.getmtime(tgz)
    os.makedirs(d, exist_ok=True)
    with tarfile.open(tgz, "r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            name = os.path.basename(m.name)
            if not name or name.startswith("."):
                continue
            data = tar.extractfile(m).read()
            target = os.path.join(d, name)
            if compressible(name):
                with open(target + ".gz", "wb") as raw:
                    with gzip.GzipFile(filename=name, mode="wb", fileobj=raw, compresslevel=6, mtime=int(m.mtime or mt)) as out:
                        out.write(data)
                os.utime(target + ".gz", (mt, mt))
            else:
                with open(target, "wb") as f:
                    f.write(data)
    os.remove(tgz)
    os.utime(d, (mt, mt))
    return d


def compress_old(cfg, paths, days=None, now=None):
    """Compress report folders older than the retention setting. Returns {'folders', 'files', 'saved'}."""
    now = now or time.time()
    days = float(cfg.get("retention", "retention", "compress_after_days", "1")) if days is None else days
    out = {"folders": 0, "files": 0, "saved": 0}
    root = paths.report_dir
    if not os.path.isdir(root):
        return out
    keep_latest = cfg.getbool("retention", "retention", "keep_latest", True)
    for host in os.listdir(root):
        for cat in (os.listdir(os.path.join(root, host)) if os.path.isdir(os.path.join(root, host)) else []):
            cdir = os.path.join(root, host, cat)
            if not os.path.isdir(cdir):
                continue
            items = sorted(os.listdir(cdir))
            latest = items[-1] if items else None
            for it in items:
                p = os.path.join(cdir, it)
                if keep_latest and it == latest:
                    continue
                if it.endswith(".tar.gz") and os.path.isfile(p):
                    p = convert_tarball(p)
                    out["folders"] += 1
                if os.path.isdir(p) and (now - os.path.getmtime(p)) / 86400.0 > days and needs_compress(p):
                    n, saved = compress_dir(p)
                    out["folders"] += 1
                    out["files"] += n
                    out["saved"] += saved
    return out


# -- finding a report ------------------------------------------------------------------------------------------
def report_dirs(paths):
    """[(rid, dir, host, category, stamp, ts)] newest first."""
    root = paths.report_dir
    out = []
    if not os.path.isdir(root):
        return out
    for host in sorted(os.listdir(root)):
        hp = os.path.join(root, host)
        for cat in (sorted(os.listdir(hp)) if os.path.isdir(hp) else []):
            cp = os.path.join(hp, cat)
            for stamp in (os.listdir(cp) if os.path.isdir(cp) else []):
                d = os.path.join(cp, stamp)
                if os.path.isdir(d):
                    m = STAMP.match(stamp)
                    try:
                        ts = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")) if m else os.path.getmtime(d)
                    except (ValueError, OverflowError):
                        ts = os.path.getmtime(d)
                    out.append(("%s/%s/%s" % (host, cat, stamp), d, host, cat, stamp, ts))
    return sorted(out, key=lambda r: -r[5])


def resolve(paths, text):
    """'host/category/stamp', 'latest', 'latest:daily', a unique start of a stamp, or a folder path -> (rid, dir)."""
    text = (text or "").strip().strip("/")
    rows = report_dirs(paths)
    if text in ("", "latest") or text.startswith("latest:"):
        cat = text.split(":", 1)[1] if ":" in text else ""
        rows = [r for r in rows if not cat or r[3] == cat]
        if not rows:
            raise ValueError("no report found")
        return rows[0][0], rows[0][1]
    exact = [r for r in rows if r[0] == text or r[1].rstrip("/") == text]
    if exact:
        return exact[0][0], exact[0][1]
    part = [r for r in rows if text in r[0]]
    if len(part) == 1:
        return part[0][0], part[0][1]
    if not part:
        raise ValueError("no report matches '%s' (try: vitals archive list)" % text)
    raise ValueError("'%s' matches %d reports; be more specific (for example host/category/stamp)" % (text, len(part)))


def pick_file(d, name=None, prefer=("txt", "json", "csv", "html")):
    files = [f["name"] for f in catalog(d)]
    if name:
        match = [f for f in files if f == name or f.endswith(name) or name in f]
        if not match:
            raise ValueError("no file '%s' in that report (has: %s)" % (name, ", ".join(files)))
        return os.path.join(d, match[0])
    for ext in prefer:
        for f in files:
            if f.endswith("." + ext):
                return os.path.join(d, f)
    raise ValueError("this report has no text file; ask for one by name (has: %s)" % ", ".join(files))


def tail_lines(path, n=20):
    """The last n lines of a (possibly compressed) text file, reading it as a stream."""
    from collections import deque
    last = deque(maxlen=max(1, n))
    if os.path.isfile(real_path(path)):
        with open_binary(path) as f:
            for line in io.TextIOWrapper(f, encoding="utf-8", errors="replace"):
                last.append(line.rstrip("\n"))
    else:                                                     # removed by retention: rebuilt from the report's source
        for line in materialize(os.path.dirname(path), os.path.basename(path))[0].decode("utf-8", "replace").splitlines():
            last.append(line)
    return list(last)


def grep(paths, pattern, host="", category="", exts=(".txt", ".json", ".csv"), limit=200, ignore_case=True):
    """Search the text files of archived reports without unpacking them. Yields (rid, file, line number, line)."""
    rx = re.compile(pattern, re.I if ignore_case else 0)
    n = 0
    for rid, d, h, c, stamp, ts in report_dirs(paths):
        if (host and h != host) or (category and c != category):
            continue
        for name, _, _, _ in list_files(d):
            if not name.lower().endswith(exts):
                continue
            with open_binary(os.path.join(d, name)) as f:
                for i, line in enumerate(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), 1):
                    if rx.search(line):
                        yield rid, name, i, line.rstrip("\n")[:300]
                        n += 1
                        if n >= limit:
                            return


# -- the index ----------------------------------------------------------------------------------------------------
TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)


def plain_text(name, data):
    text = data.decode("utf-8", "replace")
    if name.lower().endswith((".html", ".htm")):
        text = re.sub(r"\s+", " ", TAGS.sub(" ", text))
    return text


class Index(object):
    """SQLite list of every report file, with its text for searching (FTS4 when available, else a plain scan)."""

    def __init__(self, paths):
        self.paths = paths
        self.path = os.path.join(paths.data_dir, "reports_index.sqlite3")
        os.makedirs(paths.data_dir, exist_ok=True)
        self.con = sqlite3.connect(self.path, timeout=30)
        self.con.executescript("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, rel TEXT UNIQUE, rid TEXT, host TEXT, category TEXT, stamp TEXT, ts REAL,"
                               " name TEXT, size INTEGER, stored INTEGER, gz INTEGER);"
                               "CREATE INDEX IF NOT EXISTS ix_files_rid ON files(rid); CREATE INDEX IF NOT EXISTS ix_files_ts ON files(ts);")
        try:
            self.con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS body USING fts4(text)")
            self.fts = True
        except sqlite3.OperationalError:
            self.con.execute("CREATE TABLE IF NOT EXISTS body (docid INTEGER PRIMARY KEY, text TEXT)")
            self.fts = False

    def close(self):
        self.con.close()

    def update_dir(self, d):
        rid = os.path.relpath(d, self.paths.report_dir).replace(os.sep, "/")
        parts = rid.split("/")
        if len(parts) != 3 or not os.path.isdir(d):
            return 0
        host, cat, stamp = parts
        m = STAMP.match(stamp)
        try:
            ts = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")) if m else os.path.getmtime(d)
        except (ValueError, OverflowError):
            ts = os.path.getmtime(d)
        live = set()
        for f in catalog(d):                     # files on disk and files that can be rebuilt from the report's source
            name, stored, size, gz = f["name"], f["stored"], f["size"], f["compressed"]
            rel = "%s/%s" % (rid, name)
            live.add(rel)
            row = self.con.execute("SELECT id FROM files WHERE rel=?", (rel,)).fetchone()
            if row:
                self.con.execute("UPDATE files SET size=?, stored=?, gz=? WHERE id=?", (size, stored, 1 if gz else 0, row[0]))
                continue
            cur = self.con.execute("INSERT INTO files(rel, rid, host, category, stamp, ts, name, size, stored, gz) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                   (rel, rid, host, cat, stamp, ts, name, size, stored, 1 if gz else 0))
            if name.lower().endswith(TEXT_EXT):
                try:
                    raw = read_bytes(os.path.join(d, name), MAX_INDEX_BYTES) if f["on_disk"] else materialize(d, name)[0][:MAX_INDEX_BYTES]
                    text = plain_text(name, raw)
                except (OSError, IOError, EOFError, ValueError):
                    text = ""
                self.con.execute("INSERT INTO body(docid, text) VALUES(?,?)", (cur.lastrowid, text))
        for rel, fid in self.con.execute("SELECT rel, id FROM files WHERE rid=?", (rid,)).fetchall():
            if rel not in live:
                self.con.execute("DELETE FROM files WHERE id=?", (fid,))
                self.con.execute("DELETE FROM body WHERE docid=?", (fid,))
        self.con.commit()
        return len(live)

    def forget(self, d):
        """The report folder was deleted or moved away."""
        rid = os.path.relpath(d, self.paths.report_dir).replace(os.sep, "/")
        for (fid,) in self.con.execute("SELECT id FROM files WHERE rid=?", (rid,)).fetchall():
            self.con.execute("DELETE FROM body WHERE docid=?", (fid,))
        self.con.execute("DELETE FROM files WHERE rid=?", (rid,))
        self.con.commit()

    def rebuild(self):
        self.con.execute("DELETE FROM files")
        self.con.execute("DELETE FROM body")
        n = 0
        for rid, d, *_ in report_dirs(self.paths):
            n += self.update_dir(d)
        return n

    def sync(self):
        """Bring the index in step with the disk: add new reports, drop deleted ones."""
        live = set()
        for rid, d, *_ in report_dirs(self.paths):
            live.add(rid)
            self.update_dir(d)
        for (rid,) in self.con.execute("SELECT DISTINCT rid FROM files").fetchall():
            if rid not in live:
                for (fid,) in self.con.execute("SELECT id FROM files WHERE rid=?", (rid,)).fetchall():
                    self.con.execute("DELETE FROM body WHERE docid=?", (fid,))
                self.con.execute("DELETE FROM files WHERE rid=?", (rid,))
        self.con.commit()

    def stats(self):
        r = self.con.execute("SELECT COUNT(*), COUNT(DISTINCT rid), COALESCE(SUM(size),0), COALESCE(SUM(stored),0), COALESCE(SUM(gz),0) FROM files").fetchone()
        return {"files": r[0], "reports": r[1], "original": r[2], "stored": r[3], "compressed_files": r[4], "fts": self.fts}

    def listing(self, host="", category="", limit=50):
        q, args = "SELECT rid, host, category, stamp, ts, COUNT(*), SUM(size), SUM(stored), SUM(gz) FROM files WHERE 1=1", []
        for col, val in (("host", host), ("category", category)):
            if val:
                q += " AND %s=?" % col
                args.append(val)
        q += " GROUP BY rid ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [dict(zip(("rid", "host", "category", "stamp", "ts", "files", "size", "stored", "compressed"), r)) for r in self.con.execute(q, args).fetchall()]

    def search(self, text, limit=30, host="", category=""):
        """[{'rid','name','ts','snippet'}] for the words in text (all must appear)."""
        words = re.findall(r"[A-Za-z0-9_./:-]{2,}", text or "")
        if not words:
            return []
        extra, args = "", []
        for col, val in (("f.host", host), ("f.category", category)):
            if val:
                extra += " AND %s=?" % col
                args.append(val)
        if self.fts:
            match = " ".join('"%s"' % w.replace('"', "") for w in words)
            sql = ("SELECT f.rid, f.name, f.ts, snippet(body, '[', ']', ' ... ', -1, 14) FROM body JOIN files f ON f.id = body.docid "
                   "WHERE body MATCH ?" + extra + " ORDER BY f.ts DESC LIMIT ?")
            try:
                rows = self.con.execute(sql, [match] + args + [limit]).fetchall()
            except sqlite3.OperationalError:
                rows = []
            return [{"rid": r[0], "name": r[1], "ts": r[2], "snippet": r[3]} for r in rows]
        like = " AND ".join("body.text LIKE ?" for _ in words)
        rows = self.con.execute("SELECT f.rid, f.name, f.ts, substr(body.text, 1, 160) FROM body JOIN files f ON f.id = body.docid WHERE " + like + extra +
                                " ORDER BY f.ts DESC LIMIT ?", ["%" + w + "%" for w in words] + args + [limit]).fetchall()
        return [{"rid": r[0], "name": r[1], "ts": r[2], "snippet": r[3]} for r in rows]


def email_report(app, d, to=None, names=None):
    """E-mail a stored (possibly compressed) report: files go out under their original names, unpacked in memory only.
    to: list of addresses (default: the configured recipients). names: file names or extensions (default: the attach formats)."""
    import json
    from lshme.delivery import mailer
    ms = mailer.MailSettings(app.cfg)
    problems = [p for p in ms.validate() if not (to and "recipients" in p)]
    if problems:
        return {"ok": False, "error": "; ".join(problems), "attached": []}
    listing = [f["name"] for f in catalog(d)]
    if names:
        want = [n.strip().lstrip(".").lower() for n in names if n and n.strip()]
        pick = [n for n in listing if n.lower() in want or n.rsplit(".", 1)[-1].lower() in want]
        ms.attach_formats = ["all"]
    else:
        pick = listing
    if not pick:
        return {"ok": False, "error": "no matching files in this report (has: %s)" % ", ".join(listing), "attached": []}
    rid = os.path.relpath(d, app.paths.report_dir).replace(os.sep, "/")
    host, cat, stamp = (rid.split("/") + ["", "", ""])[:3]
    status = ""
    jname = next((n for n in listing if n.endswith(".json")), None)
    if jname:
        try:
            doc = json.loads(materialize(d, jname)[0].decode("utf-8", "replace"))
            status = (doc.get("health") or {}).get("status", "") or doc.get("status", "")
        except (ValueError, OSError, EOFError, AttributeError):
            status = ""
    subject = "%s Stored %s report - %s - %s" % (ms.prefix, cat, host, stamp)
    text = "Stored health report\nHost: %s\nReport: %s\nTaken: %s\n%sFiles: %s\n" % (
        host, cat, stamp, ("Status: %s\n" % status) if status else "", ", ".join(pick))
    files, how = [], {}
    for n in pick:
        ext = n.rsplit(".", 1)[-1].lower()
        if ms.attach_formats and ext not in ms.attach_formats and "all" not in ms.attach_formats:
            continue
        try:
            data, how[n] = materialize(d, n)
        except ValueError:
            continue
        files.append((n, data))
    if any(v != "stored" for v in how.values()):
        text += "Rebuilt from the stored report data: %s\n" % ", ".join(n for n, v in sorted(how.items()) if v != "stored")
    msg, recipients, attached, skipped = mailer.build_message(ms, subject, text, None, files, status or "HEALTHY", to, None if to else ms.cc)
    if not attached:
        return {"ok": False, "error": "nothing could be attached: %s" % ("; ".join(skipped) or "file types not allowed"), "attached": []}
    res = mailer.send(ms, msg, recipients, app.paths.log_dir)
    res["attached"], res["skipped_attachments"] = attached, skipped
    res["rebuilt"] = sorted(n for n, v in how.items() if v != "stored" and n in attached)
    return res


def refresh(paths, d=None, gone=None, full=False):
    """Keep the index in step with the disk; never lets an index problem break a report run."""
    try:
        ix = Index(paths)
        try:
            if full:
                ix.sync()
            if gone:
                ix.forget(gone)
            if d:
                ix.update_dir(d)
        finally:
            ix.close()
    except Exception:
        pass
