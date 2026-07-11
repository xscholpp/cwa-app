"""
1_Speakers.py
-------------
Add, view, and manage speakers, their topics, and their availability.

Availability model:
  - Arrival day/time  → earliest point the speaker is available
  - Departure day/time → latest point the speaker is available
  - Blocks → specific windows within the conference when they are NOT available
"""

import streamlit as st
from database import get_connection
from auth import require_login, has_permission
from layout import widen_content

widen_content()
require_login()

if not has_permission("can_manage_speakers"):
    st.error("You don't have permission to access this page.")
    st.stop()

st.title("Speakers")

TIMES = [f"{h:02d}:{m:02d}" for h in range(6, 24) for m in (0, 30)]


def get_conference_days(conn):
    rows = conn.execute("SELECT day_name FROM conference_days ORDER BY day_order").fetchall()
    return [r["day_name"] for r in rows]


tab_list, tab_add = st.tabs(["All Speakers", "Add Speaker"])


# ── TAB 1: List all speakers ──────────────────────────────────────────────────
with tab_list:
    conn = get_connection()
    conf_days = get_conference_days(conn)
    speakers = conn.execute("SELECT * FROM speakers ORDER BY name").fetchall()
    conn.close()

    if not speakers:
        st.info("No speakers added yet. Use the 'Add Speaker' tab to get started.")
    else:
        for speaker in speakers:
            with st.expander(speaker["name"]):
                conn = get_connection()
                sid = speaker["id"]

                topics = conn.execute(
                    "SELECT id, topic FROM speaker_topics WHERE speaker_id = ? ORDER BY topic",
                    (sid,)
                ).fetchall()
                blocks = conn.execute(
                    "SELECT id, day, start_time, end_time FROM speaker_availability "
                    "WHERE speaker_id = ? ORDER BY day, start_time",
                    (sid,)
                ).fetchall()

                # ── Edit basic info ───────────────────────────────────────────
                st.markdown("**Edit details:**")

                # Detect whether this speaker was saved as "all days"
                is_all_days = speaker["arrival_time"] == "00:00"

                # Safe index lookups (values might be outside the TIMES list)
                def time_index(t):
                    return TIMES.index(t) if t in TIMES else 0

                def day_index(d, default=0):
                    return conf_days.index(d) if d in conf_days else default

                with st.form(f"edit_speaker_{sid}"):
                    new_name = st.text_input("Name *", value=speaker["name"])
                    new_bio  = st.text_area("Bio", value=speaker["bio"] or "", height=100)

                    st.markdown("**Availability**")
                    all_days = st.checkbox("Available all conference days", value=is_all_days)
                    st.caption("Uncheck to set a specific arrival and departure.")

                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("**Arrival**")
                        e_arr_day  = st.selectbox("Day",  conf_days,
                                                   index=day_index(speaker["arrival_day"]),
                                                   key=f"e_arr_day_{sid}")
                        e_arr_time = st.selectbox("Time", TIMES,
                                                   index=time_index(speaker["arrival_time"]),
                                                   key=f"e_arr_time_{sid}")
                    with col2:
                        st.markdown("**Departure**")
                        e_dep_day  = st.selectbox("Day",  conf_days,
                                                   index=day_index(speaker["departure_day"], len(conf_days) - 1),
                                                   key=f"e_dep_day_{sid}")
                        e_dep_time = st.selectbox("Time", TIMES,
                                                   index=time_index(speaker["departure_time"]),
                                                   key=f"e_dep_time_{sid}")

                    save = st.form_submit_button("Save changes")

                if save:
                    if not new_name.strip():
                        st.error("Name is required.")
                    else:
                        if all_days:
                            arr_day, arr_time = conf_days[0], "00:00"
                            dep_day, dep_time = conf_days[-1], "23:30"
                        else:
                            arr_day, arr_time = e_arr_day, e_arr_time
                            dep_day, dep_time = e_dep_day, e_dep_time

                        conn.execute("""
                            UPDATE speakers
                            SET name=?, bio=?, arrival_day=?, arrival_time=?, departure_day=?, departure_time=?
                            WHERE id=?
                        """, (new_name.strip(), new_bio.strip() or None,
                              arr_day, arr_time, dep_day, dep_time, sid))
                        conn.commit()
                        st.success("Speaker updated.")
                        st.rerun()

                # ── Topics ────────────────────────────────────────────────────
                st.markdown("**Topics:**")
                if topics:
                    for t in topics:
                        tc1, tc2 = st.columns([6, 1])
                        with tc1:
                            st.markdown(f"- {t['topic']}")
                        with tc2:
                            if st.button("Remove", key=f"del_topic_{t['id']}"):
                                conn.execute("DELETE FROM speaker_topics WHERE id = ?", (t["id"],))
                                conn.commit()
                                st.rerun()
                else:
                    st.markdown("None")

                with st.form(f"add_topic_{sid}"):
                    new_topic = st.text_input("Add a topic", key=f"new_topic_{sid}")
                    add_topic = st.form_submit_button("Add")
                if add_topic and new_topic.strip():
                    conn.execute(
                        "INSERT INTO speaker_topics (speaker_id, topic) VALUES (?, ?)",
                        (sid, new_topic.strip())
                    )
                    conn.commit()
                    st.rerun()

                # ── Unavailable blocks ────────────────────────────────────────
                st.markdown("**Unavailable blocks:**")
                if blocks:
                    for block in blocks:
                        bc1, bc2 = st.columns([6, 1])
                        with bc1:
                            st.markdown(f"- {block['day']}  {block['start_time']} – {block['end_time']}")
                        with bc2:
                            if st.button("Remove", key=f"del_block_{block['id']}"):
                                conn.execute("DELETE FROM speaker_availability WHERE id = ?", (block["id"],))
                                conn.commit()
                                st.rerun()
                else:
                    st.markdown("None")

                with st.form(f"add_block_{sid}"):
                    st.markdown("**Add unavailable block:**")
                    bc1, bc2, bc3 = st.columns(3)
                    with bc1:
                        b_day = st.selectbox("Day", conf_days, key=f"bday_{sid}")
                    with bc2:
                        b_start = st.selectbox("Start", TIMES, key=f"bstart_{sid}")
                    with bc3:
                        b_end = st.selectbox("End", TIMES, index=2, key=f"bend_{sid}")
                    add_block = st.form_submit_button("Add block")

                if add_block:
                    conn.execute(
                        "INSERT INTO speaker_availability (speaker_id, day, start_time, end_time) VALUES (?, ?, ?, ?)",
                        (sid, b_day, b_start, b_end)
                    )
                    conn.commit()
                    st.rerun()

                # ── Delete ────────────────────────────────────────────────────
                st.divider()
                if st.button("Delete speaker", key=f"del_{sid}"):
                    conn.execute("DELETE FROM speakers WHERE id = ?", (sid,))
                    conn.commit()
                    st.rerun()

                conn.close()


