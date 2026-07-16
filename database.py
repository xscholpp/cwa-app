"""
database.py
-----------
Creates and connects to the database. Uses local SQLite (cwa.db) by default;
if TURSO_DATABASE_URL is set in Streamlit secrets, connects to that hosted
libSQL/Turso database instead so data survives redeploys on Streamlit Cloud
(whose local disk is ephemeral).

Run this file directly once to set up the database, or just launch the app —
it calls initialize_database() automatically on startup.
"""

import sqlite3
import os

import streamlit as st

# The database file will be created in the same folder as this script.
DB_PATH = os.path.join(os.path.dirname(__file__), "cwa.db")


def _turso_config():
    """Returns (url, auth_token) if Turso is configured via secrets, else None.
    Accessing st.secrets at all raises if no secrets.toml exists anywhere, so
    this has to be defensive rather than just checking for a missing key.
    """
    try:
        url = st.secrets.get("TURSO_DATABASE_URL")
    except Exception:
        return None
    if not url:
        return None
    # libsql_client's websocket transport (the "libsql://" / "wss://" scheme)
    # fails the handshake against Turso's current server (confirmed: a valid
    # token still gets a 400 on the websocket upgrade). Plain HTTPS against
    # the same host works fine and is all this app needs (no persistent
    # connection required), so rewrite the scheme rather than require users
    # to hand-edit the URL Turso's dashboard/CLI gives them.
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return url, st.secrets.get("TURSO_AUTH_TOKEN")


class _LibsqlRow:
    """Makes a libsql_client Row behave like sqlite3.Row for our call sites:
    both row["col"] and row[0] indexing, iteration, and dict(row) (sqlite3.Row
    supports dict() via .keys(), which libsql_client's Row doesn't have)."""
    __slots__ = ("_row",)

    def __init__(self, row):
        self._row = row

    def __getitem__(self, key):
        return self._row[key]

    def keys(self):
        return self._row.asdict().keys()

    def __iter__(self):
        return iter(self._row)

    def __len__(self):
        return len(self._row)


class _LibsqlCursorShim:
    """Makes a libsql_client ResultSet behave like a sqlite3 cursor."""

    def __init__(self, result_set):
        self._rs = result_set
        self.lastrowid = result_set.last_insert_rowid

    def fetchone(self):
        return _LibsqlRow(self._rs.rows[0]) if self._rs.rows else None

    def fetchall(self):
        return [_LibsqlRow(r) for r in self._rs.rows]


class _LibsqlConnectionShim:
    """Makes a libsql_client sync Client behave like a sqlite3.Connection for
    the subset of the API this app uses (execute/executescript/commit/close).
    commit() is a no-op: libsql_client autocommits each statement over HTTP,
    so there's no explicit transaction to flush.
    """

    def __init__(self, client):
        self._client = client

    def execute(self, sql, params=()):
        return _LibsqlCursorShim(self._client.execute(sql, list(params)))

    def executescript(self, script):
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self._client.execute(statement)

    def commit(self):
        pass

    def close(self):
        # No-op: the underlying client is a shared, cached resource (see
        # _turso_client below), not owned by this particular call site.
        # Closing it here would kill it for every other page/session too.
        pass


@st.cache_resource
def _turso_client(url, auth_token):
    # Creating a libsql_client costs a real network round-trip (TLS/auth
    # handshake) — confirmed via direct benchmarking against the real
    # database: ~500-700ms for a fresh connection vs ~180ms for a query on
    # an already-open one. Since get_connection() used to be called fresh
    # for nearly every single query, that handshake cost was being paid
    # over and over on every page load. st.cache_resource keeps one client
    # alive for the whole app process (shared across sessions — safe here
    # since each request is independent and commit() is already a no-op).
    import libsql_client
    return libsql_client.create_client_sync(url=url, auth_token=auth_token)


