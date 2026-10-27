from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    psycopg2 = None

BASE_DIR = Path(__file__).resolve().parent
STIMULI_DIR = BASE_DIR / "static" / "stimuli"
PRACTICE_DIR = BASE_DIR / "static" / "practice"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "responses.sqlite3"
CSV_PATH = DATA_DIR / "responses.csv"

# Database URL from environment (configured in Render Dashboard)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


def get_pg_conn():
    """
    Connect to PostgreSQL if reachable.
    On Render, this connects to the persistent cloud Postgres database.
    On local machines where the internal host is unreachable, returns None gracefully.
    """
    if not HAS_PSYCOPG2 or not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        return conn
    except Exception:
        return None


def parse_stimulus_filename(filename: str):
    """
    Expected filename convention:
        <base_clip_id>__<category>__<offset_ms>.<ext>

    Examples:
        impact01__impact__0.mp4
        impact01__impact__160.mp4
        impact01__impact__-160.mp4
        speech03__speech__320.webm
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
    # 1. Initialize SQLite (local fallback/mirror)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sqlite_conn = sqlite3.connect(DB_PATH)
    sqlite_conn.execute("""
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
    sqlite_conn.execute("""
        CREATE TABLE IF NOT EXISTS debrief (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_timestamp_utc TEXT NOT NULL,
            participant_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            feedback TEXT NOT NULL
        )
        """)
    sqlite_conn.execute("""
        CREATE TABLE IF NOT EXISTS service_heartbeat (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_ping_utc TEXT NOT NULL,
            ping_count INTEGER NOT NULL DEFAULT 0
        )
        """)
    sqlite_conn.commit()
    sqlite_conn.close()

    # 2. Initialize PostgreSQL tables if reachable
    pg_conn = get_pg_conn()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS responses (
                        id SERIAL PRIMARY KEY,
                        server_timestamp_utc TEXT NOT NULL,
                        participant_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        trial_index INTEGER,
                        base_clip_id TEXT,
                        category TEXT,
                        offset_ms INTEGER,
                        synchrony_response TEXT,
                        naturalness_rating INTEGER,
                        response_time_sync_ms DOUBLE PRECISION,
                        response_time_naturalness_ms DOUBLE PRECISION,
                        video_playback_ms DOUBLE PRECISION,
                        browser_user_agent TEXT,
                        viewport_width INTEGER,
                        viewport_height INTEGER,
                        is_practice INTEGER DEFAULT 0,
                        playback_error INTEGER DEFAULT 0
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS debrief (
                        id SERIAL PRIMARY KEY,
                        server_timestamp_utc TEXT NOT NULL,
                        participant_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        feedback TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS service_heartbeat (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        last_ping_utc TEXT NOT NULL,
                        ping_count INTEGER NOT NULL DEFAULT 0
                    )
                """)
            pg_conn.commit()
            app.logger.info("PostgreSQL tables successfully verified/created!")
        except Exception as e:
            app.logger.error(f"PostgreSQL table initialization error: {e}")
            pg_conn.rollback()
        finally:
            pg_conn.close()


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
    if not pid:
        return jsonify({"completed": 0, "practice_done": False})

    # Check PostgreSQL first
    pg_conn = get_pg_conn()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM responses WHERE participant_id = %s AND is_practice = 0",
                    (pid,),
                )
                main_count = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM responses WHERE participant_id = %s AND is_practice = 1",
                    (pid,),
                )
                prac_row = cur.fetchone()
                practice_done = (prac_row[0] > 0) if prac_row else False
            pg_conn.close()
            return jsonify({"completed": main_count, "practice_done": practice_done})
        except Exception as e:
            app.logger.warning(f"PG participant_progress failed: {e}")
            try:
                pg_conn.close()
            except Exception:
                pass

    # Fallback to SQLite
    if not DB_PATH.exists():
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

    # 1. Save to PostgreSQL (Permanent Cloud DB)
    pg_saved = False
    pg_conn = get_pg_conn()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cols = ", ".join(CSV_FIELDS)
                placeholders = ", ".join(["%s"] * len(CSV_FIELDS))
                cur.execute(
                    f"INSERT INTO responses ({cols}) VALUES ({placeholders})",
                    tuple(row[field] for field in CSV_FIELDS),
                )
            pg_conn.commit()
            pg_saved = True
        except Exception as e:
            app.logger.error(f"PostgreSQL insert failed: {e}")
            pg_conn.rollback()
        finally:
            pg_conn.close()

    # 2. Mirror into local SQLite
    try:
        conn = sqlite3.connect(DB_PATH)
        cols = ", ".join(CSV_FIELDS)
        placeholders = ", ".join(["?"] * len(CSV_FIELDS))
        conn.execute(
            f"INSERT INTO responses ({cols}) VALUES ({placeholders})",
            tuple(row[field] for field in CSV_FIELDS),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f"SQLite insert failed: {e}")

    # 3. Mirror into local CSV file
    try:
        csv_exists = CSV_PATH.exists()
        with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if not csv_exists:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        app.logger.warning(f"CSV append failed: {e}")

    return jsonify({"ok": True, "saved_pg": pg_saved})


@app.post("/api/debrief")
def save_debrief():
    payload = request.get_json(force=True, silent=False)
    timestamp = datetime.now(timezone.utc).isoformat()
    participant_id = payload.get("participant_id", "")
    session_id = payload.get("session_id", "")
    feedback = payload.get("feedback", "").strip()

    if not feedback:
        return jsonify({"ok": True})

    # Save to PostgreSQL
    pg_conn = get_pg_conn()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO debrief (server_timestamp_utc, participant_id, session_id, feedback) VALUES (%s, %s, %s, %s)",
                    (timestamp, participant_id, session_id, feedback),
                )
            pg_conn.commit()
        except Exception as e:
            app.logger.error(f"PostgreSQL debrief insert failed: {e}")
            pg_conn.rollback()
        finally:
            pg_conn.close()

    # Save to SQLite
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO debrief (server_timestamp_utc, participant_id, session_id, feedback) VALUES (?, ?, ?, ?)",
            (timestamp, participant_id, session_id, feedback),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f"SQLite debrief insert failed: {e}")

    # Save to debrief CSV
    try:
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
    except Exception as e:
        app.logger.warning(f"CSV debrief append failed: {e}")

    return jsonify({"ok": True})


@app.get("/api/health")
def health():
    pg_ok = False
    pg_conn = get_pg_conn()
    if pg_conn:
        pg_ok = True
        pg_conn.close()

    return jsonify({
        "ok": True,
        "stimuli": len(discover_stimuli()),
        "postgres_connected": pg_ok,
    })


@app.get("/api/keepalive")
def keepalive():
    """Update one heartbeat row when called by an external uptime monitor."""
    timestamp = datetime.now(timezone.utc).isoformat()
    last_ping = timestamp
    count = 1

    # Update PostgreSQL heartbeat if available
    pg_conn = get_pg_conn()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO service_heartbeat (id, last_ping_utc, ping_count)
                    VALUES (1, %s, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        last_ping_utc = EXCLUDED.last_ping_utc,
                        ping_count = service_heartbeat.ping_count + 1
                    """,
                    (timestamp,),
                )
                cur.execute("SELECT last_ping_utc, ping_count FROM service_heartbeat WHERE id = 1")
                row = cur.fetchone()
                if row:
                    last_ping, count = row[0], row[1]
            pg_conn.commit()
        except Exception as e:
            app.logger.warning(f"PG keepalive failed: {e}")
            pg_conn.rollback()
        finally:
            pg_conn.close()

    # Mirror in SQLite
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
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
        if not pg_conn:
            s_row = conn.execute(
                "SELECT last_ping_utc, ping_count FROM service_heartbeat WHERE id = 1"
            ).fetchone()
            if s_row:
                last_ping, count = s_row[0], s_row[1]
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f"SQLite keepalive failed: {e}")

    response = jsonify({"ok": True, "last_ping_utc": last_ping, "ping_count": count})
    response.headers["Cache-Control"] = "no-store"
    return response


