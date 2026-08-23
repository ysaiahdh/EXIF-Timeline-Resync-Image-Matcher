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
from datetime import datetime, timedelta

__version__ = "2.0.0"

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
FILENAME_DT_RE = re.compile(
    r"(?<!\d)(\d{4})[-_.T ]?(\d{2})[-_.T ]?(\d{2})[-_.T ]?"
    r"(\d{2})[.:T]?(\d{2})(?:[.:T]?(\d{2}))?(?!\d)"
)


# --- Matchers ---

class ResNetMatcher:
    name = "resnet"
    threshold = RESNET_SIMILARITY_THRESHOLD

    def __init__(self):
        if not HAS_VISION or not HAS_PILLOW:
            raise RuntimeError("PyTorch, torchvision and Pillow are required.")
        if HAS_NUMPY and torch.cuda.is_available():
            self.device = torch.device("cuda")
        elif HAS_NUMPY and getattr(torch.backends, "mps", None) \
                and torch.backends.mps.is_available():
            self.device = torch.device("mps")
        else:
            self.device = torch.device("cpu")
        weights = ResNet18_Weights.DEFAULT
        model = resnet18(weights=weights)
        model.eval()
        self.model = torch.nn.Sequential(*list(model.children())[:-1]).to(self.device)
        self.transform = T.Compose([
            T.Resize((224, 224)),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def get_vectors(self, paths):
        results = []
        for i in range(0, len(paths), RESNET_BATCH_SIZE):
            chunk = paths[i:i + RESNET_BATCH_SIZE]
            tensors = []
            for p in chunk:
                try:
                    tensors.append(self.transform(Image.open(p).convert("RGB")))
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
        for p in paths:
            try:
                results.append(imagehash.phash(Image.open(p)))
            except Exception:
                results.append(None)
        return results

    def similarity(self, a, b):
        if a is None or b is None:
            return 0.0
        return 1.0 - (a - b) / float(a.hash.size)


def build_matcher(mode):
    if mode == "off":
        return None

    if mode == "resnet":
        if not HAS_VISION or not HAS_PILLOW:
            sys.exit("ERROR: --matcher resnet requires PyTorch, torchvision and Pillow "
                     "(pip install -r requirements.txt).")
        return ResNetMatcher()

    if mode == "hash":
        if not HAS_PILLOW or not HAS_IMAGEHASH:
            sys.exit("ERROR: --matcher hash requires Pillow and ImageHash "
                     "(pip install Pillow ImageHash).")
        return HashMatcher()

    if HAS_VISION and HAS_PILLOW:
        return ResNetMatcher()
    if HAS_PILLOW and HAS_IMAGEHASH:
        print("PyTorch not installed: falling back to lightweight perceptual-hash matching.")
        return HashMatcher()
    print("No matcher available: install torch (AI matching) or "
          "Pillow+ImageHash (lightweight matching).")
    return None


# --- Vector cache (ResNet only) ---

def cache_load(ref_dir):
    path = os.path.join(ref_dir, VECTOR_CACHE_NAME)
    if not HAS_NUMPY or not os.path.exists(path):
        return {}
    try:
        data = np.load(path, allow_pickle=True)
        return {str(k): {"mtime": float(m), "vector": v}
                for k, m, v in zip(data["paths"], data["mtimes"], data["vectors"])}
    except Exception:
        return {}


def cache_save(ref_dir, cache):
    if not HAS_NUMPY:
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


def load_reference_gallery(ref_dir, matcher, exiftool_bin):
    reference_db = []
    print(f"\nIndexing reference gallery ({matcher.name})...")

    images_ref = sorted(
        (os.path.join(ref_dir, f) for f in os.listdir(ref_dir)
         if f.lower().endswith(('.jpg', '.jpeg', '.png'))),
        key=str.lower,
    )

    dated = {}
    for img_path in images_ref:
        cmd = [exiftool_bin, "-DateTimeOriginal", "-s3", img_path]
        res = subprocess.run(cmd, capture_output=True, text=True)
        date_str = res.stdout.strip()
        if not date_str:
            continue
        try:
            dated[img_path] = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue

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

    year = data.get("year", data.get("annee"))
    interval = data.get("default_interval_sec", data.get("intervalle_defaut_sec", 180))
    schedule = data.get("schedule", data.get("planning", {}))

    if not isinstance(interval, (int, float)) or interval < 0:
        sys.exit(f"ERROR: 'default_interval_sec' must be a positive number, got {interval!r}.")
    if not isinstance(schedule, dict):
        sys.exit('ERROR: \'schedule\' must be an object mapping "DD-MM" to "HH:MM".')

    return {
        "year": int(year) if year else None,
        "default_interval_sec": int(interval),
        "schedule": schedule,
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
        return datetime(int(year), int(month), int(day),
                        int(hour), int(minute), int(second or 0))
    except ValueError:
        return None


def determine_schedule(day_key, folder_name, description, h_blog, m_blog,
                       schedule, default_interval):
    folder_lower = folder_name.lower()
    desc_lower = description.lower()

    if "boom" in folder_lower or "soiree" in desc_lower or "veillee" in desc_lower:
        return 20, 30, 90, "keyword (evening)"
    if "bis" in folder_lower or "animaux" in folder_lower or "apres-midi" in folder_lower:
        return 15, 0, 120, "keyword (afternoon)"
    if day_key in schedule:
        h_str, m_str = schedule[day_key].split(":")
        return int(h_str), int(m_str), 210, "schedule"
    if h_blog != 12 or m_blog != 0:
        return h_blog, m_blog, default_interval, "folder time"
    return h_blog, m_blog, default_interval, "default"


def read_current_tags(exiftool_bin, img_path):
    cmd = [
        exiftool_bin,
        "-DateTimeOriginal",
        "-CreateDate",
        "-ModifyDate",
        "-ImageDescription",
        "-s3",
        "-f",
        img_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return None

    values = res.stdout.split("\n")
    while len(values) < 4:
        values.append("")
    return values[:4]


def apply_tags(exiftool_bin, img_path, date_str, description):
    cmd = [
        exiftool_bin,
        f"-AllDates={date_str}",
        f"-ImageDescription={description}",
        "-overwrite_original",
        img_path,
    ]
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


def process_photos(root_dir, config_path, ref_dir=None, dry_run=False,
                   year_override=None, report_path=None, matcher_mode="auto",
                   sync_clocks=False, timeline=False, verbose=False):
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

    config = load_config(config_path)

    year = year_override or config["year"]
    if year is None:
        year = datetime.now().year
        print(f"WARNING: no year configured. Defaulting to {year} "
              f"(use the 'year' key in {config_path} or --year to change this).")

    default_interval = config["default_interval_sec"]
    schedule = config["schedule"]

    if not os.path.isdir(root_dir):
        sys.exit(f"ERROR: directory '{root_dir}' does not exist.")

    matcher = None
    ref_db = []
    if ref_dir:
        tool_for_refs = exiftool_bin or find_exiftool()
        if not tool_for_refs:
            sys.exit("ERROR: ExifTool is required to read dates from your reference "
                     "photos (--reference). Install it or drop --dry-run.")
        matcher = build_matcher(matcher_mode)
        if matcher is not None:
            ref_db = load_reference_gallery(ref_dir, matcher, tool_for_refs)

    folders = [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]
    folders.sort(key=str.lower)

    total_images = 0
    failures = []
    report_rows = []
    timeline_rows = []
    album_vectors = {}
    drift_summaries = []

    for folder in folders:
        path_folder = os.path.join(root_dir, folder)
        parsed = parse_folder_date(folder)

        if not parsed:
            if verbose:
                print(f"\nFolder: {folder}\n  Skipped: no DD-MM date in name.")
            continue

        day_key, day, month, h_blog, m_blog = parsed

        description = ""
        txt_files = sorted(
            (f for f in os.listdir(path_folder) if f.lower().endswith(".txt")),
            key=str.lower,
        )
        if txt_files:
            try:
                with open(os.path.join(path_folder, txt_files[0]),
                          encoding="utf-8", errors="ignore") as f:
                    description = f.read().strip()
            except Exception:
                pass

        h_start, m_start, step, rule = determine_schedule(
            day_key, folder, description, h_blog, m_blog, schedule, default_interval)
        try:
            base_dt = datetime(year, month, day, h_start, m_start)
        except ValueError:
            print(f"\nFolder: {folder}\n  Skipped: invalid date ({day}/{month}/{year}).")
            continue

        images = sorted(
            (f for f in os.listdir(path_folder)
             if f.lower().endswith(('.jpg', '.jpeg', '.png'))),
            key=str.lower,
        )
        if not images:
            continue

        print(f"\nFolder: {folder}  [{rule}, step {step}s]{'  (DRY-RUN)' if dry_run else ''}")

        planned = [base_dt + timedelta(seconds=i * step) for i in range(len(images))]
        img_paths = [os.path.join(path_folder, img) for img in images]

        img_vectors = []
        if matcher and ref_db:
            img_vectors = matcher.get_vectors(img_paths)
            for img, vec in zip(images, img_vectors):
                if vec is not None:
                    album_vectors.setdefault(folder, []).append(
                        (os.path.abspath(os.path.join(path_folder, img)), vec))

        old_tags_list = []
        if not dry_run:
            old_tags_list = [read_current_tags(exiftool_bin, p) for p in img_paths]

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
                note = ("" if (sync_clocks or dry_run)
                        else "  (re-run with --sync-clocks to correct)")
                msg = (f"  Clock drift detected: median {fmt_delta(drift_offset)} "
                       f"across {len(offsets)} dated photo(s){note}")
                print(msg)
                drift_summaries.append((folder, drift_offset, len(offsets)))

        current_idx = 0
        for img, img_path, planned_dt in zip(images, img_paths, planned):
            target_dt = planned_dt
            source = "plan"
            score = ""

            fname_dt = parse_filename_date(img)
            if fname_dt is not None:
                target_dt = fname_dt.replace(year=fname_dt.year)
                source = "filename"
                if verbose:
                    print(f"  {img}: timestamp taken from filename "
                          f"({fname_dt.strftime('%Y-%m-%d %H:%M:%S')})")
            elif matcher and ref_db and current_idx < len(img_vectors):
                vec = img_vectors[current_idx]
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
                            if verbose:
                                print(f"  {img}: visual match ({best_score*100:.1f}%) -> "
                                      f"{target_dt.strftime('%H:%M:%S')}")

            applied_drift = ""
            if drift_detected and sync_clocks and source != "filename":
                target_dt = target_dt + timedelta(seconds=round(drift_offset))
                applied_drift = str(int(round(drift_offset)))

            date_str = target_dt.strftime("%Y:%m:%d %H:%M:%S")

            if dry_run:
                tag_info = ""
                if verbose and (not old_tags_list
                                or old_tags_list[current_idx] is None):
                    tag_info = "  [no previous EXIF]"
                elif verbose:
                    tag_info = ""
                else:
                    tag_info = ""
                print(f"  [{source:>8}] {img}: {date_str}{tag_info}")
            else:
                old_tags = old_tags_list[current_idx]
                ok, err = apply_tags(exiftool_bin, img_path, date_str, description)
                row = {
                    "path": os.path.abspath(img_path),
                    "old_datetimeoriginal": old_tags[0] if old_tags else "",
                    "old_createdate": old_tags[1] if old_tags else "",
                    "old_modifydate": old_tags[2] if old_tags else "",
                    "old_imagedescription": old_tags[3] if old_tags else "",
                    "new_datetime": date_str,
                    "new_imagedescription": description,
                    "match_score": score,
                    "source": source,
                    "drift_applied_sec": applied_drift,
                }
                if ok:
                    report_rows.append(row)
                else:
                    failures.append((img_path, err or "unknown error"))

            timeline_rows.append({
                "folder": folder,
                "rule": rule,
                "file": img,
                "time": date_str,
                "source": source,
                "score": score,
                "drift": applied_drift,
            })
            current_idx += 1

        total_images += len(images)

    if matcher and ref_db:
        detect_duplicates(album_vectors, matcher, root_dir)

    word = "previewed" if dry_run else "updated"
    summary = f"\nDone. {total_images} photos {word} total."
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


def detect_duplicates(album_vectors, matcher, root_dir):
    albums = sorted(album_vectors.keys())
    dupes = []
    for i, album_a in enumerate(albums):
        for album_b in albums[i + 1:]:
            for path_a, vec_a in album_vectors[album_a]:
                for path_b, vec_b in album_vectors[album_b]:
                    sim = matcher.similarity(vec_a, vec_b)
                    if sim >= DUPLICATE_SIMILARITY_THRESHOLD:
                        dupes.append((path_a, path_b, sim))
    if not dupes:
        return None

    out = os.path.join(root_dir, "duplicates_report.csv")
    try:
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["path_a", "path_b", "similarity"])
            for a, b, s in dupes:
                writer.writerow([a, b, f"{s:.4f}"])
        print(f"\n{len(dupes)} probable duplicate pair(s) saved to: {out}")
    except OSError as exc:
        print(f"WARNING: could not write duplicates report: {exc}")
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
        parts.append(f"<div class='rule'>start rule: {html.escape(rule)}"
                     f"{' &middot; DRY-RUN PREVIEW' if dry_run else ''}</div>")
        parts.append("<table>")
        for item in items:
            badge = SOURCE_LABELS.get(item["source"], item["source"].upper())
            color = BADGE_COLORS.get(badge, "#9a98a0")
            extra = []
            if item["score"]:
                extra.append(f"sim {float(item['score'])*100:.1f}%")
            if item["drift"]:
                extra.append(f"drift {fmt_delta(float(item['drift']))} applied")
            extra_txt = f"<span class='meta'>{'; '.join(extra)}</span>" if extra else ""
            parts.append(
                f"<tr><td>{item['time'][11:]}</td>"
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


def check_config(config_path, directory=None):
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
        folders = sorted((d for d in os.listdir(directory)
                          if os.path.isdir(os.path.join(directory, d))), key=str.lower)
        matched = 0
        for folder in folders:
            parsed = parse_folder_date(folder)
            if not parsed:
                print(f"  {folder}: IGNORED (no date in name)")
                continue
            day_key, _, _, h_blog, m_blog = parsed
            description = ""
            txt_files = [f for f in os.listdir(os.path.join(directory, folder))
                         if f.lower().endswith(".txt")]
            if txt_files:
                try:
                    path_txt = os.path.join(directory, folder, txt_files[0])
                    with open(path_txt, encoding="utf-8", errors="ignore") as f:
                        description = f.read(20000).strip()
                except Exception:
                    pass
            h_start, m_start, step, rule = determine_schedule(
                day_key, folder, description, h_blog, m_blog,
                config["schedule"], config["default_interval_sec"])
            matched += 1
            print(f"  {folder}: {rule} -> start {h_start:02d}:{m_start:02d}, step {step}s")
        if not matched:
            print("  (no dated folder found)")
        else:
            print(f"\n{matched} dated folder(s) found.")


def undo_from_report(report_path, exiftool_bin=None):
    if exiftool_bin is None:
        exiftool_bin = find_exiftool()
        if not exiftool_bin:
            sys.exit("ERROR: ExifTool not found on your system.")

    if not os.path.exists(report_path):
        sys.exit(f"ERROR: report file '{report_path}' not found.")

    try:
        with open(report_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError as exc:
        sys.exit(f"ERROR: cannot read '{report_path}': {exc}")

    if not rows:
        print("Report is empty. Nothing to undo.")
        return 0

    restored = 0
    failures = []

    for row in rows:
        img_path = row["path"]
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
            print(f"  Restored: {img_path}")
        else:
            failures.append((img_path, res.stderr.strip() or "unknown error"))
            print(f"  FAILED: {img_path}")

    print(f"\nUndo complete. {restored} photo(s) restored.")
    if failures:
        print(f"{len(failures)} photo(s) FAILED:")
        for path, err in failures:
            print(f"  - {path}: {err}")
        return 1

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="exif_resync",
        description="Recalibrate EXIF metadata with image matching.",
        epilog=(
            "Examples:\n"
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
    parser.add_argument("-d", "--directory", default=".",
                        help="Path to folder containing undated photos.")
    parser.add_argument("-c", "--config", default="config_planning.json",
                        help="Path to JSON config file.")
    parser.add_argument("-r", "--reference", default=None,
                        help="Path to folder of dated reference photos.")
    parser.add_argument("--matcher", choices=["auto", "resnet", "hash", "off"],
                        default="auto",
                        help="Image matching engine (default: auto).")
    parser.add_argument("--sync-clocks", action="store_true",
                        help="Apply detected clock drift to album timestamps.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing any EXIF data.")
    parser.add_argument("--timeline", action="store_true",
                        help="Generate an HTML timeline preview (timeline.html).")
    parser.add_argument("--year", type=int, default=None,
                        help="Override the year used for all reconstructed dates.")
    parser.add_argument("--report", default=None,
                        help="Path of the change report CSV (default: "
                             "<directory>/exif_resync_report.csv).")
    parser.add_argument("--undo", metavar="REPORT_CSV", default=None,
                        help="Restore original EXIF values from a previous change report.")
    parser.add_argument("--check-config", action="store_true",
                        help="Validate the config and show resolved planning, then exit.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print detailed per-photo information.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    if args.undo:
        sys.exit(undo_from_report(args.undo))

    if args.check_config:
        check_config(args.config, args.directory if args.directory != "." else None)
        sys.exit(0)

    sys.exit(process_photos(
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
    ))


if __name__ == "__main__":
    main()
