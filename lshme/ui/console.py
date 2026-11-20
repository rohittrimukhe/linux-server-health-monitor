# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Console helpers: colours, tables and prompts (input is injectable for tests)."""
import os
import sys

_input = input
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
CODES = {"CRITICAL": "1;31", "WARNING": "1;33", "UNKNOWN": "1;37", "INFO": "36", "HEALTHY": "1;32", "OK": "32",
         "title": "1;37;44", "bold": "1", "dim": "2", "cyan": "1;36", "green": "1;32", "red": "1;31", "yellow": "1;33"}


def set_input(fn):
    global _input
    _input = fn


def disable_color():
    global COLOR
    COLOR = False


def c(text, style):
    if not COLOR or style not in CODES:
        return str(text)
    return "\033[%sm%s\033[0m" % (CODES[style], text)


def out(text=""):
    print(text)
    sys.stdout.flush()


def table(headers, rows, max_width=None):
    try:
        cols = max_width or os.get_terminal_size().columns
    except OSError:
        cols = max_width or 140
    rows = [[str(x) for x in r] for r in rows]
    w = [len(str(h)) for h in headers]
    for r in rows:
        for i, x in enumerate(r[:len(w)]):
            w[i] = max(w[i], len(x))
    while sum(w) + 3 * len(w) > cols and max(w) > 12:
        w[w.index(max(w))] -= 1
    line = "  ".join(str(h)[:wi].ljust(wi) for h, wi in zip(headers, w))
    out(c(line, "bold"))
    out("  ".join("-" * wi for wi in w))
    for r in rows:
        cells = []
        for x, wi in zip(r + [""] * (len(w) - len(r)), w):
            cell = x[:wi].ljust(wi)
            cells.append(c(cell, x) if x in CODES else cell)
        out("  ".join(cells))


def ask(prompt, default="", secret=False, choices=None, validate=None):
    while True:
        suffix = " [%s]" % ("*" * 6 if secret and default else default) if default != "" else ""
        if secret and _input is input and sys.stdin.isatty():
            import getpass
            v = getpass.getpass("%s%s: " % (prompt, suffix))
        else:
            v = _input("%s%s: " % (prompt, suffix))
        v = (v or "").strip()
        if not v:
            v = default
        if choices and v not in choices:
            out(c("  Please choose one of: %s" % ", ".join(choices), "yellow"))
            continue
        if validate:
            err = validate(v)
            if err:
                out(c("  " + err, "yellow"))
                continue
        return v


def yes(prompt, default=True):
    v = ask(prompt + " (y/n)", "y" if default else "n", choices=["y", "n", "Y", "N", "yes", "no"])
    return v.lower().startswith("y")


def pause():
    try:
        _input(c("\nPress Enter to continue...", "dim"))
    except EOFError:
        pass


def num_validator(lo=None, hi=None):
    def v(x):
        try:
            f = float(x)
        except ValueError:
            return "Enter a number"
        if lo is not None and f < lo or hi is not None and f > hi:
            return "Enter a value between %s and %s" % (lo, hi)
        return None
    return v
