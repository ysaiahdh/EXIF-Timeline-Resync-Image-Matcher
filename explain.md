# How EXIF Timeline Resync Works

This document explains what happens inside `exif_resync.py`: how capture
times are reconstructed, how each time source wins or loses against the
others, how the two matching engines decide that two photos show the same
moment, and how the report / undo system keeps everything reversible.

---

## Pipeline overview

```
photos/
├── 01-05 Parc aquatique/          ① folder discovery
│   ├── resume.txt                 ② description extraction
│   ├── IMG_20260501_091533.jpg ─┐ ③ per-photo time resolution:
│   ├── IMG_002.jpg              │    filename date > visual match > plan
│   └── IMG_003.jpg              │ ④ clock-drift analysis (--sync-clocks)
├── 02-05 Safari 14h30/          │ ⑤ exiftool write
│   └── ...                      │ ⑥ duplicates detection (matching runs)
└── ...                          ▼
        exif_resync_report.csv ⇄ --undo      timeline.html      duplicates_report.csv
```

---

## 1. Folder discovery & date parsing

Only **direct sub-folders** of `-d/--directory` are scanned (one folder =
one album = one day). A folder is considered dated if its name contains a
pattern matching:

```regex
(?<![\d-])(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2})h(\d{2}))?(?!\d)
```

- `(\d{1,2})-(\d{1,2})` → day and month (`01-05`, but also `1-5`)
- optional `(\d{1,2})h(\d{2})` → hour embedded in the name; when it is not
  adjacent to the date, a second regex finds the first `XhMM` token anywhere
  in the name (`02-05 Safari 14h30`)
- the look-arounds `(?<![\d-])` / `(?!\d)` reject matches inside longer
  numbers, so ISO-style names like `2026-05-01` are ignored instead of being
  misread

Day must be within 1–31 and month within 1–12 (hour within 0–23), otherwise
the folder is skipped. Within an album, images (`*.jpg`, `.jpeg`, `.png`,
case-insensitive) are processed in alphabetical order. The first `.txt` file
found becomes the `ImageDescription` written into every photo of that album.

## 2. Album start-time resolution

Rules are evaluated top-down; first hit wins:

| Priority | Rule | Start | Step between photos |
|---|---|---|---|
| 1 | `boom` in folder name, or `soiree`/`veillee` in the text | 20:30 | 90 s |
| 2 | `bis`, `animaux`, `apres-midi` in folder name | 15:00 | 120 s |
| 3 | day present in `schedule` (a.k.a. `planning`) | configured `HH:MM` | 210 s |
| 4 | hour parsed from folder name | that hour | `default_interval_sec` |
| 5 | nothing matched | 12:00 | `default_interval_sec` |

The year comes from `--year`, else the config's `year`/`annee`, else the
current year (with a warning).

Planned times are then assigned sequentially: photo *n* receives
`start + n × step`. This guarantees strictly increasing timestamps even when
every photo would otherwise share one second.

## 3. Per-photo time resolution

Each photo's final timestamp is chosen by priority:

1. **Filename date** — `parse_filename_date` looks for a full date+time in
   the file name (`IMG_20260501_143022.jpg`, WhatsApp exports,
   `2026-05-01 14.30.22`, …) via:

   ```regex
   (?<!\d)(\d{4})[-_.T ]?(\d{2})[-_.T ]?(\d{2})[-_.T ]?
   (\d{2})[.:T]?(\d{2})(?:[.:T]?(\d{2}))?(?!\d)
   ```

   All components are range-checked through `datetime()`. A camera-stamped
   filename is treated as ground truth and overrides everything below.
   Reported as source `filename`.

2. **Visual match** — see section 4. Source `resnet` or `hash`.

3. **Plan** — the sequential time from section 2. Source `plan`.

## 4. Matching engines

Both engines expose the same small interface: embed photos into "vectors",
compare vectors with a similarity in `[0..1]`, and accept a reference only
above the engine's threshold *and* within ±1 day of the album's date. The day
guard prevents classic false positives (same pool photographed years apart).

### ResNetMatcher (`--matcher resnet`)

ResNet-18 pre-trained on ImageNet, classification head removed: every image
becomes a 512-d L2-normalized feature vector; similarity is the dot product
(cosine). Acceptance threshold **0.85**. Inference is batched (16 images),
runs on CUDA, Apple MPS or CPU depending on availability. Weights (~45 MB)
are downloaded once. Vectors of your reference gallery are cached in
`.exif_resync_cache.npz` next to the references (invalidated automatically
when a file changes), so subsequent runs skip re-embedding.

### HashMatcher (`--matcher hash`)

Perceptual hash (pHash, via Pillow + ImageHash): similarity is
`1 - hamming_distance / 64`. Threshold **0.90** (≤ ~6 flipped bits). Much
weaker than the network — it detects near-identical images, not "same scene"
— but installs in seconds and runs anywhere.

`--matcher auto` picks ResNet when torch is importable, otherwise falls back
to hashing, otherwise matching is disabled with a notice.

### Duplicate detection

Whenever matching runs, every embedded download is also compared against the
other albums. Cross-album pairs with similarity ≥ **0.95** land in
`duplicates_report.csv` — a free by-product that catches photos uploaded to
several blog posts.

## 5. Clock-drift analysis

For each album the tool compares existing EXIF timestamps against the planned
sequential ones. Every photo carrying a parseable old `DateTimeOriginal`
contributes one offset sample. When at least two samples exist, their median
is computed; if its magnitude reaches 60 seconds the drift is reported in the
run summary and in the CSV (`drift_applied_sec`). Nothing is modified unless
`--sync-clocks` is passed, in which case every non-filename-sourced target is
shifted by the median offset. Filename-derived timestamps stay untouched:
they are absolute references, not album-relative ones.

## 6. Writing metadata

```
exiftool -AllDates="2026:05:01 09:03:00" \
         -ImageDescription="<album .txt content>" \
         -overwrite_original IMG_001.jpg
```

`-AllDates=` sets `DateTimeOriginal`, `CreateDate` and `ModifyDate`
together. Exit codes are checked; failures are listed at the end and set the
process exit code to 1.

Before writing, previous values are read with `-s3 -f` so they can go into
the report.

## 7. Report & undo

`exif_resync_report.csv` stores, per photo: path, the four previous values
(`-` = absent), the new timestamp and description, the match score, the time
source, and any applied drift. `--undo report.csv` replays the inverse per
row: known old values are restored verbatim, `-` clears the tag explicitly.
Because rows use absolute paths captured at run time, don't move photos
between a run and its undo.

## 8. Timeline HTML

`--timeline` renders `timeline.html` from the same in-memory rows: one card
per album (folder name, start rule), one row per photo with assigned time, a
colored source badge (`FILE` / `AI` / `HASH` / `PLAN`), similarity percentage
and applied drift. Everything is escaped; the file has no external
dependencies and works offline.

## Known limitations

- Only direct sub-folders are scanned (no recursion yet)
- Alphabetical order is assumed to reflect chronological order
- Keywords are hard-coded and accent-free
- One default report name per run; use `--report` for parallel sessions
- PNG files receive XMP-based dates rather than true EXIF records

Natural next steps: recursive scan, configurable keywords, GPU batching
tuning, PyPI publication.
