# EXIF Timeline Resync & Image Matcher

[English](README.md) | [Français](README.fr.md)

Restore EXIF timestamps and chronology for photos downloaded from blogs,
school sites, or vacation camp websites.

When you download photos from web platforms (WordPress, Blogger, Wix, etc.),
the original EXIF data (`DateTimeOriginal`) is usually stripped. All photos
end up dated with the download date and lose their chronological order.

**EXIF Timeline Resync** solves this by combining **folder structure**, a
**JSON schedule**, **blog text descriptions**, and **AI image recognition**
using your own reference photos.

Every run also produces a **change report (CSV)** so you can audit — or fully
**undo** — everything the tool wrote.

---

## Features

- **Smart time reconstruction**: Calculates realistic capture times from
  dated folder names and your schedule.
- **Sequential incrementing**: Avoids duplicate timestamps by applying a
  dynamic time step between each photo in an album.
- **Text summary integration**: Reads `.txt` blog description files and
  writes their content into the EXIF `ImageDescription` tag.
- **Context analysis**: Adjusts time slots based on keywords (boom,
  soiree, apres-midi, safari, etc.).
- **Image recognition & alignment (optional)**: Compares downloaded images
  with your own smartphone photos (already dated) via a ResNet-18 neural
  network to align timestamps to the second.
- **Consistency guard**: Validates visual matches by checking cosine
  similarity (85% threshold) **and** day-of-year match in the schedule.
- **Dry-run mode**: Preview every computed timestamp before touching a
  single file.
- **Change report & undo**: Each write run saves a CSV report of old → new
  values; restore originals anytime with `--undo`.

---

## Requirements