# ── TAB 2: Add a new speaker ──────────────────────────────────────────────────
with tab_add:
    conn = get_connection()
    conf_days = get_conference_days(conn)
    conn.close()

    with st.form("add_speaker_form", clear_on_submit=True):
        st.subheader("Speaker details")
        name = st.text_input("Full name *")
        bio  = st.text_area("Bio", height=120)

        st.subheader("Topics")
        st.caption("Enter each topic on its own line.")
        topics_raw = st.text_area("Topics", height=100)

        st.subheader("Availability")

        all_days = st.checkbox("Available all conference days", value=True)
        st.caption(
            "Check this if the speaker is available for the entire conference. "
            "Uncheck to set a specific arrival and departure day/time. "
            "You can add specific unavailable blocks after saving."
        )

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Arrival**")
            arrival_day  = st.selectbox("Day",  conf_days, key="arr_day")
            arrival_time = st.selectbox("Time", TIMES, index=TIMES.index("09:00"), key="arr_time")
        with col2:
            st.markdown("**Departure**")
            departure_day  = st.selectbox("Day",  conf_days, index=len(conf_days) - 1, key="dep_day")
            departure_time = st.selectbox("Time", TIMES, index=TIMES.index("18:00"), key="dep_time")

        submitted = st.form_submit_button("Save speaker")

    if submitted:
        if not name.strip():
            st.error("Name is required.")
        else:
            if all_days:
                arr_day, arr_time = conf_days[0], "00:00"
                dep_day, dep_time = conf_days[-1], "23:30"
            else:
                arr_day, arr_time = arrival_day, arrival_time
                dep_day, dep_time = departure_day, departure_time

            conn = get_connection()
            cursor = conn.execute(
                "INSERT INTO speakers (name, bio, arrival_day, arrival_time, departure_day, departure_time) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name.strip(), bio.strip() or None, arr_day, arr_time, dep_day, dep_time)
            )
            speaker_id = cursor.lastrowid

            for line in topics_raw.splitlines():
                topic = line.strip()
                if topic:
                    conn.execute(
                        "INSERT INTO speaker_topics (speaker_id, topic) VALUES (?, ?)",
                        (speaker_id, topic)
                    )

            conn.commit()
            conn.close()
            st.success(f"Speaker '{name.strip()}' added.")
            st.rerun()
