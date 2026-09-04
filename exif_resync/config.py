# SPDX-License-Identifier: MIT
"""Config loading and validation (English and French keys, normalized)."""

import json
import os
import re
import sys

SCHEDULE_KEY_RE = re.compile(r"\s*(\d{1,2})-(\d{1,2})\s*")
SCHEDULE_TIME_RE = re.compile(r"\s*(\d{1,2})\s*:\s*(\d{2})\s*")

EN_KEYS = ("year", "default_interval_sec", "schedule")
FR_KEYS = ("annee", "intervalle_defaut_sec", "planning")


def normalize_day_key(key):
    """Normalize a schedule key to zero-padded "DD-MM", or None if invalid."""
    if not isinstance(key, str):
        return None
    match = SCHEDULE_KEY_RE.fullmatch(key)
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    return f"{day:02d}-{month:02d}"


def parse_time_value(value):
    """Parse an "HH:MM" time string. Returns (hour, minute) or None."""
    if not isinstance(value, str):
        return None
    match = SCHEDULE_TIME_RE.fullmatch(value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def normalize_schedule_time(value, day_key, config_path):
    """Validate a schedule value and return it as zero-padded "HH:MM"."""
    parsed = parse_time_value(value)
    if parsed is None:
        sys.exit(
            f"ERROR: '{config_path}': schedule entry for '{day_key}' must map "
            f'"DD-MM" to "HH:MM" (24h), got {value!r}.'
        )
    return f"{parsed[0]:02d}:{parsed[1]:02d}"


def load_config(config_path):
    defaults = {"year": None, "default_interval_sec": 180, "schedule": {}}

    if not os.path.exists(config_path):
        print(f"WARNING: config file '{config_path}' not found. Using default settings.")
        return defaults

    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: '{config_path}' is not valid JSON: {exc}")
    except OSError as exc:
        sys.exit(f"ERROR: cannot read '{config_path}': {exc}")

    if not isinstance(data, dict):
        sys.exit(f"ERROR: '{config_path}' must contain a JSON object.")

    if any(k in data for k in EN_KEYS) and any(k in data for k in FR_KEYS):
        print(
            f"WARNING: '{config_path}' mixes English and French keys. Pick one spelling per file."
        )

    year = data.get("year", data.get("annee"))
    interval = data.get("default_interval_sec", data.get("intervalle_defaut_sec", 180))
    schedule = data.get("schedule", data.get("planning", {}))

    if year is not None:
        try:
            year = int(str(year).strip())
        except (ValueError, TypeError, AttributeError):
            sys.exit(f"ERROR: 'year' must be a four-digit year, got {year!r}.")
        if not 1000 <= year <= 9999:
            sys.exit(f"ERROR: 'year' must be a four-digit year, got {year!r}.")

    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
        sys.exit(f"ERROR: 'default_interval_sec' must be a positive number, got {interval!r}.")
    if not isinstance(schedule, dict):
        sys.exit('ERROR: \'schedule\' must be an object mapping "DD-MM" to "HH:MM".')

    normalized_schedule = {}
    for raw_key, raw_value in schedule.items():
        day_key = normalize_day_key(raw_key)
        if day_key is None:
            sys.exit(
                f"ERROR: '{config_path}': schedule key {raw_key!r} is not a valid \"DD-MM\" date."
            )
        normalized_schedule[day_key] = normalize_schedule_time(raw_value, raw_key, config_path)

    return {
        "year": year if year else None,
        "default_interval_sec": int(interval),
        "schedule": normalized_schedule,
    }
