#!/usr/bin/env python3

"""
EXIF Timeline Resync & Image Matcher

Restores EXIF timestamps from folder names, schedules, embedded filename
dates, and visual matching against your own reference photos.

Every write run produces a change report (CSV) that can be replayed with
--undo to restore the previous state exactly.
"""

import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta

__version__ = "2.1.0"

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import imagehash

    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

try:
    import torch
    import torchvision.transforms as T
    from torchvision.models import ResNet18_Weights, resnet18

    HAS_VISION = True
except ImportError:
    HAS_VISION = False

try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


RESNET_SIMILARITY_THRESHOLD = 0.85
HASH_SIMILARITY_THRESHOLD = 0.90
DUPLICATE_SIMILARITY_THRESHOLD = 0.95
DRIFT_MIN_SAMPLES = 2
DRIFT_REPORT_THRESHOLD_SEC = 60
RESNET_BATCH_SIZE = 16
VECTOR_CACHE_NAME = ".exif_resync_cache.npz"

REPORT_FIELDS = [
    "path",
    "old_datetimeoriginal",
    "old_createdate",
    "old_modifydate",
    "old_imagedescription",
    "new_datetime",
    "new_imagedescription",
    "match_score",
    "source",
    "drift_applied_sec",
]

FOLDER_DATE_RE = re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2})h(\d{2}))?(?!\d)")
FOLDER_HOUR_RE = re.compile(r"(?<![\dh])(\d{1,2})h(\d{2})(?!\d)")
SCHEDULE_KEY_RE = re.compile(r"\s*(\d{1,2})-(\d{1,2})\s*")
SCHEDULE_TIME_RE = re.compile(r"\s*(\d{1,2})\s*:\s*(\d{2})\s*")

# Keywords are matched accent-insensitively on whole words only, so "bison"
# or "bisous" do not trigger the afternoon rule and "soirée" still matches
# "soiree". Evening keywords are looked up in both the folder name and the
# album text; afternoon keywords apply to the folder name only, because bare
# words like "bis" are common in free prose (street numbers, "bis repetita").
EVENING_KEYWORDS = ("boom", "soiree", "veillee")
AFTERNOON_KEYWORDS = ("bis", "animaux", "apres-midi")

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
FILENAME_DT_RE = re.compile(
    r"(?<!\d)(\d{4})[-_.T ]?(\d{2})[-_.T ]?(\d{2})[-_.T ]?"
    r"(\d{2})[.:T]?(\d{2})(?:[.:T]?(\d{2}))?(?!\d)"
)


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


# --- Matchers ---


class ResNetMatcher:
    name = "resnet"
    threshold = RESNET_SIMILARITY_THRESHOLD

    def __init__(self):
        if not HAS_VISION or not HAS_PILLOW:
            raise RuntimeError("PyTorch, torchvision and Pillow are required.")
        if HAS_NUMPY and torch.cuda.is_available():
            self.device = torch.device("cuda")
        elif (
            HAS_NUMPY and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
        ):
            self.device = torch.device("mps")
        else:
            self.device = torch.device("cpu")
        weights = ResNet18_Weights.DEFAULT
        model = resnet18(weights=weights)
        model.eval()
        self.model = torch.nn.Sequential(*list(model.children())[:-1]).to(self.device)
        self.transform = T.Compose(
            [
                T.Resize((224, 224)),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ]
        )

    def get_vectors(self, paths):
        results = []
        chunks = [paths[i : i + RESNET_BATCH_SIZE] for i in range(0, len(paths), RESNET_BATCH_SIZE)]
        for chunk in progress_iter(chunks, "Embedding", True):
            tensors = []
            for p in chunk:
                try:
                    with Image.open(p) as img:
                        tensors.append(self.transform(img.convert("RGB")))
                except Exception:
                    tensors.append(None)
            valid = [j for j, t in enumerate(tensors) if t is not None]
            if not valid:
                results.extend([None] * len(chunk))
                continue
            batch = torch.stack([tensors[j] for j in valid]).to(self.device)
            with torch.no_grad():
                vecs = self.model(batch).squeeze(-1).squeeze(-1)
                vecs = vecs / vecs.norm(dim=1, keepdim=True)
            by_index = dict(zip(valid, vecs.cpu()))
            results.extend(by_index.get(j) for j in range(len(chunk)))
        return results

    def similarity(self, a, b):
        if a is None or b is None:
            return 0.0
        return float(torch.dot(a, b))


