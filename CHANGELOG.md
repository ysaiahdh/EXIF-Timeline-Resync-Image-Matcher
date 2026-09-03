# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.1.0] — 2026-09-03

### Added

- Interactive `--wizard`: guided 5-step CLI (setup, preview, confirm) with
  colors, tables, and a progress bar. Standard library only.
- `--duplicates`: duplicate detection without `--reference` (within and
  across albums); `--duplicate-threshold` to tune it.
- `--match-threshold`: override the visual-match acceptance bar.
- `--recursive`: scan album and reference folders recursively
  (also honored by `--check-config`).
- `--quiet`: warnings, errors, and summary only.
- `--sync-mtime`: optionally align filesystem modification times
  (documented as not restored by `--undo`).
- `--undo --dry-run`: preview a restoration before applying it.
- `progress_cb` hook in `process_photos` for embedding progress reporting.

### Changed

- EXIF reads use one batched `exiftool -j` call per album instead of
  `-s3` line parsing (multi-line descriptions can no longer corrupt fields).
- Reference dates are read with a single batched ExifTool call.
- Keywords match whole words, accent-insensitively (`soirée` matches,
  `bison` doesn't); evening keywords now apply to folder and text.
- `ImageDescription` is only written when the album has a `.txt`;
  existing descriptions are otherwise preserved.
- `--dry-run` never writes files (no report, no duplicates CSV, no cache)
  and still works without ExifTool; drift is analyzed when ExifTool exists.
- Schedule keys/values are normalized (`1-5` → `01-05`) and validated
  with fail-fast errors; mixed EN/FR config keys warn.

### Fixed

- Crash on malformed schedule values; `interval: 0` rejected.
- Missing reference directory now errors clearly instead of traceback.
- `Image.open` handles closed properly (no more file locks on Windows).
- Release zip includes `pyproject.toml` so `pip install` works from it.

## [2.0.0] — 2026-08-23

### Added

- Filename-embedded timestamps (`IMG_20260501_143022.jpg`) as ground truth.
- Clock-drift detection with `--sync-clocks` whole-album correction.
- Dual matchers: ResNet-18 AI matching and lightweight perceptual hashing,
  with reference-vector caching and cross-album duplicate reports.
- CSV change reports with `--undo` restoration, HTML timelines, `--check-config`.
- Test suite (pytest), ruff lint, CI on Linux/Windows, English + French READMEs.

[Unreleased]: https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher/releases/tag/v2.0.0
