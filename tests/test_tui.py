# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The full-screen configuration forms, driven by scripted key presses (no terminal needed)."""
import contextlib
import os
import unittest
from unittest import mock

from fakes import ROOT, TempHome
from lshme.app import App
from lshme.ui import tui
from lshme.ui.tui_kit import UI, Field, Form, LineEdit, parse_escape


def typed(text):
    return list(text)


class MemoryBackend(object):
    """Implements the Backend interface in memory; keys come from a script."""

    def __init__(self, keys, rows=24, cols=80):
        self.keys, self.rows, self.cols = list(keys), rows, cols
        self.grid = []
        self.frames = []
        self.cursor = None
        self.erase()

    def size(self):
        return self.rows, self.cols

    def erase(self):
        self.grid = [[" "] * self.cols for _ in range(self.rows)]

    def fill(self, y, x, h, w, attr="window"):
        for r in range(y, y + h):
            self.put(r, x, " " * w, attr)

    def put(self, y, x, text, attr="window"):
        if y < 0 or y >= self.rows:
            return
        for i, ch in enumerate(text):
            if 0 <= x + i < self.cols:
                self.grid[y][x + i] = ch

    def box(self, y, x, h, w, attr="window"):
        for i in range(w):
            self.put(y, x + i, "-")
            self.put(y + h - 1, x + i, "-")
        for i in range(h):
            self.put(y + i, x, "|")
            self.put(y + i, x + w - 1, "|")

    def set_cursor(self, pos):
        self.cursor = pos

    def refresh(self):
        self.frames.append(self.text())

    def text(self):
        return "\n".join("".join(r).rstrip() for r in self.grid)

    def key(self):
        if not self.keys:
            raise AssertionError("script exhausted; last screen:\n" + self.text())
        return self.keys.pop(0)

    @contextlib.contextmanager
    def suspend(self):
        yield

    def saw(self, text):
        return any(text in f for f in self.frames)


