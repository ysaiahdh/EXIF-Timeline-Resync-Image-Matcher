# SPDX-License-Identifier: MIT
"""Small shared helpers: human-readable durations and optional progress bars."""

import sys

try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


def fmt_delta(seconds):
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(int(round(seconds)))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{sign}{d}d{h}h"
    if h:
        return f"{sign}{h}h{m:02d}m"
    if m:
        return f"{sign}{m}m{s:02d}s"
    return f"{sign}{s}s"


def progress_iter(iterable, desc, enabled):
    if enabled and HAS_TQDM and sys.stderr.isatty():
        yield from tqdm(iterable, desc=desc, unit="img", leave=False)
    else:
        yield from iterable
