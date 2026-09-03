#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""Guided interactive CLI for EXIF Timeline Resync.

Step-by-step prompts (directory, year, schedule, reference photos, preview,
confirm) with colored output and a progress bar. Standard library only, so
`python exif_resync.py --wizard` works anywhere the base tool does.
"""

import json
import os
import sys
from datetime import datetime

import exif_resync as core

_STYLE_CODES = {
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "bold": "1",
    "dim": "2",
}


def supports_color(stream=None):
    """True when ANSI colors may be used (tty, no NO_COLOR, sane TERM)."""
    stream = stream if stream is not None else sys.stdout
    return (
        hasattr(stream, "isatty")
        and stream.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )


def paint(text, *styles):
    """Wrap `text` in ANSI codes unless colors are unsupported."""
    if not styles or not supports_color():
        return text
    codes = ";".join(_STYLE_CODES[s] for s in styles)
    return f"\033[{codes}m{text}\033[0m"


def format_table(headers, rows):
    """Render a plain-text table (kept color-free for easy copy-paste)."""
    widths = [len(h) for h in headers]
    str_rows = [[str(c) for c in row] for row in rows]
    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    lines.append("  ".join("-" * w for w in widths))
    lines.extend("  ".join(cell.ljust(w) for cell, w in zip(row, widths)) for row in str_rows)
    return "\n".join(lines)


def make_progress_bar(total):
    """Build a (callback, close) stderr progress pair for core.process_photos.

    The callback matches the progress_cb protocol; output is skipped when
    stderr is not a tty so logs and pipes stay clean.
    """
    state = {"done": 0}
    enabled = total > 0 and hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

    def callback(label, _done_in_album, _album_total):
        state["done"] += 1
        if not enabled:
            return
        width = 24
        frac = min(1.0, state["done"] / total)
        bar = ("#" * int(width * frac)).ljust(width, "-")
        sys.stderr.write(f"\r[{bar}] {state['done']}/{total} {label[:40]}")
        sys.stderr.flush()

    def close():
        if enabled:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return callback, close


def ask(input_func, prompt, default=None, validator=None, hint="Please try again."):
    """Prompt until valid input. `validator` converts or returns None.

    A string default is converted through `validator` too, so callers always
    get back the converted type (stays as-is when conversion fails).
    """
    shown = default
    if isinstance(default, str) and validator is not None:
        converted = validator(default)
        if converted is not None:
            default = converted
    while True:
        suffix = f" [{shown}]" if shown is not None else ""
        raw = input_func(f"{prompt}{suffix}: ").strip()
        if not raw:
            if default is not None:
                return default
            print("A value is required.")
            continue
        if validator is None:
            return raw
        value = validator(raw)
        if value is None:
            print(hint)
            continue
        return value


def ask_yes_no(input_func, prompt, default=True):
    """Prompt for y/n (also accepts oui/non)."""
    options = "Y/n" if default else "y/N"
    while True:
        raw = input_func(f"{prompt} [{options}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "o", "oui"):
            return True
        if raw in ("n", "no", "non"):
            return False
        print("Please answer y or n.")


def ask_choice(input_func, prompt, options, default=None):
    """Prompt to pick one key from [(key, label), ...]."""
    print(prompt)
    for key, label in options:
        print(f"  {key}) {label}")
    valid = [k for k, _ in options]
    while True:
        raw = input_func(f"Choice [{default}]: ").strip()
        if not raw and default is not None:
            return default
        if raw in valid:
            return raw
        print(f"Choose one of: {', '.join(valid)}.")


def valid_dir(raw):
    path = os.path.abspath(os.path.expanduser(raw))
    return path if os.path.isdir(path) else None


def valid_year(raw):
    try:
        year = int(raw.strip())
    except (ValueError, TypeError):
        return None
    return year if 1000 <= year <= 9999 else None


def valid_positive_int(raw):
    try:
        value = int(raw.strip())
    except (ValueError, TypeError):
        return None
    return value if value > 0 else None


def matcher_options():
    """Available (key, label) matcher choices given installed packages."""
    options = [("auto", "Automatic (recommended)")]
    if core.HAS_VISION and core.HAS_PILLOW:
        options.append(("resnet", "ResNet AI matching (best quality)"))
    if core.HAS_PILLOW and core.HAS_IMAGEHASH:
        options.append(("hash", "Lightweight perceptual hash"))
    options.append(("off", "No image matching (schedule only)"))
    return options


def print_albums_table(albums):
    rows = [
        [a["label"], a["day_key"] or "-", str(a["photos"]), a["start"], str(a["step"]), a["rule"]]
        for a in albums
    ]
    print(format_table(["Album", "Day", "Photos", "Start", "Step(s)", "Rule"], rows))


def build_config_interactively(input_func, config_path, root_dir, recursive):
    """Create a config file by asking year, interval and per-day start times."""
    year = ask(
        input_func,
        "Year for all reconstructed dates",
        default=str(datetime.now().year),
        validator=valid_year,
        hint="Enter a four-digit year.",
    )
    interval = ask(
        input_func,
        "Seconds between photos when nothing else matches",
        default="180",
        validator=valid_positive_int,
        hint="Enter a positive number of seconds.",
    )
    proposals = core.summarize_albums(root_dir, year, {}, interval, recursive)
    schedule = {}
    seen = set()
    for album in proposals:
        if not album["dated"] or album["day_key"] in seen:
            continue
        seen.add(album["day_key"])
        answer = ask(
            input_func,
            f"Start time for {album['day_key']} ({album['label']})",
            default=album["start"],
            validator=core.parse_time_value,
            hint='Enter a time like "09:30".',
        )
        schedule[album["day_key"]] = f"{answer[0]:02d}:{answer[1]:02d}"
    data = {"year": year, "default_interval_sec": interval, "schedule": schedule}
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(paint(f"Config written to {config_path}", "green"))
    return core.load_config(config_path)


def load_or_build_config(input_func, root_dir, recursive):
    """Return (config, config_path, fresh), creating the file when needed.

    `fresh` is True when the config was just created. A None config means
    the user cancelled (e.g. refused to overwrite).
    """
    config_path = ask(input_func, "Config file", default="config_planning.json")
    config_path = os.path.abspath(os.path.expanduser(config_path))
    if os.path.exists(config_path):
        try:
            return core.load_config(config_path), config_path, False
        except SystemExit as exc:
            print(paint(f"That config is invalid: {exc.code}", "red"))
            if not ask_yes_no(input_func, "Create a new one instead", default=True):
                return None, config_path, False
    else:
        print(f"No config at {config_path}. Let's create one.")
    if os.path.exists(config_path) and not ask_yes_no(
        input_func, f"Overwrite {config_path}", default=False
    ):
        return None, config_path, False
    config = build_config_interactively(input_func, config_path, root_dir, recursive)
    return config, config_path, True


def guided_resync(input_func):
    """Full guided flow: setup, preview, confirm, apply. Returns exit code."""
    print(paint("\n-- Step 1/5: photos --", "cyan", "bold"))
    root_dir = ask(
        input_func,
        "Folder containing the dated albums",
        default=".",
        validator=valid_dir,
        hint="That folder does not exist.",
    )
    recursive = ask_yes_no(input_func, "Scan subfolders recursively", default=False)

    print(paint("\n-- Step 2/5: schedule --", "cyan", "bold"))
    config, _config_path, fresh = load_or_build_config(input_func, root_dir, recursive)
    if config is None:
        print("Cancelled.")
        return 1
    if fresh:
        year = config["year"]
    else:
        year = ask(
            input_func,
            "Year for all reconstructed dates",
            default=str(config["year"] or datetime.now().year),
            validator=valid_year,
            hint="Enter a four-digit year.",
        )

    albums = core.summarize_albums(
        root_dir, year, config["schedule"], config["default_interval_sec"], recursive
    )
    dated = [a for a in albums if a["dated"]]
    if not dated:
        print(
            paint(
                "No dated albums found: folder names need a DD-MM date (e.g. '01-05 Kayak trip').",
                "red",
            )
        )
        return 1
    print()
    print_albums_table(albums)
    skipped = [a for a in albums if not a["dated"]]
    if skipped:
        print(
            paint(
                f"{len(skipped)} folder(s) will be skipped "
                f"({', '.join(a['label'] for a in skipped)}).",
                "yellow",
            )
        )

    print(paint("\n-- Step 3/5: reference photos (optional) --", "cyan", "bold"))
    ref_dir = None
    matcher_mode = "off"
    if ask_yes_no(input_func, "Match against your own dated photos", default=False):
        ref_dir = ask(
            input_func, "Reference folder", validator=valid_dir, hint="That folder does not exist."
        )
        matcher_mode = ask_choice(input_func, "Matching engine:", matcher_options(), default="auto")
    duplicates = False
    if matcher_mode == "off" and ask_yes_no(
        input_func, "Still detect near-identical photos (duplicates report)", default=False
    ):
        duplicates = True
        matcher_mode = ask_choice(
            input_func, "Engine for duplicate detection:", matcher_options(), default="auto"
        )
        if matcher_mode == "off":
            duplicates = False

    print(paint("\n-- Step 4/5: preview --", "cyan", "bold"))
    sync_clocks = ask_yes_no(
        input_func, "Apply camera clock-drift correction when detected", default=False
    )
    sync_mtime = ask_yes_no(input_func, "Also update filesystem modification times", default=False)
    timeline = ask_yes_no(input_func, "Generate an HTML timeline", default=True)
    total = sum(a["photos"] for a in dated)
    run_args = dict(
        ref_dir=ref_dir,
        year_override=year,
        matcher_mode=matcher_mode,
        sync_clocks=sync_clocks,
        sync_mtime=sync_mtime,
        timeline=timeline,
        duplicates=duplicates,
        recursive=recursive,
        quiet=True,
    )
    print("Previewing (dry-run, nothing is written)...")
    callback, close_bar = make_progress_bar(total)
    try:
        rc = core.process_photos(
            root_dir, _config_path, dry_run=True, progress_cb=callback, **run_args
        )
    finally:
        close_bar()
    if rc != 0:
        print(paint("Preview reported failures; aborting.", "red"))
        return rc
    print(paint(f"Preview OK: {total} photo(s) across {len(dated)} album(s).", "green"))

    print(paint("\n-- Step 5/5: apply --", "cyan", "bold"))
    if not ask_yes_no(input_func, "Write these timestamps into your photos", default=False):
        print("Nothing was written. Re-run the wizard any time.")
        return 0
    callback, close_bar = make_progress_bar(total)
    try:
        rc = core.process_photos(root_dir, _config_path, progress_cb=callback, **run_args)
    finally:
        close_bar()
    if rc != 0:
        print(paint("Finished with failures (see above).", "yellow"))
        return rc
    report_path = os.path.join(root_dir, "exif_resync_report.csv")
    print(paint(f"Done! Report: {report_path}", "green"))
    print(f"Changed your mind? Restore with: python exif_resync.py --undo {report_path}")
    return 0


def undo_flow(input_func):
    """Guided --undo with a dry-run preview first. Returns exit code."""
    fallback = "exif_resync_report.csv" if os.path.exists("exif_resync_report.csv") else None
    report = ask(
        input_func,
        "Change report CSV",
        default=fallback,
        validator=lambda r: (
            os.path.abspath(os.path.expanduser(r))
            if os.path.exists(os.path.expanduser(r))
            else None
        ),
        hint="That file does not exist.",
    )
    print("Previewing restoration (nothing is written)...")
    rc = core.undo_from_report(report, dry_run=True)
    if rc != 0:
        return rc
    if not ask_yes_no(input_func, "Restore these original values", default=False):
        print("Nothing was written.")
        return 0
    return core.undo_from_report(report)


def check_flow(input_func):
    """Guided --check-config. Returns exit code."""
    config_path = ask(input_func, "Config file", default="config_planning.json")
    directory = ask(
        input_func,
        "Photo folder",
        default=".",
        validator=valid_dir,
        hint="That folder does not exist.",
    )
    recursive = ask_yes_no(input_func, "Scan subfolders recursively", default=False)
    core.check_config(os.path.abspath(os.path.expanduser(config_path)), directory, recursive)
    return 0


def menu_loop(input_func):
    """Show the menu until Quit. Returns a process exit code."""
    try:
        while True:
            choice = ask_choice(
                input_func,
                "What do you want to do?",
                [
                    ("1", "Resync photo timestamps (guided)"),
                    ("2", "Restore originals from a report (undo)"),
                    ("3", "Check a config file"),
                    ("4", "Quit"),
                ],
                default="1",
            )
            try:
                if choice == "1":
                    guided_resync(input_func)
                elif choice == "2":
                    undo_flow(input_func)
                elif choice == "3":
                    check_flow(input_func)
                else:
                    print("Bye!")
                    return 0
            except SystemExit as exc:
                # A flow hit a fatal error (bad report, unreadable config...):
                # report it and go back to the menu instead of exiting abruptly.
                if exc.code:
                    print(paint(str(exc.code), "red"))
                print("Back to the menu.\n")
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 130


def run_wizard(input_func=input):
    """Menu entry point. Returns a process exit code."""
    if input_func is input and not sys.stdin.isatty():
        print("ERROR: --wizard needs an interactive terminal.", file=sys.stderr)
        return 2
    print(paint("EXIF Timeline Resync — guided mode", "cyan", "bold"))
    print("Answer a few questions; nothing is written until you confirm.\n")
    try:
        return menu_loop(input_func)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 130
