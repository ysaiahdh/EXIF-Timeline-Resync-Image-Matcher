from datetime import datetime

import pytest

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
            "01-05",
            "03-05 Soiree boom",
            "",
            ctx["h_blog"],
            ctx["m_blog"],
            ctx["schedule"],
            ctx["default_interval"],
        )
        assert (h, m) == (20, 30)
        assert step == 90
        assert "keyword" in rule

    def test_evening_keyword_in_description(self):
        h, m, _, _ = determine_schedule(
            "01-05", "01-05 Plage", "Nous avons fait une grande veillee", 12, 0, {}, 180
        )
        assert (h, m) == (20, 30)

    def test_afternoon_keyword_second(self):
        h, m, step, rule = determine_schedule(
            "01-05", "02-05 animaux bis", "", 12, 0, {"01-05": "08:00"}, 180
        )
        assert (h, m) == (15, 0)
        assert step == 120

    def test_soiree_in_folder_triggers_evening(self):
        h, m, _, rule = determine_schedule("03-05", "03-05 Soiree", "", 12, 0, {}, 180)
        assert (h, m) == (20, 30)
        assert "evening" in rule

    def test_accented_keywords_match(self):
        h, m, _, _ = determine_schedule(
            "01-05", "01-05 Plage", "quelle belle soirée et veillée", 12, 0, {}, 180
        )
        assert (h, m) == (20, 30)
        h, m, _, _ = determine_schedule("02-05", "02-05 Sortie après-midi", "", 12, 0, {}, 180)
        assert (h, m) == (15, 0)

    def test_substring_lookalikes_do_not_match(self):
        # "bison" and "bisous" contain "bis" but are not the keyword.
        h, m, _, rule = determine_schedule("02-05", "02-05 Bisons bisous", "", 12, 0, {}, 180)
        assert (h, m) == (12, 0)
        assert rule == "default"

    def test_boom_in_description_triggers_evening(self):
        h, m, _, _ = determine_schedule("03-05", "03-05 Fête", "super boom ce soir", 12, 0, {}, 180)
        assert (h, m) == (20, 30)

    def test_schedule_third(self):
        h, m, step, rule = determine_schedule(
            "01-05", "01-05 Parc", "", 12, 0, {"01-05": "09:30"}, 180
        )
        assert (h, m) == (9, 30)
        assert step == 210
        assert rule == "schedule"

    def test_folder_time_fourth(self):
        h, m, step, rule = determine_schedule("02-05", "02-05 Safari 14h30", "", 14, 30, {}, 120)
        assert (h, m) == (14, 30)
        assert step == 120

    def test_default_fallback_uses_configured_interval(self):
        h, m, step, rule = determine_schedule("03-05", "03-05 Zoo", "", 12, 0, {}, 150)
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

    album = tmp_path / "01-06 Parc"
    album.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (album / name).write_bytes(b"fake")

    config_path = tmp_path / "config.json"
    config_path.write_text('{"year": 2026, "default_interval_sec": 100}')

    captured = {}

    def fake_apply(exiftool_bin, img_path, date_str, description, sync_mtime=False):
        import os

        captured[os.path.basename(img_path)] = date_str
        return True, ""

    monkeypatch.setattr("exif_resync.apply_tags", fake_apply)
    monkeypatch.setattr("exif_resync.find_exiftool", lambda: "mock-exiftool")
    monkeypatch.setattr("exif_resync.read_current_tags_batch", lambda tool, paths: {})

    rc = process_photos(str(tmp_path), str(config_path), matcher_mode="off")

    assert rc == 0
    times = [
        datetime.strptime(captured[n], "%Y:%m:%d %H:%M:%S") for n in ("a.jpg", "b.jpg", "c.jpg")
    ]
    assert times[0] < times[1] < times[2]
    assert (times[1] - times[0]).seconds == 100


class StubMatcher:
    name = "stub"

    def similarity(self, a, b):
        return 1.0 if a == b else 0.0


