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
TITLE_OPTIONS = ["None", "Mr.", "Ms.", "Mrs.", "Dr.", "Prof.", "Hon."]


def display_name(speaker):
    return f"{speaker['title']} {speaker['name']}" if speaker["title"] else speaker["name"]


def get_conference_days(conn):
    rows = conn.execute("SELECT day_name FROM conference_days ORDER BY day_order").fetchall()
    return [r["day_name"] for r in rows]


def parse_topics(raw):
    """Split on commas and/or newlines, so topics can be pasted either way."""
    parts = [p.strip() for line in raw.splitlines() for p in line.split(",")]
    seen = set()
    topics = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            topics.append(p)
    return topics


st.session_state.setdefault("speakers_open_sid", None)

tab_list, tab_add = st.tabs(["All Speakers", "Add Speaker"])


# ── TAB 1: List all speakers ──────────────────────────────────────────────────
with tab_list:
    conn = get_connection()
    conf_days = get_conference_days(conn)
    speakers = conn.execute("SELECT * FROM speakers ORDER BY name").fetchall()
    topic_preview_rows = conn.execute(
        "SELECT speaker_id, GROUP_CONCAT(topic, ', ') AS topics "
        "FROM speaker_topics GROUP BY speaker_id"
    ).fetchall()
    conn.close()

    topics_preview_by_speaker = {r["speaker_id"]: r["topics"] for r in topic_preview_rows}

    if not speakers:
        st.info("No speakers added yet. Use the 'Add Speaker' tab to get started.")
    else:
        for speaker in speakers:
            sid = speaker["id"]

            preview = topics_preview_by_speaker.get(sid, "")
            if len(preview) > 50:
                preview = preview[:47] + "..."
            label = display_name(speaker) + (f"   ·   {preview}" if preview else "")

            with st.expander(label, expanded=(sid == st.session_state["speakers_open_sid"])):
                conn = get_connection()

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
                is_all_days = speaker["arrival_time"] == "00:00"

                # Safe index lookups (values might be outside the TIMES list)
                def time_index(t):
                    return TIMES.index(t) if t in TIMES else 0

                def day_index(d, default=0):
                    return conf_days.index(d) if d in conf_days else default

                with st.container(border=True):
                    st.markdown("**Details**")
                    with st.form(f"edit_speaker_{sid}"):
                        title_col, name_col = st.columns([1, 8])
                        with title_col:
                            cur_title = speaker["title"] or "None"
                            new_title = st.selectbox(
                                "Title", TITLE_OPTIONS,
                                index=TITLE_OPTIONS.index(cur_title) if cur_title in TITLE_OPTIONS else 0,
                                key=f"e_title_{sid}"
                            )
                        with name_col:
                            new_name = st.text_input("Name *", value=speaker["name"])
                        new_bio = st.text_area("Bio", value=speaker["bio"] or "", height=80)

                        st.markdown("**Availability**")
                        all_days = st.checkbox("Available all conference days", value=is_all_days)
                        st.caption("Uncheck to set a specific arrival and departure.")

                        col1, col2 = st.columns(2)
                        with col1:
                            st.caption("Arrival")
                            e_arr_day  = st.selectbox("Day",  conf_days,
                                                       index=day_index(speaker["arrival_day"]),
                                                       key=f"e_arr_day_{sid}")
                            e_arr_time = st.selectbox("Time", TIMES,
                                                       index=time_index(speaker["arrival_time"]),
                                                       key=f"e_arr_time_{sid}")
                        with col2:
                            st.caption("Departure")
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
                            SET title=?, name=?, bio=?, arrival_day=?, arrival_time=?, departure_day=?, departure_time=?
                            WHERE id=?
                        """, (None if new_title == "None" else new_title, new_name.strip(),
                              new_bio.strip() or None,
                              arr_day, arr_time, dep_day, dep_time, sid))
                        conn.commit()
                        st.success("Speaker updated.")
                        st.session_state["speakers_open_sid"] = sid
                        st.rerun()

                # ── Topics ────────────────────────────────────────────────────
                with st.container(border=True):
                    st.markdown("**Topics**")
                    if topics:
                        for t in topics:
                            tc1, tc2 = st.columns([6, 1])
                            with tc1:
                                st.markdown(f"- {t['topic']}")
                            with tc2:
                                if st.button("Remove", key=f"del_topic_{t['id']}"):
                                    conn.execute("DELETE FROM speaker_topics WHERE id = ?", (t["id"],))
                                    conn.commit()
                                    st.session_state["speakers_open_sid"] = sid
                                    st.rerun()
                    else:
                        st.caption("None")

                    with st.form(f"add_topic_{sid}"):
                        tcol1, tcol2 = st.columns([4, 1])
                        with tcol1:
                            new_topic = st.text_input(
                                "Add a topic", key=f"new_topic_{sid}",
                                label_visibility="collapsed",
                                placeholder="Add topic(s), comma-separated"
                            )
                        with tcol2:
                            add_topic = st.form_submit_button("Add")
                if add_topic and new_topic.strip():
                    existing_topic_names = {t["topic"] for t in topics}
                    for topic in parse_topics(new_topic):
                        if topic not in existing_topic_names:
                            conn.execute(
                                "INSERT INTO speaker_topics (speaker_id, topic) VALUES (?, ?)",
                                (sid, topic)
                            )
                            existing_topic_names.add(topic)
                    conn.commit()
                    st.session_state["speakers_open_sid"] = sid
                    st.rerun()

                # ── Unavailable blocks ────────────────────────────────────────
                with st.container(border=True):
                    st.markdown("**Unavailable blocks**")
                    if blocks:
                        for block in blocks:
                            bc1, bc2 = st.columns([6, 1])
                            with bc1:
                                st.markdown(f"- {block['day']}  {block['start_time']} – {block['end_time']}")
                            with bc2:
                                if st.button("Remove", key=f"del_block_{block['id']}"):
                                    conn.execute("DELETE FROM speaker_availability WHERE id = ?", (block["id"],))
                                    conn.commit()
                                    st.session_state["speakers_open_sid"] = sid
                                    st.rerun()
                    else:
                        st.caption("None")

                    with st.form(f"add_block_{sid}"):
                        bc1, bc2, bc3, bc4 = st.columns([2, 2, 2, 1])
                        with bc1:
                            b_day = st.selectbox("Day", conf_days, key=f"bday_{sid}")
                        with bc2:
                            b_start = st.selectbox("Start", TIMES, key=f"bstart_{sid}")
                        with bc3:
                            b_end = st.selectbox("End", TIMES, index=2, key=f"bend_{sid}")
                        with bc4:
                            st.write("")
                            add_block = st.form_submit_button("Add")

                if add_block:
                    conn.execute(
                        "INSERT INTO speaker_availability (speaker_id, day, start_time, end_time) VALUES (?, ?, ?, ?)",
                        (sid, b_day, b_start, b_end)
                    )
                    conn.commit()
                    st.session_state["speakers_open_sid"] = sid
                    st.rerun()

                # ── Delete ────────────────────────────────────────────────────
                if st.button("Delete speaker", key=f"del_{sid}"):
                    conn.execute("DELETE FROM speakers WHERE id = ?", (sid,))
                    conn.commit()
                    st.session_state["speakers_open_sid"] = None
                    st.rerun()

                conn.close()


# ── TAB 2: Add a new speaker ──────────────────────────────────────────────────
with tab_add:
    conn = get_connection()
    conf_days = get_conference_days(conn)
    conn.close()

    with st.form("add_speaker_form", clear_on_submit=True):
        with st.container(border=True):
            st.markdown("**Speaker details**")
            title_col, name_col = st.columns([1, 8])
            with title_col:
                title = st.selectbox("Title", TITLE_OPTIONS)
            with name_col:
                name = st.text_input("Full name *")
            bio = st.text_area("Bio", height=80)

        with st.container(border=True):
            st.markdown("**Topics**")
            st.caption("Comma-separated, or one per line.")
            topics_raw = st.text_area("Topics", height=80, label_visibility="collapsed")

        with st.container(border=True):
            st.markdown("**Availability**")
            all_days = st.checkbox("Available all conference days", value=True)
            st.caption(
                "Check this if the speaker is available for the entire conference. "
                "Uncheck to set a specific arrival and departure day/time. "
                "You can add specific unavailable blocks after saving."
            )

            col1, col2 = st.columns(2)
            with col1:
                st.caption("Arrival")
                arrival_day  = st.selectbox("Day",  conf_days, key="arr_day")
                arrival_time = st.selectbox("Time", TIMES, index=TIMES.index("09:00"), key="arr_time")
            with col2:
                st.caption("Departure")
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
                "INSERT INTO speakers (title, name, bio, arrival_day, arrival_time, departure_day, departure_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (None if title == "None" else title, name.strip(), bio.strip() or None,
                 arr_day, arr_time, dep_day, dep_time)
            )
            speaker_id = cursor.lastrowid

            for topic in parse_topics(topics_raw):
                conn.execute(
                    "INSERT INTO speaker_topics (speaker_id, topic) VALUES (?, ?)",
                    (speaker_id, topic)
                )

            conn.commit()
            conn.close()
            st.success(f"Speaker '{name.strip()}' added.")
            st.rerun()
