# EXIF Timeline Resync & Image Matcher

Restore EXIF timestamps and chronology for photos downloaded from blogs,
school sites, or vacation camp websites.

When you download photos from web platforms (WordPress, Blogger, Wix, etc.),
the original EXIF data (`DateTimeOriginal`) is usually stripped. All photos
end up dated with the download date and lose their chronological order.

**EXIF Timeline Resync** solves this by combining **folder structure**, a
**JSON schedule**, **blog text descriptions**, and **AI image recognition**
using your own reference photos.

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

---

## Requirements

1. **Python 3.8+**
2. **ExifTool**: Required for writing EXIF tags.
   - Download the executable and place `exiftool.exe` in the project root
     (or add it to your system `PATH`).

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-username/exif-timeline-resync.git
   cd exif-timeline-resync
   ```
