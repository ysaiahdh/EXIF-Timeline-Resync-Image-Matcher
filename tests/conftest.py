# SPDX-License-Identifier: MIT
import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

exif_resync = importlib.import_module("exif_resync")
