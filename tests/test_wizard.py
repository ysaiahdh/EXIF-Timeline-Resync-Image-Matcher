import io
import json
import sys
from datetime import datetime

import pytest

import exif_resync
import wizard
from wizard import (
    ask,
    ask_choice,
    ask_yes_no,
    format_table,
    make_progress_bar,
    matcher_options,
    paint,
    run_wizard,
    supports_color,
)


def make_input(answers):
    """Fake input() replaying `answers`, then raising EOFError."""

    iterator = iter(answers)

    def fake(prompt=""):
        return next(iterator)

    return fake


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class TestColors:
    def test_plain_without_tty(self, monkeypatch, capsys):
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert supports_color() is False
        assert paint("hi", "red", "bold") == "hi"

    def test_plain_with_no_color(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", FakeTTY())
        monkeypatch.setenv("NO_COLOR", "1")
        assert supports_color() is False
        assert paint("hi", "red") == "hi"

    def test_colored_on_tty(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", FakeTTY())
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm")
        assert supports_color() is True
        assert paint("hi", "red", "bold") == "\033[31;1mhi\033[0m"

    def test_dumb_term_disables_color(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", FakeTTY())
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        assert paint("hi", "red") == "hi"


class TestTable:
    def test_headers_rows_and_alignment(self):
        out = format_table(["A", "Long"], [["x", "1"], ["yy", "222"]])
        lines = out.splitlines()
        assert lines[0].startswith("A")
        assert "Long" in lines[0]
        assert set(lines[1]) <= set("- ")
        assert "yy" in lines[3] and "222" in lines[3]


class TestProgressBar:
    def test_callback_counts_without_tty(self, capsys):
        callback, close = make_progress_bar(3)
        callback("album", 1, 2)
        callback("album", 2, 2)
        close()
        assert capsys.readouterr().err == ""

    def test_zero_total_is_safe(self):
        callback, close = make_progress_bar(0)
        callback("album", 1, 1)
        close()


class TestAsk:
    def test_default_on_empty_input(self):
        assert ask(make_input([""]), "P", default="dflt") == "dflt"

    def test_default_converted_through_validator(self):
        value = ask(make_input([""]), "Year", default="2026", validator=wizard.valid_year)
        assert value == 2026

    def test_validator_retries_then_accepts(self, capsys):
        value = ask(
            make_input(["nope", "09:30"]),
            "Time",
            validator=exif_resync.parse_time_value,
            hint="bad time",
        )
        assert value == (9, 30)
        assert "bad time" in capsys.readouterr().out

    def test_required_value_reprompts(self):
        assert ask(make_input(["", "given"]), "P") == "given"


class TestYesNo:
    def test_defaults(self):
        assert ask_yes_no(make_input([""]), "P", default=True) is True
        assert ask_yes_no(make_input([""]), "P", default=False) is False

    def test_variants(self):
        assert ask_yes_no(make_input(["y"]), "P") is True
        assert ask_yes_no(make_input(["N"]), "P") is False
        assert ask_yes_no(make_input(["oui"]), "P") is True
        assert ask_yes_no(make_input(["non"]), "P") is False

    def test_invalid_retries(self, capsys):
        assert ask_yes_no(make_input(["maybe", "y"]), "P") is True
        assert "y or n" in capsys.readouterr().out


class TestChoice:
    def test_default_and_pick(self):
        assert ask_choice(make_input([""]), "P", [("a", "A"), ("b", "B")], default="b") == "b"
        assert ask_choice(make_input(["a"]), "P", [("a", "A")]) == "a"

    def test_invalid_retries(self, capsys):
        assert ask_choice(make_input(["z", "a"]), "P", [("a", "A")]) == "a"
        assert "Choose one of" in capsys.readouterr().out


class TestValidators:
    def test_valid_dir(self, tmp_path):
        assert wizard.valid_dir(str(tmp_path)) == str(tmp_path)
        assert wizard.valid_dir(str(tmp_path / "missing")) is None

    def test_valid_year(self):
        assert wizard.valid_year("2026") == 2026
        assert wizard.valid_year("99") is None
        assert wizard.valid_year("soon") is None

    def test_valid_positive_int(self):
        assert wizard.valid_positive_int("60") == 60
        assert wizard.valid_positive_int("0") is None
        assert wizard.valid_positive_int("-3") is None

    def test_matcher_options_reflect_install(self):
        options = dict(matcher_options())
        assert set(options) >= {"auto", "off"}
        assert ("hash" in options) == (exif_resync.HAS_PILLOW and exif_resync.HAS_IMAGEHASH)
        assert ("resnet" in options) == (exif_resync.HAS_VISION and exif_resync.HAS_PILLOW)


class TestMenu:
    def test_quit(self, capsys):
        assert run_wizard(make_input(["4"])) == 0
        assert "Bye!" in capsys.readouterr().out

    def test_needs_interactive_terminal(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert run_wizard() == 2

    def test_keyboard_interrupt_cancel(self):
        def raising(_prompt=""):
            raise KeyboardInterrupt

        assert run_wizard(raising) == 130


class TestGuidedResync:
    def _tree(self, tmp_path):
        album = tmp_path / "01-05 Parc"
        album.mkdir()
        (album / "a.jpg").write_bytes(b"fake")
        return tmp_path

    def test_full_flow_creates_config_and_applies(self, tmp_path, monkeypatch, capsys):
        root = self._tree(tmp_path)
        calls = []

        def fake_process(root_dir, config_path, **kwargs):
            calls.append((root_dir, config_path, kwargs))
            return 0

        monkeypatch.setattr(exif_resync, "process_photos", fake_process)
        answers = [
            "1",  # menu: guided resync
            str(root),  # directory
            "",  # recursive? no
            str(root / "wiz.json"),  # config path (missing -> builder)
            "",  # year: current
            "",  # interval: 180
            "",  # start time for 01-05: accept proposal
            "n",  # reference photos? no
            "n",  # duplicates? no
            "n",  # sync-clocks? no
            "n",  # sync-mtime? no
            "",  # timeline? yes
            "y",  # apply? yes
            "4",  # quit
        ]
        assert run_wizard(make_input(answers)) == 0

        config_path = root / "wiz.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["year"] == datetime.now().year
        assert data["schedule"] == {"01-05": "12:00"}

        assert len(calls) == 2
        assert calls[0][2]["dry_run"] is True
        assert calls[1][2].get("dry_run", False) is False
        assert calls[0][2]["quiet"] is True
        assert callable(calls[0][2]["progress_cb"])
        out = capsys.readouterr().out
        assert "Preview OK" in out
        assert "--undo" in out

    def test_decline_apply_writes_nothing(self, tmp_path, monkeypatch, capsys):
        root = self._tree(tmp_path)
        calls = []
        monkeypatch.setattr(
            exif_resync,
            "process_photos",
            lambda *a, **k: calls.append(k) or 0,
        )
        answers = [
            "1",
            str(root),
            "",
            str(root / "wiz.json"),
            "",
            "",
            "",
            "n",
            "n",
            "n",
            "n",
            "",
            "n",
            "4",
        ]
        assert run_wizard(make_input(answers)) == 0
        assert len(calls) == 1  # preview only
        assert "Nothing was written" in capsys.readouterr().out

    def test_no_dated_albums_aborts(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "misc").mkdir()
        monkeypatch.setattr(
            exif_resync, "process_photos", lambda *a, **k: pytest.fail("must not run")
        )
        answers = ["1", str(tmp_path), "", str(tmp_path / "wiz.json"), "", "", "4"]
        assert run_wizard(make_input(answers)) == 0
        assert "No dated albums found" in capsys.readouterr().out


class TestUndoFlow:
    def test_preview_then_restore(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            exif_resync,
            "undo_from_report",
            lambda report, **k: calls.append((report, k)) or 0,
        )
        report = tmp_path / "r.csv"
        report.write_text("path\n")
        answers = ["2", str(report), "y", "4"]
        assert run_wizard(make_input(answers)) == 0
        assert calls[0] == (str(report), {"dry_run": True})
        assert calls[1][1].get("dry_run", False) is False

    def test_decline_restore(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            exif_resync, "undo_from_report", lambda report, **k: calls.append(k) or 0
        )
        report = tmp_path / "r.csv"
        report.write_text("path\n")
        answers = ["2", str(report), "n", "4"]
        assert run_wizard(make_input(answers)) == 0
        assert len(calls) == 1

    def test_flow_error_returns_to_menu(self, tmp_path, capsys):
        bad = tmp_path / "bad.csv"
        bad.write_text("path,new_datetime\n/tmp/a.jpg,2026:01:01 00:00:00\n")
        answers = ["2", str(bad), "4"]
        assert run_wizard(make_input(answers)) == 0
        out = capsys.readouterr().out
        assert "missing column" in out
        assert "Back to the menu" in out


class TestCheckFlow:
    def test_check_calls_core(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(exif_resync, "check_config", lambda *a: calls.append(a) or None)
        config = tmp_path / "c.json"
        config.write_text("{}")
        answers = ["3", str(config), str(tmp_path), "", "4"]
        assert run_wizard(make_input(answers)) == 0
        assert len(calls) == 1
        assert calls[0][2] is False


class TestCoreHelpers:
    def test_parse_time_value(self):
        assert exif_resync.parse_time_value("09:30") == (9, 30)
        assert exif_resync.parse_time_value("9:30") == (9, 30)
        assert exif_resync.parse_time_value("9h30") is None
        assert exif_resync.parse_time_value("25:00") is None
        assert exif_resync.parse_time_value(900) is None

    def test_read_album_description(self, tmp_path):
        assert exif_resync.read_album_description(str(tmp_path)) == ""
        (tmp_path / "b.txt").write_text("second")
        (tmp_path / "a.txt").write_text("  first  ")
        assert exif_resync.read_album_description(str(tmp_path)) == "first"
        assert exif_resync.read_album_description(str(tmp_path / "missing")) == ""

    def test_summarize_albums(self, tmp_path):
        dated = tmp_path / "01-05 Parc"
        dated.mkdir()
        (dated / "a.jpg").write_bytes(b"x")
        (dated / "note.txt").write_text("boom tonight")
        (tmp_path / "misc").mkdir()
        bad = tmp_path / "30-02 Oops"
        bad.mkdir()
        (bad / "a.jpg").write_bytes(b"x")

        albums = exif_resync.summarize_albums(str(tmp_path), 2026, {}, 180)
        by_label = {a["label"]: a for a in albums}
        assert by_label["01-05 Parc"]["dated"] is True
        assert by_label["01-05 Parc"]["start"] == "20:30"
        assert by_label["01-05 Parc"]["photos"] == 1
        assert by_label["misc"]["dated"] is False
        assert by_label["30-02 Oops"]["dated"] is False
        assert "invalid date" in by_label["30-02 Oops"]["rule"]

    def test_process_photos_reports_progress(self, tmp_path, monkeypatch):
        import test_report_undo

        album = tmp_path / "01-06 Parc"
        album.mkdir()
        for name in ("a.jpg", "b.jpg"):
            (album / name).write_bytes(b"fake")
        config = tmp_path / "config.json"
        config.write_text('{"year": 2026}')
        fake = test_report_undo.FakeExifTool()
        monkeypatch.setattr(exif_resync, "find_exiftool", lambda: "mock-exiftool")
        monkeypatch.setattr(exif_resync.subprocess, "run", fake)

        seen = []
        rc = exif_resync.process_photos(
            str(tmp_path),
            str(config),
            matcher_mode="off",
            progress_cb=lambda label, done, total: seen.append((label, done, total)),
        )
        assert rc == 0
        assert seen == [("01-06 Parc", 1, 2), ("01-06 Parc", 2, 2)]
