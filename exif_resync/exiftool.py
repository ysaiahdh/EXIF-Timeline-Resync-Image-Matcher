# SPDX-License-Identifier: MIT
"""All ExifTool interaction: discovery, batched reads, and tag writes."""

import json
import os
import shutil
import subprocess

EXIF_TAGS = ("DateTimeOriginal", "CreateDate", "ModifyDate", "ImageDescription")


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
