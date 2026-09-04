# SPDX-License-Identifier: MIT
"""Restore original EXIF values from a change report (--undo)."""

import csv
import os
import subprocess
import sys

from .exiftool import find_exiftool

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
