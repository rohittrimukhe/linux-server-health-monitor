# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Dependency-free PDF writer used when ReportLab is not installed.

Produces a clean multi-page A4 document with headings, wrapped paragraphs and
monospaced tables using the standard Helvetica/Courier fonts (no embedding).
"""
import struct
import time
import zlib

from lshme.reports import stable

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 42


def _esc(s):
    s = str(s).encode("cp1252", "replace").decode("cp1252")  # WinAnsiEncoding
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class MiniPDF(object):
    def __init__(self, title="Report", footer=""):
        self.title = title
        self.footer = footer
        self.pages = []
        self.images = []   # (name, width, height, colourspace, filter, data, alpha_data)
        self.wm_alpha = 0.08   # opacity of the washed-out watermark (graphics state GS1)
        self.ops = None
        self.y = 0
        self.new_page()

    def add_image(self, path):
        """Register a PNG or JPEG logo; returns (name, width, height) or None if it cannot be used."""
        try:
            img = load_image(path)
        except Exception:
            return None
        if not img:
            return None
        name = "Im%d" % (len(self.images) + 1)
        self.images.append((name,) + img)
        return name, img[0], img[1]

    def draw_image(self, name, x, y, w, h):
        self.ops.append("q %.1f 0 0 %.1f %.1f %.1f cm /%s Do Q" % (w, h, x, y, name))

    def new_page(self):
        self.ops = []
        self.pages.append(self.ops)
        self.y = PAGE_H - MARGIN

    def _need(self, h):
        if self.y - h < MARGIN + 20:
            self.new_page()

    def text(self, s, size=10, font="F1", x=MARGIN, color=(0, 0, 0)):
        self._need(size + 4)
        self.y -= size + 3
        self.ops.append("%.3f %.3f %.3f rg BT /%s %d Tf %.1f %.1f Td (%s) Tj ET" % (color + (font, size, x, self.y, _esc(s))))

    def rect(self, x, y, w, h, color):
        self.ops.append("%.3f %.3f %.3f rg %.1f %.1f %.1f %.1f re f" % (color + (x, y, w, h)))

    def wrap(self, s, size=10, font="F1", indent=0, color=(0, 0, 0)):
        width = PAGE_W - 2 * MARGIN - indent
        per_char = size * (0.6 if font == "F3" else 0.5)
        maxc = max(10, int(width / per_char))
        for para in str(s).split("\n"):
            words, line = para.split(" "), ""
            for w in words:
                if len(line) + len(w) + 1 > maxc:
                    self.text(line, size, font, MARGIN + indent, color)
                    line = w
                    while len(line) > maxc:
                        self.text(line[:maxc], size, font, MARGIN + indent, color)
                        line = line[maxc:]
                else:
                    line = (line + " " + w) if line else w
            self.text(line, size, font, MARGIN + indent, color)

    def heading(self, s, size=14, color=(0.04, 0.24, 0.43)):
        self._need(size + 18)
        self.y -= 8
        self.text(s, size, "F2", color=color)
        self.ops.append("0.8 0.84 0.88 RG 0.8 w %.1f %.1f m %.1f %.1f l S" % (MARGIN, self.y - 3, PAGE_W - MARGIN, self.y - 3))
        self.y -= 4

    def table(self, headers, rows, size=7):
        usable = PAGE_W - 2 * MARGIN
        maxc = int(usable / (size * 0.6))
        n = len(headers)
        widths = [len(str(h)) for h in headers]
        for r in rows[:500]:
            for i in range(min(n, len(r))):
                widths[i] = max(widths[i], min(len(str(r[i])), 50))
        total = sum(widths) + 2 * n
        if total > maxc:
            scale = float(maxc - 2 * n) / sum(widths)
            widths = [max(4, int(w * scale)) for w in widths]

        def fmt(r):
            cells = [str(r[i]) if i < len(r) else "" for i in range(n)]
            return "  ".join(c.replace("\n", " ")[:w].ljust(w) for c, w in zip(cells, widths))
        self._need(size * 3)
        self.rect(MARGIN - 2, self.y - size - 5, usable + 4, size + 4, (0.92, 0.94, 0.97))
        self.text(fmt(headers), size, "F4")
        for r in rows:
            self.text(fmt(r), size, "F3")
        self.y -= 4

    def save(self, path):
        objs = []

        def add(body):
            objs.append(body)
            return len(objs)
        catalog = add(None)
        pages_id = add(None)
        fonts = [add("<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % f)
                 for f in ("Helvetica", "Helvetica-Bold", "Courier", "Courier-Bold")]
        xobj = ""
        for name, w, h, cs, flt, data, alpha in self.images:
            smask = ""
            if alpha is not None:
                a_id = add("<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n%s\nendstream" % (
                    w, h, len(alpha), alpha.decode("latin-1")))
                smask = " /SMask %d 0 R" % a_id
            i_id = add("<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /%s /BitsPerComponent 8 /Filter /%s%s /Length %d >>\nstream\n%s\nendstream" % (
                w, h, cs, flt, smask, len(data), data.decode("latin-1")))
            xobj += "/%s %d 0 R " % (name, i_id)
        res = "<< /Font << /F1 %d 0 R /F2 %d 0 R /F3 %d 0 R /F4 %d 0 R >> /ExtGState << /GS1 << /ca %.2f /CA %.2f >> >> %s >>" % (
            tuple(fonts) + (self.wm_alpha, self.wm_alpha) + (("/XObject << %s>>" % xobj) if xobj else "",))
        kids = []
        total = len(self.pages)
        for i, ops in enumerate(self.pages, 1):
            pg = "Page %d of %d" % (i, total)
            foot = ("0.87 0.9 0.93 RG 0.5 w %.1f 36 m %.1f 36 l S 0.4 0.4 0.4 rg BT /F1 8 Tf %.1f 24 Td (%s) Tj ET BT /F1 8 Tf %.1f 24 Td (%s) Tj ET" % (
                MARGIN, PAGE_W - MARGIN, MARGIN, _esc(self.footer), PAGE_W - MARGIN - len(pg) * 4.45, pg))
            stream = "\n".join(ops + [foot]).encode("cp1252", "replace")
            content = add("<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream.decode("latin-1")))
            kids.append(add("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Resources %s /Contents %d 0 R >>" % (
                pages_id, PAGE_W, PAGE_H, res, content)))
        objs[catalog - 1] = "<< /Type /Catalog /Pages %d 0 R >>" % pages_id
        objs[pages_id - 1] = "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids), len(kids))
        info = add("<< /Title (%s) /Producer (LSHME) /CreationDate (D:%s) >>" % (_esc(self.title), time.strftime("%Y%m%d%H%M%S", time.localtime(stable.now()))))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += ("%d 0 obj\n%s\nendobj\n" % (i, body)).encode("latin-1", "replace")
        xref = len(out)
        out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode()
        for o in offsets:
            out += ("%010d 00000 n \n" % o).encode()
        out += ("trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, info, xref)).encode()
        with open(path, "wb") as f:
            f.write(bytes(out))
        return path


def load_image(path):
    """(width, height, colourspace, filter, data, alpha) for a JPEG or an 8-bit PNG, else None."""
    with open(path, "rb") as f:
        return load_image_bytes(f.read())


def load_image_bytes(raw):
    """Same as load_image, for the bytes of an image."""
    if raw[:2] == bytes([0xFF, 0xD8]):          # JPEG: embedded as is
        i = 2
        while i < len(raw) - 9:
            if raw[i] != 0xFF:
                i += 1
                continue
            marker = raw[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                comps = raw[i + 9]
                return w, h, {1: "DeviceGray", 3: "DeviceRGB"}.get(comps, "DeviceRGB"), "DCTDecode", raw, None
            i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
        return None
    if raw[:8] != bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]):
        return None
    pos, idat, plte, trns, hdr = 8, b"", b"", b"", None
    while pos < len(raw):
        n, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        body = raw[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        pos += 12 + n
    if not hdr:
        return None
    w, h, depth, ctype, _, _, interlace = hdr
    if depth != 8 or interlace or ctype not in (0, 2, 3, 6) or w * h > 700000:
        return None
    bpp = {0: 1, 2: 3, 3: 1, 6: 4}[ctype]
    data = zlib.decompress(idat)
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    p = 0
    for _ in range(h):
        ft = data[p]
        line = bytearray(data[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        rows.append(line)
        prev = line
    alpha = None
    if ctype == 6:
        rgb, al = bytearray(), bytearray()
        for line in rows:
            rgb += b"".join(bytes(line[i:i + 3]) for i in range(0, stride, 4))
            al += bytes(line[3::4])
        cs, pix, alpha = "DeviceRGB", bytes(rgb), zlib.compress(bytes(al))
    elif ctype == 3:
        pal = [plte[i:i + 3] for i in range(0, len(plte), 3)]
        tr = list(trns) + [255] * (len(pal) - len(trns))
        rgb, al = bytearray(), bytearray()
        has_alpha = bool(trns)
        for line in rows:
            for v in line:
                rgb += pal[v] if v < len(pal) else b"\x00\x00\x00"
                if has_alpha:
                    al.append(tr[v] if v < len(tr) else 255)
        cs, pix = "DeviceRGB", bytes(rgb)
        alpha = zlib.compress(bytes(al)) if has_alpha else None
    elif ctype == 2:
        cs, pix = "DeviceRGB", b"".join(bytes(r) for r in rows)
    else:
        cs, pix = "DeviceGray", b"".join(bytes(r) for r in rows)
    return w, h, cs, "FlateDecode", zlib.compress(pix), alpha


SEV_RGB = {"CRITICAL": (0.78, 0.16, 0.16), "WARNING": (0.85, 0.5, 0.0), "UNKNOWN": (0.4, 0.4, 0.4),
           "INFO": (0.08, 0.4, 0.75), "HEALTHY": (0.18, 0.49, 0.2)}


def render(doc, path):
    p = MiniPDF(doc.title, "%s - %s - %s" % (doc.subtitle, time.strftime("%Y-%m-%d %H:%M", time.localtime(doc.generated)), doc.copyright))
    p.rect(0, PAGE_H - 110, PAGE_W, 110, (0.04, 0.24, 0.43))
    p.y = PAGE_H - 50
    p.text(doc.title, 22, "F2", color=(1, 1, 1))
    p.text(doc.subtitle, 12, "F1", color=(0.9, 0.93, 0.97))
    p.y = PAGE_H - 130
    for k, v in doc.meta:
        p.text("%s: %s" % (k, v), 9)
    if doc.banner:
        p.text(doc.banner, 10, "F2", color=SEV_RGB["WARNING"])
    c = doc.counts
    p.y -= 6
    p.text("Overall health: %s   Score: %s/100" % (doc.status, doc.score), 16, "F2", color=SEV_RGB.get(doc.status, (0, 0, 0)))
    p.text("Critical %d   Warning %d   Unknown %d   Info %d" % (c.get("CRITICAL", 0), c.get("WARNING", 0), c.get("UNKNOWN", 0), c.get("INFO", 0)), 10)
    p.heading(doc.overview()[0])
    p.table(doc.overview()[1], doc.module_rows)
    key = [f for f in doc.findings if f["severity"] in ("CRITICAL", "WARNING", "UNKNOWN")]
    p.heading("Issues requiring attention (%d)" % len(key))
    for f in key:
        p.wrap("[%s] %s - %s" % (f["severity"], f["component"], f["message"]), 9, "F2", color=SEV_RGB.get(f["severity"], (0, 0, 0)))
        if f.get("recommendation"):
            p.wrap("Action: " + f["recommendation"], 8, indent=14)
    if doc.recommendations:
        p.heading("Recommended actions")
        for i, (s, comp, r) in enumerate(doc.recommendations, 1):
            p.wrap("%d. [%s] %s: %s" % (i, s, comp, r), 9)
    for s in doc.sections:
        p.heading("%s  (score %s)" % (s.title, s.score if s.score is not None else "n/a"), 13)
        if s.summary:
            p.wrap(s.summary, 9, color=(0.35, 0.4, 0.46))
        for n in s.notes:
            p.wrap(n, 8, color=(0.35, 0.4, 0.46))
        for t in s.tables:
            p.text(t.title, 10, "F2")
            if t.note:
                p.wrap(t.note, 7)
            p.table(t.headers, t.rows[:400])
    for title, text in doc.appendix:
        p.heading("Appendix: " + title, 12)
        p.wrap(text[:30000], 7, "F3")
    return p.save(path)