class HashMatcher:
    name = "hash"
    threshold = HASH_SIMILARITY_THRESHOLD

    def __init__(self):
        if not HAS_PILLOW or not HAS_IMAGEHASH:
            raise RuntimeError("Pillow and ImageHash are required.")

    def get_vectors(self, paths):
        results = []
        for p in progress_iter(paths, "Hashing", True):
            try:
                with Image.open(p) as img:
                    results.append(imagehash.phash(img))
            except Exception:
                results.append(None)
        return results

    def similarity(self, a, b):
        if a is None or b is None:
            return 0.0
        return 1.0 - (a - b) / float(a.hash.size)


def build_matcher(mode, threshold=None):
    """Build the requested matcher, optionally overriding its acceptance
    threshold (must be within 0 < t <= 1). Returns None when matching is off
    or no engine is available."""
    if threshold is not None and not 0.0 < threshold <= 1.0:
        sys.exit(f"ERROR: --match-threshold must be within (0, 1], got {threshold!r}.")

    if mode == "off":
        return None

    matcher = None
    if mode == "resnet":
        if not HAS_VISION or not HAS_PILLOW:
            sys.exit(
                "ERROR: --matcher resnet requires PyTorch, torchvision and Pillow "
                "(pip install -r requirements.txt)."
            )
        matcher = ResNetMatcher()

    elif mode == "hash":
        if not HAS_PILLOW or not HAS_IMAGEHASH:
            sys.exit(
                "ERROR: --matcher hash requires Pillow and ImageHash "
                "(pip install Pillow ImageHash)."
            )
        matcher = HashMatcher()

    elif HAS_VISION and HAS_PILLOW:
        matcher = ResNetMatcher()
    elif HAS_PILLOW and HAS_IMAGEHASH:
        print("PyTorch not installed: falling back to lightweight perceptual-hash matching.")
        matcher = HashMatcher()
    else:
        print(
            "No matcher available: install torch (AI matching) or "
            "Pillow+ImageHash (lightweight matching)."
        )
        return None

    if threshold is not None:
        matcher.threshold = threshold
    return matcher


# --- Vector cache (ResNet only) ---


def cache_load(ref_dir):
    path = os.path.join(ref_dir, VECTOR_CACHE_NAME)
    if not HAS_NUMPY or not os.path.exists(path):
        return {}
    try:
        data = np.load(path, allow_pickle=True)
        return {
            str(k): {"mtime": float(m), "vector": v}
            for k, m, v in zip(data["paths"], data["mtimes"], data["vectors"])
        }
    except Exception:
        return {}


def cache_save(ref_dir, cache):
    if not HAS_NUMPY or not cache:
        return
    path = os.path.join(ref_dir, VECTOR_CACHE_NAME)
    try:
        keys = list(cache.keys())
        np.savez_compressed(
            path,
            paths=np.array(keys, dtype=object),
            mtimes=np.array([cache[k]["mtime"] for k in keys], dtype=float),
            vectors=np.stack([np.asarray(cache[k]["vector"]) for k in keys]),
        )
    except Exception as exc:
        print(f"WARNING: could not save reference vector cache: {exc}")


def load_reference_gallery(ref_dir, matcher, exiftool_bin, recursive=False):
    reference_db = []
    print(f"\nIndexing reference gallery ({matcher.name})...")

    images_ref = iter_image_files(ref_dir, recursive)
    if not images_ref:
        print("No reference photos found.")
        return reference_db

    # One batched ExifTool call instead of one subprocess per image.
    dated = {}
    try:
        res = subprocess.run(
            [exiftool_bin, "-j", "-DateTimeOriginal", *images_ref], capture_output=True, text=True
        )
    except OSError as exc:
        print(f"WARNING: could not read reference dates: {exc}")
        return reference_db
    if res.returncode == 0:
        try:
            entries = json.loads(res.stdout) if res.stdout.strip() else []
        except ValueError:
            entries = []
        for img_path, entry in zip(images_ref, entries):
            date_str = (entry or {}).get("DateTimeOriginal", "")
            if not date_str:
                continue
            try:
                dated[img_path] = datetime.strptime(date_str.strip(), "%Y:%m:%d %H:%M:%S")
            except ValueError:
                continue
    else:
        print("WARNING: ExifTool failed to read reference dates.")

    skipped = len(images_ref) - len(dated)
    if skipped:
        print(f"{skipped} reference photo(s) have no readable EXIF date and were skipped.")
    if not dated:
        print("No dated reference photos found.")
        return reference_db

    if isinstance(matcher, ResNetMatcher):
        cache = cache_load(ref_dir)
        stale_paths, stale_ok = [], []
        for p, dt_ref in dated.items():
            entry = cache.get(p)
            if entry and abs(entry["mtime"] - os.path.getmtime(p)) < 1e-6:
                stale_ok.append((p, dt_ref, torch.from_numpy(np.asarray(entry["vector"])).float()))
            else:
                stale_paths.append(p)
        fresh_vecs = matcher.get_vectors(stale_paths)
        for p, v in zip(stale_paths, fresh_vecs):
            if v is not None:
                cache[p] = {"mtime": os.path.getmtime(p), "vector": v.numpy()}
        cache_save(ref_dir, cache)
        vectors = {}
        for p, dt_ref, v in stale_ok:
            vectors[p] = v
        for p, v in zip(stale_paths, fresh_vecs):
            if v is not None:
                vectors[p] = v
    else:
        vectors = {p: v for p, v in zip(dated.keys(), matcher.get_vectors(list(dated.keys())))}

    for p, dt_ref in dated.items():
        v = vectors.get(p)
        if v is not None:
            reference_db.append({"path": p, "datetime": dt_ref, "vector": v})

    print(f"{len(reference_db)} reference photos indexed.")
    return reference_db


