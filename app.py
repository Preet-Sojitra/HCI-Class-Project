from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
STIMULI_DIR = BASE_DIR / "static" / "stimuli"
PRACTICE_DIR = BASE_DIR / "static" / "practice"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "responses.sqlite3"
CSV_PATH = DATA_DIR / "responses.csv"

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


def parse_stimulus_filename(filename: str):
    """
    Expected filename convention:
        <base_clip_id>__<category>__<offset_ms>.<ext>

    Examples:
        impact01__impact__0.mp4
        impact01__impact__160.mp4
        impact01__impact__-160.mp4
        speech03__speech__320.webm

    The category can contain letters, numbers, hyphens, or underscores.
    """
    stem = Path(filename).stem
    parts = stem.rsplit("__", 2)
    if len(parts) != 3:
        return None

    base_clip_id, category, offset_text = parts
    if not re.fullmatch(r"-?\d+", offset_text.strip()):
        return None

    return {
        "base_clip_id": base_clip_id.strip(),
        "category": category.strip(),
        "offset_ms": int(offset_text.strip()),
        "filename": filename,
        "url": f"/static/stimuli/{filename}",
    }


def discover_stimuli():
    items = []
    if not STIMULI_DIR.exists():
        return items

    for path in sorted(STIMULI_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
            continue
        parsed = parse_stimulus_filename(path.name)
        if parsed:
            items.append(parsed)
    return items


def discover_practice():
    items = []
    if not PRACTICE_DIR.exists():
        return items

    for path in sorted(PRACTICE_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS:
            items.append(
                {
                    "filename": path.name,
                    "url": f"/static/practice/{path.name}",
                }
            )
    return items


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_timestamp_utc TEXT NOT NULL,
            participant_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            trial_index INTEGER,
            base_clip_id TEXT,
            category TEXT,
            offset_ms INTEGER,
            synchrony_response TEXT,
            naturalness_rating INTEGER,
            response_time_sync_ms REAL,
            response_time_naturalness_ms REAL,
            video_playback_ms REAL,
            browser_user_agent TEXT,
            viewport_width INTEGER,
            viewport_height INTEGER,
            is_practice INTEGER DEFAULT 0,
            playback_error INTEGER DEFAULT 0
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS debrief (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_timestamp_utc TEXT NOT NULL,
            participant_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            feedback TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_heartbeat (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_ping_utc TEXT NOT NULL,
            ping_count INTEGER NOT NULL DEFAULT 0
        )
        """)
    conn.commit()
    conn.close()


CSV_FIELDS = [
    "server_timestamp_utc",
    "participant_id",
    "session_id",
    "trial_index",
    "base_clip_id",
    "category",
    "offset_ms",
    "synchrony_response",
    "naturalness_rating",
    "response_time_sync_ms",
    "response_time_naturalness_ms",
    "video_playback_ms",
    "browser_user_agent",
    "viewport_width",
    "viewport_height",
    "is_practice",
    "playback_error",
]


@app.route("/")
def index():
    return render_template("index.html", test_mode=False)


@app.route("/test")
def test_mode():
    """Researcher-only staging view that runs one trial without saving it."""
    return render_template("index.html", test_mode=True)


@app.route("/preview")
def preview():
    return render_template("preview.html")


@app.get("/api/participant-progress")
def participant_progress():
    pid = request.args.get("participant_id", "").strip()
    if not pid or not DB_PATH.exists():
        return jsonify({"completed": 0, "practice_done": False})
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM responses WHERE participant_id = ? AND is_practice = 0",
            (pid,),
        )
        row = cur.fetchone()
        main_count = row[0] if row else 0

        cur.execute(
            "SELECT COUNT(*) FROM responses WHERE participant_id = ? AND is_practice = 1",
            (pid,),
        )
        prac_row = cur.fetchone()
        practice_done = (prac_row[0] > 0) if prac_row else False
        conn.close()
        return jsonify({"completed": main_count, "practice_done": practice_done})
    except Exception as e:
        return jsonify({"completed": 0, "practice_done": False, "error": str(e)})


@app.get("/api/config")
def config():
    stimuli = discover_stimuli()
    practice = discover_practice()
    categories = sorted({x["category"] for x in stimuli})
    offsets = sorted({x["offset_ms"] for x in stimuli})
    base_clips = sorted({x["base_clip_id"] for x in stimuli})

    return jsonify(
        {
            "study_title": "Perceptual Tolerance for Audio-Visual Misalignment Study",
            "stimuli": stimuli,
            "practice": practice,
            "summary": {
                "stimulus_count": len(stimuli),
                "practice_count": len(practice),
                "categories": categories,
                "offsets_ms": offsets,
                "base_clip_count": len(base_clips),
            },
        }
    )


@app.post("/api/save")
def save_response():
    payload = request.get_json(force=True, silent=False)
    payload["server_timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    payload["browser_user_agent"] = request.headers.get("User-Agent", "")

    row = {field: payload.get(field) for field in CSV_FIELDS}

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO responses (
            server_timestamp_utc,
            participant_id,
            session_id,
            trial_index,
            base_clip_id,
            category,
            offset_ms,
            synchrony_response,
            naturalness_rating,
            response_time_sync_ms,
            response_time_naturalness_ms,
            video_playback_ms,
            browser_user_agent,
            viewport_width,
            viewport_height,
            is_practice,
            playback_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(row[field] for field in CSV_FIELDS),
    )
    conn.commit()
    conn.close()

    csv_exists = CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not csv_exists:
            writer.writeheader()
        writer.writerow(row)

    return jsonify({"ok": True})


@app.post("/api/debrief")
def save_debrief():
    payload = request.get_json(force=True, silent=False)
    timestamp = datetime.now(timezone.utc).isoformat()
    participant_id = payload.get("participant_id", "")
    session_id = payload.get("session_id", "")
    feedback = payload.get("feedback", "").strip()

    if not feedback:
        return jsonify({"ok": True})

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO debrief (server_timestamp_utc, participant_id, session_id, feedback) VALUES (?, ?, ?, ?)",
        (timestamp, participant_id, session_id, feedback),
    )
    conn.commit()
    conn.close()

    debrief_csv = DATA_DIR / "debrief.csv"
    csv_exists = debrief_csv.exists()
    with debrief_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["server_timestamp_utc", "participant_id", "session_id", "feedback"]
        )
        if not csv_exists:
            writer.writeheader()
        writer.writerow(
            {
                "server_timestamp_utc": timestamp,
                "participant_id": participant_id,
                "session_id": session_id,
                "feedback": feedback,
            }
        )

    return jsonify({"ok": True})


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "stimuli": len(discover_stimuli())})


@app.get("/api/keepalive")
def keepalive():
    """Update one heartbeat row when called by an external uptime monitor."""
    timestamp = datetime.now(timezone.utc).isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_heartbeat (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_ping_utc TEXT NOT NULL,
            ping_count INTEGER NOT NULL DEFAULT 0
        )
        """)
    conn.execute(
        """
        INSERT INTO service_heartbeat (id, last_ping_utc, ping_count)
        VALUES (1, ?, 1)
        ON CONFLICT(id) DO UPDATE SET
            last_ping_utc = excluded.last_ping_utc,
            ping_count = service_heartbeat.ping_count + 1
        """,
        (timestamp,),
    )
    row = conn.execute(
        "SELECT last_ping_utc, ping_count FROM service_heartbeat WHERE id = 1"
    ).fetchone()
    conn.commit()
    conn.close()

    response = jsonify({"ok": True, "last_ping_utc": row[0], "ping_count": row[1]})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/admin/download")
def download_csv():
    if not CSV_PATH.exists():
        return ("No response CSV exists yet.", 404)
    return send_from_directory(DATA_DIR, CSV_PATH.name, as_attachment=True)


# Ensure database is initialized at startup (for gunicorn or dev server)
init_db()

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)

