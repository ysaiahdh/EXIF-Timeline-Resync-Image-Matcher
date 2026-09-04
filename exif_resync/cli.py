# SPDX-License-Identifier: MIT
"""Command-line interface: argument parsing and top-level dispatch."""

import argparse
import sys

from . import __version__
from .process import check_config, process_photos
from .undo import undo_from_report


def main():
    parser = argparse.ArgumentParser(
        prog="exif-resync",
        description="Recalibrate EXIF metadata with image matching.",
        epilog=(
            "Examples:\n"
            "  python -m exif_resync --wizard\n"
            "  python -m exif_resync -d ./photos\n"
            "  python -m exif_resync -d ./photos --dry-run --timeline\n"
            "  python -m exif_resync -d ./photos -c my_config.json --year 2025\n"
            "  python -m exif_resync -d ./photos -r ./my_reference_photos\n"
            "  python -m exif_resync -d ./photos --sync-clocks\n"
            "  python -m exif_resync -d ./photos --check-config\n"
            "  python -m exif_resync --undo ./photos/exif_resync_report.csv\n"
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
        from . import wizard

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