# --- Core Logic ---


def find_exiftool():
    path = shutil.which("exiftool") or shutil.which("exiftool(-k)")
    if not path:
        local_exe = os.path.join(os.getcwd(), "exiftool.exe")
        local_exe_k = os.path.join(os.getcwd(), "exiftool(-k).exe")
        if os.path.exists(local_exe):
            path = local_exe
        elif os.path.exists(local_exe_k):
            path = local_exe_k
    return path


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


EXIF_TAGS = ("DateTimeOriginal", "CreateDate", "ModifyDate", "ImageDescription")


def _tags_from_json_entry(entry):
    """Map one `exiftool -j` object to 4 strings ("-" for absent tags)."""
    entry = entry or {}
    values = []
    for tag in EXIF_TAGS:
        value = entry.get(tag)
        values.append(str(value) if value not in (None, "") else "-")
    return values


def read_current_tags(exiftool_bin, img_path):
    """Read the 4 tracked tags of one image (None when unreadable).

    Uses `exiftool -j` so multi-line ImageDescription values cannot shift
    the parsed fields, which plain `-s3` output would allow.
    """
    batch = read_current_tags_batch(exiftool_bin, [img_path])
    return batch.get(img_path)


def read_current_tags_batch(exiftool_bin, img_paths):
    """Read the 4 tracked tags of many images with a single ExifTool call.

    Returns {path: [DateTimeOriginal, CreateDate, ModifyDate,
    ImageDescription]}; unreadable images are simply absent.
    """
    if not img_paths:
        return {}
    cmd = [exiftool_bin, "-j", *(f"-{t}" for t in EXIF_TAGS), *img_paths]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return {}
    if res.returncode != 0:
        return {}
    try:
        entries = json.loads(res.stdout) if res.stdout.strip() else []
    except ValueError:
        return {}
    # -j preserves input order, which also keeps odd filenames unambiguous.
    return {p: _tags_from_json_entry(e) for p, e in zip(img_paths, entries)}


def apply_tags(exiftool_bin, img_path, date_str, description, sync_mtime=False):
    cmd = [exiftool_bin, f"-AllDates={date_str}"]
    if description:
        cmd.append(f"-ImageDescription={description}")
    if sync_mtime:
        cmd.append(f"-FileModifyDate={date_str}")
    cmd += ["-overwrite_original", img_path]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    return res.returncode == 0, res.stderr.strip()


def fmt_delta(seconds):
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(int(round(seconds)))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{sign}{d}d{h}h"
    if h:
        return f"{sign}{h}h{m:02d}m"
    if m:
        return f"{sign}{m}m{s:02d}s"
    return f"{sign}{s}s"


def progress_iter(iterable, desc, enabled):
    if enabled and HAS_TQDM and sys.stderr.isatty():
        yield from tqdm(iterable, desc=desc, unit="img", leave=False)
    else:
        yield from iterable


