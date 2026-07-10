"""
auth.py
-------
Handles user authentication, session management, and permissions.
"""

import hashlib
import os
import streamlit as st
from database import get_connection

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
        return True
    return False


def logout():
    st.session_state.pop("user", None)


def get_current_user():
    return st.session_state.get("user")


def require_login():
    """Call at the top of any page to block access if not logged in."""
    if get_current_user() is None:
        st.warning("Please log in from the home page.")
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
