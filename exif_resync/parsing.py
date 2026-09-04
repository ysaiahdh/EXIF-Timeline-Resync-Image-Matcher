# SPDX-License-Identifier: MIT
"""Date parsing: folder names, filenames, keywords, and album discovery."""

import os
import re
import unicodedata
from datetime import datetime

FOLDER_DATE_RE = re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2})h(\d{2}))?(?!\d)")
FOLDER_HOUR_RE = re.compile(r"(?<![\dh])(\d{1,2})h(\d{2})(?!\d)")
FILENAME_DT_RE = re.compile(
    r"(?<!\d)(\d{4})[-_.T ]?(\d{2})[-_.T ]?(\d{2})[-_.T ]?"
    r"(\d{2})[.:T]?(\d{2})(?:[.:T]?(\d{2}))?(?!\d)"
)

# Keywords are matched accent-insensitively on whole words only, so "bison"
# or "bisous" do not trigger the afternoon rule and "soirée" still matches
# "soiree". Evening keywords are looked up in both the folder name and the
# album text; afternoon keywords apply to the folder name only, because bare
# words like "bis" are common in free prose (street numbers, "bis repetita").
EVENING_KEYWORDS = ("boom", "soiree", "veillee")
AFTERNOON_KEYWORDS = ("bis", "animaux", "apres-midi")

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")


def fold_accents(text):
    """Lowercase and strip accents so "soirée" matches "soiree"."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(ch) != "Mn"
    )


def contains_word(folded_text, word):
    """True when `word` appears as a whole word in already-folded text."""
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", folded_text) is not None


def iter_album_dirs(root_dir, recursive):
    """Yield album directories: direct children, or all subdirectories when
    recursive (hidden directories pruned, the root itself excluded)."""
    if not recursive:
        for name in os.listdir(root_dir):
            full = os.path.join(root_dir, name)
            if os.path.isdir(full):
                yield full
        return
    for dirpath, subdirs, _files in os.walk(root_dir):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
        if os.path.abspath(dirpath) == os.path.abspath(root_dir):
            continue
        if os.path.basename(dirpath).startswith("."):
            continue
        yield dirpath


def iter_image_files(directory, recursive):
    """Sorted image paths under `directory` (top level, or walked)."""
    found = []
    if recursive:
        for dirpath, subdirs, files in os.walk(directory):
            subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
            for name in files:
                if name.lower().endswith(IMAGE_EXTENSIONS):
                    found.append(os.path.join(dirpath, name))
    else:
        for name in os.listdir(directory):
            if name.lower().endswith(IMAGE_EXTENSIONS):
                full = os.path.join(directory, name)
                if os.path.isfile(full):
                    found.append(full)
    return sorted(found, key=str.lower)


def parse_folder_date(folder_name):
    match = FOLDER_DATE_RE.search(folder_name)
    if not match:
        return None

    day, month, hour, minute = match.groups()
    day_i, month_i = int(day), int(month)

    if not (1 <= day_i <= 31 and 1 <= month_i <= 12):
        return None

    h_blog = int(hour) if hour is not None else None
    m_blog = int(minute) if minute is not None else None

    if h_blog is None:
        hour_match = FOLDER_HOUR_RE.search(folder_name)
        if hour_match:
            h_blog, m_blog = int(hour_match.group(1)), int(hour_match.group(2))

    if h_blog is None or h_blog > 23 or m_blog > 59:
        h_blog, m_blog = 12, 0

    return f"{day_i:02d}-{month_i:02d}", day_i, month_i, h_blog, m_blog


def parse_filename_date(filename):
    match = FILENAME_DT_RE.search(filename)
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))
    except ValueError:
        return None


def determine_schedule(
    day_key, folder_name, description, h_blog, m_blog, schedule, default_interval
):
    combined = fold_accents(folder_name + "\n" + description)
    folder_text = fold_accents(folder_name)

    if any(contains_word(combined, kw) for kw in EVENING_KEYWORDS):
        return 20, 30, 90, "keyword (evening)"
    if any(contains_word(folder_text, kw) for kw in AFTERNOON_KEYWORDS):
        return 15, 0, 120, "keyword (afternoon)"
    if day_key in schedule:
        h_str, m_str = schedule[day_key].split(":")
        return int(h_str), int(m_str), 210, "schedule"
    if h_blog != 12 or m_blog != 0:
        return h_blog, m_blog, default_interval, "folder time"
    return h_blog, m_blog, default_interval, "default"


def read_album_description(path_folder):
    """Return the stripped content of an album's first .txt file ("" if none)."""
    try:
        names = os.listdir(path_folder)
    except OSError:
        return ""
    txt_files = sorted((f for f in names if f.lower().endswith(".txt")), key=str.lower)
    if not txt_files:
        return ""
    try:
        with open(os.path.join(path_folder, txt_files[0]), encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except Exception:
        return ""


def summarize_albums(root_dir, year, schedule, default_interval, recursive=False):
    """Describe every album directory for display purposes.

    Returns a list of dicts with keys: label, dated (bool), day_key, start
    ("HH:MM" or "-"), step, rule, photos. Undated folders and impossible
    dates (e.g. Feb 30th) are included with dated=False.
    """
    albums = []
    for path_folder in sorted(iter_album_dirs(root_dir, recursive), key=str.lower):
        folder = os.path.basename(path_folder)
        label = os.path.relpath(path_folder, root_dir)
        try:
            names = os.listdir(path_folder)
        except OSError:
            names = []
        photos = sum(1 for f in names if f.lower().endswith(IMAGE_EXTENSIONS))
        entry = {
            "label": label,
            "dated": False,
            "day_key": "",
            "start": "-",
            "step": 0,
            "rule": "ignored (no DD-MM date)",
            "photos": photos,
        }
        parsed = parse_folder_date(folder)
        if parsed:
            day_key, day, month, h_blog, m_blog = parsed
            description = read_album_description(path_folder)
            h_start, m_start, step, rule = determine_schedule(
                day_key, folder, description, h_blog, m_blog, schedule, default_interval
            )
            try:
                datetime(year, month, day, h_start, m_start)
            except ValueError:
                entry.update(day_key=day_key, rule=f"invalid date ({day}/{month}/{year})")
            else:
                entry.update(
                    dated=True,
                    day_key=day_key,
                    start=f"{h_start:02d}:{m_start:02d}",
                    step=step,
                    rule=rule,
                )
        albums.append(entry)
    return albums
