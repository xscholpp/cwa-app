"""
auth.py
-------
Handles user authentication, session management, and permissions.
"""

import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta

import extra_streamlit_components as stx
import streamlit as st
from database import get_connection

COOKIE_NAME = "cwa_session"

# ── Role presets ──────────────────────────────────────────────────────────────
# These are starting points when creating a user. Every permission can be
# toggled on or off individually after applying a preset.

PRESETS = {
    "admin": {
        "label": "Admin",
        "can_manage_users":          True,
        "can_manage_admin_settings": True,
        "can_view_all_panels":       True,
        "can_edit_all_panels":       True,
        "can_manage_speakers":       True,
        "can_manage_schedule":       True,
    },
    "executive": {
        "label": "Executive",
        "can_manage_users":          False,
        "can_manage_admin_settings": False,
        "can_view_all_panels":       True,
        "can_edit_all_panels":       True,
        "can_manage_speakers":       True,
        "can_manage_schedule":       False,  # does not run the scheduler or create itineraries
    },
    "committee_leader": {
        "label": "Committee Leader",
        "can_manage_users":          False,
        "can_manage_admin_settings": False,
        "can_view_all_panels":       True,   # can view all panels, but only edit their own committee's
        "can_edit_all_panels":       False,
        "can_manage_speakers":       True,
        "can_manage_schedule":       False,
    },
}

# Human-readable labels for each permission toggle
PERMISSION_LABELS = {
    "can_manage_users":          "Manage users",
    "can_manage_admin_settings": "Manage admin settings (rooms, tracks, committees, conference days)",
    "can_view_all_panels":       "View panels from all committees",
    "can_edit_all_panels":       "Edit panels from any committee",
    "can_manage_speakers":       "Add and manage speakers",
    "can_manage_schedule":       "Build and edit the schedule / create itineraries (admin only)",
}

PERMISSIONS = list(PERMISSION_LABELS.keys())


# ── Password hashing ──────────────────────────────────────────────────────────

def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()


# ── User management ───────────────────────────────────────────────────────────

def create_user(username, display_name, password, permissions, committee_id=None):
    """Create a new user. Returns (True, None) on success or (False, error_message)."""
    salt = os.urandom(16).hex()
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO users (
                username, display_name, password_hash, password_salt,
                can_manage_users, can_manage_admin_settings,
                can_view_all_panels, can_edit_all_panels,
                can_manage_speakers, can_manage_schedule,
                committee_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            username.strip(), display_name.strip(),
            _hash_password(password, salt), salt,
            int(bool(permissions["can_manage_users"])),
            int(bool(permissions["can_manage_admin_settings"])),
            int(bool(permissions["can_view_all_panels"])),
            int(bool(permissions["can_edit_all_panels"])),
            int(bool(permissions["can_manage_speakers"])),
            int(bool(permissions["can_manage_schedule"])),
            committee_id,
        ))
        conn.commit()
        return True, None
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


# ── "Remember me" cookie (keeps you logged in across a page refresh) ────────
# The cookie stores "username:signature", signed with a secret generated once
# and stored in conference_config, so a client can't forge another user's
# session by just editing the cookie value.

def _get_cookie_manager():
    # The underlying component call in CookieManager's constructor must only
    # run once per browser session — building a fresh one on every call (e.g.
    # once from get_current_user() and again from login() in the same script
    # run) trips Streamlit's duplicate-element-key check.
    if "_cookie_manager" not in st.session_state:
        st.session_state["_cookie_manager"] = stx.CookieManager(key="cwa_cookie_manager")
    return st.session_state["_cookie_manager"]


def _session_secret():
    conn = get_connection()
    row = conn.execute("SELECT session_secret FROM conference_config WHERE id = 1").fetchone()
    secret = row["session_secret"] if row else None
    if not secret:
        secret = os.urandom(32).hex()
        conn.execute("UPDATE conference_config SET session_secret = ? WHERE id = 1", (secret,))
        conn.commit()
    conn.close()
    return secret


def _sign(username, secret):
    return hashlib.sha256(f"{username}:{secret}".encode()).hexdigest()


def _make_token(username):
    return f"{username}:{_sign(username, _session_secret())}"


def _user_from_token(token):
    try:
        username, signature = token.split(":", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(username, _session_secret())):
        return None
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(user) if user else None


# ── Session / login ───────────────────────────────────────────────────────────

def login(username, password):
    """Check credentials and store the user in session state. Returns True on success."""
    conn = get_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username.strip(),)
    ).fetchone()
    conn.close()
    if user and _hash_password(password, user["password_salt"]) == user["password_hash"]:
        st.session_state["user"] = dict(user)
        _get_cookie_manager().set(
            COOKIE_NAME, _make_token(user["username"]),
            expires_at=datetime.now() + timedelta(days=30), key="set_cwa_session"
        )
        # The cookie component needs a brief moment to actually execute in the
        # browser before we navigate away with st.rerun() — without this, the
        # rerun can race ahead of the cookie actually being set.
        time.sleep(0.2)
        return True
    return False


def logout():
    st.session_state.pop("user", None)
    _get_cookie_manager().delete(COOKIE_NAME, key="del_cwa_session")
    # Same timing issue as login(): give the browser a moment to actually
    # delete the cookie before the caller reruns and navigates away.
    time.sleep(0.2)


def get_current_user():
    if "user" in st.session_state:
        return st.session_state["user"]

    # The cookie component returns {} until the browser reports back the real
    # cookies; when it does, Streamlit automatically reruns the script with
    # the updated value, so a first-load miss here self-corrects a moment
    # later without any special handling.
    token = _get_cookie_manager().get_all().get(COOKIE_NAME)
    if not token:
        return None

    user = _user_from_token(token)
    if user:
        st.session_state["user"] = user
    return user


def require_login():
    """Call at the top of any page to block access if not logged in."""
    if get_current_user() is None:
        st.warning("Please log in from the Login page.")
        st.stop()


def has_permission(perm: str) -> bool:
    user = get_current_user()
    return bool(user.get(perm, 0)) if user else False


# ── Preset helper (used in Admin page) ───────────────────────────────────────

def apply_preset_to_session():
    """Syncs the selected preset's permissions into session state so that
    the permission checkboxes in the Add User form update automatically."""
    preset = st.session_state.get("add_user_preset", "committee_leader")
    for perm in PERMISSIONS:
        st.session_state[f"new_perm_{perm}"] = bool(PRESETS[preset][perm])
