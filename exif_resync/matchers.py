# SPDX-License-Identifier: MIT
"""Visual matching engines (ResNet AI, perceptual hash) and the reference gallery."""

import json
import os
import subprocess
import sys
from datetime import datetime

from .parsing import iter_image_files
from .util import progress_iter

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import imagehash

    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

try:
    import torch
    import torchvision.transforms as T
    from torchvision.models import ResNet18_Weights, resnet18

    HAS_VISION = True
except ImportError:
    HAS_VISION = False


RESNET_SIMILARITY_THRESHOLD = 0.85
HASH_SIMILARITY_THRESHOLD = 0.90
DUPLICATE_SIMILARITY_THRESHOLD = 0.95
RESNET_BATCH_SIZE = 16
VECTOR_CACHE_NAME = ".exif_resync_cache.npz"


class ResNetMatcher:
    name = "resnet"
    threshold = RESNET_SIMILARITY_THRESHOLD

    def __init__(self):
        if not HAS_VISION or not HAS_PILLOW:
            raise RuntimeError("PyTorch, torchvision and Pillow are required.")
        if HAS_NUMPY and torch.cuda.is_available():
            self.device = torch.device("cuda")
        elif (
            HAS_NUMPY and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
        ):
            self.device = torch.device("mps")
        else:
            self.device = torch.device("cpu")
        weights = ResNet18_Weights.DEFAULT
        model = resnet18(weights=weights)
        model.eval()
        self.model = torch.nn.Sequential(*list(model.children())[:-1]).to(self.device)
        self.transform = T.Compose(
            [
                T.Resize((224, 224)),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ]
        )

    def get_vectors(self, paths):
        results = []
        chunks = [paths[i : i + RESNET_BATCH_SIZE] for i in range(0, len(paths), RESNET_BATCH_SIZE)]
        for chunk in progress_iter(chunks, "Embedding", True):
            tensors = []
            for p in chunk:
                try:
                    with Image.open(p) as img:
                        tensors.append(self.transform(img.convert("RGB")))
                except Exception:
                    tensors.append(None)
            valid = [j for j, t in enumerate(tensors) if t is not None]
            if not valid:
                results.extend([None] * len(chunk))
                continue
            batch = torch.stack([tensors[j] for j in valid]).to(self.device)
            with torch.no_grad():
                vecs = self.model(batch).squeeze(-1).squeeze(-1)
                vecs = vecs / vecs.norm(dim=1, keepdim=True)
            by_index = dict(zip(valid, vecs.cpu()))
            results.extend(by_index.get(j) for j in range(len(chunk)))
        return results

    def similarity(self, a, b):
        if a is None or b is None:
            return 0.0
        return float(torch.dot(a, b))


class HashMatcher:
    name = "hash"
    threshold = HASH_SIMILARITY_THRESHOLD

    def __init__(self):
        if not HAS_PILLOW or not HAS_IMAGEHASH:
            raise RuntimeError("Pillow and ImageHash are required.")

    def get_vectors(self, paths):
        results = []
        for p in progress_iter(paths, "Hashing", True):
            try:
                with Image.open(p) as img:
                    results.append(imagehash.phash(img))
            except Exception:
                results.append(None)
        return results

    def similarity(self, a, b):
        if a is None or b is None:
            return 0.0
        return 1.0 - (a - b) / float(a.hash.size)


def build_matcher(mode, threshold=None):
    """Build the requested matcher, optionally overriding its acceptance
    threshold (must be within 0 < t <= 1). Returns None when matching is off
    or no engine is available."""
    if threshold is not None and not 0.0 < threshold <= 1.0:
        sys.exit(f"ERROR: --match-threshold must be within (0, 1], got {threshold!r}.")

    if mode == "off":
        return None

    matcher = None
    if mode == "resnet":
        if not HAS_VISION or not HAS_PILLOW:
            sys.exit(
                "ERROR: --matcher resnet requires PyTorch, torchvision and Pillow "
                "(pip install -r requirements.txt)."
            )
        matcher = ResNetMatcher()

    elif mode == "hash":
        if not HAS_PILLOW or not HAS_IMAGEHASH:
            sys.exit(
                "ERROR: --matcher hash requires Pillow and ImageHash "
                "(pip install Pillow ImageHash)."
            )
        matcher = HashMatcher()

    elif HAS_VISION and HAS_PILLOW:
        matcher = ResNetMatcher()
    elif HAS_PILLOW and HAS_IMAGEHASH:
        print("PyTorch not installed: falling back to lightweight perceptual-hash matching.")
        matcher = HashMatcher()
    else:
        print(
            "No matcher available: install torch (AI matching) or "
            "Pillow+ImageHash (lightweight matching)."
        )
        return None

    if threshold is not None:
        matcher.threshold = threshold
    return matcher


