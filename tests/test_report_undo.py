import csv

import pytest

from exif_resync import REPORT_FIELDS, process_photos, undo_from_report, write_report


class FakeExifTool:
    """Scriptable stand-in recording every write/undo invocation."""

    def __init__(self, initial_tags=None):
        self.calls = []
        self.initial_tags = initial_tags or ["-", "-", "-", "-"]
        self.current = {}

    def __call__(self, cmd, capture_output=False, text=False, stdout=None, stderr=None):
        self.calls.append(list(cmd))

        if "-s3" in cmd:
            path = cmd[-1]
            tags = self.current.get(path, self.initial_tags)
            return type("R", (), {"returncode": 0, "stdout": "\n".join(tags), "stderr": ""})()

        if "-overwrite_original" in cmd:
            path = cmd[-1]
            current = list(self.current.get(path, self.initial_tags))
            for arg in cmd[1:-1]:
                if arg == "-overwrite_original":
                    continue
                tag, _, value = arg.lstrip("-").partition("=")
                mapping = {"DateTimeOriginal": 0, "CreateDate": 1,
                           "ModifyDate": 2, "ImageDescription": 3}
                all_dates = tag == "AllDates"
                if all_dates:
                    current[0] = current[1] = current[2] = value
                elif tag in mapping:
                    current[mapping[tag]] = value
            self.current[path] = current
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


@pytest.fixture
def photo_tree(tmp_path):
    album = tmp_path / "01-06 Parc"
    album.mkdir()
    for name in ("a.jpg", "b.jpg"):
        (album / name).write_bytes(b"fake")
    config_path = tmp_path / "config.json"
    config_path.write_text('{"year": 2026, "default_interval_sec": 60}')
    return tmp_path, config_path


def run_process(tmp_path, config_path, fake_tool, monkeypatch, **kwargs):
    import exif_resync

    monkeypatch.setattr(exif_resync, "find_exiftool", lambda: "mock-exiftool")
    monkeypatch.setattr(exif_resync.subprocess, "run", fake_tool)
    return process_photos(str(tmp_path), str(config_path), matcher_mode="off", **kwargs)


