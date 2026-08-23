import json

from exif_resync import check_config, fmt_delta, parse_folder_date, write_timeline


class TestCheckConfig:
    def test_valid_config_output(self, tmp_path, capsys):
        config = tmp_path / "config.json"
        config.write_text(json.dumps({
            "year": 2027,
            "default_interval_sec": 90,
            "schedule": {"01-05": "09:00", "02-05": "08:30"},
        }))

        check_config(str(config))
        out = capsys.readouterr().out
        assert "Year        : 2027" in out
        assert "Interval    : 90s" in out
        assert "01-05 -> 09:00" in out
        assert "02-05 -> 08:30" in out

    def test_invalid_config_exits(self, tmp_path):
        config = tmp_path / "bad.json"
        config.write_text("{oops")
        try:
            check_config(str(config))
            raised = False
        except SystemExit:
            raised = True
        assert raised

    def test_folder_resolution_listing(self, tmp_path, capsys):
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"year": 2026, "schedule": {"01-05": "08:00"}}))
        album = tmp_path / "01-05 Parc aquatique"
        album.mkdir()

        check_config(str(config), str(tmp_path))
        out = capsys.readouterr().out
        assert "schedule" in out
        assert "start 08:00" in out
        assert "1 dated folder(s)" in out


class TestTimeline:
    def _rows(self):
        return [
            {"folder": "01-05 Parc", "rule": "schedule", "file": "IMG_001.jpg",
             "time": "2026:05:01 09:00:00", "source": "plan", "score": "", "drift": ""},
            {"folder": "01-05 Parc", "rule": "schedule", "file": "photo_2.jpg",
             "time": "2026:05:01 14:30:22", "source": "filename", "score": "",
             "drift": "-7200"},
        ]

    def test_timeline_contains_days_and_badges(self, tmp_path):
        target = tmp_path / "timeline.html"
        write_timeline(self._rows(), str(target), dry_run=False)
        html = target.read_text(encoding="utf-8")
        assert "01-05 Parc" in html
        assert ">FILE</span>" in html
        assert ">PLAN</span>" in html
        assert "drift -2h00m applied" in html
        assert "09:00:00" in html and "14:30:22" in html

    def test_dry_run_flag_visible(self, tmp_path):
        target = tmp_path / "timeline.html"
        write_timeline(self._rows(), str(target), dry_run=True)
        assert "DRY-RUN PREVIEW" in target.read_text(encoding="utf-8")

    def test_html_escaped_names(self, tmp_path):
        rows = [{"folder": "<script>alert(1)</script>", "rule": "default",
                 "file": "x<y>.jpg", "time": "2026:05:01 09:00:00",
                 "source": "plan", "score": "", "drift": ""}]
        target = tmp_path / "timeline.html"
        write_timeline(rows, str(target), dry_run=False)
        html = target.read_text(encoding="utf-8")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestFmtDeltaNegative:
    def test_negative_hours(self):
        assert fmt_delta(-8025) == "-2h13m"


def test_folder_date_regex_word_guards():
    assert parse_folder_date("colonie-15-07") is None
    assert parse_folder_date("15-07 colonie") == ("15-07", 15, 7, 12, 0)