def cache_load(ref_dir):
    path = os.path.join(ref_dir, VECTOR_CACHE_NAME)
    if not HAS_NUMPY or not os.path.exists(path):
        return {}
    try:
        data = np.load(path, allow_pickle=True)
        return {
            str(k): {"mtime": float(m), "vector": v}
            for k, m, v in zip(data["paths"], data["mtimes"], data["vectors"])
        }
    except Exception:
        return {}


def cache_save(ref_dir, cache):
    if not HAS_NUMPY or not cache:
        return
    path = os.path.join(ref_dir, VECTOR_CACHE_NAME)
    try:
        keys = list(cache.keys())
        np.savez_compressed(
            path,
            paths=np.array(keys, dtype=object),
            mtimes=np.array([cache[k]["mtime"] for k in keys], dtype=float),
            vectors=np.stack([np.asarray(cache[k]["vector"]) for k in keys]),
        )
    except Exception as exc:
        print(f"WARNING: could not save reference vector cache: {exc}")


def load_reference_gallery(ref_dir, matcher, exiftool_bin, recursive=False):
    reference_db = []
    print(f"\nIndexing reference gallery ({matcher.name})...")

    images_ref = iter_image_files(ref_dir, recursive)
    if not images_ref:
        print("No reference photos found.")
        return reference_db

    # One batched ExifTool call instead of one subprocess per image.
    dated = {}
    try:
        res = subprocess.run(
            [exiftool_bin, "-j", "-DateTimeOriginal", *images_ref], capture_output=True, text=True
        )
    except OSError as exc:
        print(f"WARNING: could not read reference dates: {exc}")
        return reference_db
    if res.returncode == 0:
        try:
            entries = json.loads(res.stdout) if res.stdout.strip() else []
        except ValueError:
            entries = []
        for img_path, entry in zip(images_ref, entries):
            date_str = (entry or {}).get("DateTimeOriginal", "")
            if not date_str:
                continue
            try:
                dated[img_path] = datetime.strptime(date_str.strip(), "%Y:%m:%d %H:%M:%S")
            except ValueError:
                continue
    else:
        print("WARNING: ExifTool failed to read reference dates.")

    skipped = len(images_ref) - len(dated)
    if skipped:
        print(f"{skipped} reference photo(s) have no readable EXIF date and were skipped.")
    if not dated:
        print("No dated reference photos found.")
        return reference_db

    if isinstance(matcher, ResNetMatcher):
        cache = cache_load(ref_dir)
        stale_paths, stale_ok = [], []
        for p, dt_ref in dated.items():
            entry = cache.get(p)
            if entry and abs(entry["mtime"] - os.path.getmtime(p)) < 1e-6:
                stale_ok.append((p, dt_ref, torch.from_numpy(np.asarray(entry["vector"])).float()))
            else:
                stale_paths.append(p)
        fresh_vecs = matcher.get_vectors(stale_paths)
        for p, v in zip(stale_paths, fresh_vecs):
            if v is not None:
                cache[p] = {"mtime": os.path.getmtime(p), "vector": v.numpy()}
        cache_save(ref_dir, cache)
        vectors = {}
        for p, dt_ref, v in stale_ok:
            vectors[p] = v
        for p, v in zip(stale_paths, fresh_vecs):
            if v is not None:
                vectors[p] = v
    else:
        vectors = {p: v for p, v in zip(dated.keys(), matcher.get_vectors(list(dated.keys())))}

    for p, dt_ref in dated.items():
        v = vectors.get(p)
        if v is not None:
            reference_db.append({"path": p, "datetime": dt_ref, "vector": v})

    print(f"{len(reference_db)} reference photos indexed.")
    return reference_db
