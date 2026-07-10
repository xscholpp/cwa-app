"""
2_Panels.py
-----------
Add, view, and manage conference panels.

Access model:
  - A user assigned to a committee can add/edit that committee's panels.
  - can_view_all_panels lets a user see panels from every committee (read-only
    unless they also have can_edit_all_panels).
  - can_edit_all_panels (Admin/Executive) can edit any committee's panels,
    including choosing which committee a panel belongs to and its status.
  - Admin notes are only visible/editable by users with
    can_manage_admin_settings, regardless of committee.
"""

import streamlit as st
from database import get_connection
from auth import require_login, has_permission, get_current_user

require_login()

current_user = get_current_user()
my_committee_id = current_user.get("committee_id")
can_view_all = has_permission("can_view_all_panels")
can_edit_all = has_permission("can_edit_all_panels")
show_admin_notes = has_permission("can_manage_admin_settings")

if not (can_view_all or can_edit_all or my_committee_id):
    st.error("You don't have permission to access this page.")
    st.stop()


def can_edit_panel(panel_committee_id):
    return can_edit_all or (my_committee_id is not None and my_committee_id == panel_committee_id)


STATUS_OPTIONS = ["draft", "approved", "to be presented"]

st.title("Panels")

tab_list, tab_add, tab_order = st.tabs(["All Panels", "Add Panel", "Priority Order"])


