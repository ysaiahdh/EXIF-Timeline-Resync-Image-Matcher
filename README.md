# EXIF Timeline Resync & Image Matcher

[English](README.md) | [Français](README.fr.md)

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

## What it can do

- Rebuild realistic capture times from folder names like `01-05 Kayak trip`
  and a JSON schedule
- Spread photos a few minutes apart so nothing shares the same timestamp
- Store the blog's `.txt` descriptions inside `ImageDescription`
- Notice keywords (`boom`, `soiree`, `animaux`...) and shift evening or
  afternoon albums accordingly
- Use timestamps found in filenames when they exist (`IMG_20260501_143022.jpg`)
- Match your downloads against your own dated reference photos, either with a
  ResNet-18 neural network or with lightweight perceptual hashing
- Detect when a camera's clock was off (and fix the whole album with one flag)
- Preview everything with `--dry-run`, generate an HTML timeline, spot
  duplicates across albums, and roll back any run

## What you need

- Python 3.8 or newer
- [ExifTool](https://exiftool.org/). On Linux: `sudo apt install
  libimage-exiftool-perl`. On macOS: `brew install exiftool`. On Windows,
  download it from the site and drop `exiftool.exe` in this folder.
- For image matching only:
  - the good way: `pip install torch torchvision Pillow numpy`
    (on a CPU-only machine, installing torch with
    `--index-url https://download.pytorch.org/whl/cpu` saves a lot of disk)
  - or the light way: `pip install Pillow ImageHash`

Check ExifTool is available with `exiftool -ver`.

## Installing

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

## Quick start

Put your downloads in folders whose names start with a day-month date:

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
you prefer; just don't mix both spellings in one file. You can sanity-check
your setup without touching anything:

```bash
python exif_resync.py -d ./photos --check-config
```

Then preview, apply, and verify:

```bash
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

## How the times are figured out

For each album, the start time comes from the first rule that matches:

1. `boom` in the folder name, or `soiree` / `veillee` in the text → 20:30,
   90 seconds between photos
2. `bis`, `animaux` or `apres-midi` in the folder name → 15:00, 120 seconds
3. an entry for that day in your schedule → the configured time, 210 seconds
4. an hour written in the folder name (`02-05 Safari 14h30`) → that hour
5. otherwise 12:00, spaced by `default_interval_sec`

Photos then get `start + n × interval`, so timestamps stay strictly
increasing within an album. A timestamp found in a filename always wins over
all of this, since the camera knew better than we ever will.

## Matching against your own photos

If you were there too and your own shots still have correct EXIF dates, point
the tool at them:

```bash
python exif_resync.py -d ./photos -r ./my_reference_photos
```

Each download gets compared to every reference photo. When the best match is
convincing enough *and* falls on the same day as the album, its exact
timestamp is copied over. Two engines are available:

| Mode | Needs | Notes |
|---|---|---|
| `--matcher resnet` | torch, torchvision, Pillow | best quality, ~45 MB of weights downloaded on first use |
| `--matcher hash` | Pillow, ImageHash | tiny and fast, catches obvious same-photo cases |
| `--matcher auto` | either | picks resnet if installed, else hash |
| `--matcher off` | nothing | schedule-only mode |

As a bonus, matching runs also compare albums against each other and write
`duplicates_report.csv` when two photos in different folders look identical.

Reference photos are embedded once and cached next to your reference folder,
so later runs start instantly.

## Wrong camera clocks

Sometimes the photos that *kept* their EXIF disagree with the schedule,
because the camera's clock was set wrong. The tool notices this: if at least
two photos in an album have existing timestamps that are consistently shifted
compared to the computed ones, it tells you by how much. Nothing changes
unless you ask for it:

```bash
python exif_resync.py -d ./photos --sync-clocks
```

That flag shifts the whole album by the median offset. Timestamps taken from
filenames are left alone.

## Reports and safety nets

Every real run saves `exif_resync_report.csv`: old values, new values, where
each timestamp came from (`filename`, AI match, hash match, or plan), and any
drift correction applied. Keep these files around; `--undo` needs them.

`--dry-run` never touches a file and works even before you install ExifTool.
`--timeline` produces a small self-contained HTML page showing every album,
every photo, its assigned time, and where that time came from.

## All the options

```
python exif_resync.py [-d DIRECTORY] [-c CONFIG] [-r REFERENCE]
                      [--matcher {auto,resnet,hash,off}] [--sync-clocks]
                      [--dry-run] [--timeline] [--year YEAR] [--report PATH]
                      [--undo REPORT_CSV] [--check-config] [--verbose]
```

| Option | What it does |
|---|---|
| `-d, --directory` | folder containing the dated albums (default: here) |
| `-c, --config` | path to the JSON config (default: `config_planning.json`) |
| `-r, --reference` | your own correctly-dated photos, enables matching |
| `--matcher` | `auto`, `resnet`, `hash` or `off` |
| `--sync-clocks` | apply detected clock drift to the whole album |
| `--dry-run` | preview without writing anything |
| `--timeline` | generate `timeline.html` |
| `--year` | override the year for all reconstructed dates |
| `--report` | custom path for the change report CSV |
| `--undo` | restore originals from a previous report |
| `--check-config` | validate config, show resolved planning, exit |
| `--verbose` | per-photo details |

## When things go wrong

**"ExifTool not found"**: install it (see above). Windows users can just put
`exiftool.exe` next to the script.

**Everything got skipped**: your folder names need a recognizable `DD-MM` (or
`D-M`) pattern somewhere in them. Folders like `misc stuff` are ignored on
purpose.

**Wrong year everywhere**: set `year` (or `annee`) in the config, or pass
`--year`.

**A visual match I expected didn't happen**: it has to clear two bars, high
similarity *and* the same day as the album. Check the reference photo's own
EXIF date first; if that's wrong, the guard rightly refuses the match.

**Only .jpg/.jpeg/.png are processed.** RAW files are left untouched, and
videos are out of scope for now.

## Contributing

Bug reports and pull requests are welcome. There is a test suite
(`pip install pytest && python -m pytest tests/`) and CI runs ruff plus the
tests on Linux and Windows, so please include a test when you fix or add
something. Have a look at `explain.md` if you want the full internals before
diving in.

## License

MIT, see [LICENSE](LICENSE).
