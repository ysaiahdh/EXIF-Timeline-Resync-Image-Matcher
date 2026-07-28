#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
EXIF Timeline Resync & Image Matcher
Restores EXIF timestamps from folder names, schedules, and visual matching.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from PIL import Image

try:
    import torch
    import torchvision.transforms as T
    from torchvision.models import resnet18, ResNet18_Weights
    HAS_VISION = True
except ImportError:
    HAS_VISION = False


# --- Feature Extractor ---

class ImageFeatureExtractor:
    def __init__(self):
        if not HAS_VISION:
            raise RuntimeError("PyTorch and torchvision are required for image recognition.")
        weights = ResNet18_Weights.DEFAULT
        self.model = resnet18(weights=weights)
        self.model.eval()
        self.model = torch.nn.Sequential(*list(self.model.children())[:-1])
        self.transform = T.Compose([
            T.Resize((224, 224)),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def get_vector(self, img_path):
        try:
            img = Image.open(img_path).convert('RGB')
            tensor = self.transform(img).unsqueeze(0)
            with torch.no_grad():
                vector = self.model(tensor).squeeze()
            return vector / torch.norm(vector)
        except Exception:
            return None


def cosine_similarity(v1, v2):
    if v1 is None or v2 is None:
        return 0.0
    return float(torch.dot(v1, v2))


# --- Reference Gallery ---

def load_reference_gallery(ref_dir, extractor, exiftool_bin):
    reference_db = []
    print("\nIndexing reference gallery...")

    images_ref = [os.path.join(ref_dir, f) for f in os.listdir(ref_dir)
                  if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

    for img_path in images_ref:
        cmd = [exiftool_bin, "-DateTimeOriginal", "-s3", img_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        date_str = res.stdout.strip()

        if date_str:
            try:
                dt_ref = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                vector = extractor.get_vector(img_path)
                if vector is not None:
                    reference_db.append({
                        "path": img_path,
                        "datetime": dt_ref,
                        "vector": vector
                    })
            except ValueError:
                continue

    print(f"{len(reference_db)} reference photos indexed.")
    return reference_db


# --- Core Logic ---

def find_exiftool():
    path = shutil.which("exiftool") or shutil.which("exiftool(-k)")
    if not path:
        local_exe = os.path.join(os.getcwd(), "exiftool.exe")
        local_exe_k = os.path.join(os.getcwd(), "exiftool(-k).exe")
        if os.path.exists(local_exe):
            path = local_exe
        elif os.path.exists(local_exe_k):
            path = local_exe_k
    return path


def load_config(config_path):
    if not os.path.exists(config_path):
        return 2026, 180, {}
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("year", 2026), data.get("default_interval_sec", 180), data.get("schedule", {})


def parse_folder_date(folder_name):
    match = re.search(r'(\d{2})-(\d{2})(?:\s+(\d{1,2})h(\d{2}))?', folder_name)
    if match:
        day, month, hour, minute = match.groups()
        day_key = f"{day}-{month}"
        blog_hour = int(hour) if hour else 12
        blog_min = int(minute) if minute else 0
        return day_key, int(day), int(month), blog_hour, blog_min
    return None, None, None, None, None


def determine_schedule(day_key, folder_name, description, h_blog, m_blog, schedule):
    folder_lower = folder_name.lower()
    desc_lower = description.lower()

    if "boom" in folder_lower or "soiree" in desc_lower or "veillee" in desc_lower:
        return 20, 30, 90
    if "bis" in folder_lower or "animaux" in folder_lower or "apres-midi" in folder_lower:
        return 15, 0, 120
    if day_key in schedule:
        h_str, m_str = schedule[day_key].split(":")
        return int(h_str), int(m_str), 210
    return h_blog, m_blog, 180


def process_photos(root_dir, config_path, ref_dir=None):
    exiftool_bin = find_exiftool()
    if not exiftool_bin:
        print("ERROR: ExifTool not found on your system.")
        return

    year, default_interval, schedule = load_config(config_path)

    extractor = None
    ref_db = []
    if ref_dir:
        if not HAS_VISION:
            print("PyTorch/Torchvision not found. Running without image matching.")
            print("To enable AI matching: pip install -r requirements.txt")
        else:
            extractor = ImageFeatureExtractor()
            ref_db = load_reference_gallery(ref_dir, extractor, exiftool_bin)

    folders = [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]
    folders.sort()

    total_images = 0

    for folder in folders:
        path_folder = os.path.join(root_dir, folder)
        day_key, day, month, h_blog, m_blog = parse_folder_date(folder)

        if not day_key:
            continue

        description = ""
        txt_files = [f for f in os.listdir(path_folder) if f.endswith(".txt")]
        if txt_files:
            try:
                with open(os.path.join(path_folder, txt_files[0]), "r", encoding="utf-8", errors="ignore") as f:
                    description = f.read().strip()
            except Exception:
                pass

        h_start, m_start, step = determine_schedule(day_key, folder, description, h_blog, m_blog, schedule)
        base_dt = datetime(year, month, day, h_start, m_start)

        images = [f for f in os.listdir(path_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        images.sort()

        if not images:
            continue

        print(f"\nFolder: {folder}")

        current_dt = base_dt
        for img in images:
            img_path = os.path.join(path_folder, img)
            target_dt = current_dt

            if extractor and ref_db:
                vector_img = extractor.get_vector(img_path)
                if vector_img is not None:
                    best_match = None
                    best_score = 0.0

                    for ref in ref_db:
                        sim = cosine_similarity(vector_img, ref["vector"])
                        if sim > best_score:
                            best_score = sim
                            best_match = ref

                    if best_score >= 0.85 and best_match:
                        if abs((best_match["datetime"].date() - base_dt.date()).days) <= 1:
                            target_dt = best_match["datetime"]
                            print(f"  Visual match ({best_score*100:.1f}%) -> {target_dt.strftime('%H:%M:%S')}")

            date_str = target_dt.strftime("%Y:%m:%d %H:%M:%S")

            cmd = [
                exiftool_bin,
                f"-AllDates={date_str}",
                f"-ImageDescription={description}",
                "-overwrite_original",
                img_path
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            current_dt += timedelta(seconds=step)

        total_images += len(images)
        print(f"  {len(images)} photos processed.")

    print(f"\nDone. {total_images} photos updated total.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recalibrate EXIF metadata with image matching.")
    parser.add_argument("-d", "--directory", default=".", help="Path to folder containing undated photos.")
    parser.add_argument("-c", "--config", default="config_planning.json", help="Path to JSON config file.")
    parser.add_argument("-r", "--reference", default=None, help="Path to folder of dated reference photos.")
    args = parser.parse_args()

    process_photos(args.directory, args.config, args.reference)
