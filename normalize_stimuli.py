#!/usr/bin/env python3
"""
normalize_stimuli.py

Performs EBU R128 audio loudness normalization (target -18 LUFS) across all
experimental clips and saves the normalized suite in static/stimuli_normalized/
so you can preview and compare before replacing your main stimuli.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_STIMULI_DIR = BASE_DIR / "static" / "stimuli"
OUT_STIMULI_DIR = BASE_DIR / "static" / "stimuli_normalized"

OFFSETS_MS = [-320, -160, 0, 160, 320]

BASE_CLIPS = [
    ("mug_clink", "discrete"),
    ("book_drop", "discrete"),
    ("guitar_strums", "music"),
    ("drum_taps", "music"),
    ("blue_notebook", "speech"),
    ("green_sweater", "speech"),
]


def check_ffmpeg() -> str:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg not found on PATH.")
    return ffmpeg_path


def normalize_base_clip(base_id: str, category: str) -> Path:
    src_file = SRC_STIMULI_DIR / f"{base_id}__{category}__0.mp4"
    out_file = OUT_STIMULI_DIR / f"{base_id}__{category}__0.mp4"

    if not src_file.exists():
        raise FileNotFoundError(f"Source file not found: {src_file}")

    print(f"Normalizing loudness (EBU R128 -18 LUFS): {src_file.name} -> {out_file.name}")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src_file),
        "-c:v", "copy",
        "-af", "loudnorm=I=-18:TP=-1.5:LRA=11",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_file),
    ]

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return out_file


def generate_offset_clip(base_0_path: Path, base_id: str, category: str, offset_ms: int) -> Path:
    out_filename = f"{base_id}__{category}__{offset_ms}.mp4"
    out_path = OUT_STIMULI_DIR / out_filename

    if offset_ms == 0:
        return base_0_path

    if offset_ms > 0:
        audio_filter = f"adelay=delays={offset_ms}:all=1,apad"
    else:
        trim_start_sec = abs(offset_ms) / 1000.0
        audio_filter = f"atrim=start={trim_start_sec:.4f},asetpts=PTS-STARTPTS,apad"

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(base_0_path),
        "-c:v", "copy",
        "-af", audio_filter,
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return out_path


def measure_volumes(directory: Path):
    print(f"\n--- Loudness Measurements for 0 ms Clips in {directory.name} ---")
    for base_id, category in BASE_CLIPS:
        p = directory / f"{base_id}__{category}__0.mp4"
        res = subprocess.run([
            "ffmpeg", "-i", str(p), "-af", "volumedetect", "-vn", "-sn", "-dn", "-f", "null", "/dev/null"
        ], capture_output=True, text=True)
        mean_v, max_v = "N/A", "N/A"
        for line in res.stderr.split("\n"):
            if "mean_volume" in line:
                mean_v = line.split(":")[-1].strip()
            if "max_volume" in line:
                max_v = line.split(":")[-1].strip()
        print(f"  {p.name:<32} | mean: {mean_v:<10} | max: {max_v}")


def main():
    check_ffmpeg()
    OUT_STIMULI_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Target directory: {OUT_STIMULI_DIR}\n")

    # Step 1: Normalize base clips
    print("--- Step 1: Normalizing 0 ms Base Clips ---")
    norm_base_clips = []
    for base_id, category in BASE_CLIPS:
        out_base_path = normalize_base_clip(base_id, category)
        norm_base_clips.append((base_id, category, out_base_path))

    # Step 2: Generate offset variants
    print("\n--- Step 2: Generating Offset Variants ---")
    for base_id, category, base_0_path in norm_base_clips:
        for offset in OFFSETS_MS:
            if offset == 0:
                continue
            generate_offset_clip(base_0_path, base_id, category, offset)

    # Step 3: Compare volume metrics
    print("\n=== Volume Comparison ===")
    print("\n[Original static/stimuli/]")
    measure_volumes(SRC_STIMULI_DIR)
    print("\n[Normalized static/stimuli_normalized/]")
    measure_volumes(OUT_STIMULI_DIR)

    all_norm_files = list(OUT_STIMULI_DIR.glob("*.mp4"))
    print(f"\nCompleted! Generated {len(all_norm_files)} normalized stimulus files in {OUT_STIMULI_DIR}.")


if __name__ == "__main__":
    main()
