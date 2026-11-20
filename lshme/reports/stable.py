# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Repeatable report files: the same report data always gives the same bytes.

A report file that was removed by retention is rebuilt from its stored source. To make the rebuilt file identical to
the one that was sent, nothing in a file may depend on the moment it is written: dates inside PDF, Excel and PowerPoint
files and the time stamps of the parts of an Office (zip) file come from the report's own creation time instead of the clock."""
import io
import re
import threading
import time
import zipfile
from contextlib import contextmanager

_local = threading.local()


def now():
    """The report's creation time while a report is being written, else the clock."""
    ts = getattr(_local, "ts", None)
    return time.time() if ts is None else ts


@contextmanager
def rendering(ts):
    old = getattr(_local, "ts", None)
    _local.ts = float(ts)
    try:
        yield
    finally:
        _local.ts = old


def zip_time(ts):
    t = time.localtime(ts)
    return (max(1980, t.tm_year), t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec - t.tm_sec % 2)


CORE_DATE = re.compile(r"(<dcterms:(created|modified)\b[^>]*>)[^<]*(</dcterms:\2>)")


def _normalize(src, dst, ts):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    with zipfile.ZipFile(src) as z:
        parts = [(i.filename, i.compress_type, z.read(i.filename)) for i in z.infolist()]
    dt = zip_time(ts)
    with zipfile.ZipFile(dst, "w") as out:
        for name, ctype, data in parts:
            if name == "docProps/core.xml":
                data = CORE_DATE.sub(lambda m: m.group(1) + stamp + m.group(3), data.decode("utf-8")).encode("utf-8")
            elif name.lower().endswith((".xlsx", ".pptx", ".docx")) and data[:2] == b"PK":
                buf = io.BytesIO()                         # a chart's embedded workbook carries its own dates
                try:
                    _normalize(io.BytesIO(data), buf, ts)
                    data = buf.getvalue()
                except zipfile.BadZipFile:
                    pass
            info = zipfile.ZipInfo(name, date_time=dt)
            info.compress_type = zipfile.ZIP_DEFLATED if ctype != zipfile.ZIP_STORED else zipfile.ZIP_STORED
            info.external_attr = 0o600 << 16
            info.create_system = 3
            out.writestr(info, data)


def normalize_zip(path, ts):
    """Rewrite an Office file so that its parts carry the report's time (Excel and PowerPoint libraries use the clock)."""
    buf = io.BytesIO()
    _normalize(path, buf, ts)
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    return path
