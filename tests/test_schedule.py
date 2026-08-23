from datetime import datetime

from exif_resync import determine_schedule, fmt_delta


def make_ctx():
    return {
        "h_blog": 12,
        "m_blog": 0,
        "schedule": {"01-05": "09:30"},
        "default_interval": 180,
    }


class TestDetermineSchedule:
    def test_evening_keyword_in_folder_wins(self):
        ctx = make_ctx()
        h, m, step, rule = determine_schedule(
            "01-05", "03-05 Soiree boom", "", ctx["h_blog"], ctx["m_blog"],
            ctx["schedule"], ctx["default_interval"])
        assert (h, m) == (20, 30)
        assert step == 90
        assert "keyword" in rule

    def test_evening_keyword_in_description(self):
        h, m, _, _ = determine_schedule(
            "01-05", "01-05 Plage", "Nous avons fait une grande veillee",
            12, 0, {}, 180)
        assert (h, m) == (20, 30)

    def test_afternoon_keyword_second(self):
        h, m, step, rule = determine_schedule(
            "01-05", "02-05 animaux bis", "", 12, 0, {"01-05": "08:00"}, 180)
        assert (h, m) == (15, 0)
        assert step == 120

    def test_schedule_third(self):
        h, m, step, rule = determine_schedule(
            "01-05", "01-05 Parc", "", 12, 0, {"01-05": "09:30"}, 180)
        assert (h, m) == (9, 30)
        assert step == 210
        assert rule == "schedule"

    def test_folder_time_fourth(self):
        h, m, step, rule = determine_schedule(
            "02-05", "02-05 Safari 14h30", "", 14, 30, {}, 120)
        assert (h, m) == (14, 30)
        assert step == 120

    def test_default_fallback_uses_configured_interval(self):
        h, m, step, rule = determine_schedule(
            "03-05", "03-05 Zoo", "", 12, 0, {}, 150)
        assert (h, m) == (12, 0)
        assert step == 150
        assert rule == "default"


class TestFmtDelta:
    def test_positive_hours(self):
        assert fmt_delta(8025) == "+2h13m"

    def test_negative_minutes(self):
        assert fmt_delta(-95) == "-1m35s"

    def test_seconds_only(self):
        assert fmt_delta(45) == "+45s"

    def test_zero(self):
        assert fmt_delta(0) == "+0s"


class TestTimelineSourceLabels:
    def test_all_sources_labelled(self):
        from exif_resync import SOURCE_LABELS
        for source in ("filename", "resnet", "hash", "plan"):
            assert source in SOURCE_LABELS


def test_version_string():
    import exif_resync
    parts = exif_resync.__version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_planned_times_are_strictly_increasing(tmp_path, monkeypatch):
    from exif_resync import process_photos
    monkeypatch.setattr("sys.argv", ["exif_resync.py"])

    album = tmp_path / "01-06 Parc"
    album.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (album / name).write_bytes(b"fake")

    config_path = tmp_path / "config.json"
    config_path.write_text('{"year": 2026, "default_interval_sec": 100}')

    captured = {}

    def fake_apply(exiftool_bin, img_path, date_str, description):
        import os
        captured[os.path.basename(img_path)] = date_str
        return True, ""

    monkeypatch.setattr("exif_resync.apply_tags", fake_apply)
    monkeypatch.setattr("exif_resync.find_exiftool", lambda: "mock-exiftool")
    monkeypatch.setattr("exif_resync.read_current_tags",
                        lambda tool, path: ["-", "-", "-", "-"])

    rc = process_photos(str(tmp_path), str(config_path), matcher_mode="off")

    assert rc == 0
    times = [datetime.strptime(captured[n], "%Y:%m:%d %H:%M:%S")
             for n in ("a.jpg", "b.jpg", "c.jpg")]
    assert times[0] < times[1] < times[2]
    assert (times[1] - times[0]).seconds == 100