# ── TAB 1: List panels ─────────────────────────────────────────────────────────
with tab_list:
    conn = get_connection()
    committees = conn.execute("SELECT id, name FROM committees ORDER BY name").fetchall()
    tracks = conn.execute("SELECT id, name FROM tracks ORDER BY name").fetchall()

    if can_view_all:
        panels = conn.execute("""
            SELECT p.*, c.name AS committee_name, t.name AS track_name
            FROM panels p
            LEFT JOIN committees c ON p.committee_id = c.id
            LEFT JOIN tracks t ON p.track_id = t.id
            ORDER BY c.name, p.title
        """).fetchall()
    else:
        panels = conn.execute("""
            SELECT p.*, c.name AS committee_name, t.name AS track_name
            FROM panels p
            LEFT JOIN committees c ON p.committee_id = c.id
            LEFT JOIN tracks t ON p.track_id = t.id
            WHERE p.committee_id = ?
            ORDER BY p.title
        """, (my_committee_id,)).fetchall()
    conn.close()

    track_names = ["— none —"] + [t["name"] for t in tracks]

    if can_view_all and committees:
        filter_choice = st.selectbox("Filter by committee", ["All"] + [c["name"] for c in committees])
        if filter_choice != "All":
            panels = [p for p in panels if p["committee_name"] == filter_choice]

    if not panels:
        st.info("No panels yet. Use the 'Add Panel' tab to get started.")
    else:
        for panel in panels:
            pid = panel["id"]
            editable = can_edit_panel(panel["committee_id"])

            with st.expander(f"{panel['title']} — {panel['committee_name'] or 'No committee'}"):
                conn = get_connection()
                topics = conn.execute(
                    "SELECT id, topic FROM panel_topics WHERE panel_id = ? ORDER BY topic",
                    (pid,)
                ).fetchall()

                st.markdown(f"**Status:** {panel['status']}")
                st.markdown(f"**Track:** {panel['track_name'] or '—'}")
                if panel["priority_ranking"] is not None:
                    st.markdown(f"**Committee priority:** {panel['priority_ranking']}")
                st.markdown(f"**Short description:** {panel['short_description'] or '—'}")
                st.markdown(f"**Full description:** {panel['full_description'] or '—'}")
                st.markdown(f"**Committee notes:** {panel['committee_notes'] or '—'}")
                st.markdown(f"**Presentation notes:** {panel['presentation_notes'] or '—'}")
                if show_admin_notes:
                    st.markdown(f"**Admin notes:** {panel['admin_notes'] or '—'}")

                st.markdown("**Relevant speaker topics:**")
                if topics:
                    for t in topics:
                        tc1, tc2 = st.columns([6, 1])
                        with tc1:
                            st.markdown(f"- {t['topic']}")
                        with tc2:
                            if editable and st.button("Remove", key=f"del_topic_{t['id']}"):
                                conn.execute("DELETE FROM panel_topics WHERE id = ?", (t["id"],))
                                conn.commit()
                                st.rerun()
                else:
                    st.markdown("None")

                if editable:
                    with st.form(f"add_topic_{pid}"):
                        new_topic = st.text_input("Add a topic", key=f"new_topic_{pid}")
                        add_topic = st.form_submit_button("Add")
                    if add_topic and new_topic.strip():
                        conn.execute(
                            "INSERT INTO panel_topics (panel_id, topic) VALUES (?, ?)",
                            (pid, new_topic.strip())
                        )
                        conn.commit()
                        st.rerun()

                # ── Speakers ──────────────────────────────────────────────────
                st.divider()
                st.markdown("**Speakers:**")

                assigned = conn.execute("""
                    SELECT ps.id AS panel_speaker_id, ps.role, s.id AS speaker_id, s.name
                    FROM panel_speakers ps
                    JOIN speakers s ON s.id = ps.speaker_id
                    WHERE ps.panel_id = ?
                    ORDER BY CASE ps.role WHEN 'panelist' THEN 0 ELSE 1 END, s.name
                """, (pid,)).fetchall()

                if assigned:
                    for row in assigned:
                        their_topics = conn.execute("""
                            SELECT st.topic FROM panel_speaker_topics pst
                            JOIN speaker_topics st ON st.id = pst.speaker_topic_id
                            WHERE pst.panel_speaker_id = ?
                            ORDER BY st.topic
                        """, (row["panel_speaker_id"],)).fetchall()
                        topic_str = ", ".join(t["topic"] for t in their_topics) or "—"

                        sc1, sc2 = st.columns([6, 1])
                        with sc1:
                            st.markdown(f"- **{row['name']}** ({row['role']}) — topics: {topic_str}")
                        with sc2:
                            if editable and st.button("Remove", key=f"del_pspeaker_{row['panel_speaker_id']}"):
                                conn.execute("DELETE FROM panel_speakers WHERE id = ?", (row["panel_speaker_id"],))
                                conn.commit()
                                st.rerun()
                else:
                    st.markdown("None")

                if editable:
                    assigned_ids = {r["speaker_id"] for r in assigned}
                    all_speakers = conn.execute("SELECT id, name FROM speakers ORDER BY name").fetchall()
                    available = [s for s in all_speakers if s["id"] not in assigned_ids]

                    if not all_speakers:
                        st.caption("No speakers exist yet — add some in the Speakers page first.")
                    elif not available:
                        st.caption("All speakers are already assigned to this panel.")
                    else:
                        st.markdown("**Add a speaker:**")
                        speaker_names = [s["name"] for s in available]
                        chosen_name = st.selectbox("Speaker", speaker_names, key=f"add_spk_sel_{pid}")
                        chosen_id = next(s["id"] for s in available if s["name"] == chosen_name)
                        role_choice = st.selectbox("Role", ["panelist", "alternate"], key=f"add_spk_role_{pid}")

                        speaker_topics = conn.execute(
                            "SELECT id, topic FROM speaker_topics WHERE speaker_id = ? ORDER BY topic",
                            (chosen_id,)
                        ).fetchall()
                        topic_choices = [t["topic"] for t in speaker_topics]
                        chosen_topics = st.multiselect(
                            "Relevant topics for this panel", topic_choices, key=f"add_spk_topics_{pid}"
                        )

                        if st.button("Add speaker to panel", key=f"add_spk_btn_{pid}"):
                            cur = conn.execute(
                                "INSERT INTO panel_speakers (panel_id, speaker_id, role) VALUES (?, ?, ?)",
                                (pid, chosen_id, role_choice)
                            )
                            panel_speaker_id = cur.lastrowid
                            for t in speaker_topics:
                                if t["topic"] in chosen_topics:
                                    conn.execute(
                                        "INSERT INTO panel_speaker_topics (panel_speaker_id, speaker_topic_id) "
                                        "VALUES (?, ?)",
                                        (panel_speaker_id, t["id"])
                                    )
                            conn.commit()
                            st.rerun()

                # ── Edit ──────────────────────────────────────────────────────
                if editable:
                    st.divider()
                    st.markdown("**Edit panel:**")
                    with st.form(f"edit_panel_{pid}"):
                        new_title = st.text_input("Title *", value=panel["title"])
                        new_short = st.text_area(
                            "Short description (one sentence)",
                            value=panel["short_description"] or "", height=68
                        )
                        new_full = st.text_area(
                            "Full description",
                            value=panel["full_description"] or "", height=120
                        )

                        cur_track_name = panel["track_name"] or "— none —"
                        track_idx = track_names.index(cur_track_name) if cur_track_name in track_names else 0
                        new_track_name = st.selectbox("Track", track_names, index=track_idx, key=f"track_{pid}")

                        new_priority = st.number_input(
                            "Committee priority (optional, lower = higher priority)",
                            min_value=0, step=1, value=panel["priority_ranking"] or 0, key=f"prio_{pid}"
                        )

                        new_committee_notes = st.text_area(
                            "Committee notes", value=panel["committee_notes"] or "", key=f"cn_{pid}"
                        )
                        new_presentation_notes = st.text_area(
                            "Presentation notes", value=panel["presentation_notes"] or "", key=f"pn_{pid}"
                        )
                        if show_admin_notes:
                            new_admin_notes = st.text_area(
                                "Admin notes", value=panel["admin_notes"] or "", key=f"an_{pid}"
                            )

                        if can_edit_all:
                            new_status = st.selectbox(
                                "Status", STATUS_OPTIONS,
                                index=STATUS_OPTIONS.index(panel["status"]), key=f"status_{pid}"
                            )

                        save = st.form_submit_button("Save changes")

                    if save:
                        if not new_title.strip():
                            st.error("Title is required.")
                        else:
                            new_track_id = None
                            if new_track_name != "— none —":
                                new_track_id = next(t["id"] for t in tracks if t["name"] == new_track_name)

                            fields = {
                                "title": new_title.strip(),
                                "short_description": new_short.strip() or None,
                                "full_description": new_full.strip() or None,
                                "track_id": new_track_id,
                                "priority_ranking": new_priority or None,
                                "committee_notes": new_committee_notes.strip() or None,
                                "presentation_notes": new_presentation_notes.strip() or None,
                            }
                            if show_admin_notes:
                                fields["admin_notes"] = new_admin_notes.strip() or None
                            if can_edit_all:
                                fields["status"] = new_status

                            set_clause = ", ".join(f"{k} = ?" for k in fields)
                            conn.execute(
                                f"UPDATE panels SET {set_clause} WHERE id = ?",
                                (*fields.values(), pid)
                            )
                            conn.commit()
                            st.success("Panel updated.")
                            st.rerun()

                    st.divider()
                    if st.button("Delete panel", key=f"del_{pid}"):
                        conn.execute("DELETE FROM panels WHERE id = ?", (pid,))
                        conn.commit()
                        st.rerun()

                conn.close()


