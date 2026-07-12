"""
pages/home.py
-------------
Dashboard shown after signing in: at-a-glance conference stats and shortcuts.
"""

from datetime import date

import streamlit as st
from database import get_connection
from auth import require_login, logout, get_current_user, has_permission
from layout import widen_content

widen_content()
require_login()

current_user = get_current_user()

col1, col2 = st.columns([5, 1])
with col1:
    st.title("Conference on World Affairs")
    st.caption(f"Signed in as **{current_user['display_name']}**")
with col2:
    st.write("")
    if st.button("Log out"):
        logout()
        st.rerun()

st.divider()

conn = get_connection()

speaker_count = conn.execute("SELECT COUNT(*) FROM speakers").fetchone()[0]
committee_count = conn.execute("SELECT COUNT(*) FROM committees").fetchone()[0]
panel_count = conn.execute("SELECT COUNT(*) FROM panels").fetchone()[0]
scheduled_count = conn.execute("SELECT COUNT(*) FROM schedule").fetchone()[0]

status_rows = conn.execute(
    "SELECT status, COUNT(*) AS n FROM panels GROUP BY status"
).fetchall()
status_counts = {row["status"]: row["n"] for row in status_rows}

unstaffed_panels = conn.execute("""
    SELECT p.id, p.title
    FROM panels p
    LEFT JOIN panel_speakers ps ON ps.panel_id = p.id
    GROUP BY p.id
    HAVING COUNT(ps.id) = 0
    ORDER BY p.title
""").fetchall()

next_day = conn.execute(
    "SELECT date, day_name FROM conference_days WHERE date >= ? ORDER BY date LIMIT 1",
    (date.today().isoformat(),)
).fetchone()

conn.close()

# ── Top-line metrics ───────────────────────────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
m1.metric("Speakers", speaker_count)
m2.metric("Panels", panel_count)
m3.metric("Committees", committee_count)
m4.metric("Scheduled", f"{scheduled_count}/{panel_count}")

st.divider()

left, right = st.columns([2, 1])

with left:
    st.subheader("Panels by status")
    if panel_count == 0:
        st.info("No panels have been created yet.")
    else:
        for status in ["draft", "approved", "to be presented"]:
            n = status_counts.get(status, 0)
            st.progress(n / panel_count, text=f"{status.title()}: {n}")

    st.subheader("Panels with no speakers assigned")
    if not unstaffed_panels:
        st.success("Every panel has at least one speaker assigned.")
    else:
        for p in unstaffed_panels:
            pc1, pc2 = st.columns([4, 1])
            with pc1:
                st.markdown(f"- {p['title']}")
            with pc2:
                if st.button("Open", key=f"open_panel_{p['id']}"):
                    st.session_state["panels_open_id"] = p["id"]
                    st.switch_page("pages/2_Panels.py")

with right:
    st.subheader("Conference dates")
    if next_day:
        days_away = (date.fromisoformat(next_day["date"]) - date.today()).days
        if days_away <= 0:
            st.success(f"**{next_day['day_name']}** is today.")
        else:
            st.info(f"**{next_day['day_name']}** ({next_day['date']}) is in **{days_away}** day(s).")
    else:
        st.caption("No upcoming conference days configured yet. Set them up in Admin.")

    st.subheader("Quick links")
    if has_permission("can_manage_speakers"):
        if st.button("Manage speakers", use_container_width=True):
            st.switch_page("pages/1_Speakers.py")
    if st.button("View panels", use_container_width=True):
        st.switch_page("pages/2_Panels.py")
    if has_permission("can_manage_schedule"):
        if st.button("Build schedule", use_container_width=True):
            st.switch_page("pages/3_Schedule.py")
    if has_permission("can_manage_admin_settings"):
        if st.button("Admin settings", use_container_width=True):
            st.switch_page("pages/0_Admin.py")
