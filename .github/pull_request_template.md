## What

<!-- One-line summary, then details if needed. Link related issues: Fixes #123 -->

## How it was verified

<!-- Commands run and their result, e.g. -->

- [ ] `python -m pytest tests/` passes
- [ ] `ruff check exif_resync.py wizard.py tests/` passes
- [ ] `ruff format --check exif_resync.py wizard.py tests/` passes
- [ ] New/changed behavior covered by tests

## Docs

- [ ] `README.md` updated
- [ ] `README.fr.md` updated (both languages stay in sync)
- [ ] `explain.md` / `CHANGELOG.md` updated if behavior changed
- [ ] No change needed (docs-only or internal change — delete the irrelevant boxes)

## Checklist

- [ ] Wizard (`--wizard`) and flags behave the same (they share core functions)
- [ ] `--dry-run` writes nothing new
- [ ] No new dependencies (core stays stdlib-only)