# ── TAB 2: Add a new panel ─────────────────────────────────────────────────────
with tab_add:
    conn = get_connection()
    committees = conn.execute("SELECT id, name FROM committees ORDER BY name").fetchall()
    tracks = conn.execute("SELECT id, name FROM tracks ORDER BY name").fetchall()
    conn.close()

    committee_options = {c["name"]: c["id"] for c in committees}
    track_names = ["— none —"] + [t["name"] for t in tracks]

    if can_edit_all:
        if not committees:
            st.warning("No committees exist yet. Add one in Admin → Committees first.")
        else:
            with st.form("add_panel_form", clear_on_submit=True):
                st.subheader("Panel details")
                committee_name = st.selectbox("Committee *", list(committee_options.keys()))
                title = st.text_input("Title *")
                short_description = st.text_area("Short description (one sentence)", height=68)
                full_description = st.text_area("Full description", height=120)
                track_name = st.selectbox("Track", track_names)
                priority = st.number_input(
                    "Committee priority (optional, lower = higher priority)", min_value=0, step=1, value=0
                )

                st.subheader("Notes")
                committee_notes = st.text_area("Committee notes", height=80)
                presentation_notes = st.text_area("Presentation notes", height=80)
                admin_notes = st.text_area("Admin notes", height=80)

                st.subheader("Relevant speaker topics")
                st.caption("Enter each topic on its own line.")
                topics_raw = st.text_area("Topics", height=100)

                submitted = st.form_submit_button("Save panel")

            if submitted:
                if not title.strip():
                    st.error("Title is required.")
                else:
                    track_id = None
                    if track_name != "— none —":
                        track_id = next(t["id"] for t in tracks if t["name"] == track_name)

                    conn = get_connection()
                    cursor = conn.execute("""
                        INSERT INTO panels (
                            title, short_description, full_description, committee_id,
                            track_id, priority_ranking, committee_notes, presentation_notes, admin_notes
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        title.strip(), short_description.strip() or None, full_description.strip() or None,
                        committee_options[committee_name], track_id, priority or None,
                        committee_notes.strip() or None, presentation_notes.strip() or None,
                        admin_notes.strip() or None,
                    ))
                    panel_id = cursor.lastrowid

                    for line in topics_raw.splitlines():
                        topic = line.strip()
                        if topic:
                            conn.execute(
                                "INSERT INTO panel_topics (panel_id, topic) VALUES (?, ?)",
                                (panel_id, topic)
                            )

                    conn.commit()
                    conn.close()
                    st.success(f"Panel '{title.strip()}' added.")
                    st.rerun()

    elif my_committee_id is None:
        st.warning("You're not assigned to a committee, so you can't add panels. Ask an admin to assign you to one.")

    else:
        my_committee_name = next(
            (c["name"] for c in committees if c["id"] == my_committee_id), "Your committee"
        )
        with st.form("add_panel_form", clear_on_submit=True):
            st.subheader("Panel details")
            st.caption(f"Adding to: **{my_committee_name}**")
            title = st.text_input("Title *")
            short_description = st.text_area("Short description (one sentence)", height=68)
            full_description = st.text_area("Full description", height=120)
            track_name = st.selectbox("Track", track_names)
            priority = st.number_input(
                "Committee priority (optional, lower = higher priority)", min_value=0, step=1, value=0
            )

            st.subheader("Notes")
            committee_notes = st.text_area("Committee notes", height=80)
            presentation_notes = st.text_area("Presentation notes", height=80)

            st.subheader("Relevant speaker topics")
            st.caption("Enter each topic on its own line.")
            topics_raw = st.text_area("Topics", height=100)

            submitted = st.form_submit_button("Save panel")

        if submitted:
            if not title.strip():
                st.error("Title is required.")
            else:
                track_id = None
                if track_name != "— none —":
                    track_id = next(t["id"] for t in tracks if t["name"] == track_name)

                conn = get_connection()
                cursor = conn.execute("""
                    INSERT INTO panels (
                        title, short_description, full_description, committee_id,
                        track_id, priority_ranking, committee_notes, presentation_notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    title.strip(), short_description.strip() or None, full_description.strip() or None,
                    my_committee_id, track_id, priority or None,
                    committee_notes.strip() or None, presentation_notes.strip() or None,
                ))
                panel_id = cursor.lastrowid

                for line in topics_raw.splitlines():
                    topic = line.strip()
                    if topic:
                        conn.execute(
                            "INSERT INTO panel_topics (panel_id, topic) VALUES (?, ?)",
                            (panel_id, topic)
                        )

                conn.commit()
                conn.close()
                st.success(f"Panel '{title.strip()}' added.")
                st.rerun()


