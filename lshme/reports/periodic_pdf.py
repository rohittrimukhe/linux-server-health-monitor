# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""PDF renderer for the daily, weekly and monthly reports (no dependencies): large readable type, coloured tables,
usage bars, KPI tiles, line charts and logo slots, drawn directly with PDF operators.

Layout rules: text is measured with the real Helvetica widths, so nothing runs past the right margin or is cut off; every page after the
first has a slim running header; a heading always stays with the first rows of what it introduces; tables choose the largest font size
at which every column fits; the logo watermark is faint and only a quarter of the page wide."""
import os
import threading
import time

from lshme.reports import minipdf as _mp
from lshme.reports.minipdf import MARGIN, PAGE_H, PAGE_W, MiniPDF, _esc
from lshme.reports.periodic import Bar, Cell

# paper sizes in points (portrait); the layout is worked out from the page width, so every size and orientation fits
PAGES = {"a4": (595.28, 841.89), "letter": (612.0, 792.0), "legal": (612.0, 1008.0), "a3": (841.89, 1190.55)}
DEFAULT_MARGIN_MM = 8           # small margins: as much room for the content as printers allow
_PAGE_LOCK = threading.RLock()  # the page geometry is module state while one PDF is drawn

BLUE = (0.043, 0.369, 0.843)
DARK = (0.04, 0.25, 0.62)
INK = (0.106, 0.141, 0.188)
MUT = (0.37, 0.42, 0.48)
LINE = (0.87, 0.9, 0.93)
HEADBG = (0.914, 0.933, 0.961)
ZEBRA = (0.98, 0.984, 0.992)
FG = {"ok": (0.12, 0.56, 0.24), "wn": (0.70, 0.42, 0.0), "cr": (0.77, 0.13, 0.12), "na": (0.4, 0.4, 0.4)}
BG = {"ok": (0.90, 0.96, 0.92), "wn": (1.0, 0.953, 0.84), "cr": (0.99, 0.91, 0.90), "na": (0.93, 0.93, 0.93)}
SOLID = {"HEALTHY": (0.12, 0.56, 0.24), "WARNING": (0.94, 0.56, 0.0), "CRITICAL": (0.77, 0.13, 0.12)}
USABLE = PAGE_W - 2 * MARGIN
TXT = 9.2          # table text size (largest tried)
TEXT_SIZES = (9.2, 8.6, 8.0, 7.4)
TOP_OTHER = 54     # first line of content on pages after the first (below the running header)
BOTTOM = 42        # last line of content (above the footer)
RUN_TOP = 16       # distance of the running header from the top edge
PALETTE = ["#0b5ed7", "#d9730d", "#1e8e3e", "#7b3fe4", "#c5221f", "#0d8a72", "#b8860b", "#c2185b", "#5d4037", "#00838f", "#6a1b9a", "#455a64"]
SERIES = {"#0b5ed7": BLUE, "#7b3fe4": (0.48, 0.25, 0.89), "#d9730d": (0.85, 0.45, 0.05), "#0d8a72": (0.05, 0.54, 0.45)}

# Helvetica and Helvetica-Bold advance widths (1/1000 em) for the characters 32 to 126: the standard PDF fonts used by the writer
_ASCII = "".join(chr(c) for c in range(32, 127))
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
         556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
         1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
         667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
         333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
         556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_HELVB = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
          556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
          975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
          667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
          333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
          611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
assert len(_ASCII) == len(_HELV) == len(_HELVB) == 95
W_REG = dict(zip(_ASCII, _HELV))
W_BOLD = dict(zip(_ASCII, _HELVB))
W_REG[" "] = W_BOLD[" "] = 278           # a non-breaking space keeps '(30 d)' together on one line


def keep_units(text):
    """Spaces inside brackets become non-breaking, so a header such as 'Growth (30 d)' never splits inside the brackets."""
    out, depth = [], 0
    for ch in str(text):
        depth += ch == "("
        out.append(" " if ch == " " and depth > 0 else ch)
        depth -= ch == ")"
    return "".join(out)


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def tw(s, size, bold=False):
    """Width in points of a string in Helvetica (or Helvetica-Bold) at the given size."""
    t = W_BOLD if bold else W_REG
    return sum(t.get(ch, 556) for ch in str(s)) * size / 1000.0


def wrap(s, width, size, bold=False, max_lines=8):
    lines = []
    for para in str(s).split("\n"):
        line = ""
        for word in para.split(" "):
            cand = (line + " " + word) if line else word
            if tw(cand, size, bold) <= width or not line:
                line = cand
                while tw(line, size, bold) > width and len(line) > 1:       # a single word longer than the line
                    cut = max(1, int(len(line) * width / tw(line, size, bold)))
                    while cut > 1 and tw(line[:cut], size, bold) > width:
                        cut -= 1
                    lines.append(line[:cut])
                    line = line[cut:]
            else:
                lines.append(line)
                line = word
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-3] + "..." if len(lines[-1]) > 3 else lines[-1]
    return lines


def fit_one(text, width, size, bold=False):
    """The text on one line: shortened with '...' only when it really cannot fit."""
    if tw(text, size, bold) <= width:
        return text
    while text and tw(text + "...", size, bold) > width:
        text = text[:-1]
    return text.rstrip() + "..."


class PeriodicPDF(MiniPDF):
    def __init__(self, title, footer):
        self.watermark = None
        self.running = None          # (title text, host text, LRS logo image, customer logo image or None, customer name)
        MiniPDF.__init__(self, title, footer)
        self.wm_alpha = 0.045

    def put(self, x, y, s, size=9, font="F1", color=INK):
        self.ops.append("%.3f %.3f %.3f rg BT /%s %.1f Tf %.1f %.1f Td (%s) Tj ET" % (color + (font, size, x, y, _esc(s))))

    def fill(self, x, y, w, h, color):
        self.ops.append("%.3f %.3f %.3f rg %.1f %.1f %.1f %.1f re f" % (color + (x, y, w, h)))

    def stroke(self, pts, color, width=1.0, dash=None):
        if len(pts) < 2:
            return
        path = "%.1f %.1f m " % pts[0] + " ".join("%.1f %.1f l" % p for p in pts[1:])
        self.ops.append("%.3f %.3f %.3f RG %.2f w %s %s S [] 0 d" % (color + (width, "[%s] 0 d" % dash if dash else "[] 0 d", path)))

    def stamp_watermark(self, ops):
        """Put the faded logo at the start of a page's drawing commands so everything else is drawn over it."""
        wm = getattr(self, "watermark", None)
        if not wm:
            return
        name, iw, ih = wm
        w = USABLE * 0.62
        h = w * ih / float(iw)
        x, y = (PAGE_W - w) / 2.0, (PAGE_H - h) / 2.0 - 10
        ops.insert(0, "q /GS1 gs q %.1f 0 0 %.1f %.1f %.1f cm /%s Do Q Q" % (w, h, x, y, name))

    def running_header(self):
        """A slim header on every page after the first: small logo and report title on the left, customer on the right."""
        if not self.running:
            return
        title, host, lrs, cust, cname = self.running
        top = PAGE_H - RUN_TOP
        if lrs:
            self.fit_image(lrs, MARGIN, top - 20, 70, 22)
        self.put(MARGIN + (80 if lrs else 0), top - 9, fit_one(title, USABLE * 0.6, 9.5, True), 9.5, "F2", INK)
        self.put(MARGIN + (80 if lrs else 0), top - 20, fit_one(host, USABLE * 0.6, 8, False), 8, "F1", MUT)
        if cust:
            cw = int(max(60, min(170, 22 * cust[1] / float(cust[2]))))
            self.fit_image(cust, PAGE_W - MARGIN - cw, top - 22, cw, 24)
        elif cname:
            s = fit_one(cname, 180, 9, True)
            self.put(PAGE_W - MARGIN - tw(s, 9, True), top - 12, s, 9, "F2", MUT)
        self.ops.append("%.3f %.3f %.3f RG 1.4 w %.1f %.1f m %.1f %.1f l S" % (BLUE + (MARGIN, top - 29, PAGE_W - MARGIN, top - 29)))
        self.ops.append("%.3f %.3f %.3f RG 0.5 w %.1f %.1f m %.1f %.1f l S" % (LINE + (MARGIN, top - 32, PAGE_W - MARGIN, top - 32)))

    def new_page(self):
        MiniPDF.new_page(self)
        self.stamp_watermark(self.ops)
        if len(self.pages) > 1:
            self.running_header()
            self.y = PAGE_H - TOP_OTHER

    def need(self, h):
        if self.y - h < BOTTOM:
            self.new_page()
            return True
        return False

    def fit_image(self, img, x, y, w, h):
        """Draw a registered image centred inside the box, keeping its proportions."""
        name, iw, ih = img
        scale = min(w / float(iw), h / float(ih))
        dw, dh = iw * scale, ih * scale
        self.draw_image(name, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh)

    # -- header -----------------------------------------------------------------------
    def header(self, doc, logo):
        top = PAGE_H
        band = 140
        self.fill(0, top - band, PAGE_W, band, DARK)
        self.fill(0, top - band, PAGE_W, 4, BLUE)
        bh, by = 46, top - 62
        lw = 150
        self.fill(MARGIN, by, lw, bh, (1, 1, 1))
        lrs = getattr(self, "watermark", None) or (self.add_image(logo) if logo else None)
        if lrs:
            self.fit_image(lrs, MARGIN + 8, by + 5, lw - 16, bh - 10)
        else:
            self.put(MARGIN + 12, by + 16, "LRS Vitals", 15, "F2", DARK)
        cimg = None
        cpath = getattr(doc, "customer_logo", "")
        if cpath and os.path.isfile(cpath):
            cimg = self.add_image(cpath)
        cw_logo = 170
        if cimg:                                              # a wide logo gets a wider box (up to 300 points), a tall one a narrower box
            cw_logo = int(max(120, min(300, (bh - 10) * cimg[1] / float(cimg[2]) + 16)))
        cx = PAGE_W - MARGIN - cw_logo
        self.fill(cx, by, cw_logo, bh, (1, 1, 1))
        if cimg:
            self.fit_image(cimg, cx + 8, by + 5, cw_logo - 16, bh - 10)
        else:
            label = getattr(doc, "customer", "") or "Customer logo"
            lines = wrap(label, cw_logo - 20, 11.5, True, 2)
            for i, line in enumerate(lines):
                self.put(cx + cw_logo / 2.0 - tw(line, 11.5, True) / 2, by + bh / 2 + (len(lines) - 1) * 6.5 - 4 - i * 13.5, line, 11.5, "F2",
                         INK if getattr(doc, "customer", "") else (0.6, 0.64, 0.7))
        self.running = (getattr(doc, "title_plain", "") or doc.title, doc.subtitle.split(" - period")[0], lrs, cimg, getattr(doc, "customer", ""))
        base = getattr(doc, "title_plain", "") or doc.title
        suffix = doc.title[len(base):].strip() if doc.title.startswith(base) else ""
        left_w = USABLE - 150
        tsize = 24
        while tsize > 16 and tw(base, tsize, True) > left_w:        # a long title gets a smaller type size instead of being cut off
            tsize -= 1
        self.put(MARGIN, top - 88, fit_one(base, left_w, tsize, True), tsize, "F2", (1, 1, 1))
        if suffix:
            self.put(MARGIN, top - 103, fit_one(suffix, left_w, 11, True), 11, "F2", (0.75, 0.86, 1.0))
        for i, line in enumerate(wrap(doc.subtitle, left_w, 9, False, 2)):
            self.put(MARGIN, top - 118 - i * 11, line, 9, "F1", (0.84, 0.9, 1.0))
        col = SOLID.get(doc.status, (0.4, 0.4, 0.4))
        sx = PAGE_W - MARGIN - 136
        self.fill(sx, top - 122, 136, 44, col)
        self.put(sx + 68 - tw(doc.status, 16, True) / 2, top - 100, doc.status, 16, "F2", (1, 1, 1))
        att = doc.attention if getattr(doc, "attention", None) is not None else doc.counts.get("CRITICAL", 0) + doc.counts.get("WARNING", 0)
        msg = ("1 item needs attention" if att == 1 else "%d items need attention" % att) if att else "all clear"
        self.put(sx + 68 - tw(msg, 8.5) / 2, top - 114, msg, 8.5, "F1", (1, 1, 1))
        self.y = top - band - 14
        det = getattr(doc, "details", [])
        if det:
            cols = 4 if USABLE > 700 else 3
            gap = 8
            cw = (USABLE - gap * (cols - 1)) / float(cols)
            for i in range(0, len(det), cols):
                row = det[i:i + cols]
                vals = [wrap(str(v), cw - 16, 10.5, True, 3) for _, v in row]
                h = 22 + max(len(v) for v in vals) * 12.5
                self.need(h + 6)
                for j, (k, v) in enumerate(row):
                    x = MARGIN + j * (cw + gap)
                    self.fill(x, self.y - h, cw, h, (0.953, 0.965, 0.984))
                    self.put(x + 8, self.y - 12, str(k).upper(), 7, "F1", MUT)
                    for li, line in enumerate(vals[j]):
                        self.put(x + 8, self.y - 25 - li * 12.5, line, 10.5, "F2", INK)
                self.y -= h + 6
        if doc.banner:
            self.put(MARGIN, self.y - 10, doc.banner, 10, "F2", FG["wn"])
            self.y -= 16

    def section(self, n, title, first_h=60):
        """A section heading; it moves to the next page together with the first block under it."""
        self.need(36 + first_h)
        self.y -= 12
        self.fill(MARGIN, self.y - 22, 5, 22, BLUE)
        self.put(MARGIN + 13, self.y - 16, "%d. %s" % (n, title), 14.5, "F2", INK)
        self.ops.append("%.3f %.3f %.3f RG 0.8 w %.1f %.1f m %.1f %.1f l S" % (LINE + (MARGIN, self.y - 27, MARGIN + USABLE, self.y - 27)))
        self.y -= 34

    def kpis(self, items):
        """Tiles sized to their content, in rows of at most four (three when there are five or six); a long label wraps to a second line."""
        cap = 6 if USABLE > 700 else 4                             # a landscape or A3 page has room for more tiles in a row
        per = len(items) if len(items) <= cap else ((len(items) + 1) // 2 if len(items) <= 2 * cap else cap)
        self.need(68 * ((len(items) + per - 1) // per) + 8)
        for i in range(0, len(items), per):
            self.kpi_row(items[i:i + per])

    def kpi_row(self, items):
        self.need(76)
        n = len(items)
        gap = 8
        vals = [(str(v), str(label).upper(), lv) for v, label, lv in items]
        mins = [max(tw(v, 19, True), min(max(tw(w, 7.6) for w in lab.split()) if lab.split() else 0, 90)) + 34 for v, lab, lv in vals]
        nat = [max(tw(v, 19, True), min(tw(lab, 7.6), 120)) + 26 for v, lab, lv in vals]
        avail = USABLE - gap * (n - 1)
        if sum(nat) <= avail:
            widths = [w + (avail - sum(nat)) / n for w in nat]
        else:
            spare = max(0.0, avail - sum(mins))
            room = sum(a - b for a, b in zip(nat, mins)) or 1
            widths = [m + (a - m) * min(1.0, spare / room) for m, a in zip(mins, nat)]
            if sum(widths) > avail:
                widths = [w * avail / sum(widths) for w in widths]
        x = MARGIN
        h = 58
        for (v, label, lv), w in zip(vals, widths):
            self.fill(x, self.y - h, w, h, BG[lv])
            self.fill(x, self.y - h, 4.5, h, FG[lv])
            size = 20
            while size > 12 and tw(v, size, True) > w - 20:
                size -= 1
            self.put(x + 13, self.y - 26, v, size, "F2", INK)
            for i, line in enumerate(wrap(label, w - 20, 7.6, False, 2)):
                self.put(x + 13, self.y - 40 - i * 9.2, line, 7.6, "F1", MUT)
            x += w + gap
        self.y -= h + 10

    def points(self, items):
        icon = {"ok": "OK", "wn": "!", "cr": "X"}
        for lv, text in items:
            lines = wrap(text, USABLE - 56, 11.5)
            h = len(lines) * 15 + 13
            self.need(h + 6)
            self.fill(MARGIN, self.y - h, USABLE, h, BG[lv])
            self.fill(MARGIN, self.y - h, 5, h, FG[lv])
            self.fill(MARGIN + 13, self.y - h / 2 - 9, 22, 18, FG[lv])
            self.put(MARGIN + 24 - tw(icon[lv], 9, True) / 2, self.y - h / 2 - 3, icon[lv], 9, "F2", (1, 1, 1))
            for i, line in enumerate(lines):
                self.put(MARGIN + 46, self.y - 18 - i * 15, line, 11.5, "F1", INK)
            self.y -= h + 6
        self.y -= 4

    # -- tables -----------------------------------------------------------------------
    @staticmethod
    def _kind(c):
        if isinstance(c, Bar):
            return "bar"
        if isinstance(c, Cell) and c.style.startswith("chip-"):
            return "chip"
        return "text"

    def _metrics(self, headers, rows, size, hs):
        """(natural widths, minimum widths) of every column at a text size."""
        nat, mn = [], []
        for i in range(len(headers)):
            hw = keep_units(str(headers[i]).upper())
            a = tw(hw, hs, True) + 10
            m = max([tw(w, hs, True) for w in hw.split()] or [0]) + 10
            for r in rows[:300]:
                c = r[i] if i < len(r) else ""
                k = self._kind(c)
                if k == "bar":
                    a, m = max(a, 70), max(m, 62)
                elif k == "chip":
                    w = tw(c.text, size, True) + 16
                    a, m = max(a, w), max(m, w)
                else:
                    bold = isinstance(c, Cell) and c.style in ("b", "ok", "wn", "cr")
                    parts = str(c).split("\n")
                    a = max(a, min(max(tw(x, size, bold) for x in parts), 250) + 10)
                    words = [w for x in parts for w in x.split()]
                    if len(parts) == 1 and len(str(c).strip()) <= 18:       # a short value such as '14.5 GB' or '97.0 %' never wraps
                        words = [str(c).strip()]
                    m = max(m, max([tw(w, size, bold) for w in words] or [0]) + 10)
            nat.append(a)
            mn.append(min(m, USABLE * 0.45))
        return nat, mn

    def table(self, b):
        headers, rows, align = b["headers"], b["rows"], b.get("align") or []
        n = len(headers)
        hs = 7.8
        for size in TEXT_SIZES:                      # the largest size at which every column's longest word fits
            nat, mn = self._metrics(headers, rows, size, hs)
            if sum(mn) <= USABLE:
                break
        lh = size * 1.27
        if sum(nat) <= USABLE:
            widths = [w * USABLE / sum(nat) for w in nat]
        else:
            spare, room = USABLE - sum(mn), (sum(nat) - sum(mn)) or 1
            widths = [m + (a - m) * max(0.0, min(1.0, spare / room)) for m, a in zip(mn, nat)]
        if sum(widths) > USABLE + 0.5:
            widths = [w * USABLE / sum(widths) for w in widths]
        xs = [MARGIN]
        for w in widths:
            xs.append(xs[-1] + w)
        hlines = [[l.replace(" ", " ") for l in wrap(keep_units(str(h).upper()), widths[i] - 8, hs, True, 3)] for i, h in enumerate(headers)]
        hh = max(len(l) for l in hlines) * 9.5 + 9

        def layout(r):
            cells = []
            for i in range(n):
                c = r[i] if i < len(r) else ""
                if self._kind(c) != "text":
                    cells.append((c, [""]))
                else:
                    bold = isinstance(c, Cell) and c.style in ("b", "ok", "wn", "cr")
                    cells.append((c, wrap(str(c), widths[i] - 9, size, bold)))
            return cells, max(len(l) for _, l in cells) * lh + 8
        laid = [layout(r) for r in rows[:2]]
        title_h = 19 if b["title"] else 0
        self.need(title_h + hh + sum(h for _, h in laid) + 14)      # the title, the header and the first rows stay together
        if b["title"]:
            self.put(MARGIN, self.y - 12, b["title"], 11.5, "F2", INK)
            self.y -= title_h

        hmax = max(len(l) for l in hlines)

        def draw_header():
            self.fill(MARGIN, self.y - hh, USABLE, hh, HEADBG)
            for i, lines in enumerate(hlines):
                first = self.y - hh / 2.0 + (len(lines) - 1) * 9.5 / 2.0 - hs * 0.36   # every header label is centred top to bottom
                for k, s in enumerate(lines):
                    x = xs[i + 1] - 4 - tw(s, hs, True) if (i < len(align) and align[i] == "r") else xs[i] + 4
                    self.put(x, first - k * 9.5, s, hs, "F2", (0.2, 0.25, 0.35))
            self.y -= hh
        draw_header()
        for ri, r in enumerate(rows):
            cells, h = laid[ri] if ri < len(laid) else layout(r)
            if self.need(h + 4):
                draw_header()
            top = self.y
            if ri % 2:
                self.fill(MARGIN, top - h, USABLE, h, ZEBRA)
            for i, (c, lines) in enumerate(cells):
                x0, w = xs[i], widths[i]
                mid = top - h / 2.0                                     # every cell is centred top to bottom in its row
                if isinstance(c, Bar):
                    bw = w - 10
                    self.fill(x0 + 5, mid - 5.5, bw, 11, (0.89, 0.91, 0.93))
                    self.fill(x0 + 5, mid - 5.5, bw * min(c.pct, 100) / 100.0, 11, FG[c.level()])
                    s = "%.0f%%" % c.pct
                    self.put(x0 + 5 + bw / 2 - tw(s, 7.5, True) / 2, mid - 2.6, s, 7.5, "F2", (0.07, 0.07, 0.07))
                    continue
                if isinstance(c, Cell) and c.style.startswith("chip-"):
                    lv = c.style[-2:]
                    cw = min(tw(c.text, size, True) + 14, w - 6)
                    self.fill(x0 + 4, mid - 7, cw, 14, BG.get(lv, BG["na"]))
                    self.put(x0 + 4 + (cw - tw(c.text, size, True)) / 2, mid - size * 0.36, str(c.text), size, "F2", FG.get(lv, FG["na"]))
                    continue
                color, font = INK, "F1"
                if isinstance(c, Cell):
                    st = c.style
                    if st in ("ok", "wn", "cr"):
                        self.fill(x0, top - h, w, h, BG[st])
                        color, font = FG[st], "F2"
                    elif st in ("up", "dn", "fl", "b"):
                        color = {"up": FG["cr"], "dn": FG["ok"], "fl": MUT, "b": INK}[st]
                        font = "F2" if st in ("up", "dn", "b") else "F1"
                first = mid + (len(lines) - 1) * lh / 2.0 - size * 0.36   # the block of lines sits on the row's centre line
                for li, line in enumerate(lines):
                    x = (x0 + w - 4.5 - tw(line, size, font == "F2")) if (i < len(align) and align[i] == "r") else x0 + 4.5
                    self.put(x, first - li * lh, line, size, font, color)
            self.ops.append("%.3f %.3f %.3f RG 0.4 w %.1f %.1f m %.1f %.1f l S" % (LINE + (MARGIN, top - h, MARGIN + USABLE, top - h)))
            self.y -= h
        if b.get("legend"):
            for line in wrap(b["legend"], USABLE, 8.4):
                self.need(12)
                self.put(MARGIN, self.y - 10, line, 8.4, "F1", MUT)
                self.y -= 11
        self.y -= 10

    def charts(self, items, wide=False):
        gap, ch = 12, 168 if not wide else 215
        w = USABLE if wide else (USABLE - gap) / 2.0
        step = 1 if wide else 2
        for i in range(0, len(items), step):
            self.need(ch + 14)
            for j, c in enumerate(items[i:i + step]):
                self.chart(c, MARGIN + j * (w + gap), self.y, w, ch)
            self.y -= ch + 12
        self.put(MARGIN, self.y - 8, "Dashed lines are the warning (amber) and critical (red) limits. The HTML report shows exact values when you hover.", 8, "F1", MUT)
        self.y -= 16

    def chart(self, c, x, top, w, h):
        S = c["series"]
        P = S[0]["pts"]
        self.ops.append("%.3f %.3f %.3f RG 0.6 w %.1f %.1f %.1f %.1f re S" % (LINE + (x, top - h, w, h)))
        if not P:
            return
        vals = [p[1] for s in S for p in s["pts"]]
        mx = c.get("max") or (max(vals) * 1.1 or 1)
        mn = c["min"] if c.get("min") is not None else (0 if c.get("max") else min(vals) - (max(vals) - min(vals)) * 0.1)
        if mx <= mn:
            mx = mn + 1
        avg = sum(p[1] for p in P) / len(P)
        title = c["title"]
        if c.get("info"):
            info = c["info"]
        elif not c.get("multi"):
            info = ("avg %.1f%s, peak %.1f%s" % (avg, c["unit"], max(vals), c["unit"])) if c.get("max") else ("now %.2f%s" % (P[-1][1], c["unit"]))
        else:
            info = ""
        stacked = bool(info) and tw(title, 10.5, True) + tw(info, 8.5) + 26 > w          # no room side by side: the figures go under the title
        self.put(x + 8, top - 14, fit_one(title, w - 16, 10.5, True), 10.5, "F2", INK)
        extra = 0
        if info and stacked:
            self.put(x + 8, top - 25, fit_one(info, w - 16, 8.5), 8.5, "F1", MUT)
            extra = 11
        elif info:
            self.put(x + w - 8 - tw(info, 8.5), top - 14, info, 8.5, "F1", MUT)
        key_h = 0
        if c.get("multi"):
            kx, ky = x + 8, top - 27 - extra
            for k, se in enumerate(S):
                label = se["name"]
                if kx + tw(label, 7.5) + 18 > x + w - 6:
                    break
                self.fill(kx, ky, 7, 7, hexrgb((c.get("colors") or PALETTE)[k % len(c.get("colors") or PALETTE)]))
                self.put(kx + 10, ky + 1, label, 7.5, "F1", MUT)
                kx += tw(label, 7.5) + 22
            key_h = 10
        px0, px1, py1, py0 = x + 36, x + w - 10, top - 24 - key_h - extra, top - h + 20
        t0, t1 = P[0][0], max(max(se["pts"][-1][0] for se in S if se["pts"]), P[0][0] + 1)
        X = lambda t: px0 + (t - t0) * (px1 - px0) / float(t1 - t0)
        Y = lambda v: py0 + (v - mn) * (py1 - py0) / float(mx - mn)
        for g in range(5):
            v = mn + (mx - mn) * g / 4.0
            self.stroke([(px0, Y(v)), (px1, Y(v))], LINE, 0.4)
            s = "%.1f" % v if mx - mn < 5 else "%.0f" % v
            self.put(px0 - 4 - tw(s, 7.5), Y(v) - 2.5, s, 7.5, "F1", MUT)
        span = (t1 - t0) / 86400.0
        for q in range(5):
            t = t0 + (t1 - t0) * q / 4.0
            s = time.strftime("%H:%M" if span <= 2 else "%d %b", time.localtime(t))
            self.put(min(max(X(t) - tw(s, 7.5) / 2, px0 - 6), px1 - tw(s, 7.5)), py0 - 11, s, 7.5, "F1", MUT)
        for val, col in ((c.get("warn"), (0.94, 0.56, 0.0)), (c.get("crit"), FG["cr"])):
            if val is not None and mn <= val <= mx:
                self.stroke([(px0, Y(val)), (px1, Y(val))], col, 0.8, "3 2")
        for k in range(len(S) - 1, -1, -1):
            pts = [(X(t), Y(min(max(v, mn), mx))) for t, v in S[k]["pts"]]
            if c.get("multi"):
                self.stroke(pts, hexrgb((c.get("colors") or PALETTE)[k % len(c.get("colors") or PALETTE)]), 0.9)
            elif k == 0:
                self.stroke(pts, SERIES.get(c["color"], BLUE), 1.3)
            else:
                self.stroke(pts, (0.65, 0.7, 0.76), 0.7, "2 2")

    def note(self, text, color=BLUE):
        lines = wrap(text, USABLE - 24, 10.5)
        h = len(lines) * 13.5 + 12
        self.need(h + 6)
        self.fill(MARGIN, self.y - h, USABLE, h, (0.96, 0.97, 0.98))
        self.fill(MARGIN, self.y - h, 4, h, color)
        for i, line in enumerate(lines):
            self.put(MARGIN + 13, self.y - 15 - i * 13.5, line, 10.5, "F1", INK)
        self.y -= h + 8

    def actions(self, items):
        for i, (lv, msg, why) in enumerate(items, 1):
            ml, rl = wrap(msg, USABLE - 50, 11.5, True), wrap("Why it matters: " + why, USABLE - 50, 9.8)
            h = len(ml) * 14.5 + len(rl) * 12.5 + 14
            self.need(h + 6)
            self.fill(MARGIN, self.y - h, USABLE, h, BG[lv])
            self.fill(MARGIN, self.y - h, 4.5, h, FG[lv])
            self.put(MARGIN + 14, self.y - 21, str(i), 16, "F2", FG[lv])
            for k, line in enumerate(ml):
                self.put(MARGIN + 38, self.y - 17 - k * 14.5, line, 11.5, "F2", INK)
            for k, line in enumerate(rl):
                self.put(MARGIN + 38, self.y - 17 - len(ml) * 14.5 - k * 12.5, line, 9.8, "F1", MUT)
            self.y -= h + 6

    def kv(self, rows):
        lw = 96
        cw = (USABLE - 2 * lw) / 2.0
        for r in rows:
            vals = [wrap(str(r[j * 2 + 1]), cw - 12, 9.6, False, 3) for j in range(2)]
            most = max(len(v) for v in vals)
            h = most * 12 + 10
            self.need(h + 2)
            for j in range(2):
                x = MARGIN + j * (lw + cw)
                self.fill(x, self.y - h, lw, h, HEADBG)
                self.put(x + 6, self.y - 14 - (most - 1) * 6, fit_one(str(r[j * 2]).upper(), lw - 10, 7.6, True), 7.6, "F2", (0.2, 0.25, 0.35))
                for k, line in enumerate(vals[j]):
                    self.put(x + lw + 6, self.y - 14 - (most - len(vals[j])) * 6 - k * 12, line, 9.6, "F1", INK)
            self.ops.append("%.3f %.3f %.3f RG 0.4 w %.1f %.1f m %.1f %.1f l S" % (LINE + (MARGIN, self.y - h, MARGIN + USABLE, self.y - h)))
            self.y -= h
        self.y -= 8


def first_height(b):
    """About how much room the first block of a section needs, so the heading never sits alone at the bottom of a page."""
    t = b.get("type")
    if t == "kpis":
        n = len(b.get("items", []))
        return 70 * (1 if n <= (6 if USABLE > 700 else 4) else 2)
    if t == "points":
        return 44
    if t == "table":
        return 96
    if t in ("charts", "wide_charts"):
        return 190
    if t == "actions":
        return 70
    return 50


def page_geometry(size="a4", orientation="auto", margin_mm=DEFAULT_MARGIN_MM, doc=None):
    """(width, height, margin) in points. 'auto' turns the page sideways only when a table cannot fit across a portrait page."""
    w, h = PAGES.get(str(size or "a4").strip().lower(), PAGES["a4"])
    try:
        m = max(5.0, min(25.0, float(margin_mm))) * 72 / 25.4
    except (TypeError, ValueError):
        m = DEFAULT_MARGIN_MM * 72 / 25.4
    o = str(orientation or "auto").strip().lower()
    if o == "landscape":
        return h, w, m
    if o == "auto" and doc is not None:
        usable = w - 2 * m
        for _, blocks in doc.sections:
            for b in blocks:
                if b.get("type") == "table" and not b.get("excel_only") and b.get("rows"):
                    _, mn = PeriodicPDF._metrics(PeriodicPDF, b["headers"], b["rows"], TEXT_SIZES[-1], 7.8)
                    if sum(mn) > usable + 0.5:
                        return h, w, m
    return w, h, m


def set_page(w, h, m):
    """Point the drawing code (this module and the PDF writer) at one page size and margin."""
    global PAGE_W, PAGE_H, MARGIN, USABLE, BOTTOM, TOP_OTHER, RUN_TOP
    PAGE_W, PAGE_H, MARGIN, USABLE = w, h, m, w - 2 * m
    RUN_TOP = max(12.0, m - 6)
    TOP_OTHER = RUN_TOP + 38
    BOTTOM = m + 20
    _mp.PAGE_W, _mp.PAGE_H, _mp.MARGIN = w, h, m
    _mp.FOOT_LINE, _mp.FOOT_TEXT = m + 8, m - 4


def render(doc, path, logo=""):
    """Draw the report on the paper size, orientation and margin stored with it (A4, auto orientation and 8 mm when not set)."""
    geo = page_geometry(getattr(doc, "page_size", "a4"), getattr(doc, "orientation", "auto"), getattr(doc, "margin_mm", DEFAULT_MARGIN_MM), doc)
    with _PAGE_LOCK:
        saved = (PAGE_W, PAGE_H, MARGIN, USABLE, BOTTOM, TOP_OTHER, RUN_TOP, _mp.PAGE_W, _mp.PAGE_H, _mp.MARGIN, _mp.FOOT_LINE, _mp.FOOT_TEXT)
        set_page(*geo)
        try:
            return _render(doc, path, logo)
        finally:
            _restore(saved)


def _restore(s):
    global PAGE_W, PAGE_H, MARGIN, USABLE, BOTTOM, TOP_OTHER, RUN_TOP
    PAGE_W, PAGE_H, MARGIN, USABLE, BOTTOM, TOP_OTHER, RUN_TOP = s[:7]
    _mp.PAGE_W, _mp.PAGE_H, _mp.MARGIN, _mp.FOOT_LINE, _mp.FOOT_TEXT = s[7:]


def _render(doc, path, logo=""):
    foot ="%s - %s - %s" % (getattr(doc, "title_plain", "") or doc.title, time.strftime("%Y-%m-%d %H:%M", time.localtime(doc.generated)), doc.copyright)
    p = PeriodicPDF(doc.title, foot)
    mark = doc.brand_logo or logo
    p.watermark = p.add_image(mark) if mark and os.path.isfile(mark) else None
    p.stamp_watermark(p.ops)                    # the first page was made before the logo was loaded
    p.header(doc, mark)
    for n, (title, blocks) in enumerate(doc.sections, 1):
        shown = [b for b in blocks if not b.get("excel_only")]
        p.section(n, title, first_height(shown[0]) if shown else 40)
        for b in shown:
            t = b["type"]
            if t == "kpis":
                p.kpis(b["items"])
            elif t == "points":
                p.points(b["items"])
            elif t == "table":
                p.table(b)
            elif t == "charts":
                p.charts(b["items"])
            elif t == "wide_charts":
                p.charts(b["items"], True)
            elif t == "note":
                p.note(b["text"])
            elif t == "actions":
                p.actions(b["items"])
            elif t == "kv":
                p.kv(b["rows"])
    return p.save(path)
