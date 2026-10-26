#!/usr/bin/env python3
"""
generate_stimuli.py

Renames original base clips to standard format: <base_clip_id>__<category>__0.mp4
and generates audio lag / lead variants for HCI AV Synchrony Study:
Offsets: -320 ms, -160 ms, 0 ms, +160 ms, +320 ms.

Negative offset (-ms): Audio leads video (audio occurs earlier).
Positive offset (+ms): Audio lags video (audio occurs later / delayed).
0 ms: Original synchronized clip.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STIMULI_DIR = BASE_DIR / "static" / "stimuli"

# Mapping from original filename to (base_clip_id, category)
CLIP_MAPPING = {
    "discrete_p1_mug_clink.mp4": ("mug_clink", "discrete"),
    "discrete_gemini_generated_video_ec3bb76a.mp4": ("book_drop", "discrete"),
    "music_p1_guitar_strums.mp4": ("guitar_strums", "music"),
    "music_p3_drum_taps.mp4": ("drum_taps", "music"),
    "speech_p1_blue_notebook.mp4": ("blue_notebook", "speech"),
    "speech_gemini_generated_video_88655df0.mp4": ("green_sweater", "speech"),
}

OFFSETS_MS = [-320, -160, 0, 160, 320]


def check_ffmpeg() -> str:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg not found on PATH. Please ensure ffmpeg is installed.")
    return ffmpeg_path


def probe_file(file_path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def rename_original_clips() -> list[tuple[str, str, Path]]:
    """
    Renames original files to <base_clip_id>__<category>__0.mp4.
    Returns a list of (base_clip_id, category, path_to_0_clip).
    """
    base_clips = []

    for orig_name, (base_id, category) in CLIP_MAPPING.items():
        dest_name = f"{base_id}__{category}__0.mp4"
        orig_path = STIMULI_DIR / orig_name
        dest_path = STIMULI_DIR / dest_name

        if orig_path.exists():
            print(f"Renaming '{orig_name}' -> '{dest_name}'")
            orig_path.rename(dest_path)
            base_clips.append((base_id, category, dest_path))
        elif dest_path.exists():
            print(f"Base clip '{dest_name}' already exists.")
            base_clips.append((base_id, category, dest_path))
        else:
            raise FileNotFoundError(
                f"Neither '{orig_name}' nor '{dest_name}' found in {STIMULI_DIR}"
            )

    return base_clips


def generate_audio_lag_clip(base_0_path: Path, base_id: str, category: str, offset_ms: int) -> Path:
    """
    Generates a clip with audio offset in milliseconds.
    - offset_ms < 0: Audio leads video. First |offset_ms| of audio is trimmed; end padded with silence.
    - offset_ms > 0: Audio lags video. |offset_ms| silence added at start of audio.
    - offset_ms == 0: The base clip itself.
    """
    out_filename = f"{base_id}__{category}__{offset_ms}.mp4"
    out_path = STIMULI_DIR / out_filename

    if offset_ms == 0:
        return base_0_path

    print(f"Generating offset {offset_ms:+d} ms: {out_filename} ...")

    if offset_ms > 0:
        # Audio is delayed relative to video
        audio_filter = f"adelay=delays={offset_ms}:all=1,apad"
    else:
        # Audio leads video (trimmed from beginning)
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


def verify_all_stimuli() -> None:
    print("\n--- Verifying All Stimuli ---")
    all_files = sorted([f for f in STIMULI_DIR.iterdir() if f.suffix == ".mp4"])
    print(f"Found {len(all_files)} mp4 files in {STIMULI_DIR.name}:")

    from app import parse_stimulus_filename

    success_count = 0
    for f in all_files:
        parsed = parse_stimulus_filename(f.name)
        if not parsed:
            print(f"❌ FAILED parse: {f.name}")
            continue

        probe_data = probe_file(f)
        duration = float(probe_data.get("format", {}).get("duration", 0))
        streams = probe_data.get("streams", [])
        v_streams = [s for s in streams if s.get("codec_type") == "video"]
        a_streams = [s for s in streams if s.get("codec_type") == "audio"]

        if not v_streams or not a_streams:
            print(f"❌ Missing video/audio stream in {f.name}")
            continue

        print(
            f"  ✓ {f.name:<38} | clip: {parsed['base_clip_id']:<14} | cat: {parsed['category']:<8} "
            f"| offset: {parsed['offset_ms']:>4} ms | dur: {duration:.2f}s"
        )
        success_count += 1

    print(f"\nVerification summary: {success_count}/{len(all_files)} stimuli valid and verified.")
    if success_count != 30:
        print(f"⚠️ Warning: Expected 30 files, found {success_count}")
    else:
        print("🎉 All 30 experimental stimuli generated and verified successfully!")


def main():
    check_ffmpeg()
    print(f"Using stimuli directory: {STIMULI_DIR}")

    # Step 1: Rename original clips to base 0 clips
    print("\n--- Step 1: Renaming Original Videos ---")
    base_clips = rename_original_clips()

    # Step 2: Generate audio offset variants (-320, -160, +160, +320)
    print("\n--- Step 2: Generating Audio Lag / Lead Clips ---")
    for base_id, category, base_0_path in base_clips:
        for offset_ms in OFFSETS_MS:
            if offset_ms == 0:
                continue
            generate_audio_lag_clip(base_0_path, base_id, category, offset_ms)

    # Step 3: Verification
    verify_all_stimuli()


if __name__ == "__main__":
    main()