# ── TAB 3: Priority order per committee ────────────────────────────────────────
with tab_order:
    conn = get_connection()
    committees = conn.execute("SELECT id, name FROM committees ORDER BY name").fetchall()
    conn.close()

    order_committee_id = None

    if can_view_all and committees:
        names = [c["name"] for c in committees]
        default_idx = 0
        if my_committee_id:
            my_name = next((c["name"] for c in committees if c["id"] == my_committee_id), None)
            if my_name in names:
                default_idx = names.index(my_name)
        chosen_name = st.selectbox("Committee", names, index=default_idx, key="order_committee_select")
        order_committee_id = next(c["id"] for c in committees if c["name"] == chosen_name)
    elif my_committee_id:
        my_name = next((c["name"] for c in committees if c["id"] == my_committee_id), "Your committee")
        st.caption(f"Showing: **{my_name}**")
        order_committee_id = my_committee_id

    if order_committee_id is None:
        st.info("You're not assigned to a committee.")
    else:
        editable = can_edit_panel(order_committee_id)

        conn = get_connection()
        ordered = conn.execute("""
            SELECT id, title, status, priority_ranking FROM panels
            WHERE committee_id = ?
            ORDER BY (priority_ranking IS NULL), priority_ranking, title
        """, (order_committee_id,)).fetchall()

        # Normalize to sequential 1..N (preserving current order) so the
        # up/down buttons always have well-defined, gap-free positions.
        if any(p["priority_ranking"] != i + 1 for i, p in enumerate(ordered)):
            for i, p in enumerate(ordered):
                conn.execute("UPDATE panels SET priority_ranking = ? WHERE id = ?", (i + 1, p["id"]))
            conn.commit()
            ordered = conn.execute("""
                SELECT id, title, status, priority_ranking FROM panels
                WHERE committee_id = ? ORDER BY priority_ranking
            """, (order_committee_id,)).fetchall()
        conn.close()

        if not ordered:
            st.info("No panels for this committee yet.")
        else:
            for i, p in enumerate(ordered):
                c1, c2, c3, c4, c5 = st.columns([1, 5, 2, 1, 1])
                with c1:
                    st.markdown(f"**{p['priority_ranking']}**")
                with c2:
                    st.markdown(p["title"])
                with c3:
                    st.caption(p["status"])
                with c4:
                    if editable and st.button("↑", key=f"up_{p['id']}", disabled=(i == 0)):
                        other = ordered[i - 1]
                        conn = get_connection()
                        conn.execute("UPDATE panels SET priority_ranking = ? WHERE id = ?",
                                     (other["priority_ranking"], p["id"]))
                        conn.execute("UPDATE panels SET priority_ranking = ? WHERE id = ?",
                                     (p["priority_ranking"], other["id"]))
                        conn.commit()
                        conn.close()
                        st.rerun()
                with c5:
                    if editable and st.button("↓", key=f"down_{p['id']}", disabled=(i == len(ordered) - 1)):
                        other = ordered[i + 1]
                        conn = get_connection()
                        conn.execute("UPDATE panels SET priority_ranking = ? WHERE id = ?",
                                     (other["priority_ranking"], p["id"]))
                        conn.execute("UPDATE panels SET priority_ranking = ? WHERE id = ?",
                                     (p["priority_ranking"], other["id"]))
                        conn.commit()
                        conn.close()
                        st.rerun()
