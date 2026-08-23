# How EXIF Timeline Resync Works

This document explains the internal logic of `exif_resync.py`: how capture
times are reconstructed, how the optional AI matching decides that two photos
show the same moment, and how the change report / undo system guarantees
reversibility.

---

## Pipeline overview

```
photos/
├── 01-05 Parc aquatique/          ① folder discovery
│   ├── resume.txt                 ② description extraction
│   ├── IMG_001.jpg ───────────┐
│   └── IMG_002.jpg            │   ③ start-time resolution
├── 02-05 Safari 14h30/        │   ④ sequential incrementing
│   └── ...                    │   ⑤ optional AI matching
└── ...                        │   ⑥ exiftool write
                                ▼
                     exif_resync_report.csv  ⇄  --undo
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
- optional `(\d{1,2})h(\d{2})` → hour embedded in the name (`14h30`); when it
  is not adjacent to the date (`02-05 Safari 14h30`), a second search finds
  the first `XhMM` token anywhere in the name
- the look-arounds `(?<![\d-])` / `(?!\d)` prevent false positives inside
  longer numbers such as ISO dates (`2026-05-01`) or phone numbers.

Day must be within 1–31 and month within 1–12 (and any parsed hour within
0–23), otherwise the folder is ignored. Folders without any match are
silently skipped.

Within each album, images (`*.jpg`, `*.jpeg`, `.png`, case-insensitive) are
processed in **alphabetical order** (also case-insensitive). If a `.txt` file
exists in the album, its full text becomes the value written to
`ImageDescription` for every photo of that album.

---

## 2. Start-time resolution

For each album, one start time is chosen by evaluating rules top-down; the
first hit wins:

| Priority | Rule | Start time | Interval between photos |
|---|---|---|---|
| 1 | `"boom"` in folder name, or `soiree`/`veillee` in the description | 20:30 | 90 s |
| 2 | `"bis"`, `"animaux"` or `"apres-midi"` in folder name | 15:00 | 120 s |
| 3 | Day present in `schedule` (a.k.a. `planning`) config key | configured `HH:MM` | 210 s |
| 4 | Hour parsed from folder name (`14h30`) | that hour | `default_interval_sec` |
| 5 | Nothing matched | 12:00 | `default_interval_sec` |

The year always comes from (in priority order): `--year` CLI flag →
`year`/`annee` config key → current calendar year (with a warning).

---

## 3. Sequential incrementing

Web albums are usually uploaded in chronological order, so photo *n* receives:

```
start_time + n × interval
```

This guarantees strictly increasing, duplicate-free timestamps inside each
album even though all photos would otherwise share the same second.

---

## 4. Optional AI matching (`-r/--reference`)

Purpose: if you took your own photos during the same events (with a phone or
camera, so their EXIF dates are still correct), the tool can transfer those
exact timestamps to the downloaded copies.

**Embedding** — a ResNet-18 pre-trained on ImageNet has its classification
head removed; the remaining network maps every image to a 512-dimension
feature vector (L2-normalized). Images looking alike end up with close
vectors regardless of resolution or compression.

**Matching decision** — for each download *D* and reference *R*:

1. Compute cosine similarity `cos(v_D, v_R)` against every reference;
   keep the best pair `(R*, score)`.
2. Accept only if **both** guards pass:
   - `score ≥ 0.85`
   - `date(R*)` within **±1 day** of the album's reconstructed date
3. On acceptance, *D*'s timestamp becomes exactly `datetime(R*)`
   (to the second); the sequential counter then continues from there.

The double check matters: high visual similarity alone can link photos taken
years apart (same pool, same classroom…). The day guard anchors matches to
your declared timeline.

Costs: first run downloads the ResNet-18 weights (~45 MB). Inference runs on
CPU at roughly 5–10 images/s.

---

## 5. Writing metadata

Each accepted photo is updated through ExifTool:

```
exiftool -AllDates="2026:05:01 09:03:00" \
         -ImageDescription="<album .txt content>" \
         -overwrite_original IMG_001.jpg
```

- `-AllDates=` sets `DateTimeOriginal`, `CreateDate` and `ModifyDate` together.
- `-overwrite_original` avoids leaving `_original` backup copies next to your
  photos (the CSV report is the safety net instead).

ExifTool exit codes are checked; failures are listed at the end of the run
and reflected in the process exit code.

---

## 6. Change report & undo

Before rewriting a photo, the tool reads its previous values:

```
exiftool -DateTimeOriginal -CreateDate -ModifyDate \
         -ImageDescription -s3 -f IMG_001.jpg
```

and stores them alongside the new values in
`<directory>/exif_resync_report.csv`. Missing previous values are recorded
as `-`.

`--undo report.csv` then replays the inverse operation per row:

- known old values → restored verbatim,
- `-` → the tag is explicitly **cleared** (restoring "no metadata").

Because restoration uses absolute paths recorded at run time, keep the report
next to your library and avoid moving photos between a run and its undo.

---

## Known limitations

- Only direct sub-folders are scanned (no recursion).
- Alphabetical order is assumed to reflect chronological order.
- Keywords (`boom`, `soiree`, …) are hard-coded and accent-free.
- PNG files get XMP-based dates rather than true EXIF records.
- One report file per run: running twice overwrites the default report
  unless `--report` is used.

Ideas welcome — recursive scan, configurable keywords, filename-date
detection (`IMG_20260501_143022.jpg`), GPU acceleration are natural next
steps.