def process_photos(
    root_dir,
    config_path,
    ref_dir=None,
    dry_run=False,
    year_override=None,
    report_path=None,
    matcher_mode="auto",
    sync_clocks=False,
    timeline=False,
    verbose=False,
    quiet=False,
    recursive=False,
    match_threshold=None,
    duplicate_threshold=None,
    duplicates=False,
    sync_mtime=False,
    progress_cb=None,
):
    """Run the resync. When `progress_cb` is given it is called after every
    photo as progress_cb(album_label, done_in_album, album_total)."""
    if duplicate_threshold is not None and not 0.0 < duplicate_threshold <= 1.0:
        sys.exit(
            f"ERROR: --duplicate-threshold must be within (0, 1], got {duplicate_threshold!r}."
        )
    if match_threshold is not None and not 0.0 < match_threshold <= 1.0:
        sys.exit(f"ERROR: --match-threshold must be within (0, 1], got {match_threshold!r}.")

    exiftool_bin = None
    if not dry_run:
        exiftool_bin = find_exiftool()
        if not exiftool_bin:
            sys.exit(
                "ERROR: ExifTool not found on your system.\n"
                "Install it first:\n"
                "  - Windows: download from https://exiftool.org and place exiftool.exe "
                "in the project root or your PATH\n"
                "  - Linux:   sudo apt install libimage-exiftool-perl\n"
                "  - macOS:   brew install exiftool\n"
                "Or re-run with --dry-run to preview changes without writing."
            )
    else:
        # Dry-run stays usable without ExifTool; drift analysis below simply
        # degrades to "unknown" when it is absent.
        exiftool_bin = find_exiftool()

    def log(msg):
        if not quiet:
            print(msg)

    def vlog(msg):
        if verbose and not quiet:
            print(msg)

    config = load_config(config_path)

    year = year_override or config["year"]
    if year is None:
        year = datetime.now().year
        print(
            f"WARNING: no year configured. Defaulting to {year} "
            f"(use the 'year' key in {config_path} or --year to change this)."
        )

    default_interval = config["default_interval_sec"]
    schedule = config["schedule"]

    if not os.path.isdir(root_dir):
        sys.exit(f"ERROR: directory '{root_dir}' does not exist.")
    if ref_dir and not os.path.isdir(ref_dir):
        sys.exit(f"ERROR: reference directory '{ref_dir}' does not exist.")

    matcher = None
    ref_db = []
    if ref_dir:
        tool_for_refs = exiftool_bin or find_exiftool()
        if not tool_for_refs:
            sys.exit(
                "ERROR: ExifTool is required to read dates from your reference "
                "photos (--reference). Install it or drop --dry-run."
            )
        matcher = build_matcher(matcher_mode, match_threshold)
        if matcher is not None:
            ref_db = load_reference_gallery(ref_dir, matcher, tool_for_refs, recursive)
    elif duplicates and matcher_mode != "off":
        # Duplicate detection without timestamp matching: embed the downloads
        # only to compare them against each other.
        matcher = build_matcher(matcher_mode, match_threshold)

    folders = sorted(iter_album_dirs(root_dir, recursive), key=str.lower)

    total_images = 0
    failures = []
    report_rows = []
    timeline_rows = []
    album_vectors = {}
    drift_summaries = []

    for path_folder in folders:
        folder = os.path.basename(path_folder)
        label = os.path.relpath(path_folder, root_dir)
        parsed = parse_folder_date(folder)

        if not parsed:
            vlog(f"\nFolder: {label}\n  Skipped: no DD-MM date in name.")
            continue

        day_key, day, month, h_blog, m_blog = parsed

        description = read_album_description(path_folder)

        h_start, m_start, step, rule = determine_schedule(
            day_key, folder, description, h_blog, m_blog, schedule, default_interval
        )
        try:
            base_dt = datetime(year, month, day, h_start, m_start)
        except ValueError:
            print(f"\nFolder: {label}\n  Skipped: invalid date ({day}/{month}/{year}).")
            continue

        images = sorted(
            (f for f in os.listdir(path_folder) if f.lower().endswith(IMAGE_EXTENSIONS)),
            key=str.lower,
        )
        if not images:
            continue

        log(f"\nFolder: {label}  [{rule}, step {step}s]{'  (DRY-RUN)' if dry_run else ''}")

        planned = [base_dt + timedelta(seconds=i * step) for i in range(len(images))]
        img_paths = [os.path.join(path_folder, img) for img in images]

        img_vectors = []
        if matcher and (ref_db or duplicates):
            img_vectors = matcher.get_vectors(img_paths)
            for img, vec in zip(images, img_vectors):
                if vec is not None:
                    album_vectors.setdefault(label, []).append(
                        (os.path.abspath(os.path.join(path_folder, img)), vec)
                    )

        # One batched read per album. In dry-run without ExifTool there is
        # nothing to read, so drift analysis is skipped for those albums.
        old_tags_by_path = {}
        if exiftool_bin:
            old_tags_by_path = read_current_tags_batch(exiftool_bin, img_paths)
        old_tags_list = [old_tags_by_path.get(p) for p in img_paths]

        offsets = []
        for i, tags in enumerate(old_tags_list):
            if tags and tags[0] not in ("", "-"):
                try:
                    old_dt = datetime.strptime(tags[0].strip(), "%Y:%m:%d %H:%M:%S")
                    offsets.append((old_dt - planned[i]).total_seconds())
                except ValueError:
                    pass

        drift_offset = 0.0
        drift_detected = False
        if len(offsets) >= DRIFT_MIN_SAMPLES:
            drift_offset = statistics.median(offsets)
            if abs(drift_offset) >= DRIFT_REPORT_THRESHOLD_SEC:
                drift_detected = True
                note = (
                    "" if (sync_clocks or dry_run) else "  (re-run with --sync-clocks to correct)"
                )
                msg = (
                    f"  Clock drift detected: median {fmt_delta(drift_offset)} "
                    f"across {len(offsets)} dated photo(s){note}"
                )
                print(msg)
                drift_summaries.append((label, drift_offset, len(offsets)))

        for idx, (img, img_path, planned_dt) in enumerate(zip(images, img_paths, planned)):
            target_dt = planned_dt
            source = "plan"
            score = ""

            fname_dt = parse_filename_date(img)
            if fname_dt is not None:
                # A timestamp baked into the filename by the camera is ground
                # truth and overrides every other source.
                target_dt = fname_dt
                source = "filename"
                vlog(
                    f"  {img}: timestamp taken from filename "
                    f"({fname_dt.strftime('%Y-%m-%d %H:%M:%S')})"
                )
            elif matcher and ref_db and idx < len(img_vectors):
                vec = img_vectors[idx]
                if vec is not None:
                    best_match, best_score = None, 0.0
                    for ref in ref_db:
                        sim = matcher.similarity(vec, ref["vector"])
                        if sim > best_score:
                            best_score, best_match = sim, ref
                    if best_score >= matcher.threshold and best_match:
                        if abs((best_match["datetime"].date() - base_dt.date()).days) <= 1:
                            target_dt = best_match["datetime"]
                            source = matcher.name
                            score = f"{best_score:.4f}"
                            vlog(
                                f"  {img}: visual match ({best_score * 100:.1f}%) -> "
                                f"{target_dt.strftime('%H:%M:%S')}"
                            )

            applied_drift = ""
            if drift_detected and sync_clocks and source != "filename":
                target_dt = target_dt + timedelta(seconds=round(drift_offset))
                applied_drift = str(int(round(drift_offset)))

            date_str = target_dt.strftime("%Y:%m:%d %H:%M:%S")

            if dry_run:
                tag_info = ""
                if verbose and exiftool_bin is not None and old_tags_list[idx] is None:
                    tag_info = "  [unreadable EXIF]"
                log(f"  [{source:>8}] {img}: {date_str}{tag_info}")
            else:
                old_tags = old_tags_list[idx]
                ok, err = apply_tags(exiftool_bin, img_path, date_str, description, sync_mtime)
                row = {
                    "path": os.path.abspath(img_path),
                    "old_datetimeoriginal": old_tags[0] if old_tags else "",
                    "old_createdate": old_tags[1] if old_tags else "",
                    "old_modifydate": old_tags[2] if old_tags else "",
                    "old_imagedescription": old_tags[3] if old_tags else "",
                    "new_datetime": date_str,
                    "new_imagedescription": (
                        description if description else (old_tags[3] if old_tags else "")
                    ),
                    "match_score": score,
                    "source": source,
                    "drift_applied_sec": applied_drift,
                }
                if ok:
                    report_rows.append(row)
                else:
                    failures.append((img_path, err or "unknown error"))

            timeline_rows.append(
                {
                    "folder": label,
                    "rule": rule,
                    "file": img,
                    "time": date_str,
                    "source": source,
                    "score": score,
                    "drift": applied_drift,
                }
            )
            if progress_cb is not None:
                progress_cb(label, idx + 1, len(images))

        total_images += len(images)

    if matcher and album_vectors and (ref_db or duplicates):
        detect_duplicates(
            album_vectors,
            matcher,
            root_dir,
            threshold=(
                duplicate_threshold
                if duplicate_threshold is not None
                else DUPLICATE_SIMILARITY_THRESHOLD
            ),
            dry_run=dry_run,
        )

    word = "previewed" if dry_run else "updated"
    summary = f"\nDone. {total_images} photos {word} total."
    if not total_images:
        summary += (
            "\nNo dated albums found: album folders need a DD-MM date "
            "in their name (e.g. '01-05 Kayak trip')."
        )
    if drift_summaries:
        summary += f"\nClock drift detected in {len(drift_summaries)} album(s)."
        if not sync_clocks and not dry_run:
            summary += " Re-run with --sync-clocks to apply the correction."
    print(summary)

    if failures:
        print(f"\n{len(failures)} photo(s) FAILED:")
        for path, err in failures[:20]:
            print(f"  - {path}: {err}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more.")

    if not dry_run and report_rows:
        write_report(report_rows, report_path or os.path.join(root_dir, "exif_resync_report.csv"))

    if timeline:
        if report_path:
            tl_path = os.path.splitext(report_path)[0] + "_timeline.html"
        else:
            tl_path = os.path.join(root_dir, "timeline.html")
        write_timeline(timeline_rows, tl_path, dry_run)

    return 1 if failures else 0


