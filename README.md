# 📸 EXIF Timeline Resync & Image Matcher

[![CI](https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher/actions/workflows/ci.yml/badge.svg)](https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher/actions)
[![Python](https://img.shields.io/badge/python-%3E%3D3.9-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> *Your downloaded photos lost their sense of time. This brings it back —
> realistic EXIF timestamps rebuilt from folder names, schedules, filenames,
> and visual matching.*

**[English](README.md) | [Français](README.fr.md)**

---

## 📖 Contents

- [The story](#-the-story)
- [What it can do](#-what-it-can-do)
- [What you need](#-what-you-need)
- [Installing](#-installing)
- [Guided mode](#-guided-mode--start-here)
- [Quick start](#-quick-start)
- [Config reference](#-config-reference)
- [How the times are figured out](#-how-the-times-are-figured-out)
- [Matching against your own photos](#-matching-against-your-own-photos)
- [Wrong camera clocks](#-wrong-camera-clocks)
- [Reports and safety nets](#-reports-and-safety-nets)
- [All the options](#-all-the-options)
- [When things go wrong](#-when-things-go-wrong)
- [Contributing](#-contributing)
- [License](#-license)

---

## 📷 The story

I built this tool after downloading a few hundred photos from a summer camp
website. Every single one was stamped with the download date. The blog posts
told the story day by day, the folder names had the dates, but the files
themselves had lost all sense of time. Sorting them by hand was not going to
happen.

This script puts the chronology back. It reads the dates hidden in your
folder names, combines them with a small schedule file and the text
descriptions saved next to the photos, and writes proper EXIF timestamps back
into each image. If you also took your own photos during those events (with a
phone or camera that keeps its clock right), it can even match pictures
visually and copy the exact second they were taken.

And because it rewrites metadata, everything it does is recorded in a CSV
report you can replay with `--undo` if you don't like the result.

```
  Before                                    After
  ──────────────────────────────────────────────────────────────────
  IMG_001.jpg  📥 June 2026 (download)  →  📅 2026:05:01 09:00:00 · plan
  IMG_002.jpg  📥 June 2026 (download)  →  📅 2026:05:01 09:03:30 · plan
  IMG_003.jpg  📥 June 2026 (download)  →  📅 2026:05:01 14:30:22 · filename 🤖
```

## ✨ What it can do

- 🕰️ **Rebuild realistic capture times** from folder names like
  `01-05 Kayak trip` and a JSON schedule
- ↔️ **Spread photos minutes apart** so nothing shares the same timestamp
- 📝 **Store the blog's `.txt` descriptions** inside `ImageDescription`
  (only when a `.txt` exists; existing descriptions are otherwise left alone)
- 🌙 **Notice keywords** (`boom`, `soiree`, `animaux`...) and shift evening or
  afternoon albums accordingly — accent-insensitive, whole words only, so
  `soirée` matches but `bison` doesn't
- 🔢 **Use timestamps found in filenames** when they exist
  (`IMG_20260501_143022.jpg`)
- 🧠 **Match your downloads against your own dated reference photos**, with a
  ResNet-18 neural network or lightweight perceptual hashing
- ⏱️ **Detect when a camera's clock was off** — and fix the whole album with
  one flag
- 🔍 **Preview everything** with `--dry-run`, generate an HTML timeline, spot
  duplicates within and across albums, and roll back any run

## 📦 What you need

- Python 3.9 or newer
- [ExifTool](https://exiftool.org/)
  - Linux: `sudo apt install libimage-exiftool-perl`
  - macOS: `brew install exiftool`
  - Windows: download it from the site and drop `exiftool.exe` in this folder
- For image matching only:
  - 💪 the good way: `pip install torch torchvision Pillow numpy`
  - 🪶 the light way: `pip install Pillow ImageHash`

  > [!TIP]
  > On a CPU-only machine, install torch with
  > `--index-url https://download.pytorch.org/whl/cpu` to save a lot of disk.

Check ExifTool is available with `exiftool -ver`.

## 🚀 Installing

Clone and run, that's really it:

```bash
git clone https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher.git
cd EXIF-Timeline-Resync-Image-Matcher
python exif_resync.py --help
```

If you prefer a proper install, this works too:

```bash
pip install .
exif-resync --help
```

## 🧙 Guided mode — start here

Don't want to memorize flags? Run the interactive wizard:

```bash
python exif_resync.py --wizard
```

It walks you through five steps:

1. 📁 photo folder
2. 🗓️ schedule (creating the config file *with you* when it's missing)
3. 🖼️ optional reference photos
4. 👀 dry-run preview with a progress bar
5. ✅ final confirmation before anything is written

The same menu also offers config checking and `--undo` restoration with a
preview first.

> [!NOTE]
> Standard library only — it needs an interactive terminal. Colors and the
> progress bar switch off automatically when output is redirected, and
> `Ctrl-C` cancels safely at any point.

## ⚡ Quick start

Put your downloads in folders whose names contain a day-month date:

```
photos/
├── 01-05 Parc aquatique/
│   ├── IMG_001.jpg
│   ├── IMG_002.jpg
│   └── resume.txt        <- optional, becomes ImageDescription
├── 02-05 Safari 14h30/
└── 03-05 Soiree boom/
```

Copy the sample config and adjust it:

```bash
cp config.example.json config_planning.json
```

```json
{
  "year": 2026,
  "default_interval_sec": 180,
  "schedule": {
    "01-05": "09:00",
    "02-05": "08:30"
  }
}
```

French keys work as well (`annee`, `intervalle_defaut_sec`, `planning`) if
you prefer; just don't mix both spellings in one file (you'll get a warning).
Schedule keys and values are flexible: `1-5` means `01-05`, and `9:30` means
`09:30`. Anything else (a bad date, `25:00`, a zero interval) fails fast with
a clear error instead of crashing halfway through a run.

Then preview, apply, and verify:

```bash
python exif_resync.py -d ./photos --check-config   # sanity check, touches nothing
python exif_resync.py -d ./photos --dry-run --timeline
python exif_resync.py -d ./photos
open photos/timeline.html      # see what was written
```

Changed your mind?

```bash
python exif_resync.py --undo ./photos/exif_resync_report.csv
```

The undo restores the previous values exactly, including removing tags that
didn't exist before.

## ⚙️ Config reference

| Key (EN / FR) | Default | Rules |
|---|---|---|
| `year` / `annee` | current year (with a warning) | four-digit year, e.g. `2026` |
| `default_interval_sec` / `intervalle_defaut_sec` | `180` | positive number of seconds between photos when nothing else matches |
| `schedule` / `planning` | `{}` | object mapping `"DD-MM"` to `"HH:MM"` (24h), e.g. `"01-05": "09:00"` |

## 🕰️ How the times are figured out

For each album, the start time comes from the first rule that matches:

| # | Rule | Start | Step |
|---|---|---|---|
| 1 | 🌙 Evening keyword (`boom`, `soiree`/`soirée`, `veillee`/`veillée`) in the folder name or the album text | 20:30 | 90 s |
| 2 | ☀️ Afternoon keyword (`bis`, `animaux`, `apres-midi`/`après-midi`) in the folder name | 15:00 | 120 s |
| 3 | 🗓️ An entry for that day in your schedule | configured time | 210 s |
| 4 | 🕑 An hour in the folder name (`02-05 Safari 14h30`) | that hour | `default_interval_sec` |
| 5 | 💤 Nothing matched | 12:00 | `default_interval_sec` |

Keywords match whole words only, so a folder named `Bisons` won't be mistaken
for an afternoon album.

Photos then get `start + n × interval`, so timestamps stay strictly
increasing within an album. A timestamp found in a filename always wins over
all of this, since the camera knew better than we ever will.

## 🧠 Matching against your own photos

If you were there too and your own shots still have correct EXIF dates, point
the tool at them:

```bash
python exif_resync.py -d ./photos -r ./my_reference_photos
```

Each download gets compared to every reference photo. When the best match is
convincing enough *and* falls within a day of the album date, its exact
timestamp is copied over. Two engines are available:

| Mode | Needs | Notes |
|---|---|---|
| `--matcher resnet` | torch, torchvision, Pillow | best quality, ~45 MB of weights downloaded on first use |
| `--matcher hash` | Pillow, ImageHash | tiny and fast, catches obvious same-photo cases |
| `--matcher auto` | either | picks resnet if installed, else hash |
| `--matcher off` | nothing | schedule-only mode |

As a bonus, matching runs also compare photos against each other and write
`duplicates_report.csv` when two photos look identical — within an album as
well as across different folders. No reference photos handy? `--duplicates`
runs just that comparison on its own:

```bash
python exif_resync.py -d ./photos --duplicates --matcher hash
```

Reference photos are embedded once and cached next to your reference folder,
so later runs start instantly.

## ⏱️ Wrong camera clocks

Sometimes the photos that *kept* their EXIF disagree with the schedule,
because the camera's clock was set wrong. The tool notices this: if at least
two photos in an album have existing timestamps that are consistently shifted
compared to the computed ones, it tells you by how much. Drift is also
reported during `--dry-run` previews whenever ExifTool is installed.
Nothing changes unless you ask for it:

```bash
python exif_resync.py -d ./photos --sync-clocks
```

That flag shifts the whole album by the median offset. Timestamps taken from
filenames are left alone.

## 🛡️ Reports and safety nets

Every real run saves `exif_resync_report.csv`: old values, new values, where
each timestamp came from (`filename`, AI match, hash match, or plan), and any
drift correction applied. Keep these files around; `--undo` needs them.
`--undo` also accepts `--dry-run` (preview what would be restored) and
`--quiet` (summary only).

> [!NOTE]
> `--dry-run` never touches a file and works even before you install ExifTool.

`--timeline` produces a small self-contained HTML page showing every album,
every photo, its assigned time, and where that time came from. Combined with
`--report`, the timeline is written next to the report instead of inside the
photo folder.

> [!WARNING]
> `--sync-mtime` also updates the filesystem modification time — and unlike
> EXIF tags, that one is **not** restored by `--undo`.

## 🧰 All the options

<details>
<summary><b>Click to expand the full option list</b></summary>

```
python exif_resync.py [-d DIRECTORY] [-c CONFIG] [-r REFERENCE]
                      [--matcher {auto,resnet,hash,off}]
                      [--match-threshold FLOAT] [--duplicates]
                      [--duplicate-threshold FLOAT] [--recursive]
                      [--sync-clocks] [--sync-mtime]
                      [--dry-run] [--timeline] [--year YEAR] [--report PATH]
                      [--undo REPORT_CSV] [--check-config] [--verbose] [--quiet]
                      [--wizard]
```

| Option | What it does |
|---|---|
| `-d, --directory` | folder containing the dated albums (default: here) |
| `-c, --config` | path to the JSON config (default: `config_planning.json`) |
| `-r, --reference` | your own correctly-dated photos, enables matching |
| `--matcher` | `auto`, `resnet`, `hash` or `off` |
| `--match-threshold` | override the matcher's acceptance similarity, within (0, 1] |
| `--duplicates` | detect near-identical photos even without `--reference` |
| `--duplicate-threshold` | similarity at or above which photos count as duplicates (default: 0.95) |
| `--recursive` | scan album and reference folders recursively |
| `--sync-clocks` | apply detected clock drift to the whole album |
| `--sync-mtime` | also set the filesystem modification time (not restored by `--undo`) |
| `--dry-run` | preview without writing anything (also previews `--undo`) |
| `--timeline` | generate `timeline.html` |
| `--year` | override the year for all reconstructed dates |
| `--report` | custom path for the change report CSV |
| `--undo` | restore originals from a previous report |
| `--check-config` | validate config, show resolved planning, exit |
| `--verbose` | per-photo details |
| `--quiet` | only warnings, errors and the final summary |
| `--wizard` | guided interactive mode (prompts + preview + confirm) |

</details>

## 🆘 When things go wrong

**"ExifTool not found"** → install it (see above). Windows users can just put
`exiftool.exe` next to the script.

**Everything got skipped** → your folder names need a recognizable `DD-MM`
(or `D-M`) pattern somewhere in them. Folders like `misc stuff` are ignored on
purpose.

**Wrong year everywhere** → set `year` (or `annee`) in the config, or pass
`--year`.

**A visual match I expected didn't happen** → it has to clear two hurdles,
high similarity *and* a date within a day of the album. Check the reference
photo's own EXIF date first; if that's wrong, the guard rightly refuses the
match. You can loosen the similarity bar with `--match-threshold`.

**Only .jpg/.jpeg/.png are processed.** RAW files are left untouched, and
videos are out of scope for now.

> [!NOTE]
> PNG dates are stored as XMP rather than true EXIF records.

## 🤝 Contributing

Bug reports and pull requests are welcome. There is a test suite — please
include a test when you fix or add something:

```bash
pip install pytest Pillow ImageHash
python -m pytest tests/
ruff check exif_resync.py wizard.py tests/
ruff format --check exif_resync.py wizard.py tests/
```

CI runs ruff plus the tests on Linux and Windows across Python 3.9–3.13.
Have a look at `explain.md` if you want the full internals before diving in.

## 📄 License

MIT, see [LICENSE](LICENSE).