# ==========================================
# ADMIN & DATA EXPORT ROUTES
# ==========================================

@app.route("/admin")
def admin_dashboard():
    """Researcher dashboard displaying real-time database metrics and export actions."""
    db_type = "sqlite"
    total_responses = 0
    unique_participants = 0
    debrief_count = 0
    ping_count = 0
    recent_rows = []

    pg_conn = get_pg_conn()
    if pg_conn:
        try:
            db_type = "postgres"
            with pg_conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM responses")
                total_responses = cur.fetchone()[0]

                cur.execute("SELECT COUNT(DISTINCT participant_id) FROM responses")
                unique_participants = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM debrief")
                debrief_count = cur.fetchone()[0]

                cur.execute("SELECT ping_count FROM service_heartbeat WHERE id = 1")
                h_row = cur.fetchone()
                if h_row:
                    ping_count = h_row[0]

                cols = ", ".join(CSV_FIELDS)
                cur.execute(f"SELECT {cols} FROM responses ORDER BY id DESC LIMIT 15")
                recent_rows = cur.fetchall()
        except Exception as e:
            app.logger.warning(f"Error querying PG for admin dashboard: {e}")
        finally:
            pg_conn.close()

    if db_type == "sqlite" and DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM responses")
            total_responses = cur.fetchone()[0]

            cur.execute("SELECT COUNT(DISTINCT participant_id) FROM responses")
            unique_participants = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM debrief")
            debrief_count = cur.fetchone()[0]

            cur.execute("SELECT ping_count FROM service_heartbeat WHERE id = 1")
            h_row = cur.fetchone()
            if h_row:
                ping_count = h_row[0]

            cols = ", ".join(CSV_FIELDS)
            cur.execute(f"SELECT {cols} FROM responses ORDER BY id DESC LIMIT 15")
            recent_rows = cur.fetchall()
            conn.close()
        except Exception as e:
            app.logger.warning(f"Error querying SQLite for admin dashboard: {e}")

    return render_template(
        "admin.html",
        db_type=db_type,
        total_responses=total_responses,
        unique_participants=unique_participants,
        debrief_count=debrief_count,
        ping_count=ping_count,
        recent_rows=recent_rows,
        sqlite_exists=DB_PATH.exists(),
    )