def detect_duplicates(
    album_vectors, matcher, root_dir, threshold=DUPLICATE_SIMILARITY_THRESHOLD, dry_run=False
):
    """Find near-identical photos within each album and across albums.

    Returns the report path, or None when there is nothing to report (or in
    dry-run mode, which never writes files).
    """
    if not 0.0 < threshold <= 1.0:
        sys.exit(f"ERROR: --duplicate-threshold must be within (0, 1], got {threshold!r}.")
    albums = sorted(album_vectors.keys())
    dupes = []
    for album in albums:
        items = album_vectors[album]
        for x in range(len(items)):
            for y in range(x + 1, len(items)):
                sim = matcher.similarity(items[x][1], items[y][1])
                if sim >= threshold:
                    dupes.append((items[x][0], items[y][0], sim))
    for i, album_a in enumerate(albums):
        for album_b in albums[i + 1 :]:
            for path_a, vec_a in album_vectors[album_a]:
                for path_b, vec_b in album_vectors[album_b]:
                    sim = matcher.similarity(vec_a, vec_b)
                    if sim >= threshold:
                        dupes.append((path_a, path_b, sim))
    dupes.sort(key=lambda d: -d[2])
    if not dupes:
        return None

    out = os.path.join(root_dir, "duplicates_report.csv")
    if dry_run:
        print(f"\n{len(dupes)} probable duplicate pair(s) found (not saved in dry-run).")
        return None
    try:
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["path_a", "path_b", "similarity"])
            for a, b, s in dupes:
                writer.writerow([a, b, f"{s:.4f}"])
        print(f"\n{len(dupes)} probable duplicate pair(s) saved to: {out}")
    except OSError as exc:
        print(f"WARNING: could not write duplicates report: {exc}")
        return None
    return out


