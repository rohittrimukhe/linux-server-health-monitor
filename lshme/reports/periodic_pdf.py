# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""PDF renderer for the daily, weekly and monthly reports (no dependencies): large readable type, coloured tables,
usage bars, KPI tiles, line charts and logo slots, drawn directly with PDF operators."""
import os
import time

from lshme.reports.minipdf import MARGIN, PAGE_H, PAGE_W, MiniPDF, _esc
from lshme.reports.periodic import Bar, Cell

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
NARROW = set("il.,:;|'!()[]/ jt")
WIDE = set("mwMW@")
TXT = 9.2          # table text size
LH = 11.6          # table line height
PALETTE = ["#0b5ed7", "#d9730d", "#1e8e3e", "#7b3fe4", "#c5221f", "#0d8a72", "#b8860b", "#c2185b", "#5d4037", "#00838f", "#6a1b9a", "#455a64"]
SERIES = {"#0b5ed7": BLUE, "#7b3fe4": (0.48, 0.25, 0.89), "#d9730d": (0.85, 0.45, 0.05), "#0d8a72": (0.05, 0.54, 0.45)}


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def tw(s, size, bold=False):
    w = 0.0
    for ch in str(s):
        w += 0.28 if ch in NARROW else 0.82 if ch in WIDE else 0.556 if ch.isdigit() else 0.64 if ch.isupper() else 0.5
    return w * size * (1.07 if bold else 1.0)


def wrap(s, width, size, bold=False, max_lines=8):
    lines = []
    for para in str(s).split("\n"):
        line = ""
        for word in para.split(" "):
            cand = (line + " " + word) if line else word
            if tw(cand, size, bold) <= width or not line:
                line = cand
                while tw(line, size, bold) > width and len(line) > 1:
                    cut = max(1, int(len(line) * width / tw(line, size, bold)))
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


class PeriodicPDF(MiniPDF):
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
        w = USABLE * 0.78
        h = w * ih / float(iw)
        x, y = (PAGE_W - w) / 2.0, (PAGE_H - h) / 2.0 - 20
        ops.insert(0, "q /GS1 gs q %.1f 0 0 %.1f %.1f %.1f cm /%s Do Q Q" % (w, h, x, y, name))

    def new_page(self):
        MiniPDF.new_page(self)
        self.stamp_watermark(self.ops)

    def need(self, h):
        if self.y - h < MARGIN + 18:
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
        self.fill(0, top - 124, PAGE_W, 124, DARK)
        self.fill(0, top - 124, PAGE_W, 4, BLUE)
        bw, bh, by = 130, 40, top - 56
        self.fill(MARGIN, by, bw, bh, (1, 1, 1))
        lrs = getattr(self, "watermark", None) or (self.add_image(logo) if logo else None)
        if lrs:
            self.fit_image(lrs, MARGIN + 6, by + 4, bw - 12, bh - 8)
        else:
            self.put(MARGIN + 10, by + 14, "LRS Vitals", 14, "F2", DARK)
        cx = PAGE_W - MARGIN - 160
        self.fill(cx, by, 160, bh, (1, 1, 1))
        cimg = None
        cpath = getattr(doc, "customer_logo", "")
        if cpath and os.path.isfile(cpath):
            cimg = self.add_image(cpath)
        if cimg:
            self.fit_image(cimg, cx + 6, by + 4, 148, bh - 8)
        else:
            label = getattr(doc, "customer", "") or "Customer logo"
            lines = wrap(label, 148, 11, True, 2)
            for i, line in enumerate(lines):
                self.put(cx + 80 - tw(line, 11, True) / 2, by + bh / 2 + (len(lines) - 1) * 6 - 4 - i * 13, line, 11, "F2", INK if getattr(doc, "customer", "") else (0.6, 0.64, 0.7))
        base = getattr(doc, "title_plain", "") or doc.title
        suffix = doc.title[len(base):].strip() if doc.title.startswith(base) else ""
        self.put(MARGIN, top - 82, base, 22, "F2", (1, 1, 1))
        if suffix:
            self.put(MARGIN, top - 96, wrap(suffix, USABLE - 150, 10.5, True, 1)[0], 10.5, "F2", (0.75, 0.86, 1.0))
        for i, line in enumerate(wrap(doc.subtitle, USABLE - 150, 8.8, False, 1)):
            self.put(MARGIN, top - 110 - i * 11, line, 8.8, "F1", (0.84, 0.9, 1.0))
        col = SOLID.get(doc.status, (0.4, 0.4, 0.4))
        sx = PAGE_W - MARGIN - 130
        self.fill(sx, top - 112, 130, 40, col)
        self.put(sx + 65 - tw(doc.status, 15, True) / 2, top - 91, doc.status, 15, "F2", (1, 1, 1))
        att = doc.counts.get("CRITICAL", 0) + doc.counts.get("WARNING", 0)
        msg = "%d item%s need attention" % (att, "" if att == 1 else "s") if att else "all clear"
        self.put(sx + 65 - tw(msg, 8.5) / 2, top - 105, msg, 8.5, "F1", (1, 1, 1))
        self.y = top - 136
        det = getattr(doc, "details", [])
        if det:
            cols = 3
            cw = (USABLE - 8 * (cols - 1)) / float(cols)
            for i in range(0, len(det), cols):
                self.need(34)
                for j, (k, v) in enumerate(det[i:i + cols]):
                    x = MARGIN + j * (cw + 8)
                    self.fill(x, self.y - 30, cw, 30, (0.953, 0.965, 0.984))
                    self.put(x + 8, self.y - 11, str(k).upper(), 7, "F1", MUT)
                    self.put(x + 8, self.y - 24, wrap(str(v), cw - 14, 10.5, True, 1)[0], 10.5, "F2", INK)
                self.y -= 36
        if doc.banner:
            self.put(MARGIN, self.y - 10, doc.banner, 10, "F2", FG["wn"])
            self.y -= 16

    def section(self, n, title):
        self.need(80)
        self.y -= 14
        self.fill(MARGIN, self.y - 22, 5, 22, BLUE)
        self.put(MARGIN + 13, self.y - 16, "%d. %s" % (n, title), 14.5, "F2", INK)
        self.ops.append("%.3f %.3f %.3f RG 0.8 w %.1f %.1f m %.1f %.1f l S" % (LINE + (MARGIN, self.y - 27, MARGIN + USABLE, self.y - 27)))
        self.y -= 34

    def kpis(self, items):
        self.need(70)
        n = len(items)
        gap = 8
        w = (USABLE - gap * (n - 1)) / float(n)
        for i, (v, label, lv) in enumerate(items):
            x = MARGIN + i * (w + gap)
            self.fill(x, self.y - 52, w, 52, BG[lv])
            self.fill(x, self.y - 52, 4.5, 52, FG[lv])
            self.put(x + 13, self.y - 27, v, 20, "F2", INK)
            self.put(x + 13, self.y - 43, label.upper(), 8, "F1", MUT)
        self.y -= 62

    def points(self, items):
        icon = {"ok": "OK", "wn": "!", "cr": "X"}
        for lv, text in items:
            lines = wrap(text, USABLE - 52, 12)
            h = len(lines) * 15.5 + 12
            self.need(h + 6)
            self.fill(MARGIN, self.y - h, USABLE, h, BG[lv])
            self.fill(MARGIN, self.y - h, 5, h, FG[lv])
            self.fill(MARGIN + 13, self.y - h / 2 - 9, 22, 18, FG[lv])
            self.put(MARGIN + 24 - tw(icon[lv], 9, True) / 2, self.y - h / 2 - 3, icon[lv], 9, "F2", (1, 1, 1))
            for i, line in enumerate(lines):
                self.put(MARGIN + 44, self.y - 17 - i * 15.5, line, 12, "F1", INK)
            self.y -= h + 6
        self.y -= 4

    def table(self, b):
        headers, rows, align = b["headers"], b["rows"], b.get("align") or []
        size, hs = TXT, 7.8
        n = len(headers)
        if b["title"]:
            self.need(50)
            self.put(MARGIN, self.y - 12, b["title"], 11.5, "F2", INK)
            self.y -= 19
        nat = []
        for i in range(n):
            wmax = tw(str(headers[i]).upper(), hs, True) + 10
            for r in rows[:300]:
                c = r[i] if i < len(r) else ""
                if isinstance(c, Bar):
                    w = 70
                elif isinstance(c, Cell) and c.style.startswith("chip-"):
                    w = tw(c.text, size, True) + 16
                else:
                    longest = max([tw(x, size, isinstance(c, Cell) and c.style in ("b",)) for x in str(c).split("\n")] or [0])
                    w = min(longest, 250) + 10
                wmax = max(wmax, w)
            nat.append(wmax)
        total = sum(nat)
        if total > USABLE:
            fixed = [i for i in range(n) if max([isinstance(r[i], Bar) or (isinstance(r[i], Cell) and r[i].style.startswith("chip-")) for r in rows[:50] if i < len(r)] or [False])]
            flex = [i for i in range(n) if i not in fixed]
            avail = USABLE - sum(nat[i] for i in fixed)
            ftot = sum(nat[i] for i in flex) or 1
            hmin = [tw(str(h).upper(), hs, True) + 10 for h in headers]
            widths = [nat[i] if i in fixed else max(28, hmin[i], nat[i] * avail / ftot) for i in range(n)]
            if sum(widths) > USABLE * 1.01:
                widths = [w * USABLE / sum(widths) for w in widths]
        else:
            widths = [w * USABLE / total for w in nat] if n else nat
        # no column may be narrower than its longest unbreakable word (so a date never wraps onto a second line)
        def widest_word(text, bold, sz):
            return max([tw(w, sz, bold) for w in str(text).split()] or [0])
        wm = []
        for i in range(n):
            m = widest_word(str(headers[i]).upper(), True, hs) + 10
            for r in rows[:300]:
                c = r[i] if i < len(r) else ""
                if isinstance(c, Bar):
                    m = max(m, 62)
                elif isinstance(c, Cell) and c.style.startswith("chip-"):
                    m = max(m, tw(c.text, size, True) + 16)
                else:
                    m = max(m, max([widest_word(x, isinstance(c, Cell) and c.style in ("b", "ok", "wn", "cr"), size) for x in str(c).splitlines()] or [0]) + 10)
            wm.append(min(m, USABLE * 0.45))
        widths = [max(w, m) for w, m in zip(widths, wm)]
        for _ in range(6):
            over = sum(widths) - USABLE
            if over <= 0.5:
                break
            slack = [max(0.0, w - m) for w, m in zip(widths, wm)]
            if sum(slack) <= 0:
                break
            widths = [w - over * s / sum(slack) for w, s in zip(widths, slack)]
        if sum(widths) > USABLE + 0.5:
            widths = [w * USABLE / sum(widths) for w in widths]
        xs = [MARGIN]
        for w in widths:
            xs.append(xs[-1] + w)
        hlines = [wrap(str(h).upper(), widths[i] - 8, hs, True, 2) for i, h in enumerate(headers)]
        hh = max(len(l) for l in hlines) * 9.5 + 9

        def draw_header():
            self.fill(MARGIN, self.y - hh, USABLE, hh, HEADBG)
            for i, lines in enumerate(hlines):
                for k, s in enumerate(lines):
                    x = xs[i + 1] - 4 - tw(s, hs, True) if (i < len(align) and align[i] == "r") else xs[i] + 4
                    self.put(x, self.y - 12 - k * 9.5, s, hs, "F2", (0.2, 0.25, 0.35))
            self.y -= hh
        self.need(52)
        draw_header()
        for ri, r in enumerate(rows):
            cells = []
            for i in range(n):
                c = r[i] if i < len(r) else ""
                if isinstance(c, Bar) or (isinstance(c, Cell) and c.style.startswith("chip-")):
                    cells.append((c, [""]))
                else:
                    bold = isinstance(c, Cell) and c.style in ("b", "ok", "wn", "cr")
                    cells.append((c, wrap(str(c), widths[i] - 9, size, bold)))
            h = max(len(l) for _, l in cells) * LH + 8
            if self.need(h + 16):
                draw_header()
            top = self.y
            if ri % 2:
                self.fill(MARGIN, top - h, USABLE, h, ZEBRA)
            for i, (c, lines) in enumerate(cells):
                x0, w = xs[i], widths[i]
                if isinstance(c, Bar):
                    bw = w - 10
                    self.fill(x0 + 5, top - 15, bw, 11, (0.89, 0.91, 0.93))
                    self.fill(x0 + 5, top - 15, bw * min(c.pct, 100) / 100.0, 11, FG[c.level()])
                    s = "%.0f%%" % c.pct
                    self.put(x0 + 5 + bw / 2 - tw(s, 7.5, True) / 2, top - 12.6, s, 7.5, "F2", (0.07, 0.07, 0.07))
                    continue
                if isinstance(c, Cell) and c.style.startswith("chip-"):
                    lv = c.style[-2:]
                    cw = min(tw(c.text, size, True) + 14, w - 6)
                    self.fill(x0 + 4, top - 16, cw, 14, BG.get(lv, BG["na"]))
                    self.put(x0 + 4 + (cw - tw(c.text, size, True)) / 2, top - 12.5, str(c.text), size, "F2", FG.get(lv, FG["na"]))
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
                for li, line in enumerate(lines):
                    x = (x0 + w - 4.5 - tw(line, size, font == "F2")) if (i < len(align) and align[i] == "r") else x0 + 4.5
                    self.put(x, top - 11.5 - li * LH, line, size, font, color)
            self.ops.append("%.3f %.3f %.3f RG 0.4 w %.1f %.1f m %.1f %.1f l S" % (LINE + (MARGIN, top - h, MARGIN + USABLE, top - h)))
            self.y -= h
        if b.get("legend"):
            for line in wrap(b["legend"], USABLE, 8.4):
                self.need(12)
                self.put(MARGIN, self.y - 10, line, 8.4, "F1", MUT)
                self.y -= 11
        self.y -= 9

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
        self.put(x + 8, top - 14, c["title"], 10.5, "F2", INK)
        if c.get("info"):
            inf = c["info"]
            self.put(x + w - 8 - tw(inf, 8.5), top - 14, inf, 8.5, "F1", MUT)
        elif not c.get("multi"):
            info = ("avg %.1f%s, peak %.1f%s" % (avg, c["unit"], max(vals), c["unit"])) if c.get("max") else ("now %.2f%s" % (P[-1][1], c["unit"]))
            self.put(x + w - 8 - tw(info, 8.5), top - 14, info, 8.5, "F1", MUT)
        key_h = 0
        if c.get("multi"):
            kx = x + 8
            for k, se in enumerate(S):
                label = se["name"]
                if kx + tw(label, 7.5) + 18 > x + w - 6:
                    break
                self.fill(kx, top - 27, 7, 7, hexrgb((c.get("colors") or PALETTE)[k % len(c.get("colors") or PALETTE)]))
                self.put(kx + 10, top - 26, label, 7.5, "F1", MUT)
                kx += tw(label, 7.5) + 22
            key_h = 10
        px0, px1, py1, py0 = x + 36, x + w - 10, top - 24 - key_h, top - h + 20
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
        lines = wrap(text, USABLE - 20, 10.5)
        h = len(lines) * 13.5 + 10
        self.need(h + 6)
        self.fill(MARGIN, self.y - h, USABLE, h, (0.96, 0.97, 0.98))
        self.fill(MARGIN, self.y - h, 4, h, color)
        for i, line in enumerate(lines):
            self.put(MARGIN + 12, self.y - 14 - i * 13.5, line, 10.5, "F1", INK)
        self.y -= h + 7

    def actions(self, items):
        for i, (lv, msg, why) in enumerate(items, 1):
            ml, rl = wrap(msg, USABLE - 44, 11.5, True), wrap("Why it matters: " + why, USABLE - 44, 9.8)
            h = len(ml) * 14.5 + len(rl) * 12.5 + 12
            self.need(h + 6)
            self.fill(MARGIN, self.y - h, USABLE, h, BG[lv])
            self.fill(MARGIN, self.y - h, 4.5, h, FG[lv])
            self.put(MARGIN + 14, self.y - 20, str(i), 16, "F2", FG[lv])
            for k, line in enumerate(ml):
                self.put(MARGIN + 36, self.y - 16 - k * 14.5, line, 11.5, "F2", INK)
            for k, line in enumerate(rl):
                self.put(MARGIN + 36, self.y - 16 - len(ml) * 14.5 - k * 12.5, line, 9.8, "F1", MUT)
            self.y -= h + 6

    def kv(self, rows):
        lw = 92
        cw = (USABLE - 2 * lw) / 2.0
        for r in rows:
            vals = [wrap(str(r[j * 2 + 1]), cw - 10, 9.6, False, 3) for j in range(2)]
            h = max(len(v) for v in vals) * 12 + 8
            self.need(h + 2)
            for j in range(2):
                x = MARGIN + j * (lw + cw)
                self.fill(x, self.y - h, lw, h, HEADBG)
                self.put(x + 5, self.y - 13, str(r[j * 2]).upper(), 7.6, "F2", (0.2, 0.25, 0.35))
                for k, line in enumerate(vals[j]):
                    self.put(x + lw + 5, self.y - 13 - k * 12, line, 9.6, "F1", INK)
            self.ops.append("%.3f %.3f %.3f RG 0.4 w %.1f %.1f m %.1f %.1f l S" % (LINE + (MARGIN, self.y - h, MARGIN + USABLE, self.y - h)))
            self.y -= h
        self.y -= 8


def render(doc, path, logo=""):
    foot = "%s - %s - %s" % (getattr(doc, "title_plain", "") or doc.title, time.strftime("%Y-%m-%d %H:%M", time.localtime(doc.generated)), doc.copyright)
    p = PeriodicPDF(doc.title, foot)
    mark = doc.brand_logo or logo
    p.watermark = p.add_image(mark) if mark and os.path.isfile(mark) else None
    p.stamp_watermark(p.ops)                    # the first page was made before the logo was loaded
    p.header(doc, mark)
    for n, (title, blocks) in enumerate(doc.sections, 1):
        p.section(n, title)
        for b in blocks:
            if b.get("excel_only"):
                continue
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
