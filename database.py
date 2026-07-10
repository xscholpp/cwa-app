"""
database.py
-----------
Creates and connects to the SQLite database (cwa.db).
Run this file directly once to set up the database, or just launch the app —
it calls initialize_database() automatically on startup.
"""

import sqlite3
import os

# The database file will be created in the same folder as this script.
DB_PATH = os.path.join(os.path.dirname(__file__), "cwa.db")


def get_connection():
    """Open and return a connection to the database."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row          # lets us access columns by name (e.g. row["title"])
    conn.execute("PRAGMA foreign_keys = ON") # enforce relationships between tables
    conn.execute("PRAGMA journal_mode = WAL") # allow concurrent readers while a write is in progress
    conn.execute("PRAGMA busy_timeout = 10000") # wait up to 10s on a lock instead of erroring immediately
    return conn


def initialize_database():
    """Create all tables if they don't already exist."""
    conn = get_connection()
    conn.executescript("""

        -- People who speak at panels
        CREATE TABLE IF NOT EXISTS speakers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT    NOT NULL,
            bio            TEXT,
            arrival_day    TEXT,
            arrival_time   TEXT,
            departure_day  TEXT,
            departure_time TEXT
        );

        -- Global schedule defaults (single row)
        CREATE TABLE IF NOT EXISTS conference_config (
            id                        INTEGER PRIMARY KEY DEFAULT 1 CHECK(id = 1),
            default_panel_duration    INTEGER DEFAULT 90,
            default_break_minutes     INTEGER DEFAULT 15,
            default_concurrent_panels INTEGER DEFAULT 3,
            default_start_time        TEXT    DEFAULT '09:00',
            default_end_time          TEXT    DEFAULT '17:30'
        );

        -- Conference days with actual dates and per-day schedule settings
        CREATE TABLE IF NOT EXISTS conference_days (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            date               TEXT    NOT NULL UNIQUE,  -- ISO: "2026-04-07"
            day_name           TEXT    NOT NULL,         -- "Monday"
            day_order          INTEGER NOT NULL,
            start_time         TEXT    DEFAULT '09:00',
            end_time           TEXT    DEFAULT '17:30',
            concurrent_panels  INTEGER DEFAULT 3,
            lunch_start        TEXT,                     -- e.g. "12:00"
            lunch_end          TEXT                      -- e.g. "13:30"
        );

        -- One row per topic per speaker (e.g. "Climate", "Trade Policy")
        CREATE TABLE IF NOT EXISTS speaker_topics (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            speaker_id INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
            topic      TEXT    NOT NULL
        );

        -- Windows of time when a speaker is available
        CREATE TABLE IF NOT EXISTS speaker_availability (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            speaker_id INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
            day        TEXT    NOT NULL,  -- e.g. "Monday"
            start_time TEXT    NOT NULL,  -- e.g. "09:00"
            end_time   TEXT    NOT NULL   -- e.g. "12:00"
        );

        -- Organizing committees that propose panels
        CREATE TABLE IF NOT EXISTS committees (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            description TEXT
        );

        -- Conference panels proposed by committees
        CREATE TABLE IF NOT EXISTS panels (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            title               TEXT    NOT NULL,
            short_description   TEXT,
            full_description    TEXT,
            status              TEXT    NOT NULL DEFAULT 'draft'
                                        CHECK(status IN ('draft', 'approved', 'to be presented')),
            priority_ranking    INTEGER,
            committee_id        INTEGER REFERENCES committees(id),
            track_id            INTEGER REFERENCES tracks(id),
            committee_notes     TEXT,  -- visible to committee members and admins
            presentation_notes  TEXT,  -- day-of logistics notes, visible to committee members and admins
            admin_notes         TEXT   -- internal, admin-only to read or edit
        );

        -- Topics a panel is meant to cover (used to help match relevant speakers)
        CREATE TABLE IF NOT EXISTS panel_topics (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            panel_id INTEGER NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
            topic    TEXT    NOT NULL
        );

        -- Which speakers are on which panels, and in what role
        CREATE TABLE IF NOT EXISTS panel_speakers (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            panel_id         INTEGER NOT NULL REFERENCES panels(id)   ON DELETE CASCADE,
            speaker_id       INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
            role             TEXT    NOT NULL CHECK(role IN ('panelist', 'alternate')),
            priority_ranking INTEGER  -- order preference within the panel
        );

        -- Which of a speaker's own topics are relevant to their spot on a given panel
        CREATE TABLE IF NOT EXISTS panel_speaker_topics (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            panel_speaker_id INTEGER NOT NULL REFERENCES panel_speakers(id) ON DELETE CASCADE,
            speaker_topic_id INTEGER NOT NULL REFERENCES speaker_topics(id) ON DELETE CASCADE
        );

        -- Physical rooms where panels take place
        CREATE TABLE IF NOT EXISTS rooms (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT    NOT NULL UNIQUE,
            capacity INTEGER
        );

        -- Named conference tracks (e.g. "Global Economy", "Environment")
        CREATE TABLE IF NOT EXISTS tracks (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        -- The final schedule: when and where each panel happens
        CREATE TABLE IF NOT EXISTS schedule (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            panel_id         INTEGER NOT NULL UNIQUE REFERENCES panels(id),   -- one slot per panel
            room_id          INTEGER REFERENCES rooms(id),
            track_id         INTEGER REFERENCES tracks(id),
            moderator_id     INTEGER REFERENCES speakers(id),
            date             TEXT    NOT NULL,  -- ISO format: "2026-04-07"
            start_time       TEXT    NOT NULL,  -- "14:00"
            duration_minutes INTEGER NOT NULL
        );

        -- User accounts with per-user permission flags
        CREATE TABLE IF NOT EXISTS users (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            username                  TEXT    NOT NULL UNIQUE,
            display_name              TEXT    NOT NULL,
            password_hash             TEXT    NOT NULL,
            password_salt             TEXT    NOT NULL,
            can_manage_users          INTEGER NOT NULL DEFAULT 0,
            can_manage_admin_settings INTEGER NOT NULL DEFAULT 0,
            can_view_all_panels       INTEGER NOT NULL DEFAULT 0,
            can_edit_all_panels       INTEGER NOT NULL DEFAULT 0,
            can_manage_speakers       INTEGER NOT NULL DEFAULT 0,
            can_manage_schedule       INTEGER NOT NULL DEFAULT 0,
            committee_id              INTEGER REFERENCES committees(id)
        );

    """)

    # Add arrival/departure columns to existing databases that predate this change
    existing = [row[1] for row in conn.execute("PRAGMA table_info(speakers)").fetchall()]
    for col in ["arrival_day", "arrival_time", "departure_day", "departure_time"]:
        if col not in existing:
            conn.execute(f"ALTER TABLE speakers ADD COLUMN {col} TEXT")

    # Add new columns to conference_days for existing databases
    existing_day_cols = [r[1] for r in conn.execute("PRAGMA table_info(conference_days)").fetchall()]
    day_col_upgrades = [
        ("date",              "TEXT"),
        ("start_time",        "TEXT    DEFAULT '09:00'"),
        ("end_time",          "TEXT    DEFAULT '17:30'"),
        ("concurrent_panels", "INTEGER DEFAULT 3"),
        ("lunch_start",       "TEXT"),
        ("lunch_end",         "TEXT"),
    ]
    for col, definition in day_col_upgrades:
        if col not in existing_day_cols:
            conn.execute(f"ALTER TABLE conference_days ADD COLUMN {col} {definition}")

    # Add new columns to panels for existing databases
    existing_panel_cols = [r[1] for r in conn.execute("PRAGMA table_info(panels)").fetchall()]
    panel_col_upgrades = [
        ("track_id",           "INTEGER REFERENCES tracks(id)"),
        ("committee_notes",    "TEXT"),
        ("presentation_notes", "TEXT"),
        ("admin_notes",        "TEXT"),
    ]
    for col, definition in panel_col_upgrades:
        if col not in existing_panel_cols:
            conn.execute(f"ALTER TABLE panels ADD COLUMN {col} {definition}")

    # Seed a single conference_config row if none exists
    if conn.execute("SELECT COUNT(*) FROM conference_config").fetchone()[0] == 0:
        conn.execute("INSERT INTO conference_config (id) VALUES (1)")

    conn.commit()
    conn.close()


# If you run this file directly (python database.py), it sets up the database
# and prints a confirmation message.
if __name__ == "__main__":
    initialize_database()
    print(f"Database ready at: {DB_PATH}")