class TestDetectDuplicates:
    def test_within_album_pair_found(self, tmp_path):
        from exif_resync import detect_duplicates

        vectors = {"album": [("p1", "v"), ("p2", "v"), ("p3", "w")]}
        out = detect_duplicates(vectors, StubMatcher(), str(tmp_path))
        assert out is not None
        content = open(out, encoding="utf-8").read()
        assert "p1" in content and "p2" in content
        assert "p3" not in content

    def test_cross_album_pair_found(self, tmp_path):
        from exif_resync import detect_duplicates

        vectors = {"a": [("p1", "v")], "b": [("p2", "v")]}
        assert detect_duplicates(vectors, StubMatcher(), str(tmp_path)) is not None

    def test_no_dupes_no_file(self, tmp_path):
        from exif_resync import detect_duplicates

        vectors = {"a": [("p1", "v")], "b": [("p2", "w")]}
        assert detect_duplicates(vectors, StubMatcher(), str(tmp_path)) is None
        assert not (tmp_path / "duplicates_report.csv").exists()

    def test_dry_run_never_writes(self, tmp_path, capsys):
        from exif_resync import detect_duplicates

        vectors = {"a": [("p1", "v")], "b": [("p2", "v")]}
        assert detect_duplicates(vectors, StubMatcher(), str(tmp_path), dry_run=True) is None
        assert not (tmp_path / "duplicates_report.csv").exists()
        assert "not saved in dry-run" in capsys.readouterr().out

    def test_invalid_threshold_exits(self, tmp_path):
        from exif_resync import detect_duplicates

        with pytest.raises(SystemExit):
            detect_duplicates({}, StubMatcher(), str(tmp_path), threshold=1.5)


class TestRecursiveDiscovery:
    def test_nested_album_found_only_when_recursive(self, tmp_path, monkeypatch, capsys):
        import exif_resync

        nested = tmp_path / "season" / "01-06 Parc"
        nested.mkdir(parents=True)
        (nested / "a.jpg").write_bytes(b"fake")
        config = tmp_path / "config.json"
        config.write_text('{"year": 2026}')
        monkeypatch.setattr(exif_resync, "find_exiftool", lambda: None)

        rc = exif_resync.process_photos(
            str(tmp_path), str(config), dry_run=True, matcher_mode="off"
        )
        assert rc == 0
        assert "No dated albums found" in capsys.readouterr().out

        rc = exif_resync.process_photos(
            str(tmp_path), str(config), dry_run=True, matcher_mode="off", recursive=True
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "01-06 Parc" in out


class TestThresholdOptions:
    def test_match_threshold_override(self):
        pytest.importorskip("imagehash")
        from exif_resync import HashMatcher, build_matcher

        matcher = build_matcher("hash", 0.5)
        assert isinstance(matcher, HashMatcher)
        assert matcher.threshold == 0.5

    def test_invalid_match_threshold_exits(self):
        from exif_resync import build_matcher

        with pytest.raises(SystemExit):
            build_matcher("hash", 0.0)

    def test_invalid_thresholds_rejected_upfront(self, tmp_path):
        import exif_resync

        config = tmp_path / "config.json"
        config.write_text('{"year": 2026}')
        with pytest.raises(SystemExit):
            exif_resync.process_photos(str(tmp_path), str(config), match_threshold=2.0)
        with pytest.raises(SystemExit):
            exif_resync.process_photos(str(tmp_path), str(config), duplicate_threshold=0.0)


class TestTextHelpers:
    def test_fold_accents(self):
        from exif_resync import contains_word, fold_accents

        assert fold_accents("Soirée VÉILLÉE Après-Midi") == "soiree veillee apres-midi"
        assert contains_word(fold_accents("les bisons"), "bis") is False
        assert contains_word(fold_accents("02-05 animaux bis"), "bis") is True

    def test_iter_image_files_top_level_only_by_default(self, tmp_path):
        from exif_resync import iter_image_files

        (tmp_path / "a.jpg").write_bytes(b"x")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.jpg").write_bytes(b"x")
        assert len(iter_image_files(str(tmp_path), False)) == 1
        assert len(iter_image_files(str(tmp_path), True)) == 2

    def test_cache_save_empty_writes_nothing(self, tmp_path):
        from exif_resync import cache_save

        cache_save(str(tmp_path), {})
        assert list(tmp_path.iterdir()) == []