class TestReportAndUndoRoundTrip:
    def test_report_records_old_and_new_values(self, photo_tree, monkeypatch):
        tmp_path, config_path = photo_tree
        old = ["2020:01:01 00:00:00", "2020:01:01 00:00:00", "-", "hello"]
        fake = FakeExifTool()
        fake.current = {
            str(tmp_path / "01-06 Parc" / name): old for name in ("a.jpg", "b.jpg")
        }

        rc = run_process(tmp_path, config_path, fake, monkeypatch)
        assert rc == 0

        report = tmp_path / "exif_resync_report.csv"
        assert report.exists()
        with open(report, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 2
        row = rows[0]
        assert set(row.keys()) == set(REPORT_FIELDS)
        assert row["old_datetimeoriginal"] == "2020:01:01 00:00:00"
        assert row["old_imagedescription"] == "hello"
        assert row["new_datetime"].startswith("2026:06:01")
        assert row["source"] == "plan"

    def test_undo_restores_previous_values(self, photo_tree, monkeypatch):
        tmp_path, config_path = photo_tree
        old = ["2020:01:01 10:20:30", "2020:01:01 10:20:30", "-", "old text"]
        paths = {str(tmp_path / "01-06 Parc" / name): old for name in ("a.jpg", "b.jpg")}

        fake = FakeExifTool()
        fake.current = dict(paths)
        run_process(tmp_path, config_path, fake, monkeypatch)

        # Simulate a second state so undo has something to roll back.
        for p in fake.current:
            fake.current[p] = ["1999:09:09 09:09:09", "1999:09:09 09:09:09",
                               "1999:09:09 09:09:09", "resync wrote this"]

        report = tmp_path / "exif_resync_report.csv"
        rc = undo_from_report(str(report), exiftool_bin="mock-exiftool")
        assert rc == 0

        for path in paths:
            restored = fake.current[path]
            assert restored[0] == "2020:01:01 10:20:30"
            assert restored[1] == "2020:01:01 10:20:30"
            # "-" means the tag was absent before: it must be cleared again.
            assert restored[2] == ""
            assert restored[3] == "old text"

    def test_undo_missing_file_reports_failure(self, photo_tree, monkeypatch, capsys):
        import exif_resync

        tmp_path, _ = photo_tree
        fake = FakeExifTool()
        fake.current = {}
        monkeypatch.setattr(exif_resync.subprocess, "run", fake)

        report = tmp_path / "stale.csv"
        with open(report, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerow({**{k: "" for k in REPORT_FIELDS},
                             "path": str(tmp_path / "gone.jpg"),
                             "old_datetimeoriginal": "2020:01:01 00:00:00"})

        rc = undo_from_report(str(report), exiftool_bin="mock-exiftool")
        assert rc == 1

    def test_dry_run_writes_nothing(self, photo_tree, monkeypatch, capsys):
        tmp_path, config_path = photo_tree
        fake = FakeExifTool()
        rc = run_process(tmp_path, config_path, fake, monkeypatch, dry_run=True)

        assert rc == 0
        write_calls = [c for c in fake.calls if "-AllDates=" in " ".join(c)]
        assert not write_calls
        assert not (tmp_path / "exif_resync_report.csv").exists()
        out = capsys.readouterr().out
        assert "DRY-RUN" in out


class TestClockDrift:
    def test_drift_detected_and_reported(self, photo_tree, monkeypatch, capsys):

        tmp_path, config_path = photo_tree
        album = tmp_path / "01-06 Parc"
        # Cameras were 1h ahead of the reconstructed schedule.
        stale = ["2026:06:01 10:31:00", "-", "-", "-"]
        fake = FakeExifTool()
        fake.current = {
            str(album / "a.jpg"): stale,
            str(album / "b.jpg"): ["2026:06:01 10:32:00", "-", "-", "-"],
        }

        rc = run_process(tmp_path, config_path, fake, monkeypatch)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Clock drift detected" in out
        assert "--sync-clocks" in out

        with open(tmp_path / "exif_resync_report.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert all(row["drift_applied_sec"] == "" for row in rows)

    def test_sync_clocks_shifts_album(self, photo_tree, monkeypatch):
        tmp_path, config_path = photo_tree
        album = tmp_path / "01-06 Parc"
        fake = FakeExifTool()
        fake.current = {
            str(album / "a.jpg"): ["2026:06:01 10:31:00", "-", "-", "-"],
            str(album / "b.jpg"): ["2026:06:01 10:32:00", "-", "-", "-"],
        }

        rc = run_process(tmp_path, config_path, fake, monkeypatch, sync_clocks=True)
        assert rc == 0

        with open(tmp_path / "exif_resync_report.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        drift_values = {row["drift_applied_sec"] for row in rows}
        assert len(drift_values) == 1
        applied = int(next(iter(drift_values)))
        # Plan starts at 12:00, photos claim 10:31/10:32 -> correction ~ -89 min.
        assert -5500 <= applied <= -5300

    def test_small_drift_is_ignored(self, photo_tree, monkeypatch, capsys):
        tmp_path, config_path = photo_tree
        album = tmp_path / "01-06 Parc"
        fake = FakeExifTool()
        fake.current = {
            str(album / "a.jpg"): ["2026:06:01 12:00:10", "-", "-", "-"],
            str(album / "b.jpg"): ["2026:06:01 12:01:10", "-", "-", "-"],
        }

        rc = run_process(tmp_path, config_path, fake, monkeypatch)
        assert rc == 0
        assert "Clock drift detected" not in capsys.readouterr().out


class TestFilenameDatesTakePriority:
    def test_embedded_date_beats_plan(self, photo_tree, monkeypatch):
        tmp_path, config_path = photo_tree
        album = tmp_path / "01-06 Parc"
        (album / "a.jpg").unlink()
        (album / "IMG_20260601_183000.jpg").write_bytes(b"fake")

        fake = FakeExifTool()
        rc = run_process(tmp_path, config_path, fake, monkeypatch)
        assert rc == 0

        with open(tmp_path / "exif_resync_report.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        img_rows = [r for r in rows if r["path"].endswith("IMG_20260601_183000.jpg")]
        assert len(img_rows) == 1
        assert img_rows[0]["source"] == "filename"
        assert img_rows[0]["new_datetime"] == "2026:06:01 18:30:00"


def test_write_report_roundtrip(tmp_path):
    rows = [{field: f"v{i}" for field in REPORT_FIELDS} for i in range(3)]
    target = tmp_path / "r.csv"
    write_report(rows, str(target))
    with open(target, newline="", encoding="utf-8") as f:
        back = list(csv.DictReader(f))
    assert back == rows
