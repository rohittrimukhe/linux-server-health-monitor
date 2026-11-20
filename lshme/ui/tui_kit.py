# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Full-screen text-UI toolkit in the style of nmtui (Python curses, standard library only).

Everything except CursesBackend is plain Python that talks to a small Backend interface, so every
screen can be driven by scripted keys in tests without a terminal.

Backend interface: size() erase() fill(y, x, h, w, attr) put(y, x, text, attr) box(y, x, h, w, attr)
refresh() key() set_cursor(y, x | None) suspend().  key() returns a name: "UP" "DOWN" "LEFT" "RIGHT"
"HOME" "END" "PGUP" "PGDN" "ENTER" "ESC" "TAB" "BTAB" "BACKSPACE" "DELETE" "F1".."F12" "CTRL_A".."CTRL_Z"
"RESIZE", or the single character typed.
"""
import contextlib
import os
import textwrap
from collections import namedtuple

Rect = namedtuple("Rect", "y x h w")

MIN_ROWS, MIN_COLS = 20, 70
YES = ("yes", "no")


def printable(key):
    return len(key) == 1 and key >= " " and key != "\x7f"


def wrap(text, width):
    out = []
    for para in str(text).split("\n"):
        out.extend(textwrap.wrap(para, max(10, width)) or [""])
    return out


# ---------------------------------------------------------------------------------------------
# keyboard: terminals send the same key in different ways (ESC [ A or ESC O A for Up, ESC [ 5 ~ for
# PgUp, ...), and curses only knows the one in its terminfo. Reading raw characters and decoding the
# sequences here works with PuTTY, plink, Windows Terminal, xterm and tmux alike.
# ---------------------------------------------------------------------------------------------
CSI_LETTERS = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT", "H": "HOME", "F": "END", "Z": "BTAB"}
CSI_NUMBERS = {"1": "HOME", "2": "INSERT", "3": "DELETE", "4": "END", "5": "PGUP", "6": "PGDN", "7": "HOME", "8": "END",
               "11": "F1", "12": "F2", "13": "F3", "14": "F4", "15": "F5", "17": "F6", "18": "F7", "19": "F8",
               "20": "F9", "21": "F10", "23": "F11", "24": "F12"}
SS3_LETTERS = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT", "H": "HOME", "F": "END", "M": "ENTER",
               "P": "F1", "Q": "F2", "R": "F3", "S": "F4"}


def parse_escape(read, unread):
    """Decode what follows an ESC byte. read() returns the next character or None if nothing more arrives
    straight away; unread(ch) puts one back. Returns a key name ("ESC" when ESC was pressed on its own)."""
    c = read()
    if c is None:
        return "ESC"
    if c == "[":  # CSI: ESC [ <digits and ;> <final letter or ~>
        seq = ""
        while len(seq) < 12:
            c = read()
            if c is None:
                return "ESC"
            seq += c
            if c.isalpha() or c == "~":
                break
        if seq.endswith("~"):
            return CSI_NUMBERS.get(seq[:-1].split(";")[0], "UNKNOWN")
        return CSI_LETTERS.get(seq[-1], "UNKNOWN")  # also covers modified keys such as ESC [ 1 ; 2 B
    if c == "O":  # SS3: ESC O <letter> (application cursor mode, F1-F4)
        c = read()
        return "ESC" if c is None else SS3_LETTERS.get(c, "UNKNOWN")
    unread(c)  # ESC followed by an ordinary key (Alt+key): treat as Esc, then deliver the key
    return "ESC"


# ---------------------------------------------------------------------------------------------
# curses backend
# ---------------------------------------------------------------------------------------------
class CursesBackend(object):
    PALETTE = (  # name, foreground, background, bold
        ("screen", "white", "blue", False), ("title", "white", "blue", True),
        ("help", "white", "blue", False), ("note_err", "white", "red", True),
        ("window", "black", "white", False), ("wtitle", "black", "white", True),
        ("label", "black", "white", False), ("heading", "blue", "white", True),
        ("field", "white", "blue", False), ("field_sel", "black", "cyan", False),
        ("edit", "black", "green", False), ("button", "white", "blue", False),
        ("button_sel", "white", "red", True), ("error", "red", "white", True),
        ("list_sel", "white", "blue", True), ("dim", "blue", "white", False),
    )
    FALLBACK = {"title": "bold", "wtitle": "bold", "heading": "bold", "error": "bold", "note_err": "bold",
                "field": "underline", "field_sel": "reverse", "edit": "reverse", "button": "normal",
                "button_sel": "reverse", "list_sel": "reverse"}

    def __init__(self, scr):
        import curses
        self.curses = curses
        self.scr = scr
        self.cursor = None
        scr.keypad(False)  # raw characters: parse_escape decodes the keys, whatever the terminal sends
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.attrs = {}
        colour = curses.has_colors()
        if colour:
            names = {"black": curses.COLOR_BLACK, "white": curses.COLOR_WHITE, "blue": curses.COLOR_BLUE,
                     "red": curses.COLOR_RED, "cyan": curses.COLOR_CYAN, "green": curses.COLOR_GREEN}
            for i, (name, fg, bg, bold) in enumerate(self.PALETTE, 1):
                curses.init_pair(i, names[fg], names[bg])
                self.attrs[name] = curses.color_pair(i) | (curses.A_BOLD if bold else 0)
        else:
            extra = {"bold": curses.A_BOLD, "underline": curses.A_UNDERLINE, "reverse": curses.A_REVERSE, "normal": 0}
            for name, _, _, _ in self.PALETTE:
                self.attrs[name] = extra[self.FALLBACK.get(name, "normal")]

    def size(self):
        return self.scr.getmaxyx()

    def erase(self):
        self.scr.erase()

    def attr(self, name):
        return self.attrs.get(name, 0)

    def put(self, y, x, text, attr="window"):
        rows, cols = self.size()
        if y < 0 or y >= rows or x >= cols or not text:
            return
        if x < 0:
            text, x = text[-x:], 0
        try:
            self.scr.addstr(y, x, text[:cols - x], self.attr(attr))
        except self.curses.error:
            pass  # writing the bottom-right cell raises although the text is drawn

    def fill(self, y, x, h, w, attr="window"):
        for row in range(y, y + h):
            self.put(row, x, " " * w, attr)

    def box(self, y, x, h, w, attr="window"):
        c, a = self.curses, self.attr(attr)
        try:
            for i in range(1, w - 1):
                self.scr.addch(y, x + i, c.ACS_HLINE, a)
                self.scr.addch(y + h - 1, x + i, c.ACS_HLINE, a)
            for i in range(1, h - 1):
                self.scr.addch(y + i, x, c.ACS_VLINE, a)
                self.scr.addch(y + i, x + w - 1, c.ACS_VLINE, a)
            self.scr.addch(y, x, c.ACS_ULCORNER, a)
            self.scr.addch(y, x + w - 1, c.ACS_URCORNER, a)
            self.scr.addch(y + h - 1, x, c.ACS_LLCORNER, a)
            self.scr.addch(y + h - 1, x + w - 1, c.ACS_LRCORNER, a)
        except c.error:
            pass

    def set_cursor(self, pos):
        self.cursor = pos

    def refresh(self):
        c = self.curses
        try:
            if self.cursor:
                c.curs_set(1)
                self.scr.move(*self.cursor)
            else:
                c.curs_set(0)
        except c.error:
            pass
        self.scr.refresh()

    def key(self):
        c = self.curses
        while True:
            try:
                self.scr.timeout(-1)
                k = self.scr.get_wch()
            except KeyboardInterrupt:
                return "ESC"
            except c.error:
                continue
            if isinstance(k, int):
                return "RESIZE" if k == c.KEY_RESIZE else "UNKNOWN"
            if k in ("\n", "\r"):
                return "ENTER"
            if k == "\t":
                return "TAB"
            if k == "\x1b":
                return self.escape_key()
            if k in ("\x7f", "\b"):
                return "BACKSPACE"
            if len(k) == 1 and ord(k) < 32:
                return "CTRL_" + chr(ord(k) + 64)
            return k

    def escape_key(self):
        """After an ESC byte: wait a moment for the rest of a key sequence (they arrive together)."""
        c = self.curses

        def read():
            self.scr.timeout(60)  # a lone Esc is recognised after this long
            try:
                k = self.scr.get_wch()
            except c.error:
                return None
            return k if isinstance(k, str) else None
        try:
            return parse_escape(read, lambda ch: c.unget_wch(ch))
        finally:
            self.scr.timeout(-1)

    @contextlib.contextmanager
    def suspend(self):
        """Leave full-screen mode so ordinary console code can print, then come back."""
        c = self.curses
        c.def_prog_mode()
        c.endwin()
        try:
            yield
        finally:
            c.reset_prog_mode()
            self.scr.clear()
            self.scr.refresh()


def run_curses(main):
    """Run main(backend) inside curses; returns whatever main returns."""
    import curses
    os.environ.setdefault("ESCDELAY", "25")  # a lone Esc must not feel sluggish

    def wrapper(scr):
        return main(CursesBackend(scr))
    return curses.wrapper(wrapper)


# ---------------------------------------------------------------------------------------------
# line editing
# ---------------------------------------------------------------------------------------------
class LineEdit(object):
    def __init__(self, text="", secret=False):
        self.text, self.pos, self.secret = text, len(text), secret

    def handle(self, key):
        """Returns "done" (Enter), "cancel" (Esc) or None."""
        t, p = self.text, self.pos
        if key == "ENTER":
            return "done"
        if key == "ESC":
            return "cancel"
        if printable(key):
            self.text, self.pos = t[:p] + key + t[p:], p + 1
        elif key == "BACKSPACE" and p:
            self.text, self.pos = t[:p - 1] + t[p:], p - 1
        elif key == "DELETE":
            self.text = t[:p] + t[p + 1:]
        elif key == "LEFT":
            self.pos = max(0, p - 1)
        elif key == "RIGHT":
            self.pos = min(len(t), p + 1)
        elif key in ("HOME", "CTRL_A"):
            self.pos = 0
        elif key in ("END", "CTRL_E"):
            self.pos = len(t)
        elif key == "CTRL_U":
            self.text, self.pos = t[p:], 0
        elif key == "CTRL_K":
            self.text = t[:p]
        return None

    def view(self, width):
        shown = "*" * len(self.text) if self.secret else self.text
        start = max(0, self.pos - width + 1)
        return shown[start:start + width], self.pos - start


# ---------------------------------------------------------------------------------------------
# form items
# ---------------------------------------------------------------------------------------------
class Heading(object):
    def __init__(self, text):
        self.text = text


class Field(object):
    """One editable value. kind: text int float bool choice multi readonly. Values are strings;
    bool is "yes"/"no" and multi a comma-separated list."""

    def __init__(self, label, kind="text", value="", help="", choices=None, lo=None, hi=None, validate=None,
                 secret=False, has_value=False, optional=False, bind=None, new=False):
        self.label, self.kind, self.help = label, kind, help
        self.choices = [c if isinstance(c, tuple) else (c, c or "(default)") for c in (choices or [])]
        self.lo, self.hi, self.validate = lo, hi, validate
        self.secret, self.optional, self.bind = secret, optional, bind
        self.value = "" if value is None else str(value)
        self.original = None if new else self.value  # None: a new item, so every value must be written
        self.has_secret = bool(secret and has_value)
        self.bad = False

    @property
    def readonly(self):
        return self.kind == "readonly"

    def changed(self):
        if self.readonly:
            return False
        return self.value != self.original

    def commit(self):
        self.original = self.value
        if self.secret and self.value:
            self.has_secret = True

    def error(self):
        v = self.value
        if self.readonly:
            return None
        if self.kind in ("int", "float"):
            if v == "":
                return None if self.optional else "enter a number"
            try:
                f = float(v)
            except ValueError:
                return "enter a number"
            if self.kind == "int" and f != int(f):
                return "enter a whole number"
            if (self.lo is not None and f < self.lo) or (self.hi is not None and f > self.hi):
                return "enter a value between %s and %s" % (self.lo if self.lo is not None else "-", self.hi if self.hi is not None else "-")
        elif self.kind == "bool":
            if v not in YES:
                return "choose yes or no"
        elif self.kind == "choice":
            if v not in [c[0] for c in self.choices]:
                return "choose one of: %s" % ", ".join(c[0] for c in self.choices if c[0])
        elif self.kind == "multi":
            allowed = [c[0] for c in self.choices]
            bad = [x for x in self.items() if x not in allowed]
            if bad:
                return "unknown: %s" % ", ".join(bad)
        if self.validate:
            return self.validate(v)
        return None

    def items(self):
        return [x.strip() for x in self.value.split(",") if x.strip()]

    def display(self, width):
        if self.secret:
            text = "******" if (self.value or self.has_secret) else ""
        elif self.kind == "bool":
            text = "[X] yes" if self.value == "yes" else "[ ] no"
        elif self.kind == "choice":
            label = dict(self.choices).get(self.value, self.value)
            text = "< %s >" % label
        elif self.kind == "multi":
            text = self.value or "(none)"
        else:
            text = self.value
        return text if len(text) <= width else text[:max(0, width - 1)] + "~"


# ---------------------------------------------------------------------------------------------
# the screen controller
# ---------------------------------------------------------------------------------------------
class UI(object):
    def __init__(self, backend, title="LSHME configuration"):
        self.b = backend
        self.title = title
        self.note_text, self.note_kind = "", "info"
        self.on_error = None  # called with the exception when a menu action fails unexpectedly

    def working(self, text):
        """Show a small 'please wait' box (no key needed) before a slow operation."""
        r = self.window(5, max(30, len(text) + 6), "Please wait")
        self.b.put(r.y + 2, r.x + 2, text[:r.w - 4], "label")
        self.b.refresh()

    # -- footer notes ------------------------------------------------------------------
    def note(self, text, kind="info"):
        self.note_text, self.note_kind = text, kind

    def clear_note(self):
        self.note_text = ""

    def size(self):
        return self.b.size()

    # -- frame ---------------------------------------------------------------------------
    def background(self, help="", hints=""):
        rows, cols = self.size()
        self.b.erase()
        self.b.set_cursor(None)
        self.b.fill(0, 0, rows, cols, "screen")
        title = "  %s  " % self.title
        self.b.put(0, max(0, (cols - len(title)) // 2), title, "title")
        self.b.put(rows - 2, 1, help[:cols - 2], "help")
        if self.note_text:
            self.b.fill(rows - 1, 0, 1, cols, "note_err" if self.note_kind == "error" else "title")
            self.b.put(rows - 1, 1, self.note_text[:cols - 2], "note_err" if self.note_kind == "error" else "title")
        else:
            self.b.put(rows - 1, 1, hints[:cols - 2], "help")

    def window(self, h, w, title=""):
        rows, cols = self.size()
        h, w = max(5, min(h, rows - 3)), max(20, min(w, cols - 2))  # row 0 is the title, the last two rows the footer
        y = 1 + max(0, (rows - 3 - h) // 2)
        x = max(0, (cols - w) // 2)
        self.b.fill(y, x, h, w, "window")
        self.b.box(y, x, h, w, "window")
        if title:
            t = " %s " % title[:w - 6]
            self.b.put(y, x + max(2, (w - len(t)) // 2), t, "wtitle")
        return Rect(y, x, h, w)

    def buttons(self, y, x, w, labels, focus):
        """Draw a centred row of buttons; returns nothing. focus: index or None."""
        texts = ["< %s >" % l for l in labels]
        total = sum(len(t) for t in texts) + 2 * (len(texts) - 1)
        cx = x + max(0, (w - total) // 2)
        for i, t in enumerate(texts):
            self.b.put(y, cx, t, "button_sel" if i == focus else "button")
            cx += len(t) + 2

    # -- popups ----------------------------------------------------------------------------
    def message(self, text, title="Message", kind="info"):
        _, cols = self.size()
        lines = wrap(text, min(64, cols - 10))
        while True:
            r = self.window(len(lines) + 5, max(34, max(len(l) for l in lines) + 6), title)
            for i, l in enumerate(lines[:r.h - 4]):
                self.b.put(r.y + 1 + i, r.x + 2, l, "error" if kind == "error" else "label")
            self.buttons(r.y + r.h - 2, r.x, r.w, ["OK"], 0)
            self.b.refresh()
            if self.b.key() in ("ENTER", "ESC", " ", "q"):
                return

    def confirm(self, text, title="Confirm", default=True):
        _, cols = self.size()
        lines = wrap(text, min(60, cols - 10))
        focus = 0 if default else 1
        while True:
            r = self.window(len(lines) + 5, max(34, max(len(l) for l in lines) + 6), title)
            for i, l in enumerate(lines[:r.h - 4]):
                self.b.put(r.y + 1 + i, r.x + 2, l, "label")
            self.buttons(r.y + r.h - 2, r.x, r.w, ["Yes", "No"], focus)
            self.b.refresh()
            k = self.b.key()
            if k in ("LEFT", "RIGHT", "TAB", "BTAB", "UP", "DOWN"):
                focus = 1 - focus
            elif k in ("y", "Y"):
                return True
            elif k in ("n", "N", "ESC"):
                return False
            elif k == "ENTER":
                return focus == 0

    def choose(self, title, options, current=None):
        """options: strings or (value, label). Returns the value, or None when cancelled."""
        opts = [o if isinstance(o, tuple) else (o, o or "(default)") for o in options]
        idx = next((i for i, o in enumerate(opts) if o[0] == current), 0)
        top = 0
        while True:
            r = self.window(len(opts) + 4, max(30, max(len(o[1]) for o in opts) + 8), title)
            bh = r.h - 4
            top = min(max(0, idx - bh + 1) if idx >= top + bh else top, max(0, len(opts) - bh))
            if idx < top:
                top = idx
            for k in range(bh):
                i = top + k
                if i >= len(opts):
                    break
                self.b.put(r.y + 1 + k, r.x + 2, (" %s" % opts[i][1]).ljust(r.w - 4)[:r.w - 4], "list_sel" if i == idx else "label")
            self.b.put(r.y + r.h - 2, r.x + 2, "Enter select   Esc cancel", "dim")
            self.b.refresh()
            k = self.b.key()
            if k == "UP":
                idx = max(0, idx - 1)
            elif k == "DOWN":
                idx = min(len(opts) - 1, idx + 1)
            elif k == "PGUP":
                idx = max(0, idx - bh)
            elif k == "PGDN":
                idx = min(len(opts) - 1, idx + bh)
            elif k == "HOME":
                idx = 0
            elif k == "END":
                idx = len(opts) - 1
            elif k == "ENTER":
                return opts[idx][0]
            elif k == "ESC":
                return None
            elif printable(k):
                order = list(range(idx + 1, len(opts))) + list(range(0, idx + 1))
                for i in order:
                    if opts[i][1].lower().startswith(k.lower()):
                        idx = i
                        break

    def checklist(self, title, options, selected):
        """options: strings or (value, label). Returns the chosen values (in option order) or None."""
        opts = [o if isinstance(o, tuple) else (o, o) for o in options]
        on = set(selected)
        idx, top = 0, 0
        while True:
            r = self.window(len(opts) + 5, max(34, max(len(o[1]) for o in opts) + 10), title)
            bh = r.h - 5
            if idx >= top + bh:
                top = idx - bh + 1
            if idx < top:
                top = idx
            for k in range(bh):
                i = top + k
                if i >= len(opts):
                    break
                mark = "[X]" if opts[i][0] in on else "[ ]"
                self.b.put(r.y + 1 + k, r.x + 2, (" %s %s" % (mark, opts[i][1])).ljust(r.w - 4)[:r.w - 4], "list_sel" if i == idx else "label")
            self.b.put(r.y + r.h - 3, r.x + 2, "Space toggle  a all  n none", "dim")
            self.buttons(r.y + r.h - 2, r.x, r.w, ["OK", "Cancel"], None)
            self.b.refresh()
            k = self.b.key()
            if k == "UP":
                idx = max(0, idx - 1)
            elif k == "DOWN":
                idx = min(len(opts) - 1, idx + 1)
            elif k == "HOME":
                idx = 0
            elif k == "END":
                idx = len(opts) - 1
            elif k == " ":
                v = opts[idx][0]
                on.symmetric_difference_update([v])
            elif k == "a":
                on = set(o[0] for o in opts)
            elif k == "n":
                on = set()
            elif k == "ENTER":
                return [o[0] for o in opts if o[0] in on]
            elif k == "ESC":
                return None

    def prompt(self, title, label, value="", secret=False, validate=None):
        edit = LineEdit(value, secret)
        err = ""
        while True:
            r = self.window(8, 62, title)
            self.b.put(r.y + 2, r.x + 2, label[:r.w - 4], "label")
            vw = r.w - 6
            text, col = edit.view(vw)
            self.b.put(r.y + 3, r.x + 2, " " + text.ljust(vw), "edit")
            self.b.put(r.y + 5, r.x + 2, err[:r.w - 4], "error")
            self.b.put(r.y + r.h - 2, r.x + 2, "Enter accept   Esc cancel", "dim")
            self.b.set_cursor((r.y + 3, r.x + 3 + col))
            self.b.refresh()
            res = edit.handle(self.b.key())
            if res == "cancel":
                self.b.set_cursor(None)
                return None
            if res == "done":
                v = edit.text.strip()
                err = (validate(v) if validate else None) or ""
                if not err:
                    self.b.set_cursor(None)
                    return v

    def view(self, title, lines):
        top = 0
        while True:
            self.background(hints="Up/Down/PgUp/PgDn scroll   Esc or Enter close")
            rows, cols = self.size()
            r = self.window(rows - 4, cols - 4, title)
            bh, cw = r.h - 2, r.w - 4
            top = max(0, min(top, len(lines) - bh))
            for k in range(bh):
                if top + k < len(lines):
                    self.b.put(r.y + 1 + k, r.x + 2, lines[top + k][:cw], "label")
            if len(lines) > bh:
                self.b.put(r.y + r.h - 1, r.x + r.w - 12, " %d/%d " % (min(len(lines), top + bh), len(lines)), "wtitle")
            self.b.refresh()
            k = self.b.key()
            if k in ("ESC", "ENTER", "q"):
                return
            if k == "UP":
                top -= 1
            elif k == "DOWN":
                top += 1
            elif k == "PGUP":
                top -= bh
            elif k == "PGDN" or k == " ":
                top += bh
            elif k == "HOME":
                top = 0
            elif k == "END":
                top = len(lines)

    @contextlib.contextmanager
    def console(self):
        """Run ordinary print/input code outside full-screen mode."""
        with self.b.suspend():
            yield


# ---------------------------------------------------------------------------------------------
# screens
# ---------------------------------------------------------------------------------------------
class Form(object):
    """A scrolling form of Fields (and Headings) with Save / Back buttons.
    on_save(form) writes the values and returns a message; raise ValueError(message) to refuse.
    check(form) may return an error message before saving. Returns True when the values were saved."""

    def __init__(self, ui, title, items, on_save, buttons=None, intro="", check=None, width=76):
        self.ui, self.title, self.items = ui, title, items
        self.on_save, self.check, self.intro, self.width = on_save, check, intro, width
        self.fields = [i for i in items if isinstance(i, Field)]
        self.extra = list(buttons or [])
        self.labels = [b[0] for b in self.extra] + ["Save", "Back"]
        self.order = [("f", i) for i, it in enumerate(items) if isinstance(it, Field) and not it.readonly]
        self.order += [("b", j) for j in range(len(self.labels))]
        self.focus = 0
        self.top = 0
        self.edit = None
        self.saved = False

    # -- helpers ----------------------------------------------------------------------------
    def field(self, label):
        return next(f for f in self.fields if f.label == label)

    def changed(self):
        return any(f.changed() for f in self.fields)

    def current(self):
        return self.order[self.focus]

    def current_field(self):
        kind, i = self.current()
        return self.items[i] if kind == "f" else None

    # -- drawing ----------------------------------------------------------------------------
    def draw(self):
        ui = self.ui
        rows, cols = ui.size()
        f = self.current_field()
        help_text = f.help if f else ""
        hints = ("Typing: Enter accept  Esc cancel" if self.edit else
                 "Up/Down move   Enter edit/select   Space toggle   F10 save   Esc back")
        ui.background(help_text, hints)
        w = min(self.width, cols - 2)
        cw = w - 4
        intro = wrap(self.intro, cw) if self.intro else []
        want = 2 + (len(intro) + 1 if intro else 0) + len(self.items) + 3
        r = ui.window(want, w, self.title)
        y0 = r.y + 1 + (len(intro) + 1 if intro else 0)
        for i, l in enumerate(intro):
            ui.b.put(r.y + 1 + i, r.x + 2, l, "label")
        bh = r.y + r.h - 3 - y0 + 1
        if self.current()[0] == "f":
            idx = self.current()[1]
            first = next((k for k, it in enumerate(self.items) if isinstance(it, Field)), 0)
            if idx < self.top or idx <= first:
                self.top = 0 if idx <= first else idx
            if idx >= self.top + bh:
                self.top = idx - bh + 1
        self.top = max(0, min(self.top, max(0, len(self.items) - bh)))
        lw = min(max([len(f_.label) for f_ in self.fields] + [8]) + 2, cw // 2)
        vw = cw - lw
        cursor = None
        for k in range(bh):
            i = self.top + k
            if i >= len(self.items):
                break
            it, y = self.items[i], y0 + k
            if isinstance(it, Heading):
                ui.b.put(y, r.x + 2, ("-- %s " % it.text).ljust(cw, "-"), "heading")
                continue
            focused = self.current() == ("f", i)
            ui.b.put(y, r.x + 2, it.label[:lw - 1].ljust(lw), "error" if it.bad else "label")
            if it.readonly:
                ui.b.put(y, r.x + 2 + lw, it.value[:vw], "label")
                continue
            if focused and self.edit:
                text, col = self.edit.view(vw - 2)
                ui.b.put(y, r.x + 2 + lw, (" " + text).ljust(vw), "edit")
                cursor = (y, r.x + 2 + lw + 1 + col)
            else:
                ui.b.put(y, r.x + 2 + lw, (" " + it.display(vw - 2)).ljust(vw), "field_sel" if focused else "field")
        if self.top > 0:
            ui.b.put(y0, r.x + r.w - 2, "^", "heading")
        if self.top + bh < len(self.items):
            ui.b.put(y0 + bh - 1, r.x + r.w - 2, "v", "heading")
        kind, j = self.current()
        ui.buttons(r.y + r.h - 2, r.x, r.w, self.labels, j if kind == "b" else None)
        ui.b.set_cursor(cursor)
        ui.b.refresh()

    # -- behaviour --------------------------------------------------------------------------
    def move(self, step):
        self.focus = max(0, min(len(self.order) - 1, self.focus + step))

    def activate(self, f):
        ui = self.ui
        if f.kind == "bool":
            f.value = "no" if f.value == "yes" else "yes"
        elif f.kind == "choice":
            v = ui.choose(f.label, f.choices, f.value)
            if v is not None:
                f.value = v
        elif f.kind == "multi":
            v = ui.checklist(f.label, f.choices, f.items())
            if v is not None:
                f.value = ",".join(v)
        else:
            self.edit = LineEdit("" if f.secret else f.value, f.secret)

    def finish_edit(self, f):
        text = self.edit.text.strip()
        if f.secret and not text:
            self.edit = None
            return
        old, f.value = f.value, text
        err = f.error()
        if err:
            f.value = old
            self.ui.note("%s: %s" % (f.label, err), "error")
            return  # keep editing
        f.bad = False
        self.edit = None

    def save(self):
        ui = self.ui
        bad = [f for f in self.fields if f.error()]
        for f in self.fields:
            f.bad = f in bad
        if bad:
            first = bad[0]
            self.focus = next(n for n, o in enumerate(self.order) if o == ("f", self.items.index(first)))
            ui.note("%s: %s" % (first.label, first.error()), "error")
            return False
        err = self.check(self) if self.check else None
        if err:
            ui.note(err, "error")
            return False
        try:
            msg = self.on_save(self)
        except ValueError as e:
            ui.message(str(e), "Cannot save", "error")
            return False
        for f in self.fields:
            f.commit()
        self.saved = True
        ui.note(msg or "Saved", "info")
        return True

    def back(self):
        if self.changed() and not self.ui.confirm("Discard your changes?", "Unsaved changes", False):
            return None
        return "close"

    def handle(self, key):
        ui = self.ui
        f = self.current_field()
        if self.edit is not None:
            res = self.edit.handle(key)
            if res == "done":
                self.finish_edit(f)
            elif res == "cancel":
                self.edit = None
            return None
        kind, j = self.current()
        if key in ("DOWN", "TAB"):
            self.move(1)
        elif key in ("UP", "BTAB"):
            self.move(-1)
        elif key == "PGDN":
            self.move(5)
        elif key == "PGUP":
            self.move(-5)
        elif key == "HOME":
            self.focus = 0
        elif key == "END":
            self.focus = len(self.order) - 1
        elif key in ("LEFT", "RIGHT") and kind == "b":
            nxt = self.focus + (1 if key == "RIGHT" else -1)
            if 0 <= nxt < len(self.order) and self.order[nxt][0] == "b":
                self.focus = nxt  # stay on the button row
        elif key == "F10":
            if self.save():
                return "close"
        elif key == "ESC":
            return self.back()
        elif key in ("ENTER", " "):
            if kind == "f":
                if f.kind == "bool":
                    self.activate(f)  # Enter or Space toggles
                elif f.kind == "choice" and key == " ":
                    opts = [c[0] for c in f.choices]
                    f.value = opts[(opts.index(f.value) + 1) % len(opts)] if f.value in opts else opts[0]
                elif key == "ENTER":
                    self.activate(f)
            elif j < len(self.extra):
                self.extra[j][1](self)
            elif j == len(self.extra):
                if self.save():
                    return "close"
            else:
                return self.back()
        return None

    def run(self):
        while True:
            self.draw()
            key = self.ui.b.key()
            self.ui.clear_note()
            if key == "RESIZE":
                continue
            if self.handle(key) == "close":
                self.ui.b.set_cursor(None)
                return self.saved


class Menu(object):
    """A list of entries: (label, help, callback). Esc or Back leaves."""

    def __init__(self, ui, title, entries, intro="", back="Back", status=None):
        self.ui, self.title, self.entries, self.intro, self.back, self.status = ui, title, entries, intro, back, status
        self.idx = 0
        self.top = 0

    def draw(self, focus_button):
        ui = self.ui
        rows, cols = ui.size()
        ui.background(self.entries[self.idx][1] if self.entries else "",
                      "Up/Down move   Enter open   Esc %s" % self.back.lower())
        intro = wrap(self.intro() if callable(self.intro) else self.intro, 66) if self.intro else []
        want = 2 + (len(intro) + 1 if intro else 0) + len(self.entries) + 3
        r = ui.window(want, 72, self.title)
        y0 = r.y + 1 + (len(intro) + 1 if intro else 0)
        for i, l in enumerate(intro):
            ui.b.put(r.y + 1 + i, r.x + 2, l, "label")
        bh = r.y + r.h - 3 - y0 + 1
        if self.idx >= self.top + bh:
            self.top = self.idx - bh + 1
        if self.idx < self.top:
            self.top = self.idx
        for k in range(bh):
            i = self.top + k
            if i >= len(self.entries):
                break
            sel = i == self.idx and not focus_button
            ui.b.put(y0 + k, r.x + 2, (" %s" % self.entries[i][0]).ljust(r.w - 4)[:r.w - 4], "list_sel" if sel else "label")
        if self.top > 0:
            ui.b.put(y0, r.x + r.w - 2, "^", "heading")
        if self.top + bh < len(self.entries):
            ui.b.put(y0 + bh - 1, r.x + r.w - 2, "v", "heading")
        ui.buttons(r.y + r.h - 2, r.x, r.w, [self.back], 0 if focus_button else None)
        ui.b.refresh()

    def run(self):
        on_button = False
        while True:
            self.draw(on_button)
            key = self.ui.b.key()
            self.ui.clear_note()
            if key == "ESC" or key == "q" and not on_button:
                return
            if key in ("TAB", "BTAB"):
                on_button = not on_button
            elif key == "DOWN":
                if self.idx == len(self.entries) - 1:
                    on_button = True
                elif not on_button:
                    self.idx += 1
                else:
                    on_button = False
                    self.idx = 0
            elif key == "UP":
                if on_button:
                    on_button = False
                else:
                    self.idx = max(0, self.idx - 1)
            elif key == "PGDN":
                self.idx = min(len(self.entries) - 1, self.idx + 5)
            elif key == "PGUP":
                self.idx = max(0, self.idx - 5)
            elif key == "HOME":
                self.idx = 0
            elif key == "END":
                self.idx = len(self.entries) - 1
            elif key == "ENTER":
                if on_button:
                    return
                try:
                    self.entries[self.idx][2]()
                except Exception as e:  # a bug in one screen must not drop the user out of the whole UI
                    if self.ui.on_error:
                        self.ui.on_error(e)
                    self.ui.message("%s: %s" % (type(e).__name__, e), "Unexpected error", "error")


class ListScreen(object):
    """Edit a collection: rows() -> [(id, text)]. add() / edit(id) / delete(id) are optional."""

    def __init__(self, ui, title, rows, add=None, edit=None, delete=None, extra=None, intro="", empty="(nothing configured yet)"):
        self.ui, self.title, self.rows = ui, title, rows
        self.add, self.edit, self.delete = add, edit, delete
        self.extra = list(extra or [])  # (label, fn(selected id or None))
        self.intro, self.empty = intro, empty
        self.labels = []
        if add:
            self.labels.append("Add")
        if edit:
            self.labels.append("Edit")
        if delete:
            self.labels.append("Delete")
        self.labels += [e[0] for e in self.extra] + ["Back"]
        self.idx, self.top, self.zone = 0, 0, 0  # zone 0: list, n: button n-1

    def draw(self, items):
        ui = self.ui
        ui.background("", "Up/Down move   Enter edit   A add   D delete   Tab buttons   Esc back")
        intro = wrap(self.intro, 66) if self.intro else []
        want = 2 + (len(intro) + 1 if intro else 0) + max(1, len(items)) + 3
        r = ui.window(want, 76, self.title)
        y0 = r.y + 1 + (len(intro) + 1 if intro else 0)
        for i, l in enumerate(intro):
            ui.b.put(r.y + 1 + i, r.x + 2, l, "label")
        bh = r.y + r.h - 3 - y0 + 1
        self.idx = max(0, min(self.idx, len(items) - 1))
        if self.idx >= self.top + bh:
            self.top = self.idx - bh + 1
        if self.idx < self.top:
            self.top = self.idx
        if not items:
            ui.b.put(y0, r.x + 2, self.empty, "dim")
        for k in range(bh):
            i = self.top + k
            if i >= len(items):
                break
            sel = i == self.idx and self.zone == 0
            ui.b.put(y0 + k, r.x + 2, (" %s" % items[i][1]).ljust(r.w - 4)[:r.w - 4], "list_sel" if sel else "label")
        ui.buttons(r.y + r.h - 2, r.x, r.w, self.labels, self.zone - 1 if self.zone else None)
        ui.b.refresh()

    def selected(self, items):
        return items[self.idx][0] if items else None

    def press(self, label, items):
        sel = self.selected(items)
        if label == "Add":
            self.add()
        elif label == "Edit":
            if sel is not None:
                self.edit(sel)
        elif label == "Delete":
            if sel is not None:
                self.delete(sel)
        elif label == "Back":
            return "close"
        else:
            next(e for e in self.extra if e[0] == label)[1](sel)
        return None

    def run(self):
        while True:
            items = self.rows()
            self.draw(items)
            key = self.ui.b.key()
            self.ui.clear_note()
            if key == "ESC":
                return
            if key == "TAB":
                self.zone = (self.zone + 1) % (len(self.labels) + 1)
            elif key == "BTAB":
                self.zone = (self.zone - 1) % (len(self.labels) + 1)
            elif self.zone == 0:
                if key == "UP":
                    self.idx = max(0, self.idx - 1)
                elif key == "DOWN":
                    self.idx = min(len(items) - 1, self.idx + 1)
                elif key == "PGUP":
                    self.idx = max(0, self.idx - 5)
                elif key == "PGDN":
                    self.idx = min(len(items) - 1, self.idx + 5)
                elif key == "ENTER" and self.edit:
                    self.press("Edit", items)
                elif key in ("a", "A") and self.add:
                    self.press("Add", items)
                elif key in ("d", "D", "DELETE") and self.delete:
                    self.press("Delete", items)
            else:
                if key == "LEFT":
                    self.zone = max(1, self.zone - 1)
                elif key == "RIGHT":
                    self.zone = min(len(self.labels), self.zone + 1)
                elif key in ("UP",):
                    self.zone = 0
                elif key == "ENTER":
                    if self.press(self.labels[self.zone - 1], items) == "close":
                        return