def write_report(rows, report_path):
    try:
        with open(report_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Change report saved to: {report_path}")
        print("(Keep this file: it can be used with --undo to restore original values.)")
    except OSError as exc:
        print(f"WARNING: could not write change report '{report_path}': {exc}")


SOURCE_LABELS = {
    "filename": "FILE",
    "resnet": "AI",
    "hash": "HASH",
    "plan": "PLAN",
}

BADGE_COLORS = {
    "FILE": "#7bd88f",
    "AI": "#78dce8",
    "HASH": "#ffd866",
    "PLAN": "#ab9df2",
}


def write_timeline(rows, timeline_path, dry_run):
    import html

    days = []
    for row in rows:
        if not days or days[-1][0] != row["folder"]:
            days.append((row["folder"], row["rule"], []))
        days[-1][2].append(row)

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>EXIF Timeline Resync</title>",
        "<style>",
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#1e1f24;"
        "color:#e8e6e3;margin:0;padding:2rem}",
        "h1{font-size:1.4rem} .day{background:#2a2b33;border-radius:10px;"
        "padding:1rem 1.25rem;margin-bottom:1.25rem}",
        ".day h2{font-size:1.05rem;margin:0 0 .15rem} .rule{color:#9a98a0;"
        "font-size:.85rem;margin-bottom:.75rem}",
        "table{border-collapse:collapse;width:100%;font-size:.88rem}",
        "td{padding:.28rem .6rem;border-top:1px solid #3a3b44;font-family:ui-monospace,monospace}",
        ".badge{display:inline-block;border-radius:4px;padding:.05rem .45rem;"
        "font-weight:600;color:#1e1f24;font-family:sans-serif;font-size:.78rem}",
        ".meta{color:#9a98a0}.dry{color:#ffd866}",
        "</style></head><body>",
        f"<h1>EXIF Timeline Resync {'&mdash; preview' if dry_run else '&mdash; result'}</h1>",
    ]

    for folder, rule, items in days:
        parts.append("<div class='day'>")
        parts.append(f"<h2>{html.escape(folder)}</h2>")
        parts.append(
            f"<div class='rule'>start rule: {html.escape(rule)}"
            f"{' &middot; DRY-RUN PREVIEW' if dry_run else ''}</div>"
        )
        parts.append("<table>")
        for item in items:
            badge = SOURCE_LABELS.get(item["source"], item["source"].upper())
            color = BADGE_COLORS.get(badge, "#9a98a0")
            extra = []
            if item["score"]:
                try:
                    extra.append(f"sim {float(item['score']) * 100:.1f}%")
                except (TypeError, ValueError):
                    pass
            if item["drift"]:
                try:
                    extra.append(f"drift {fmt_delta(float(item['drift']))} applied")
                except (TypeError, ValueError):
                    pass
            extra_txt = f"<span class='meta'>{'; '.join(extra)}</span>" if extra else ""
            time_only = item["time"].split(" ", 1)[1] if " " in item["time"] else item["time"]
            parts.append(
                f"<tr><td>{time_only}</td>"
                f"<td>{html.escape(item['file'])}</td>"
                f"<td><span class='badge' style='background:{color}'>{badge}</span></td>"
                f"<td>{extra_txt}</td></tr>"
            )
        parts.append("</table></div>")

    parts.append("</body></html>")

    try:
        with open(timeline_path, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        print(f"Timeline preview written to: {timeline_path}")
    except OSError as exc:
        print(f"WARNING: could not write timeline '{timeline_path}': {exc}")


def check_config(config_path, directory=None, recursive=False):
    config = load_config(config_path)
    year = config["year"] or datetime.now().year
    print(f"Config file : {config_path}")
    print(f"Year        : {config['year'] or '(unset -> ' + str(year) + ')'}")
    print(f"Interval    : {config['default_interval_sec']}s between photos (fallback)")
    print(f"Schedule    : {len(config['schedule'])} day(s)")

    if not config["schedule"]:
        print("  (empty)")
    else:
        width = max(len(k) for k in config["schedule"])
        for day_key in sorted(config["schedule"]):
            print(f"  {day_key.ljust(width)} -> {config['schedule'][day_key]}")

    if directory and os.path.isdir(directory):
        print("\nFolder resolution:")
        matched = 0
        for path_folder in sorted(iter_album_dirs(directory, recursive), key=str.lower):
            folder = os.path.basename(path_folder)
            label = os.path.relpath(path_folder, directory)
            parsed = parse_folder_date(folder)
            if not parsed:
                print(f"  {label}: IGNORED (no date in name)")
                continue
            day_key, _, _, h_blog, m_blog = parsed
            description = read_album_description(path_folder)
            h_start, m_start, step, rule = determine_schedule(
                day_key,
                folder,
                description,
                h_blog,
                m_blog,
                config["schedule"],
                config["default_interval_sec"],
            )
            matched += 1
            print(f"  {label}: {rule} -> start {h_start:02d}:{m_start:02d}, step {step}s")
        if not matched:
            print("  (no dated folder found)")
        else:
            print(f"\n{matched} dated folder(s) found.")


UNDO_REQUIRED_COLUMNS = frozenset(
    {
        "path",
        "old_datetimeoriginal",
        "old_createdate",
        "old_modifydate",
        "old_imagedescription",
    }
)


def undo_from_report(report_path, exiftool_bin=None, dry_run=False, quiet=False):
    if not os.path.exists(report_path):
        sys.exit(f"ERROR: report file '{report_path}' not found.")

    if exiftool_bin is None and not dry_run:
        exiftool_bin = find_exiftool()
        if not exiftool_bin:
            sys.exit("ERROR: ExifTool not found on your system.")

    try:
        with open(report_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                sys.exit(f"ERROR: '{report_path}' is empty or not a CSV report.")
            missing = UNDO_REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                sys.exit(
                    f"ERROR: '{report_path}' is missing column(s): {', '.join(sorted(missing))}."
                )
            rows = list(reader)
    except OSError as exc:
        sys.exit(f"ERROR: cannot read '{report_path}': {exc}")

    if not rows:
        print("Report is empty. Nothing to undo.")
        return 0

    restored = 0
    failures = []

    for row in rows:
        img_path = (row.get("path") or "").strip()
        if not img_path:
            failures.append(("(missing path in report)", "no path recorded"))
            continue
        if dry_run:
            restored += 1
            if not quiet:
                print(f"  Would restore: {img_path}")
            continue
        if not os.path.exists(img_path):
            failures.append((img_path, "file not found"))
            continue

        cmd = [exiftool_bin, "-overwrite_original"]
        for tag, column in (
            ("DateTimeOriginal", "old_datetimeoriginal"),
            ("CreateDate", "old_createdate"),
            ("ModifyDate", "old_modifydate"),
            ("ImageDescription", "old_imagedescription"),
        ):
            value = (row.get(column) or "").strip()
            if value in ("", "-"):
                cmd.append(f"-{tag}=")
            else:
                cmd.append(f"-{tag}={value}")
        cmd.append(img_path)

        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            restored += 1
            if not quiet:
                print(f"  Restored: {img_path}")
        else:
            failures.append((img_path, res.stderr.strip() or "unknown error"))
            print(f"  FAILED: {img_path}")

    word = "would be restored" if dry_run else "restored"
    print(f"\nUndo {'preview' if dry_run else 'complete'}. {restored} photo(s) {word}.")
    if failures:
        print(f"{len(failures)} photo(s) FAILED:")
        for path, err in failures:
            print(f"  - {path}: {err}")
        return 1

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="exif-resync",
        description="Recalibrate EXIF metadata with image matching.",
        epilog=(
            "Examples:\n"
            "  python exif_resync.py --wizard\n"
            "  python exif_resync.py -d ./photos\n"
            "  python exif_resync.py -d ./photos --dry-run --timeline\n"
            "  python exif_resync.py -d ./photos -c my_config.json --year 2025\n"
            "  python exif_resync.py -d ./photos -r ./my_reference_photos\n"
            "  python exif_resync.py -d ./photos --sync-clocks\n"
            "  python exif_resync.py -d ./photos --check-config\n"
            "  python exif_resync.py --undo ./photos/exif_resync_report.csv\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-d",
        "--directory",
        default=".",
        help="Path to the folder containing dated album subfolders.",
    )
    parser.add_argument(
        "-c", "--config", default="config_planning.json", help="Path to JSON config file."
    )
    parser.add_argument(
        "-r", "--reference", default=None, help="Path to folder of dated reference photos."
    )
    parser.add_argument(
        "--matcher",
        choices=["auto", "resnet", "hash", "off"],
        default="auto",
        help="Image matching engine (default: auto).",
    )
    parser.add_argument(
        "--match-threshold",
        type=float,
        default=None,
        help="Override the matcher's acceptance similarity, within "
        "(0, 1] (default: 0.85 resnet, 0.90 hash).",
    )
    parser.add_argument(
        "--duplicates",
        action="store_true",
        help="Detect near-identical photos (within and across albums) "
        "into duplicates_report.csv, even without --reference.",
    )
    parser.add_argument(
        "--duplicate-threshold",
        type=float,
        default=None,
        help="Similarity at or above which two photos count as "
        "duplicates, within (0, 1] (default: 0.95).",
    )
    parser.add_argument(
        "--recursive", action="store_true", help="Scan album and reference folders recursively."
    )
    parser.add_argument(
        "--sync-clocks", action="store_true", help="Apply detected clock drift to album timestamps."
    )
    parser.add_argument(
        "--sync-mtime",
        action="store_true",
        help="Also set the filesystem modification time to the new "
        "timestamp (not restored by --undo).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing any EXIF data (also previews --undo).",
    )
    parser.add_argument(
        "--timeline",
        action="store_true",
        help="Generate an HTML timeline preview (timeline.html, or next to --report when given).",
    )
    parser.add_argument(
        "--year", type=int, default=None, help="Override the year used for all reconstructed dates."
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path of the change report CSV (default: <directory>/exif_resync_report.csv).",
    )
    parser.add_argument(
        "--undo",
        metavar="REPORT_CSV",
        default=None,
        help="Restore original EXIF values from a previous change report.",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate the config and show resolved planning, then exit.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print detailed per-photo information."
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Only print warnings, errors and the final summary."
    )
    parser.add_argument(
        "--wizard",
        action="store_true",
        help="Guided interactive mode: step-by-step prompts with a preview "
        "before anything is written.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    if args.year is not None and not 1000 <= args.year <= 9999:
        parser.error("--year must be a four-digit year.")

    if args.wizard:
        import wizard

        sys.exit(wizard.run_wizard())

    if args.undo:
        sys.exit(undo_from_report(args.undo, dry_run=args.dry_run, quiet=args.quiet))

    if args.check_config:
        check_config(args.config, args.directory, args.recursive)
        sys.exit(0)

    sys.exit(
        process_photos(
            args.directory,
            args.config,
            ref_dir=args.reference,
            dry_run=args.dry_run,
            year_override=args.year,
            report_path=args.report,
            matcher_mode=args.matcher,
            sync_clocks=args.sync_clocks,
            timeline=args.timeline,
            verbose=args.verbose,
            quiet=args.quiet,
            recursive=args.recursive,
            match_threshold=args.match_threshold,
            duplicate_threshold=args.duplicate_threshold,
            duplicates=args.duplicates,
            sync_mtime=args.sync_mtime,
        )
    )


if __name__ == "__main__":
    main()
