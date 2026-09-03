# SPDX-License-Identifier: MIT
import json

import pytest

from exif_resync import load_config, parse_filename_date, parse_folder_date


class TestParseFolderDate:
    def test_two_digit_day_month(self):
        assert parse_folder_date("01-05 Parc") == ("01-05", 1, 5, 12, 0)

    def test_single_digit_day_month(self):
        assert parse_folder_date("2-5 soiree boom") == ("02-05", 2, 5, 12, 0)

    def test_hour_after_date(self):
        assert parse_folder_date("01-05 09h00 Matin") == ("01-05", 1, 5, 9, 0)

    def test_hour_anywhere_in_name(self):
        assert parse_folder_date("05-06 Kayak 14h30") == ("05-06", 5, 6, 14, 30)

    def test_no_date(self):
        assert parse_folder_date("Misc photos") is None

    def test_iso_like_name_rejected(self):
        assert parse_folder_date("2026-05-01 trip") is None

    def test_invalid_month_rejected(self):
        assert parse_folder_date("99-99 bad") is None

    def test_invalid_hour_falls_back_to_default(self):
        assert parse_folder_date("05-05 Rando 99h99") == ("05-05", 5, 5, 12, 0)


class TestParseFilenameDate:
    def test_img_pattern(self):
        dt = parse_filename_date("IMG_20260501_143022.jpg")
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (
            2026,
            5,
            1,
            14,
            30,
            22,
        )

    def test_whatsapp_style(self):
        dt = parse_filename_date("WhatsApp-20260502-091530.mp4.jpg")
        assert dt is not None
        assert (dt.year, dt.hour) == (2026, 9)

    def test_dashed_with_time(self):
        dt = parse_filename_date("2026-05-01 14.30.22.jpg")
        assert dt is not None
        assert (dt.month, dt.day, dt.minute) == (5, 1, 30)

    def test_date_only_rejected(self):
        assert parse_filename_date("2026-05-01 holiday.jpg") is None

    def test_plain_name_rejected(self):
        assert parse_filename_date("photo_001.jpg") is None

    def test_invalid_values_rejected(self):
        assert parse_filename_date("IMG_20261301_250000.jpg") is None


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path, capsys):
        config = load_config(str(tmp_path / "nope.json"))
        assert config["year"] is None
        assert config["default_interval_sec"] == 180
        assert config["schedule"] == {}

    def test_english_keys(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "year": 2029,
                    "default_interval_sec": 45,
                    "schedule": {"01-05": "08:00"},
                }
            )
        )
        config = load_config(str(path))
        assert config == {"year": 2029, "default_interval_sec": 45, "schedule": {"01-05": "08:00"}}

    def test_french_keys(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "annee": 2027,
                    "intervalle_defaut_sec": 60,
                    "planning": {"01-05": "08:00"},
                }
            )
        )
        config = load_config(str(path))
        assert config == {"year": 2027, "default_interval_sec": 60, "schedule": {"01-05": "08:00"}}

    def test_invalid_json_exits(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(SystemExit):
            load_config(str(path))

    def test_negative_interval_exits(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"default_interval_sec": -5}))
        with pytest.raises(SystemExit):
            load_config(str(path))

    def test_zero_interval_exits(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"default_interval_sec": 0}))
        with pytest.raises(SystemExit):
            load_config(str(path))

    def test_schedule_keys_normalized(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"year": 2026, "schedule": {"1-5": "9:30"}}))
        config = load_config(str(path))
        assert config["schedule"] == {"01-05": "09:30"}

    def test_invalid_schedule_key_exits(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"schedule": {"tomorrow": "09:00"}}))
        with pytest.raises(SystemExit):
            load_config(str(path))

    def test_invalid_schedule_value_exits(self, tmp_path):
        for bad in ("9h00", "09-00", "25:00", "09:60", 900, "09:00:00"):
            path = tmp_path / "config.json"
            path.write_text(json.dumps({"schedule": {"01-05": bad}}))
            with pytest.raises(SystemExit):
                load_config(str(path))

    def test_non_dict_schedule_exits(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"schedule": ["01-05"]}))
        with pytest.raises(SystemExit):
            load_config(str(path))

    def test_mixed_language_keys_warn(self, tmp_path, capsys):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"year": 2026, "planning": {"01-05": "09:00"}}))
        config = load_config(str(path))
        assert config["schedule"] == {"01-05": "09:00"}
        assert "mixes English and French" in capsys.readouterr().out

    def test_year_string_coerced(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"year": "2027"}))
        assert load_config(str(path))["year"] == 2027

    def test_invalid_year_exits(self, tmp_path):
        for bad in ("soon", 99, 10000):
            path = tmp_path / "config.json"
            path.write_text(json.dumps({"year": bad}))
            with pytest.raises(SystemExit):
                load_config(str(path))
