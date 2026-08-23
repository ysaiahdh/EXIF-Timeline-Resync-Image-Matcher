#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
EXIF Timeline Resync & Image Matcher
Restores EXIF timestamps from folder names, schedules, and visual matching.
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from PIL import Image

try:
    import torch
    import torchvision.transforms as T
    from torchvision.models import resnet18, ResNet18_Weights
    HAS_VISION = True
except ImportError:
    HAS_VISION = False


MATCH_THRESHOLD = 0.85

REPORT_FIELDS = [
    "path",
    "old_datetimeoriginal",
    "old_createdate",
    "old_modifydate",
    "old_imagedescription",
    "new_datetime",
    "new_imagedescription",
    "match_score",
]

FOLDER_DATE_RE = re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2})h(\d{2}))?(?!\d)")
FOLDER_HOUR_RE = re.compile(r"(?<![\dh])(\d{1,2})h(\d{2})(?!\d)")


# --- Feature Extractor ---

class ImageFeatureExtractor:
    def __init__(self):
        if not HAS_VISION:
            raise RuntimeError("PyTorch and torchvision are required for image recognition.")
        weights = ResNet18_Weights.DEFAULT
        self.model = resnet18(weights=weights)
        self.model.eval()
        self.model = torch.nn.Sequential(*list(self.model.children())[:-1])
        self.transform = T.Compose([
            T.Resize((224, 224)),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def get_vector(self, img_path):
        try:
            img = Image.open(img_path).convert('RGB')
            tensor = self.transform(img).unsqueeze(0)
            with torch.no_grad():
                vector = self.model(tensor).squeeze()
            return vector / torch.norm(vector)
        except Exception:
            return None


def cosine_similarity(v1, v2):
    if v1 is None or v2 is None:
        return 0.0
    return float(torch.dot(v1, v2))


# --- Reference Gallery ---

def load_reference_gallery(ref_dir, extractor, exiftool_bin):
    reference_db = []
    print("\nIndexing reference gallery...")

    images_ref = [os.path.join(ref_dir, f) for f in os.listdir(ref_dir)
                  if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

    for img_path in images_ref:
        cmd = [exiftool_bin, "-DateTimeOriginal", "-s3", img_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        date_str = res.stdout.strip()

        if date_str:
            try:
                dt_ref = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                vector = extractor.get_vector(img_path)
                if vector is not None:
                    reference_db.append({
                        "path": img_path,
                        "datetime": dt_ref,
                        "vector": vector
                    })
            except ValueError:
                continue

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
        with open(config_path, "r", encoding="utf-8") as f:
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
        sys.exit("ERROR: 'schedule' must be an object mapping \"DD-MM\" to \"HH:MM\".")

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


def determine_schedule(day_key, folder_name, description, h_blog, m_blog,
                       schedule, default_interval):
    folder_lower = folder_name.lower()
    desc_lower = description.lower()

    if "boom" in folder_lower or "soiree" in desc_lower or "veillee" in desc_lower:
        return 20, 30, 90
    if "bis" in folder_lower or "animaux" in folder_lower or "apres-midi" in folder_lower:
        return 15, 0, 120
    if day_key in schedule:
        h_str, m_str = schedule[day_key].split(":")
        return int(h_str), int(m_str), 210
    return h_blog, m_blog, default_interval


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
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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


def process_photos(root_dir, config_path, ref_dir=None, dry_run=False,
                   year_override=None, report_path=None):
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

    extractor = None
    ref_db = []
    if ref_dir:
        if not HAS_VISION:
            print("PyTorch/Torchvision not found. Running without image matching.")
            print("To enable AI matching: pip install -r requirements.txt")
        else:
            exiftool_for_refs = exiftool_bin or find_exiftool()
            if not exiftool_for_refs:
                sys.exit("ERROR: ExifTool is required to read dates from your reference "
                         "photos (--reference). Install it or drop --dry-run.")
            extractor = ImageFeatureExtractor()
            ref_db = load_reference_gallery(ref_dir, extractor, exiftool_for_refs)

    if not os.path.isdir(root_dir):
        sys.exit(f"ERROR: directory '{root_dir}' does not exist.")

    folders = [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]
    folders.sort(key=str.lower)

    total_images = 0
    failures = []
    report_rows = []

    for folder in folders:
        path_folder = os.path.join(root_dir, folder)
        parsed = parse_folder_date(folder)

        if not parsed:
            continue

        day_key, day, month, h_blog, m_blog = parsed

        description = ""
        txt_files = [f for f in os.listdir(path_folder) if f.lower().endswith(".txt")]
        if txt_files:
            try:
                with open(os.path.join(path_folder, txt_files[0]), "r",
                          encoding="utf-8", errors="ignore") as f:
                    description = f.read().strip()
            except Exception:
                pass

        h_start, m_start, step = determine_schedule(day_key, folder, description,
                                                    h_blog, m_blog, schedule,
                                                    default_interval)
        try:
            base_dt = datetime(year, month, day, h_start, m_start)
        except ValueError:
            print(f"\nFolder: {folder}\n  Skipped: invalid date ({day}/{month}/{year}).")
            continue

        images = [f for f in os.listdir(path_folder)
                  if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        images.sort(key=str.lower)

        if not images:
            continue

        print(f"\nFolder: {folder}")

        current_dt = base_dt
        for img in images:
            img_path = os.path.join(path_folder, img)
            target_dt = current_dt
            match_score = ""

            if extractor and ref_db:
                vector_img = extractor.get_vector(img_path)
                if vector_img is not None:
                    best_match = None
                    best_score = 0.0

                    for ref in ref_db:
                        sim = cosine_similarity(vector_img, ref["vector"])
                        if sim > best_score:
                            best_score = sim
                            best_match = ref

                    if best_score >= MATCH_THRESHOLD and best_match:
                        if abs((best_match["datetime"].date() - base_dt.date()).days) <= 1:
                            target_dt = best_match["datetime"]
                            match_score = f"{best_score:.4f}"
                            print(f"  Visual match ({best_score*100:.1f}%) "
                                  f"-> {target_dt.strftime('%H:%M:%S')}")

            date_str = target_dt.strftime("%Y:%m:%d %H:%M:%S")

            if dry_run:
                print(f"  [DRY-RUN] {img}: {date_str}")
            else:
                old_tags = read_current_tags(exiftool_bin, img_path)
                ok, err = apply_tags(exiftool_bin, img_path, date_str, description)
                if ok:
                    report_rows.append({
                        "path": os.path.abspath(img_path),
                        "old_datetimeoriginal": old_tags[0] if old_tags else "",
                        "old_createdate": old_tags[1] if old_tags else "",
                        "old_modifydate": old_tags[2] if old_tags else "",
                        "old_imagedescription": old_tags[3] if old_tags else "",
                        "new_datetime": date_str,
                        "new_imagedescription": description,
                        "match_score": match_score,
                    })
                else:
                    failures.append((img_path, err or "unknown error"))

            current_dt += timedelta(seconds=step)

        total_images += len(images)
        print(f"  {len(images)} photos processed.")

    summary = f"\nDone. {total_images} photos {'previewed' if dry_run else 'updated'} total."

    if failures:
        summary += f"\n{len(failures)} photo(s) FAILED:"
        for path, err in failures[:20]:
            summary += f"\n  - {path}: {err}"
        if len(failures) > 20:
            summary += f"\n  ... and {len(failures) - 20} more."
        print(summary)

        if report_rows and not dry_run:
            write_report(report_rows, report_path or os.path.join(root_dir, "exif_resync_report.csv"))

        return 1

    print(summary)

    if not dry_run and report_rows:
        final_report = report_path or os.path.join(root_dir, "exif_resync_report.csv")
        write_report(report_rows, final_report)

    return 0


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


def undo_from_report(report_path, exiftool_bin=None):
    if exiftool_bin is None:
        exiftool_bin = find_exiftool()
        if not exiftool_bin:
            sys.exit("ERROR: ExifTool not found on your system.")

    if not os.path.exists(report_path):
        sys.exit(f"ERROR: report file '{report_path}' not found.")

    try:
        with open(report_path, "r", newline="", encoding="utf-8") as f:
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
        else:
            failures.append((img_path, res.stderr.strip() or "unknown error"))

        print(f"  Restored: {img_path}" if res.returncode == 0 else f"  FAILED: {img_path}")

    print(f"\nUndo complete. {restored} photo(s) restored.")
    if failures:
        print(f"{len(failures)} photo(s) FAILED:")
        for path, err in failures:
            print(f"  - {path}: {err}")
        return 1

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="exif_resync",
        description="Recalibrate EXIF metadata with image matching.",
        epilog=(
            "Examples:\n"
            "  python exif_resync.py -d ./photos\n"
            "  python exif_resync.py -d ./photos --dry-run\n"
            "  python exif_resync.py -d ./photos -c my_config.json --year 2025\n"
            "  python exif_resync.py -d ./photos -r ./my_reference_photos\n"
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
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing any EXIF data.")
    parser.add_argument("--year", type=int, default=None,
                        help="Override the year used for all reconstructed dates.")
    parser.add_argument("--report", default=None,
                        help="Path of the change report CSV (default: "
                             "<directory>/exif_resync_report.csv).")
    parser.add_argument("--undo", metavar="REPORT_CSV", default=None,
                        help="Restore original EXIF values from a previous change report.")
    args = parser.parse_args()

    if args.undo:
        sys.exit(undo_from_report(args.undo))
    else:
        sys.exit(process_photos(args.directory, args.config, args.reference,
                                dry_run=args.dry_run, year_override=args.year,
                                report_path=args.report))