@app.get("/admin/download")
def download_csv():
    """
    Dynamically stream responses CSV from PostgreSQL (or SQLite).
    Never returns 404: if empty, returns CSV with full headers ready for analysis.
    """
    rows = []

    # 1. Fetch from PostgreSQL first
    pg_conn = get_pg_conn()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cols = ", ".join(CSV_FIELDS)
                cur.execute(f"SELECT {cols} FROM responses ORDER BY id ASC")
                rows = cur.fetchall()
        except Exception as e:
            app.logger.warning(f"PG download_csv fetch error: {e}")
        finally:
            pg_conn.close()

    # 2. If Postgres had no rows or was unreachable, fallback to SQLite
    if not rows and DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cols = ", ".join(CSV_FIELDS)
            cur.execute(f"SELECT {cols} FROM responses ORDER BY id ASC")
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            app.logger.warning(f"SQLite download_csv fetch error: {e}")

    # Build CSV in-memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_FIELDS)
    for r in rows:
        writer.writerow(r)

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=responses.csv"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@app.get("/admin/download-debrief")
def download_debrief():
    """Dynamically stream debrief qualitative feedback CSV."""
    fields = ["server_timestamp_utc", "participant_id", "session_id", "feedback"]
    rows = []

    pg_conn = get_pg_conn()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cols = ", ".join(fields)
                cur.execute(f"SELECT {cols} FROM debrief ORDER BY id ASC")
                rows = cur.fetchall()
        except Exception as e:
            app.logger.warning(f"PG debrief download fetch error: {e}")
        finally:
            pg_conn.close()

    if not rows and DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cols = ", ".join(fields)
            cur.execute(f"SELECT {cols} FROM debrief ORDER BY id ASC")
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            app.logger.warning(f"SQLite debrief download fetch error: {e}")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(fields)
    for r in rows:
        writer.writerow(r)

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=debrief.csv"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@app.get("/admin/download-db")
def download_db():
    """Download the raw SQLite responses.sqlite3 file if available."""
    if not DB_PATH.exists():
        return ("No local SQLite database file exists.", 404)
    return send_from_directory(DATA_DIR, DB_PATH.name, as_attachment=True)


# Initialize databases at startup
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