class TuiCase(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.app = App(ROOT, home=self.home.dir)

    def tearDown(self):
        self.home.cleanup()

    def ctx(self, keys, rows=24, cols=80):
        self.be = MemoryBackend(keys, rows, cols)
        return tui.Ctx(self.app, UI(self.be))

    def read(self, name):
        with open(self.app.cfg.path(name), encoding="utf-8") as f:
            return f.read()


class KeyDecodingTests(unittest.TestCase):
    """Terminals send the same key in different ways; all of them must work."""

    @staticmethod
    def decode(after_esc):
        chars, back = list(after_esc), []
        key = parse_escape(lambda: chars.pop(0) if chars else None, back.append)
        return key, back

    def test_arrows_in_normal_and_application_mode(self):
        for name, plain, app in (("UP", "[A", "OA"), ("DOWN", "[B", "OB"), ("RIGHT", "[C", "OC"), ("LEFT", "[D", "OD")):
            self.assertEqual(self.decode(plain)[0], name, plain)  # PuTTY, plink, Windows Terminal, xterm
            self.assertEqual(self.decode(app)[0], name, app)      # application cursor mode

    def test_navigation_and_function_keys(self):
        for seq, name in (("[5~", "PGUP"), ("[6~", "PGDN"), ("[H", "HOME"), ("[F", "END"), ("[1~", "HOME"), ("[4~", "END"),
                          ("OH", "HOME"), ("OF", "END"), ("[3~", "DELETE"), ("[Z", "BTAB"), ("[21~", "F10"), ("[15~", "F5"),
                          ("OP", "F1"), ("[1;5A", "UP"), ("[1;2B", "DOWN"), ("[5;5~", "PGUP")):
            self.assertEqual(self.decode(seq)[0], name, seq)

    def test_lone_escape_and_alt_key(self):
        self.assertEqual(self.decode("")[0], "ESC")
        self.assertEqual(self.decode("["), ("ESC", []))  # an incomplete sequence is just Esc
        self.assertEqual(self.decode("x"), ("ESC", ["x"]))  # Alt+x: Esc, and x is delivered afterwards

    def test_unknown_sequence_is_ignored_not_misread(self):
        self.assertEqual(self.decode("[99~")[0], "UNKNOWN")


class LineEditTests(unittest.TestCase):
    def test_editing_keys(self):
        e = LineEdit("abc")
        for k in ("LEFT", "x", "HOME", "y", "END", "z", "BACKSPACE", "DELETE"):
            self.assertIsNone(e.handle(k))
        self.assertEqual(e.text, "yabxc")
        self.assertEqual(e.handle("ENTER"), "done")
        self.assertEqual(e.handle("ESC"), "cancel")

    def test_kill_keys_and_secret_view(self):
        e = LineEdit("hello world", secret=True)
        e.pos = 5
        e.handle("CTRL_K")
        self.assertEqual(e.text, "hello")
        e.handle("CTRL_U")
        self.assertEqual(e.text, "")
        e.handle("a")
        self.assertEqual(e.view(10)[0], "*")

    def test_long_text_scrolls_to_cursor(self):
        e = LineEdit("0123456789")
        shown, col = e.view(5)
        self.assertEqual(shown, "6789")
        self.assertEqual(col, 4)


class FieldTests(unittest.TestCase):
    def test_validation_by_kind(self):
        self.assertEqual(Field("n", "int", "x").error(), "enter a number")
        self.assertEqual(Field("n", "int", "1.5").error(), "enter a whole number")
        self.assertIn("between", Field("n", "int", "500", lo=1, hi=100).error())
        self.assertIsNone(Field("n", "int", "", optional=True).error())
        self.assertIsNotNone(Field("c", "choice", "z", choices=["a", "b"]).error())
        self.assertIsNotNone(Field("m", "multi", "a,q", choices=["a", "b"]).error())
        self.assertIsNone(Field("m", "multi", "a,b", choices=["a", "b"]).error())
        self.assertIsNotNone(Field("b", "bool", "maybe").error())

    def test_changed_and_new(self):
        f = Field("t", "text", "a")
        self.assertFalse(f.changed())
        f.value = "b"
        self.assertTrue(f.changed())
        self.assertTrue(Field("t", "text", "a", new=True).changed())  # a new item must write every value

    def test_secret_is_masked(self):
        f = Field("p", "text", "", secret=True, has_value=True)
        self.assertEqual(f.display(20), "******")


class FormTests(TuiCase):
    def test_edit_and_save_identity(self):
        ctx = self.ctx(["ENTER"] + typed("Example Ltd") + ["ENTER", "F10"])
        tui.page_identity(ctx)
        self.assertEqual(self.app.cfg.get("main", "general", "company"), "Example Ltd")
        self.assertEqual(oct(os.stat(self.app.cfg.path("main")).st_mode & 0o777), "0o600")
        self.assertTrue(os.listdir(self.app.paths.config_backup_dir))  # a backup is taken before each change
        self.assertEqual(ctx.ui.note_text, "Saved main.conf")  # shown in the footer of the screen that called the form

    def test_bad_number_is_refused_and_nothing_written(self):
        ctx = self.ctx(["ENTER", "CTRL_U"] + typed("abc") + ["ENTER", "ESC", "ESC"])
        tui.page_thresholds(ctx)
        self.assertTrue(self.be.saw("enter a number"))
        self.assertEqual(self.app.cfg.get("thresholds", "cpu", "warning"), "85")
        self.assertFalse(os.path.exists(self.app.cfg.path("thresholds")))

    def test_warning_above_critical_is_refused(self):
        ctx = self.ctx(["ENTER", "CTRL_U"] + typed("99") + ["ENTER", "F10", "ESC", "y"])
        tui.page_thresholds(ctx)
        self.assertTrue(self.be.saw("must not be above critical"))
        self.assertEqual(self.app.cfg.get("thresholds", "cpu", "warning"), "85")

    def test_valid_threshold_saved(self):
        ctx = self.ctx(["ENTER", "CTRL_U"] + typed("80") + ["ENTER", "F10"])
        tui.page_thresholds(ctx)
        self.assertEqual(self.app.cfg.get("thresholds", "cpu", "warning"), "80")

    def test_escape_with_changes_asks_first(self):
        # edit, Esc (asked), Esc (= "No"), Esc (asked again), y (= discard)
        ctx = self.ctx(["ENTER"] + typed("X") + ["ENTER", "ESC", "ESC", "ESC", "y"])
        tui.page_identity(ctx)
        self.assertTrue(self.be.saw("Discard your changes?"))
        self.assertEqual(self.app.cfg.get("main", "general", "company"), "")

    def test_module_switch(self):
        ctx = self.ctx([" ", "F10"])
        tui.page_modules(ctx)
        self.assertEqual(self.app.cfg.get("main", "modules", "disabled"), "system")

    def test_multi_choice_formats(self):
        ctx = self.ctx(["ENTER", " ", "ENTER", "F10"])  # untick html in the checklist
        tui.page_reports(ctx)
        self.assertEqual(self.app.cfg.get("reports", "reports", "formats"), "pdf,json")

    def test_smtp_password_goes_to_credentials_file_only(self):
        keys = ["DOWN", "ENTER"] + typed("smtp.example.com") + ["ENTER"]  # server
        keys += ["DOWN"] * 4 + ["ENTER"] + typed("s3cret") + ["ENTER", "F10"]  # password
        ctx = self.ctx(keys)
        tui.page_smtp(ctx)
        self.assertEqual(self.app.cfg.get("smtp", "smtp", "host"), "smtp.example.com")
        self.assertEqual(self.app.cfg.secret("smtp"), "s3cret")
        self.assertNotIn("s3cret", self.read("smtp"))
        self.assertEqual(oct(os.stat(self.app.cfg.path("credentials")).st_mode & 0o777), "0o600")

    def test_bad_email_refused(self):
        keys = ["DOWN"] * 6 + ["ENTER"] + typed("not-an-address") + ["ENTER", "ESC", "ESC"]  # the Sender (From) field
        ctx = self.ctx(keys)
        tui.page_smtp(ctx)
        self.assertTrue(self.be.saw("enter email addresses"))
        self.assertEqual(self.app.cfg.get("smtp", "smtp", "from"), "")

    def test_save_failure_is_reported_not_raised(self):
        ctx = self.ctx(["ENTER"] + typed("Y") + ["ENTER", "F10", "ENTER", "ESC", "y"])
        original = self.app.save_config

        def boom(name):
            raise OSError("read-only file system")
        self.app.save_config = boom
        tui.page_identity(ctx)
        self.app.save_config = original
        self.assertTrue(self.be.saw("Cannot write the configuration"))


class CollectionTests(TuiCase):
    def test_ports_add_then_delete(self):
        keys = ["a", "ENTER"] + typed("listener") + ["ENTER", "DOWN", "ENTER"] + typed("1521") + ["ENTER", "F10"]
        ctx = self.ctx(keys + ["d", "y", "ESC"])
        tui.kv_page(ctx, "Expected listening ports", "services", "ports", "Label", "Port",
                    value_check=lambda v: None if v.isdigit() else "enter a port")
        self.assertTrue(self.be.saw("listener = 1521"))
        self.assertFalse(self.app.cfg.parsers["services"].has_option("ports", "listener"))

    def test_port_must_be_number(self):
        keys = ["a", "ENTER"] + typed("x") + ["ENTER", "DOWN", "ENTER"] + typed("abc") + ["ENTER", "ESC", "ESC", "y", "ESC"]
        ctx = self.ctx(keys)
        tui.kv_page(ctx, "Ports", "services", "ports", "Label", "Port", value_check=lambda v: None if v.isdigit() else "enter a port")
        self.assertTrue(self.be.saw("enter a port"))

    def test_database_instance_added_with_defaults(self):
        keys = ["DOWN", "ENTER", "a", "ENTER", "ENTER", "F10", "ESC", "ESC"]  # Instances > Add > oracle > ORCL > save
        ctx = self.ctx(keys)
        tui.page_databases(ctx)
        self.assertTrue(self.app.cfg.get("database", "oracle:ORCL", "oracle_home").startswith("/u01/"))
        self.assertEqual(self.app.cfg.get("database", "oracle:ORCL", "sid"), "ORCL")
        self.assertFalse(self.app.cfg.parsers["database"].has_option("oracle:ORCL", "username"))  # blank values are not written

    def test_new_custom_schedule_and_builtin_cannot_be_deleted(self):
        ctx = self.ctx(["F10"])
        tui.schedule_form(ctx, "nightly", new=True, frequency="daily")
        self.assertEqual(self.app.cfg.get("schedule", "schedule:nightly", "frequency"), "daily")
        self.assertEqual(self.app.cfg.get("schedule", "schedule:nightly", "time"), "00:15")
        ctx = self.ctx(["DOWN", "d", "ENTER", "ESC"])  # row 1 is "nightly", row 2 the built-in "daily"
        tui.page_schedules(ctx)
        self.assertTrue(self.be.saw("built in"))

    def test_existing_schedule_without_every_key_can_be_saved(self):
        # a hand-written schedule with only a few keys must open with the scheduler's defaults and save cleanly
        self.home.cleanup()
        self.home = TempHome({"schedule": "[schedule:mine]\nfrequency = weekly\nweekday = fri\ntime = 06:30\n"})
        self.app = App(ROOT, home=self.home.dir)
        ctx = self.ctx(["F10"])
        tui.schedule_form(ctx, "mine")
        self.assertEqual(ctx.ui.note_text, "No changes")  # accepted, not refused for a blank report type
        self.assertEqual(self.app.cfg.get("schedule", "schedule:mine", "weekday"), "fri")

    def test_stored_choice_in_other_case_is_accepted(self):
        self.home.cleanup()
        self.home = TempHome({"main": "[watch]\nalert_min_severity = critical\n"})
        self.app = App(ROOT, home=self.home.dir)
        ctx = self.ctx(["F10"])
        tui.page_watch(ctx)
        self.assertEqual(ctx.ui.note_text, "No changes")  # "critical" is a valid CRITICAL, so nothing blocks the save

    def test_schedule_time_validated(self):
        ctx = self.ctx(["DOWN", "ENTER", "CTRL_U"] + typed("25:99") + ["ENTER", "ESC", "ESC", "y"])
        tui.schedule_form(ctx, "daily")
        self.assertTrue(self.be.saw("use HH:MM"))
        self.assertEqual(self.app.cfg.get("schedule", "schedule:daily", "time"), "07:00")

    def test_server_added(self):
        keys = ["a", "CTRL_U"] + typed("web01") + ["ENTER", "F10", "ESC"]  # the name box starts with a suggestion
        ctx = self.ctx(keys)
        tui.page_fleet(ctx)
        self.assertEqual(self.app.cfg.get("servers", "server:web01", "host"), "web01")
        self.assertEqual(self.app.cfg.get("servers", "server:web01", "enabled"), "yes")


class HelpTextTests(unittest.TestCase):
    def test_every_help_line_fits_an_80_column_screen(self):
        import re
        with open(tui.__file__.replace(".pyc", ".py"), encoding="utf-8") as f:
            source = f.read()
        texts = re.findall(r'help(?:_)?="([^"]+)"', source) + [h for _, _, h, _ in tui.PAGES]
        self.assertGreater(len(texts), 100)
        too_long = [t for t in texts if len(t) > 76]
        self.assertEqual(too_long, [], "help lines wider than the footer would be cut off")


class NavigationTests(TuiCase):
    def test_main_menu_opens_a_page_and_quits(self):
        ctx = self.ctx(["ENTER", "ESC", "ESC"])
        tui.start(ctx)
        self.assertTrue(self.be.saw("Main menu"))
        self.assertTrue(self.be.saw("Organisation & report identity"))

    def test_main_menu_shows_every_entry_on_an_80x24_screen(self):
        ctx = self.ctx(["ESC"])
        tui.start(ctx)
        first = self.be.frames[0]
        for _, label, _, _ in tui.PAGES:
            self.assertIn(label, first)  # nothing hidden behind a scroll marker
        self.assertIn("< Quit >", first)

    def test_tools_menu_reaches_validate_and_backup(self):
        ctx = self.ctx(["ENTER", "ESC", "DOWN", "ENTER", "ESC", "ESC"])  # Validate > close, Back up / restore > leave, leave
        tui.page_tools(ctx)
        self.assertTrue(self.be.saw("Configuration files"))
        self.assertTrue(self.be.saw("Back up now"))

    def test_page_shortcut_and_unknown_page(self):
        ctx = self.ctx(["ESC"])
        tui.start(ctx, "identity")
        self.assertTrue(self.be.saw("Company name"))
        with self.assertRaises(ValueError):
            tui.start(self.ctx([]), "nope")

    def test_every_menu_page_opens_and_closes(self):
        for key, label, _, fn in tui.PAGES:
            ctx = self.ctx(["ESC"] * 4)
            tui.start(ctx, key)
            self.assertTrue(self.be.frames, key)

    def test_small_terminal_asks_for_a_bigger_window(self):
        ctx = self.ctx(["ESC"], rows=10, cols=40)
        tui.start(ctx)
        self.assertTrue(self.be.saw("Terminal too small"))

    def test_long_form_fits_80x24_and_scrolls(self):
        ctx = self.ctx(["DOWN"] * 58 + ["ESC"])  # walk down to the very last threshold
        tui.page_thresholds(ctx)
        self.assertTrue(self.be.saw("< Save >"))
        self.assertTrue(self.be.saw("< Back >"))
        self.assertFalse(self.be.frames[0].count("SQL Server backup max age"))  # not on the first screen...
        self.assertTrue(self.be.saw("SQL Server backup max age"))  # ...but reached by scrolling

    def test_validate_page_shows_results(self):
        ctx = self.ctx(["ESC"])
        tui.page_validate(ctx)
        self.assertTrue(self.be.saw("Configuration files"))

    def test_backup_then_restore(self):
        ctx = self.ctx(["ENTER", "ENTER", "ESC"])  # Back up now > OK > leave
        tui.page_backup(ctx)
        self.assertTrue([f for f in os.listdir(self.app.paths.config_backup_dir) if f.endswith("manual.tar.gz")])
        ctx = self.ctx(["DOWN", "ENTER", "ENTER", "y", "ESC"])  # Restore > first backup > yes
        tui.page_backup(ctx)
        self.assertTrue(self.be.saw("Replace the current configuration"))


class FallbackTests(TuiCase):
    def test_not_available_without_a_terminal(self):
        with mock.patch("lshme.ui.tui.sys") as fake:
            fake.stdin.isatty.return_value = False
            fake.stdout.isatty.return_value = False
            ok, why = tui.available()
            self.assertFalse(ok)
            self.assertTrue(why)
            self.assertEqual(tui.run(self.app), (False, why))


if __name__ == "__main__":
    unittest.main()