1. **Python 3.8+**
2. **ExifTool**: required to read and write EXIF tags.
   - **Windows**: download the full distribution from
     [exiftool.org](https://exiftool.org/) and place `exiftool.exe` in the
     project root (or add it to your system `PATH`).
   - **Linux**: `sudo apt install libimage-exiftool-perl`
     (or `sudo dnf install perl-Image-ExifTool`)
   - **macOS**: `brew install exiftool`
3. *(Optional)* **PyTorch + torchvision**: only needed for AI image
   matching with `-r/--reference`. See `requirements.txt`.

Check that ExifTool works:

```bash
exiftool -ver
```

---

## Installation

```bash
git clone https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher.git
cd EXIF-Timeline-Resync-Image-Matcher

# Optional: enable AI image matching
pip install -r requirements.txt
```

> On Windows, remember to put `exiftool.exe` in this folder if it is not in
> your `PATH`.

---

## Quick start

### 1. Prepare your photos

Organize downloads into folders whose names contain a day-month date
(`DD-MM`, optionally followed by an hour like `14h30`):

```
photos/
├── 01-05 Parc aquatique/
│   ├── IMG_001.jpg
│   ├── IMG_002.jpg
│   └── resume.txt          <- optional: stored in ImageDescription
├── 02-05 Safari 14h30/
│   └── ...
└── 03-05 Soiree boom/
    └── ...
```

### 2. Create your schedule config

```bash
cp config.example.json config_planning.json
```

```json
{
  "year": 2026,
  "default_interval_sec": 180,
  "schedule": {
    "01-05": "09:00",
    "02-05": "08:30",
    "03-05": "10:00"
  }
}
```

| Key | French alias | Description |
|---|---|---|
| `year` | `annee` | Year applied to all reconstructed dates. |
| `default_interval_sec` | `intervalle_defaut_sec` | Seconds added between consecutive photos when no special rule applies (default: `180`). |
| `schedule` | `planning` | Maps `"DD-MM"` folder dates to the activity start time `"HH:MM"`. |

Both English and French keys are accepted; do not mix them within one file.

### 3. Preview, then apply

Always start with a dry run:

```bash
python exif_resync.py -d ./photos --dry-run
```

Then write the metadata for real:

```bash
python exif_resync.py -d ./photos
```

If anything looks wrong, roll back:

```bash
python exif_resync.py --undo ./photos/exif_resync_report.csv
```

---

## How times are chosen

For each album folder, a start time is picked with this priority:

1. **Party keywords** (`boom` in folder name, `soiree`/`veillee` in the text
   description) → starts at **20:30**, 90 s between photos.
2. **Afternoon keywords** (`bis`, `animaux`, `apres-midi` in folder name)
   → starts at **15:00**, 120 s between photos.
3. **Your schedule** (`schedule` entry for that day) → starts at the
   configured time, 210 s between photos.
4. **Folder name time** (`02-05 Safari 14h30` → 14:30).
5. **Fallback** → 12:00, using `default_interval_sec` between photos.

Each following photo gets `start_time + n × interval`, guaranteeing strictly
increasing, duplicate-free timestamps inside an album.

---

## AI image matching (optional)

Do you have *your own* photos of the same events, taken with a phone or
camera (so they still have correct timestamps)? Put them in a folder and run:

```bash
python exif_resync.py -d ./photos -r ./my_reference_photos
```

How it works:

1. Every reference photo is embedded with a ResNet-18 network (pre-trained
   on ImageNet) into a feature vector.
2. Every download is embedded the same way.
3. The closest reference vector is found via cosine similarity.
4. A match is accepted only if similarity ≥ **0.85** *and* the reference was
   shot within ±1 day of the album's date — this double check prevents false
   positives.
5. Accepted matches copy the reference's exact timestamp (to the second).

Notes:

- First run downloads the ResNet-18 weights (~45 MB), so internet access is
  needed once.
- Without PyTorch installed, the tool automatically falls back to
  schedule-only mode.
- Matching runs on CPU by default and processes roughly 5–10 images/second.

---

## Change report & undo

Every real (non-dry-run) execution writes
`<directory>/exif_resync_report.csv` containing, per photo:

| Column | Meaning |
|---|---|
| `path` | Absolute path of the photo. |
| `old_datetimeoriginal` / `old_createdate` / `old_modifydate` | Previous EXIF dates (`-` = none). |
| `old_imagedescription` | Previous description (`-` = none). |
| `new_datetime` | Timestamp written by the tool. |
| `new_imagedescription` | Description written by the tool. |
| `match_score` | Cosine similarity of the visual match (empty if unused). |

To restore the previous state:

```bash
python exif_resync.py --undo ./photos/exif_resync_report.csv
```

Keep one report per session — each new run overwrites the default report
name, or use `--report my_session.csv` to control it yourself.

---

## Command-line reference

```
python exif_resync.py [-d DIRECTORY] [-c CONFIG] [-r REFERENCE]
                      [--dry-run] [--year YEAR] [--report PATH]
                      [--undo REPORT_CSV]
```

| Option | Description |
|---|---|
| `-d, --directory` | Folder containing dated sub-albums (default: current). |
| `-c, --config` | Path to the JSON config (default: `config_planning.json`). |
| `-r, --reference` | Folder of your correctly-dated reference photos (enables AI matching). |
| `--dry-run` | Preview all computed changes without writing anything. |
| `--year YEAR` | Override the year for all reconstructed dates. |
| `--report PATH` | Custom path for the change report CSV. |
| `--undo REPORT_CSV` | Restore original EXIF values recorded in the given report. |

---

## Troubleshooting

**`ERROR: ExifTool not found`**
Install ExifTool (see Requirements). On Windows you can drop `exiftool.exe`
next to `exif_resync.py`.

**My photos were skipped entirely**
Folder names must contain a recognizable `DD-MM` (or `D-M`) pattern, e.g.
`05-06 Kayak`. Folders without a date are ignored.

**All timestamps use the wrong year**
Set `"year"` (or `"annee"`) in your config, or pass `--year 2025`.

**The config file seems ignored**
Make sure it is valid JSON and uses either English keys (`year`,
`default_interval_sec`, `schedule`) or French keys (`annee`,
`intervalle_defaut_sec`, `planning`). The tool prints a warning when the
config file cannot be found or parsed.

**A visual match was rejected although the photos look identical**
The match must clear both checks: cosine similarity ≥ 0.85 and a capture
date within ±1 day of the album's reconstructed date. Verify the reference
photo's own `DateTimeOriginal` is correct.

---

## FAQ

**Is it safe? Will it destroy my files?**
The tool only rewrites EXIF metadata fields (`AllDates`, `ImageDescription`)
and never touches image pixels. Use `--dry-run` first, and keep the CSV
report — `--undo` restores the previous values exactly, even "no value".

**Does it rename or move files?**
No. File names and locations never change.

**Which formats are supported?**
`.jpg`, `.jpeg` and `.png`. RAW formats (CR2, NEF, ARW…) are not scanned but
are left untouched.

**Can I use it on videos?**
No. Only `.jpg` / `.jpeg` / `.png` are processed.

## License

[MIT](LICENSE)
