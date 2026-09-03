# Contributing

Thanks for your interest in contributing! Bug reports and pull requests are
welcome. All participants are expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md). This document keeps the process smooth
for everyone.

## Reporting bugs

- Use the **bug report** issue template and include:
  - the exact command you ran,
  - the full error output (or what you expected vs. what happened),
  - your OS and Python version (`python --version`),
  - whether ExifTool is installed (`exiftool -ver`).
- If the bug involves photo matching, mention which `--matcher` you used.
- Do **not** attach private photos. If needed, reproduce with generated
  images (see `tests/` for examples using Pillow).

## Suggesting features

Open a **feature request** issue describing the problem first ("I want to…,
but I can't because…"), then the proposed solution if you have one. This
keeps new flags and behaviors consistent with the existing design
(see `explain.md`).

## Pull requests

1. Fork the repo and create a branch from `main`:
   `git checkout -b fix/short-description`.
2. Follow the existing code style: stdlib-only core, `ruff check` and
   `ruff format` clean (a pre-commit config is provided, see below).
3. **Include a test** when you fix or add something (`tests/` mirrors the
   modules: parsing, schedule, matchers, report/undo, timeline, wizard).
4. Update the docs when behavior changes: `README.md` **and** `README.fr.md`
   (both languages stay in sync), plus `explain.md` for internals.
5. Keep the wizard (`--wizard`) and the flags in sync — they share the same
   core functions, and it should stay that way.
6. Open the PR against `main` using the pull request template. CI must be
   green (ruff + tests on Linux/Windows, Python 3.9–3.13).

## Development setup

```bash
git clone https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher.git
cd EXIF-Timeline-Resync-Image-Matcher
pip install pytest Pillow ImageHash ruff pre-commit
python -m pytest tests/
pre-commit install   # optional, runs ruff on every commit
```

ExifTool is only needed for end-to-end runs; the test suite mocks it, and
`--dry-run` works without it.

## Release process (maintainers)

1. Bump `__version__` in `exif_resync.py` and `version` in `pyproject.toml`
   (single source of truth is manual — keep both in sync).
2. Add a `CHANGELOG.md` entry.
3. Commit, tag `vX.Y.Z`, push the tag: the release workflow builds the zip
   and opens the GitHub release.