def get_connection():
    """Open and return a connection to the database."""
    turso = _turso_config()
    if turso:
        url, auth_token = turso
        client = _turso_client(url, auth_token)
        return _LibsqlConnectionShim(client)

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
            title          TEXT,  -- e.g. "Dr.", "Prof." — optional, shown before the name
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
            default_end_time          TEXT    DEFAULT '17:30',
            session_secret            TEXT  -- signs "remember me" login cookies
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

        -- Bookable time+room slots that make up the schedule grid. Defined
        -- and edited by admins independently of which panel (if any) occupies
        -- each one — this is the grid itself, not an assignment.
        CREATE TABLE IF NOT EXISTS schedule_slots (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT    NOT NULL,  -- ISO format: "2026-04-07"
            room_id    INTEGER REFERENCES rooms(id),
            start_time TEXT    NOT NULL,  -- "14:00"
            end_time   TEXT    NOT NULL   -- "15:30"
        );

        -- Which panel (if any) occupies which slot
        CREATE TABLE IF NOT EXISTS schedule (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id      INTEGER NOT NULL UNIQUE REFERENCES schedule_slots(id),
            panel_id     INTEGER NOT NULL UNIQUE REFERENCES panels(id),   -- one slot per panel
            track_id     INTEGER REFERENCES tracks(id),
            moderator_id INTEGER REFERENCES speakers(id)
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

    # Add arrival/departure/title columns to existing databases that predate these changes
    existing = [row[1] for row in conn.execute("PRAGMA table_info(speakers)").fetchall()]
    for col in ["arrival_day", "arrival_time", "departure_day", "departure_time", "title"]:
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

    # Add new columns to conference_config for existing databases
    existing_config_cols = [r[1] for r in conn.execute("PRAGMA table_info(conference_config)").fetchall()]
    if "session_secret" not in existing_config_cols:
        conn.execute("ALTER TABLE conference_config ADD COLUMN session_secret TEXT")

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

    # The schedule table used to store room/date/time directly on each panel's
    # row; it now references a schedule_slots row instead (see the CREATE
    # TABLE comments above). Existing databases predating this change had an
    # empty, never-actually-used schedule table (the Schedule page was a
    # stub), so it's safe to just drop and recreate it in the new shape.
    existing_schedule_cols = [r[1] for r in conn.execute("PRAGMA table_info(schedule)").fetchall()]
    if existing_schedule_cols and "slot_id" not in existing_schedule_cols:
        conn.execute("DROP TABLE schedule")
        conn.execute("""
            CREATE TABLE schedule (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_id      INTEGER NOT NULL UNIQUE REFERENCES schedule_slots(id),
                panel_id     INTEGER NOT NULL UNIQUE REFERENCES panels(id),
                track_id     INTEGER REFERENCES tracks(id),
                moderator_id INTEGER REFERENCES speakers(id)
            )
        """)

    # Seed a single conference_config row if none exists
    if conn.execute("SELECT COUNT(*) FROM conference_config").fetchone()[0] == 0:
        conn.execute("INSERT INTO conference_config (id) VALUES (1)")

    conn.commit()
    conn.close()


@st.cache_resource
def ensure_database_ready():
    """Run initialize_database() exactly once per app process. app.py used
    to call initialize_database() directly on every rerun — harmless against
    local SQLite (sub-millisecond checks), but each of its migration checks
    is a real network round-trip against Turso, so it was silently repaying
    ~1s of redundant PRAGMA queries on every single page interaction."""
    initialize_database()
    return True


# ── Explicit cascade-delete helpers ───────────────────────────────────────────
# The schema declares ON DELETE CASCADE for these relationships, but that only
# reliably fires with a real, single, foreign-keys-enabled SQLite connection.
# These helpers do the cleanup explicitly in application code instead, so
# deletes behave the same regardless of what's actually running behind
# get_connection() (local SQLite today, a hosted DB later).

def delete_speaker(conn, speaker_id):
    conn.execute("""
        DELETE FROM panel_speaker_topics
        WHERE panel_speaker_id IN (SELECT id FROM panel_speakers WHERE speaker_id = ?)
    """, (speaker_id,))
    conn.execute("DELETE FROM panel_speakers WHERE speaker_id = ?", (speaker_id,))
    conn.execute("DELETE FROM speaker_topics WHERE speaker_id = ?", (speaker_id,))
    conn.execute("DELETE FROM speaker_availability WHERE speaker_id = ?", (speaker_id,))
    conn.execute("UPDATE schedule SET moderator_id = NULL WHERE moderator_id = ?", (speaker_id,))
    conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))


def delete_panel_speaker(conn, panel_speaker_id):
    conn.execute(
        "DELETE FROM panel_speaker_topics WHERE panel_speaker_id = ?", (panel_speaker_id,)
    )
    conn.execute("DELETE FROM panel_speakers WHERE id = ?", (panel_speaker_id,))


def delete_panel(conn, panel_id):
    conn.execute("""
        DELETE FROM panel_speaker_topics
        WHERE panel_speaker_id IN (SELECT id FROM panel_speakers WHERE panel_id = ?)
    """, (panel_id,))
    conn.execute("DELETE FROM panel_speakers WHERE panel_id = ?", (panel_id,))
    conn.execute("DELETE FROM panel_topics WHERE panel_id = ?", (panel_id,))
    conn.execute("DELETE FROM schedule WHERE panel_id = ?", (panel_id,))
    conn.execute("DELETE FROM panels WHERE id = ?", (panel_id,))


def delete_room(conn, room_id):
    # Slots in that room aren't deleted outright — just unassigned, so the
    # time slot (and whatever panel might occupy it) survives and the admin
    # can pick a different room for it afterward.
    conn.execute("UPDATE schedule_slots SET room_id = NULL WHERE room_id = ?", (room_id,))
    conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))


def delete_schedule_slot(conn, slot_id):
    conn.execute("DELETE FROM schedule WHERE slot_id = ?", (slot_id,))
    conn.execute("DELETE FROM schedule_slots WHERE id = ?", (slot_id,))


def delete_conference_day(conn, day_id, date):
    conn.execute("""
        DELETE FROM schedule
        WHERE slot_id IN (SELECT id FROM schedule_slots WHERE date = ?)
    """, (date,))
    conn.execute("DELETE FROM schedule_slots WHERE date = ?", (date,))
    conn.execute("DELETE FROM conference_days WHERE id = ?", (day_id,))


def reset_day_slots(conn, date):
    """Delete all schedule_slots (and their panel assignments) for a date,
    without touching the conference_days row itself."""
    conn.execute("""
        DELETE FROM schedule
        WHERE slot_id IN (SELECT id FROM schedule_slots WHERE date = ?)
    """, (date,))
    conn.execute("DELETE FROM schedule_slots WHERE date = ?", (date,))


# If you run this file directly (python database.py), it sets up the database
# and prints a confirmation message.
if __name__ == "__main__":
    initialize_database()
    print(f"Database ready at: {DB_PATH}")
