# SPDX-License-Identifier: MIT
"""EXIF Timeline Resync & Image Matcher.

Restores EXIF timestamps from folder names, schedules, embedded filename
dates, and visual matching against your own reference photos.

Every write run produces a change report (CSV) that can be replayed with
--undo to restore the previous state exactly.
"""

__version__ = "2.1.0"

from .cli import main
from .config import (
    EN_KEYS,
    FR_KEYS,
    load_config,
    normalize_day_key,
    normalize_schedule_time,
    parse_time_value,
)
from .exiftool import (
    EXIF_TAGS,
    apply_tags,
    find_exiftool,
    read_current_tags,
    read_current_tags_batch,
)
from .matchers import (
    DUPLICATE_SIMILARITY_THRESHOLD,
    HAS_IMAGEHASH,
    HAS_NUMPY,
    HAS_PILLOW,
    HAS_VISION,
    HASH_SIMILARITY_THRESHOLD,
    RESNET_BATCH_SIZE,
    RESNET_SIMILARITY_THRESHOLD,
    VECTOR_CACHE_NAME,
    HashMatcher,
    ResNetMatcher,
    build_matcher,
    cache_load,
    cache_save,
    load_reference_gallery,
)
from .parsing import (
    AFTERNOON_KEYWORDS,
    EVENING_KEYWORDS,
    FILENAME_DT_RE,
    FOLDER_DATE_RE,
    FOLDER_HOUR_RE,
    IMAGE_EXTENSIONS,
    contains_word,
    determine_schedule,
    fold_accents,
    iter_album_dirs,
    iter_image_files,
    parse_filename_date,
    parse_folder_date,
    read_album_description,
    summarize_albums,
)
from .process import (
    BADGE_COLORS,
    DRIFT_MIN_SAMPLES,
    DRIFT_REPORT_THRESHOLD_SEC,
    REPORT_FIELDS,
    SOURCE_LABELS,
    check_config,
    detect_duplicates,
    process_photos,
    write_report,
    write_timeline,
)
from .undo import UNDO_REQUIRED_COLUMNS, undo_from_report
from .util import HAS_TQDM, fmt_delta, progress_iter

__all__ = [
    "__version__",
    "main",
    "EN_KEYS",
    "FR_KEYS",
    "load_config",
    "normalize_day_key",
    "normalize_schedule_time",
    "parse_time_value",
    "EXIF_TAGS",
    "apply_tags",
    "find_exiftool",
    "read_current_tags",
    "read_current_tags_batch",
    "DUPLICATE_SIMILARITY_THRESHOLD",
    "HASH_SIMILARITY_THRESHOLD",
    "HAS_IMAGEHASH",
    "HAS_NUMPY",
    "HAS_PILLOW",
    "HAS_TQDM",
    "HAS_VISION",
    "RESNET_BATCH_SIZE",
    "RESNET_SIMILARITY_THRESHOLD",
    "VECTOR_CACHE_NAME",
    "HashMatcher",
    "ResNetMatcher",
    "build_matcher",
    "cache_load",
    "cache_save",
    "load_reference_gallery",
    "AFTERNOON_KEYWORDS",
    "EVENING_KEYWORDS",
    "FILENAME_DT_RE",
    "FOLDER_DATE_RE",
    "FOLDER_HOUR_RE",
    "IMAGE_EXTENSIONS",
    "contains_word",
    "determine_schedule",
    "fold_accents",
    "iter_album_dirs",
    "iter_image_files",
    "parse_filename_date",
    "parse_folder_date",
    "read_album_description",
    "summarize_albums",
    "BADGE_COLORS",
    "DRIFT_MIN_SAMPLES",
    "DRIFT_REPORT_THRESHOLD_SEC",
    "REPORT_FIELDS",
    "SOURCE_LABELS",
    "check_config",
    "detect_duplicates",
    "process_photos",
    "write_report",
    "write_timeline",
    "UNDO_REQUIRED_COLUMNS",
    "undo_from_report",
    "fmt_delta",
    "progress_iter",
]
