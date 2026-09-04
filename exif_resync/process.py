# SPDX-License-Identifier: MIT
"""Main pipeline: per-album scheduling, matching, drift, reports, timeline."""

import csv
import os
import statistics
import sys
from datetime import datetime, timedelta

from .config import load_config
from .exiftool import apply_tags, find_exiftool, read_current_tags_batch
from .matchers import (
    DUPLICATE_SIMILARITY_THRESHOLD,
    build_matcher,
    load_reference_gallery,
)
from .parsing import (
    IMAGE_EXTENSIONS,
    determine_schedule,
    iter_album_dirs,
    parse_filename_date,
    parse_folder_date,
    read_album_description,
)
from .util import fmt_delta

DRIFT_MIN_SAMPLES = 2
DRIFT_REPORT_THRESHOLD_SEC = 60

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
