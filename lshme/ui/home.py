# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""vitals: the home screen (in the style of nmtui).

A live dashboard (CPU, memory, filesystems and mounts, network, disks, processes), the reports already
written with a score trend, run-a-check-now with progress, the server scan, help, and Configuration."""
import os
import time

from lshme import APP_NAME, VERSION
from lshme.core.config import as_list
from lshme.core.context import Context
from lshme.core.lock import LockBusy
from lshme.ops import discover, reportlist, sysstats
from lshme.ui import helptext, tui
from lshme.ui.tui_kit import MIN_COLS, MIN_ROWS, UI, ListScreen, Menu, run_curses, wrap

STATUS_ATTR = {"HEALTHY": "good", "OK": "good", "WARNING": "warn", "CRITICAL": "error", "UNKNOWN": "warn"}


def vbars(values, height, vmax=100.0, unicode_blocks=True):
    """Vertical bar chart rows (top row first), one character per value."""
    ramp = "▁▂▃▄▅▆▇█" if unicode_blocks else ".:-=+*##"
    step = vmax / float(height)
    rows = []
    for r in range(height):
        floor = vmax - (r + 1) * step  # value at the bottom of this row
        line = ""
        for v in values:
            if v >= floor + step:
                line += ramp[-1]
            elif v > floor:
                line += ramp[min(len(ramp) - 2, int((v - floor) / step * (len(ramp) - 1)))]
            else:
                line += " "
        rows.append(line)
    return rows


def clip(text, width):
    return text if len(text) <= width else text[:max(0, width - 1)] + "~"


# ---------------------------------------------------------------------------------------------
# live dashboard
# ---------------------------------------------------------------------------------------------
class Dashboard(object):
    TABS = ("Overview", "CPU & load", "Filesystems", "Network & disks", "Processes")

    def __init__(self, ctx, sampler=None, interval=2.0, tab=0, warm=True):
        self.ctx, self.ui = ctx, ctx.ui
        self.sampler = sampler or sysstats.Sampler()
        self.interval, self.tab, self.warm = interval, tab, warm
        self.paused, self.scroll, self.snap = False, 0, None
        self.health, self.health_at = None, 0.0
        th = ctx.cfg.threshold
        self.limits = {"cpu": (th("cpu", "warning", 85), th("cpu", "critical", 95)),
                       "mem": (th("memory", "warning", 85), th("memory", "critical", 95)),
                       "swap": (th("swap", "warning", 50), th("swap", "critical", 80)),
                       "disk": (th("disk", "warning", 80), th("disk", "critical", 90)),
                       "inode": (th("inode", "warning", 80), th("inode", "critical", 90))}
        self.os_name = ""
        for line in self.sampler.read("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                self.os_name = line.split("=", 1)[1].strip().strip('"')

    # -- helpers ------------------------------------------------------------------------
    def lvl(self, kind, value):
        warn, crit = self.limits[kind]
        return sysstats.level(value, warn, crit)

    def meter(self, y, x, w, label, value, text, level):
        b = self.ui.b
        b.put(y, x, label.ljust(5), "label")
        bw = max(8, w - 5 - len(text) - 1)
        self.ui.bar(y, x + 5, bw, value, level)
        b.put(y, x + 5 + bw + 1, text[:max(0, w - 6 - bw)], "val")

    def refresh_health(self):
        if time.time() - self.health_at > 30:
            self.health, self.health_at = reportlist.latest_summary(self.ctx.app.paths), time.time()

    # -- loop ---------------------------------------------------------------------------
    def run(self):
        self.snap = self.sampler.sample()
        if self.warm:
            time.sleep(0.4)  # a second sample straight away, so the first screen already shows rates
            self.snap = self.sampler.sample()
        while True:
            self.refresh_health()
            self.draw()
            key = self.ui.key(timeout=self.interval)
            if key is None:
                if not self.paused:
                    self.snap = self.sampler.sample()
                continue
            key = {"j": "DOWN", "k": "UP"}.get(key, key)
            if key in ("q", "ESC"):
                return
            if key in "12345" and len(key) == 1:
                self.tab, self.scroll = int(key) - 1, 0
            elif key in ("TAB", "RIGHT"):
                self.tab, self.scroll = (self.tab + 1) % len(self.TABS), 0
            elif key in ("BTAB", "LEFT"):
                self.tab, self.scroll = (self.tab - 1) % len(self.TABS), 0
            elif key in ("p", " "):
                self.paused = not self.paused
            elif key == "+":
                self.interval = max(0.5, self.interval / 2.0)
            elif key == "-":
                self.interval = min(10.0, self.interval * 2.0)
            elif key == "DOWN":
                self.scroll += 1
            elif key == "UP":
                self.scroll = max(0, self.scroll - 1)
            elif key == "PGDN":
                self.scroll += 10
            elif key == "PGUP":
                self.scroll = max(0, self.scroll - 10)
            elif key == "HOME":
                self.scroll = 0
            elif key == "r":
                for h in self.sampler.history.values():
                    del h[:]

    def draw(self):
        ui, b = self.ui, self.ui.b
        rows, cols = ui.size()
        ui.background("", "1-5 or Tab view   p %s   + / - speed (%.1fs)   Up/Down scroll   q back" % (
            "resume" if self.paused else "pause", self.interval))
        x = 1
        for i, name in enumerate(self.TABS):
            label = " %d %s " % (i + 1, name)
            b.put(1, x, label, "list_sel" if i == self.tab else "title")
            x += len(label) + 1
        if self.paused:
            b.put(0, max(0, cols - 10), " PAUSED ", "note_err")
        view = (self.overview, self.cpu_view, self.fs_view, self.net_view, self.proc_view)[self.tab]
        view(2, rows - 4, cols, self.snap)
        b.refresh()

    # -- view 1: overview ------------------------------------------------------------------
    def overview(self, y, H, W, s):
        ui, b = self.ui, self.ui.b
        cpu, mem = s["cpu"], s["mem"]
        ui.panel(y, 0, 3, W, "System")
        b.put(y + 1, 2, clip("%s   Up %s   Load %s   %d CPU%s   %s" % (
            s["hostname"], sysstats.fmt_uptime(s["uptime"]), " ".join("%.2f" % v for v in s["load"]), cpu["cores"],
            "" if cpu["cores"] == 1 else "s", self.os_name), W - 4), "val")
        y2 = y + 3
        ui.panel(y2, 0, 5, W, "CPU")
        self.meter(y2 + 1, 2, W - 4, "CPU", cpu["total"], "%5.1f%%" % cpu["total"], self.lvl("cpu", cpu["total"]))
        b.put(y2 + 2, 2, "trend", "dim")
        ui.spark(y2 + 2, 8, W - 10, s["history"]["cpu"], 100.0)
        b.put(y2 + 3, 2, clip("user %.1f%%   system %.1f%%   iowait %.1f%%   steal %.1f%%" % (
            cpu["user"], cpu["system"], cpu["iowait"], cpu["steal"]), W - 4), "label")
        y3 = y2 + 5
        ui.panel(y3, 0, 4, W, "Memory")
        self.meter(y3 + 1, 2, W - 4, "RAM", mem["pct"], "%5.1f%%  %s / %s" % (mem["pct"], sysstats.fmt_size(mem["used"]), sysstats.fmt_size(mem["total"])),
                   self.lvl("mem", mem["pct"]))
        if mem["swap_total"]:
            self.meter(y3 + 2, 2, W - 4, "Swap", mem["swap_pct"], "%5.1f%%  %s / %s" % (
                mem["swap_pct"], sysstats.fmt_size(mem["swap_used"]), sysstats.fmt_size(mem["swap_total"])), self.lvl("swap", mem["swap_pct"]))
        else:
            b.put(y3 + 2, 2, "Swap  none configured", "dim")
        y4 = y3 + 4
        h4 = H - (y4 - y)
        if h4 >= 4:
            wl = int(W * 0.58)
            self.fs_panel(y4, 0, h4, wl, s)
            hn = max(4, h4 // 2)
            self.net_panel(y4, wl, hn, W - wl, s)
            if h4 - hn >= 3:
                self.health_panel(y4 + hn, wl, h4 - hn, W - wl)

    def fs_panel(self, y, x, h, w, s):
        ui, b = self.ui, self.ui.b
        ui.panel(y, x, h, w, "Filesystems (fullest first)")
        mounts = sorted(s["mounts"], key=lambda m: -m["pct"])
        inner = h - 2
        self.scroll = min(self.scroll, max(0, len(mounts) - inner)) if self.tab == 0 else 0
        mw = min(16, max(8, w // 4))
        for i, m in enumerate(mounts[self.scroll:self.scroll + inner]):
            text = "%3.0f%% %s" % (m["pct"], sysstats.fmt_size(m["total"]))
            bw = max(4, w - 4 - mw - 1 - len(text) - 1)
            b.put(y + 1 + i, x + 2, clip(m["mount"], mw).ljust(mw), "label")
            ui.bar(y + 1 + i, x + 2 + mw + 1, bw, m["pct"], self.lvl("disk", m["pct"]))
            b.put(y + 1 + i, x + 2 + mw + 1 + bw + 1, text, "val")
        if len(mounts) > inner + self.scroll:
            b.put(y + h - 1, x + w - 8, " more ", "wtitle")

    def net_panel(self, y, x, h, w, s):
        ui, b = self.ui, self.ui.b
        ui.panel(y, x, h, w, "Network")
        ifs = [n for n in s["net"] if n["iface"] != "lo"]
        inner = h - 2
        for i, n in enumerate(ifs[:inner]):
            b.put(y + 1 + i, x + 2, clip("%-7s In %-9s Out %-9s" % (clip(n["iface"], 7), sysstats.fmt_rate(n["rx_bps"]),
                                                                    sysstats.fmt_rate(n["tx_bps"])), w - 4), "label" if n["state"] != "down" else "dim")
        if not ifs:
            b.put(y + 1, x + 2, "no network interfaces", "dim")

    def health_panel(self, y, x, h, w):
        ui, b = self.ui, self.ui.b
        ui.panel(y, x, h, w, "Last health check")
        hl = self.health
        if not hl:
            b.put(y + 1, x + 2, clip("none yet - run one from the menu", w - 4), "dim")
            return
        b.put(y + 1, x + 2, clip("Score %s/100  %s" % (hl["score"], hl["status"]), w - 4), STATUS_ATTR.get(hl["status"], "val"))
        if h > 3:
            c = hl["counts"]
            b.put(y + 2, x + 2, clip("Crit %d  Warn %d  %s" % (c["CRITICAL"], c["WARNING"], hl["when"][5:16]), w - 4), "label")

    # -- view 2: CPU and load --------------------------------------------------------------------
    def cpu_view(self, y, H, W, s):
        ui, b = self.ui, self.ui.b
        cpu = s["cpu"]
        top_h = max(8, H - 8)
        wl = W // 2
        ui.panel(y, 0, top_h, wl, "CPU cores (%d)" % cpu["cores"])
        inner = top_h - 2
        cols_n = max(1, -(-cpu["cores"] // inner))
        cw = (wl - 4) // cols_n
        for i, v in enumerate(cpu["per_core"][:inner * cols_n]):
            cx, cy = 2 + (i // inner) * cw, y + 1 + i % inner
            b.put(cy, cx, ("cpu%d" % i).ljust(5), "label")
            ui.bar(cy, cx + 5, max(4, cw - 14), v, self.lvl("cpu", v))
            b.put(cy, cx + 5 + max(4, cw - 14) + 1, "%3.0f%%" % v, "val")
        ui.panel(y, wl, top_h, W - wl, "Load and CPU time")
        rw = W - wl - 4
        per_core = s["load"][0] / max(1, cpu["cores"])
        b.put(y + 1, wl + 2, clip("Load  1m %.2f   5m %.2f   15m %.2f" % tuple(s["load"]), rw), "val")
        b.put(y + 2, wl + 2, clip("per core %.2f  (%d cores)" % (per_core, cpu["cores"]), rw), "label")
        ui.spark(y + 3, wl + 2, rw, s["history"]["load"], max(1.0, max(s["history"]["load"] or [1.0])))
        for i, (name, key) in enumerate((("user", "user"), ("system", "system"), ("iowait", "iowait"), ("steal", "steal"))):
            if y + 5 + i < y + top_h - 1:
                self.meter(y + 5 + i, wl + 2, rw, name, cpu[key], "%5.1f%%" % cpu[key], "ok" if key in ("user", "system") or cpu[key] < 10 else "warn")
        self.proc_table(y + top_h, 8, W, s["procs"]["top_cpu"], "Busiest processes", "cpu")

    def proc_table(self, y, h, w, procs, title, kind):
        ui, b = self.ui, self.ui.b
        ui.panel(y, 0, h, w, title)
        b.put(y + 1, 2, clip("%-7s %-10s %7s %10s  %s" % ("PID", "USER", "CPU%", "MEMORY", "COMMAND"), w - 4), "heading")
        for i, p in enumerate(procs[:h - 3]):
            b.put(y + 2 + i, 2, clip("%-7d %-10s %7.1f %10s  %s" % (p["pid"], clip(p["user"], 10), p["cpu"], sysstats.fmt_size(p["rss"]), p["name"]), w - 4), "label")

    # -- view 3: filesystems -----------------------------------------------------------------------
    def fs_view(self, y, H, W, s):
        ui, b = self.ui, self.ui.b
        ui.panel(y, 0, H, W, "Filesystems and mounts (%d)" % len(s["mounts"]))
        wide = W >= 100
        # fixed columns first, the usage bar takes whatever is left (10 characters at 80 columns)
        cols = [("Mounted on", 16), ("Type", 6), ("Size", 8), ("Used", 8), ("Avail", 8), ("Use%", 4)]
        if wide:
            cols.insert(0, ("Device", 20))
        tail = [("Inode%", 6), ("Flags", 5)]
        used_w = sum(w for _, w in cols) + len(cols) + sum(w for _, w in tail) + len(tail)
        bar_w = max(6, W - 4 - used_w)
        offs, x = [], 2
        for name, w in cols:
            offs.append(x)
            x += w + 1
        bar_x = x
        tail_x = bar_x + bar_w + 1
        for (name, w), ox in zip(cols, offs):
            b.put(y + 1, ox, name[:w], "heading")
        b.put(y + 1, bar_x, "Usage"[:bar_w], "heading")
        b.put(y + 1, tail_x, "Inode%", "heading")
        b.put(y + 1, tail_x + 7, "Flags", "heading")
        inner = H - 3
        mounts = s["mounts"]
        self.scroll = min(self.scroll, max(0, len(mounts) - inner))
        for i, m in enumerate(mounts[self.scroll:self.scroll + inner]):
            row = y + 2 + i
            values = [m["device"], m["mount"], m["fstype"], sysstats.fmt_size(m["total"]), sysstats.fmt_size(m["used"]),
                      sysstats.fmt_size(m["free"]), "%.0f%%" % m["pct"]]
            if not wide:
                values = values[1:]
            for (name, w), ox, v in zip(cols, offs, values):
                b.put(row, ox, clip(v, w), "label")
            ui.bar(row, bar_x, bar_w, m["pct"], self.lvl("disk", m["pct"]))
            bad = m["error"] or m["inode_pct"] >= self.limits["inode"][1]
            b.put(row, tail_x, "%5.0f%%" % m["inode_pct"], "error" if bad else "label")
            b.put(row, tail_x + 7, ("RO " if m["readonly"] else "") + ("ERR" if m["error"] else ""), "error" if m["error"] else "label")
        if len(mounts) > inner:
            b.put(y + H - 1, W - 22, " %d-%d of %d " % (self.scroll + 1, min(len(mounts), self.scroll + inner), len(mounts)), "wtitle")

    # -- view 4: network and disks ------------------------------------------------------------------
    def net_view(self, y, H, W, s):
        ui, b = self.ui, self.ui.b
        nets = s["net"]
        nh = min(H // 2 + 1, len(nets) + 6)
        ui.panel(y, 0, nh, W, "Network interfaces")
        b.put(y + 1, 2, clip("%-12s %-8s %12s %12s %12s %12s %8s" % ("Interface", "State", "In /s", "Out /s", "Total in", "Total out", "Errors"), W - 4), "heading")
        for i, n in enumerate(nets[:max(0, nh - 6)]):
            b.put(y + 2 + i, 2, clip("%-12s %-8s %12s %12s %12s %12s %8d" % (clip(n["iface"], 12), n["state"], sysstats.fmt_rate(n["rx_bps"]),
                                                                              sysstats.fmt_rate(n["tx_bps"]), sysstats.fmt_size(n["rx_total"]),
                                                                              sysstats.fmt_size(n["tx_total"]), n["errors"]), W - 4), "label")
        sy = y + nh - 3
        b.put(sy, 2, "In ", "dim")
        ui.spark(sy, 5, W - 8, s["history"]["rx"])
        b.put(sy + 1, 2, "Out", "dim")
        ui.spark(sy + 1, 5, W - 8, s["history"]["tx"])
        dy, dh = y + nh, H - nh
        if dh >= 4:
            ui.panel(dy, 0, dh, W, "Disk I/O")
            b.put(dy + 1, 2, clip("%-14s %12s %12s   %s" % ("Device", "Read /s", "Write /s", "Busy"), W - 4), "heading")
            for i, d in enumerate(s["disks"][:dh - 3]):
                b.put(dy + 2 + i, 2, "%-14s %12s %12s" % (clip(d["name"], 14), sysstats.fmt_rate(d["read_bps"]), sysstats.fmt_rate(d["write_bps"])), "label")
                self.ui.bar(dy + 2 + i, 2 + 14 + 1 + 12 + 1 + 12 + 3, max(8, W - 48), d["util"], self.lvl("cpu", d["util"]))
                b.put(dy + 2 + i, W - 8, "%4.0f%%" % d["util"], "val")
            if not s["disks"]:
                b.put(dy + 2, 2, "no physical disks reported", "dim")

    # -- view 5: processes --------------------------------------------------------------------------
    def proc_view(self, y, H, W, s):
        ui, b = self.ui, self.ui.b
        p = s["procs"]
        top_h = H // 2
        for py, ph, title, rows in ((y, top_h, "Top by CPU (%d processes, %d running)" % (p["total"], p["running"]), p["top_cpu"]),
                                    (y + top_h, H - top_h, "Top by memory", p["top_mem"])):
            ui.panel(py, 0, ph, W, title)
            b.put(py + 1, 2, clip("%-7s %-12s %7s %11s  %s" % ("PID", "USER", "CPU%", "MEMORY", "COMMAND"), W - 4), "heading")
            for i, r in enumerate(rows[:ph - 3]):
                b.put(py + 2 + i, 2, clip("%-7d %-12s %7.1f %11s  %s" % (r["pid"], clip(r["user"], 12), r["cpu"], sysstats.fmt_size(r["rss"]), r["name"]), W - 4), "label")


# ---------------------------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------------------------
def report_row(r):
    score = "-" if r["score"] is None else "%d" % r["score"]
    return "%s  %-10s %-9s %3s  C%-2d W%-2d  %2d files" % (r["when"][:16], r["category"][:10], (r["status"] or "-")[:9], score, r["critical"], r["warning"], len(r["files"]))


def show_report(ctx, r):
    ui = ctx.ui
    files = [os.path.join(r["dir"], f) for f in r["files"]]
    if r["json"]:
        try:
            lines = reportlist.run_lines(reportlist.load_run(r["json"]), files)
        except (OSError, ValueError) as e:
            lines = ["Could not read %s: %s" % (r["json"], e), ""] + files
    else:
        lines = ["No JSON result in this report folder, so no summary can be shown.", "", "Report files"] + files
    lines += ["", "Open the .html file in a browser, or copy the folder with scp / WinSCP."]
    ui.view("Report - %s - %s" % (r["category"], r["when"][:16]), lines)


def reports_screen(ctx):
    ui = ctx.ui
    paths = ctx.app.paths
    items = reportlist.list_reports(paths)
    series = reportlist.score_series(paths, 365)
    idx = top = 0
    while True:
        rows, cols = ui.size()
        ui.background("", "Up/Down move   Enter open report   r reload   q back")
        ch = 8
        ui.panel(1, 0, ch, cols, "Health score over time (%d runs)" % len(series))
        vals = [v for _, v in series][-(cols - 12):]
        for i, label in enumerate(("100", " 50", "  0")):
            ui.b.put(2 + (0, 2, 4)[i], 2, label, "dim")
        for i, line in enumerate(vbars(vals, ch - 3, 100.0, getattr(ui.b, "unicode", False))):
            ui.b.put(2 + i, 7, line, "val")
        if series:
            first, last = time.strftime("%Y-%m-%d", time.localtime(series[-len(vals)][0])), time.strftime("%Y-%m-%d %H:%M", time.localtime(series[-1][0]))
            ui.b.put(ch, 7, clip("%s  ->  %s    latest %s" % (first, last, series[-1][1]), cols - 10), "dim")
        else:
            ui.b.put(3, 7, "No history yet - run a health check first.", "dim")
        ly, lh = 1 + ch, rows - 3 - ch
        ui.panel(ly, 0, lh, cols, "Reports (%d), newest first" % len(items))
        inner = lh - 3
        idx = max(0, min(idx, len(items) - 1))
        top = min(max(0, idx - inner + 1) if idx >= top + inner else top, max(0, len(items) - inner))
        if idx < top:
            top = idx
        ui.b.put(ly + 1, 2, clip("%-16s  %-10s %-9s %3s  %-6s %-6s %s" % ("When", "Schedule", "Status", "Score", "Crit", "Warn", "Files"), cols - 4), "heading")
        for i, r in enumerate(items[top:top + inner]):
            ui.b.put(ly + 2 + i, 2, clip(" " + report_row(r), cols - 4).ljust(cols - 4), "list_sel" if top + i == idx else "label")
        if not items:
            ui.b.put(ly + 2, 2, "No reports yet. Choose 'Run a health check now' in the main menu.", "dim")
        ui.b.refresh()
        key = ui.key()
        key = {"j": "DOWN", "k": "UP"}.get(key, key)
        if key in ("ESC", "q"):
            return
        if key == "DOWN":
            idx += 1
        elif key == "UP":
            idx -= 1
        elif key == "PGDN":
            idx += inner
        elif key == "PGUP":
            idx -= inner
        elif key == "HOME":
            idx = 0
        elif key == "END":
            idx = len(items) - 1
        elif key == "r":
            items, series = reportlist.list_reports(paths), reportlist.score_series(paths, 365)
        elif key == "ENTER" and items:
            show_report(ctx, items[idx])


# ---------------------------------------------------------------------------------------------
# run a health check now
# ---------------------------------------------------------------------------------------------
def run_now(ctx):
    ui, app = ctx.ui, ctx.app

    def progress(i, n, title):
        r = ui.window(7, 62, "Health check running")
        ui.b.put(r.y + 2, r.x + 2, clip("Checking: %s" % title, r.w - 4), "label")
        ui.bar(r.y + 4, r.x + 2, r.w - 13, i, "ok", maximum=max(1, n))
        ui.b.put(r.y + 4, r.x + r.w - 9, "%d / %d" % (i, n), "val")
        ui.b.refresh()
    if not ui.confirm("Run a complete health check now and write the reports?\nIt usually takes under a minute.", "Run a health check", True):
        return
    try:
        run, result = app.full_run(None, 1, None, None, None, progress=progress)
    except LockBusy:
        ui.message("Another health check is already running. Try again in a minute.", "Run a health check")
        return
    except (OSError, ValueError) as e:
        ui.message("The health check could not be completed: %s" % e, "Run a health check", "error")
        return
    app.reload()
    c = run.counts()
    ui.message("Score %s / 100 - %s\nCritical %d, warning %d.\n\nReports are in:\n%s" % (
        run.health["score"], run.health["status"], c["CRITICAL"], c["WARNING"], result["report_dir"] or "(no report files written)"),
        "Health check complete")
    if ui.confirm("View the results now?", "Results", True):
        ui.view("Health check - %s" % run.host, reportlist.run_lines(run, result["files"]))


# ---------------------------------------------------------------------------------------------
# server scan
# ---------------------------------------------------------------------------------------------
def scan_screen(ctx):
    from lshme import modules as modreg
    ui, app = ctx.ui, ctx.app
    state = {"profile": discover.load_profile(app.paths)}

    def do_scan():
        ui.working("Scanning this server...")
        state["profile"] = discover.scan(Context(app.cfg, app.paths, app.runner))

    def intro():
        p = state["profile"]
        inv = p["inventory"]
        cfg = app.cfg
        when = ("every %s at %s" % (cfg.get("main", "scan", "weekday", "sun").capitalize(), cfg.get("main", "scan", "time", "02:00"))
                if cfg.getbool("main", "scan", "enabled", True) else "switched off")
        return ("%s: %s, %d CPU, %d MB RAM, %s.\nScanned %s. Automatic rescan: %s.\n"
                "Enter on a module that was not found lets you check it anyway. Apply stores the result."
                % (inv["hostname"], inv["os"], inv["cpus"], inv["memory_mb"], inv["virtualization"], p["scanned_at_text"], when))

    def rows():
        forced = set(as_list(app.cfg.get("main", "modules", "force_enabled", "")))
        out = []
        for name in modreg.MODULE_ORDER:
            m = state["profile"]["modules"].get(name)
            if m:
                status = "checked" if m["present"] else ("checked (forced on)" if name in forced else "not present - left out")
                out.append((name, "%-27s %-23s %s" % (m["title"][:27], status, m["reason"])))
        return out

    def toggle(name):
        m = state["profile"]["modules"][name]
        if m["present"]:
            ui.message("'%s' is checked because it was found on this server." % m["title"], "Server scan")
            return
        forced = as_list(app.cfg.get("main", "modules", "force_enabled", ""))
        if name in forced:
            forced.remove(name)
        elif ui.confirm("Nothing was found for '%s'. Check it anyway? It will then appear in reports." % m["title"], "Check anyway", False):
            forced.append(name)
        else:
            return
        app.cfg.set("main", "modules", "force_enabled", ",".join(forced))
        try:
            ui.note(tui.persist(ctx, ["main"]))
        except ValueError as e:
            ui.message(str(e), "Cannot save", "error")

    def rescan(_sel):
        do_scan()
        screen.intro = intro()

    def apply_(_sel):
        try:
            changes, excluded = discover.apply(app, state["profile"])
        except OSError as e:
            ui.message("Cannot store the scan: %s\nRun as root (sudo vitals)." % e, "Server scan", "error")
            return
        text = "%d module(s) with nothing to check are now left out of every scan and report." % len(excluded)
        if changes:
            text += "\n\nChanged since the last scan:\n" + "\n".join("  " + c for c in changes)
        ui.message(text, "Scan applied")

    if state["profile"] is None:
        do_scan()
    screen = ListScreen(ui, "Server scan - what is present", rows, edit=toggle, extra=[("Rescan", rescan), ("Apply", apply_)], intro=intro())
    screen.run()


# ---------------------------------------------------------------------------------------------
# the main menu
# ---------------------------------------------------------------------------------------------
def menu_intro(ctx):
    import socket
    hl = reportlist.latest_summary(ctx.app.paths)
    first = "%s    %s v%s" % (socket.gethostname(), APP_NAME, VERSION)
    if not hl:
        return first + "\nNo health check has been run yet - choose 'Run a health check now'."
    c = hl["counts"]
    return first + "\nLast health check %s: %s/100 %s  (critical %d, warning %d)" % (hl["when"][:16], hl["score"], hl["status"], c["CRITICAL"], c["WARNING"])


def wait_for_size(ctx):
    rows, cols = ctx.ui.size()
    while rows < MIN_ROWS or cols < MIN_COLS:
        ctx.ui.background("", "Esc quit")
        ctx.ui.b.put(2, 2, "Terminal too small: %dx%d (need at least %dx%d)." % (cols, rows, MIN_COLS, MIN_ROWS), "title")
        ctx.ui.b.put(3, 2, "Make the window larger.", "title")
        ctx.ui.b.refresh()
        if ctx.ui.key() == "ESC":
            return False
        rows, cols = ctx.ui.size()
    return True


def pages(ctx):
    """[(name, menu label, help, function)]; the name also works as 'vitals <name>'."""
    def setup():
        from lshme.ui import firstrun
        firstrun.run(ctx, rerun=True)

    return [
        ("dashboard", "Live dashboard", "CPU, memory, filesystems, mounts, network, disks and processes, updating live.", lambda: Dashboard(ctx).run()),
        ("reports", "Reports", "Browse the health reports already written, with the score trend.", lambda: reports_screen(ctx)),
        ("run", "Run a health check now", "Check this server now and write the reports.", lambda: run_now(ctx)),
        ("scan", "Server scan", "What is present on this server, and what is left out of scans and reports.", lambda: scan_screen(ctx)),
        ("web", "Web interface", "Use these pages in a browser: address, encryption, passwords and the service.", lambda: tui.page_web(ctx)),
        ("config", "Configuration", "Change any setting: identity, thresholds, email, schedules, alerts, databases...", lambda: tui.start(ctx)),
        ("setup", "First-time setup", "Go through the setup pages again, one at a time.", setup),
        ("help", "Help", "Commands, keys and where things are.", lambda: ctx.ui.view("Help", helptext.full_help())),
    ]


def start(ctx, page=None):
    """The main menu (or one screen directly). Used by run() and by tests with a scripted backend."""
    if not wait_for_size(ctx):
        return
    items = pages(ctx)
    if page:
        found = dict((k, fn) for k, _, _, fn in items)
        if page not in found:
            raise ValueError("unknown screen '%s' (choose from %s)" % (page, ", ".join(k for k, _, _, _ in items)))
        found[page]()
        return
    Menu(ctx.ui, APP_NAME, [(label, help_, fn) for _, label, help_, fn in items], intro=lambda: menu_intro(ctx), back="Quit").run()


def run(app, page=None):
    """Open the health screen. Returns (True, "") or (False, reason) when there is no usable terminal."""
    ok, why = tui.available()
    if not ok:
        return False, why

    def main(backend):
        ui = UI(backend, "%s  v%s" % (APP_NAME, VERSION))
        ctx = tui.Ctx(app, ui)
        ui.on_error = lambda e: app.log.exception("vitals screen failed")
        if page is None:
            from lshme.ui import firstrun
            if firstrun.needed(app) and wait_for_size(ctx):
                firstrun.run(ctx)
        start(ctx, page)
    run_curses(main)
    return True, ""
