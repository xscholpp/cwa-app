"""
2_Panels.py
-----------
Add, view, and manage conference panels.

Navigation model:
  - "All Panels" / "Priority Order" tabs show list views.
  - Clicking "+ New Panel" immediately creates a draft panel row and opens
    its dedicated full-page view, so speakers can be added right away
    without saving anything first. Clicking an existing panel opens the
    same view for editing. Tracked via st.session_state["panels_open_id"].

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
from streamlit_sortables import sort_items
from database import get_connection, delete_panel, delete_panel_speaker, add_panel_conflict
from auth import require_login, has_permission, get_current_user
from layout import widen_content

widen_content()
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


def speaker_display(row):
    return f"{row['title']} {row['name']}" if row["title"] else row["name"]


# Compact styling for the drag-to-reorder speaker/alternate widget.
# Speakers/Alternates are stacked (not side-by-side): the library's own
# "vertical" mode only controls item flow *within* a container, so the
# stacking of the two containers themselves is forced here via
# flex-direction, and the boundary between them gets a red divider.
SORTABLE_STYLE = """
.sortable-component.vertical {
    display: flex !important;
    flex-direction: column !important;
    border: 1px solid rgba(49, 51, 63, 0.2);
    border-radius: 0.5rem;
}
.sortable-container {
    background-color: transparent;
}
.sortable-container:nth-of-type(2) {
    border-top: 3px solid #e03131;
}
.sortable-container-header {
    font-weight: 600;
    font-size: 0.85rem;
    padding: 0.25rem 0.75rem;
    border-bottom: 1px solid rgba(49, 51, 63, 0.2);
}
.sortable-container-body {
    padding: 0.25rem;
}
.sortable-item, .sortable-item:hover {
    padding: 0.25rem 0.6rem 0.25rem 1.5rem;
    margin: 0.15rem 0;
    border-radius: 0.25rem;
    background-color: rgba(49, 51, 63, 0.06);
    color: #000 !important;
    font-size: 0.9rem;
    position: relative;
}
.sortable-item::before {
    content: "⠿";
    position: absolute;
    left: 0.5rem;
    color: rgba(49, 51, 63, 0.45);
}
"""

STATUS_OPTIONS = ["draft", "approved", "to be presented"]
ATTENDANCE_OPTIONS = ["— none —", "low", "medium", "high"]

conn = get_connection()
committees = conn.execute("SELECT id, name FROM committees ORDER BY name").fetchall()
tracks = conn.execute("SELECT id, name FROM tracks ORDER BY name").fetchall()
conn.close()

committee_options = {c["name"]: c["id"] for c in committees}
track_names = ["— none —"] + [t["name"] for t in tracks]
my_committee_name = next((c["name"] for c in committees if c["id"] == my_committee_id), None)

st.session_state.setdefault("panels_open_id", None)


def open_panel(pid):
    st.session_state["panels_open_id"] = pid
    st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# LIST VIEW
# ════════════════════════════════════════════════════════════════════════════
if st.session_state["panels_open_id"] is None:
    st.title("Panels")
    tab_list, tab_order = st.tabs(["All Panels", "Priority Order"])

    # ── All Panels ──────────────────────────────────────────────────────────
    with tab_list:
        can_add = (can_edit_all and bool(committees)) or my_committee_id is not None
        if can_add:
            if st.button("+ New Panel"):
                default_committee_id = my_committee_id
                if default_committee_id is None and committees:
                    default_committee_id = committees[0]["id"]
                conn = get_connection()
                cursor = conn.execute(
                    "INSERT INTO panels (title, committee_id, status) VALUES (?, ?, ?)",
                    ("Untitled panel", default_committee_id, "draft")
                )
                new_pid = cursor.lastrowid
                conn.commit()
                conn.close()
                open_panel(new_pid)

        conn = get_connection()
        if can_view_all:
            panels = conn.execute("""
                SELECT p.*, c.name AS committee_name
                FROM panels p
                LEFT JOIN committees c ON p.committee_id = c.id
                ORDER BY c.name, p.title
            """).fetchall()
        else:
            panels = conn.execute("""
                SELECT p.*, c.name AS committee_name
                FROM panels p
                LEFT JOIN committees c ON p.committee_id = c.id
                WHERE p.committee_id = ?
                ORDER BY p.title
            """, (my_committee_id,)).fetchall()
        conn.close()

        if can_view_all and committees:
            filter_choice = st.selectbox("Filter by committee", ["All"] + [c["name"] for c in committees])
            if filter_choice != "All":
                panels = [p for p in panels if p["committee_name"] == filter_choice]

        if not panels:
            st.info("No panels yet. Use '+ New Panel' to get started.")
        else:
            for panel in panels:
                c1, c2, c3 = st.columns([5, 2, 2])
                with c1:
                    if st.button(panel["title"], key=f"open_{panel['id']}", use_container_width=True):
                        open_panel(panel["id"])
                with c2:
                    st.caption(panel["committee_name"] or "No committee")
                with c3:
                    st.caption(panel["status"])

    # ── Priority Order ──────────────────────────────────────────────────────
    with tab_order:
        order_committee_id = None

        if can_view_all and committees:
            names = [c["name"] for c in committees]
            default_idx = 0
            if my_committee_name in names:
                default_idx = names.index(my_committee_name)
            chosen_name = st.selectbox("Committee", names, index=default_idx, key="order_committee_select")
            order_committee_id = committee_options[chosen_name]
        elif my_committee_id:
            st.caption(f"Showing: **{my_committee_name or 'your committee'}**")
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
                        if st.button(p["title"], key=f"order_open_{p['id']}", use_container_width=True):
                            open_panel(p["id"])
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


# ════════════════════════════════════════════════════════════════════════════
# DETAIL VIEW (create or edit — one page for both)
# ════════════════════════════════════════════════════════════════════════════
else:
    pid = st.session_state["panels_open_id"]
    conn = get_connection()
    panel = conn.execute("SELECT * FROM panels WHERE id = ?", (pid,)).fetchone()
    conn.close()
    if panel is None:
        st.session_state["panels_open_id"] = None
        st.rerun()
    panel_committee_id = panel["committee_id"]

    editable = can_edit_panel(panel_committee_id)
    key_suffix = str(pid)

    st.title(panel["title"])
    if st.button("← Back to all panels"):
        st.session_state["panels_open_id"] = None
        st.rerun()

    owner_name = next((c["name"] for c in committees if c["id"] == panel["committee_id"]), "No committee")
    st.caption(f"Panel #{panel['id']} · Owner: {owner_name}")

    if not editable:
        # ── Read-only view ───────────────────────────────────────────────────
        conn = get_connection()
        track_name = None
        if panel["track_id"]:
            track_name = next((t["name"] for t in tracks if t["id"] == panel["track_id"]), None)
        assigned = conn.execute("""
            SELECT ps.role, s.title, s.name
            FROM panel_speakers ps JOIN speakers s ON s.id = ps.speaker_id
            WHERE ps.panel_id = ?
            ORDER BY (ps.priority_ranking IS NULL), ps.priority_ranking, ps.id
        """, (pid,)).fetchall()
        conn.close()

        st.markdown(f"**Short description:** {panel['short_description'] or '—'}")
        st.markdown(f"**Full description:** {panel['full_description'] or '—'}")

        st.markdown("### Speakers")
        panelists = [r for r in assigned if r["role"] == "panelist"]
        alternates = [r for r in assigned if r["role"] == "alternate"]
        if panelists:
            for row in panelists:
                st.markdown(f"- {speaker_display(row)}")
        else:
            st.markdown("None")
        if alternates:
            st.markdown("**Alternates**")
            for row in alternates:
                st.markdown(f"- {speaker_display(row)}")

        st.markdown(f"**Status:** {panel['status']}")
        st.markdown(f"**Track:** {track_name or '—'}")
        st.markdown(f"**Committee notes:** {panel['committee_notes'] or '—'}")
        st.markdown(f"**Presentation notes:** {panel['presentation_notes'] or '—'}")
        if show_admin_notes:
            st.markdown(f"**Admin notes:** {panel['admin_notes'] or '—'}")

    else:
        # ── Editable form fields (top half) ──────────────────────────────────
        if can_edit_all:
            names = list(committee_options.keys())
            default_idx = names.index(owner_name) if owner_name in names else 0
            committee_choice = st.selectbox("Committee *", names, index=default_idx, key=f"pf_committee_{key_suffix}")
        else:
            st.caption(f"Committee: **{my_committee_name or 'Unassigned'}**")
            committee_choice = my_committee_name

        title = st.text_input("Title *", value=panel["title"], key=f"pf_title_{key_suffix}")
        short_description = st.text_area(
            "One-sentence description", value=panel["short_description"] or "", height=68,
            key=f"pf_short_{key_suffix}"
        )
        full_description = st.text_area(
            "Panel description", value=panel["full_description"] or "", height=150,
            key=f"pf_full_{key_suffix}"
        )

        # ── Speakers (moved up, right below the description) ────────────────
        st.divider()
        st.markdown("### Speakers")

        conn = get_connection()
        assigned = conn.execute("""
            SELECT ps.id AS panel_speaker_id, ps.role, ps.priority_ranking, s.id AS speaker_id,
                   s.title, s.name
            FROM panel_speakers ps JOIN speakers s ON s.id = ps.speaker_id
            WHERE ps.panel_id = ?
            ORDER BY (ps.priority_ranking IS NULL), ps.priority_ranking, ps.id
        """, (pid,)).fetchall()

        # Normalize to a gap-free 1..N sequence spanning both speakers and
        # alternates, so the drag widget always has well-defined positions.
        if any(row["priority_ranking"] != i + 1 for i, row in enumerate(assigned)):
            for i, row in enumerate(assigned):
                conn.execute(
                    "UPDATE panel_speakers SET priority_ranking = ? WHERE id = ?",
                    (i + 1, row["panel_speaker_id"])
                )
            conn.commit()
            assigned = conn.execute("""
                SELECT ps.id AS panel_speaker_id, ps.role, ps.priority_ranking, s.id AS speaker_id,
                       s.title, s.name
                FROM panel_speakers ps JOIN speakers s ON s.id = ps.speaker_id
                WHERE ps.panel_id = ?
                ORDER BY ps.priority_ranking
            """, (pid,)).fetchall()

        all_speakers = conn.execute("SELECT id, title, name FROM speakers ORDER BY name").fetchall()
        # Every speaker's topics, fetched once here and reused below both for
        # the assigned-speakers "Topics" section and the add-a-speaker blank
        # rows — avoids a separate query per speaker in either spot.
        all_topics_rows = conn.execute("SELECT id, speaker_id, topic FROM speaker_topics ORDER BY topic").fetchall()
        conn.close()

        topics_by_speaker_id = {}
        for t in all_topics_rows:
            topics_by_speaker_id.setdefault(t["speaker_id"], []).append(t)

        assigned_ids = {r["speaker_id"] for r in assigned}

        if not assigned:
            st.caption("No speakers assigned yet.")
        else:
            # Build unique drag labels (guards against two speakers sharing
            # the same displayed name).
            label_by_pspid = {}
            seen_labels = set()
            for row in assigned:
                label = speaker_display(row)
                if label in seen_labels:
                    label = f"{label} ({row['panel_speaker_id']})"
                seen_labels.add(label)
                label_by_pspid[row["panel_speaker_id"]] = label
            pspid_by_label = {v: k for k, v in label_by_pspid.items()}

            panelist_labels = [label_by_pspid[r["panel_speaker_id"]] for r in assigned if r["role"] == "panelist"]
            alternate_labels = [label_by_pspid[r["panel_speaker_id"]] for r in assigned if r["role"] == "alternate"]

            # Included in the widget's key so that adding/removing a speaker,
            # or a drag completing, forces a fresh mount showing the current
            # true list — otherwise the component can keep showing its own
            # last-rendered state until a full page reload.
            assigned_signature = "|".join(f"{r['panel_speaker_id']}:{r['role']}" for r in assigned)

            if editable:
                sortable_result = sort_items(
                    [
                        {"header": "Speakers", "items": panelist_labels},
                        {"header": "Alternates", "items": alternate_labels},
                    ],
                    multi_containers=True,
                    direction="vertical",
                    key=f"sortable_speakers_{pid}_{assigned_signature}",
                    custom_style=SORTABLE_STYLE,
                )
                st.caption("Drag to reorder, or drag between Speakers and Alternates.")
            else:
                sortable_result = [
                    {"header": "Speakers", "items": panelist_labels},
                    {"header": "Alternates", "items": alternate_labels},
                ]

            new_order_labels = sortable_result[0]["items"] + sortable_result[1]["items"]
            new_role_by_pspid = {}
            for label in sortable_result[0]["items"]:
                new_role_by_pspid[pspid_by_label[label]] = "panelist"
            for label in sortable_result[1]["items"]:
                new_role_by_pspid[pspid_by_label[label]] = "alternate"

            changed = False
            for i, label in enumerate(new_order_labels):
                pspid = pspid_by_label[label]
                orig = next(r for r in assigned if r["panel_speaker_id"] == pspid)
                if orig["priority_ranking"] != i + 1 or orig["role"] != new_role_by_pspid[pspid]:
                    changed = True
                    break

            if changed:
                conn = get_connection()
                for i, label in enumerate(new_order_labels):
                    pspid = pspid_by_label[label]
                    conn.execute(
                        "UPDATE panel_speakers SET priority_ranking = ?, role = ? WHERE id = ?",
                        (i + 1, new_role_by_pspid[pspid], pspid)
                    )
                conn.commit()
                conn.close()
                st.rerun()

            # ── All assigned speakers' topics at a glance, with a delete
            # button on the right of each — so committees can see why every
            # speaker is on the panel without opening one at a time. ───────
            # Which topics are currently tagged, fetched once for every
            # assigned speaker instead of once per speaker in the loop below
            # (topics_by_speaker_id itself was already fetched further up).
            pspids = [r["panel_speaker_id"] for r in assigned]
            conn = get_connection()
            psh = ",".join("?" * len(pspids))
            all_selected = conn.execute(
                f"SELECT panel_speaker_id, speaker_topic_id FROM panel_speaker_topics "
                f"WHERE panel_speaker_id IN ({psh})",
                pspids
            ).fetchall()
            conn.close()

            selected_ids_by_pspid = {}
            for s in all_selected:
                selected_ids_by_pspid.setdefault(s["panel_speaker_id"], set()).add(s["speaker_topic_id"])

            st.markdown("**Topics**")
            for row in assigned:
                their_topics = topics_by_speaker_id.get(row["speaker_id"], [])
                selected_ids = selected_ids_by_pspid.get(row["panel_speaker_id"], set())
                topic_name_by_id = {t["id"]: t["topic"] for t in their_topics}
                current_topic_names = [
                    topic_name_by_id[tid] for tid in selected_ids if tid in topic_name_by_id
                ]

                rcol1, rcol2, rcol3 = st.columns([2, 4, 1])
                with rcol1:
                    role_suffix = " (Alt)" if row["role"] == "alternate" else ""
                    st.markdown(f"{speaker_display(row)}{role_suffix}")
                with rcol2:
                    new_topics = st.multiselect(
                        "Topics", [t["topic"] for t in their_topics], default=current_topic_names,
                        key=f"topics_{row['panel_speaker_id']}", label_visibility="collapsed"
                    )
                    if set(new_topics) != set(current_topic_names):
                        conn = get_connection()
                        conn.execute(
                            "DELETE FROM panel_speaker_topics WHERE panel_speaker_id = ?",
                            (row["panel_speaker_id"],)
                        )
                        for t in their_topics:
                            if t["topic"] in new_topics:
                                conn.execute(
                                    "INSERT INTO panel_speaker_topics "
                                    "(panel_speaker_id, speaker_topic_id) VALUES (?, ?)",
                                    (row["panel_speaker_id"], t["id"])
                                )
                        conn.commit()
                        conn.close()
                        st.rerun()
                with rcol3:
                    if st.button("Delete", key=f"del_pspeaker_{row['panel_speaker_id']}"):
                        conn = get_connection()
                        delete_panel_speaker(conn, row["panel_speaker_id"])
                        conn.commit()
                        conn.close()
                        st.rerun()

        available = [s for s in all_speakers if s["id"] not in assigned_ids]

        if not all_speakers:
            st.caption("No speakers exist yet — add some in the Speakers page first.")
        elif not available:
            st.caption("All speakers are already assigned to this panel.")
        else:
            st.markdown("**Add a speaker:**")
            extra_key = f"panel_{pid}_extra_rows"
            st.session_state.setdefault(extra_key, 1)

            blank_rows_data = []
            chosen_so_far = set()

            for j in range(st.session_state[extra_key]):
                # Speakers picked in earlier rows of this same batch drop out of
                # later rows' options too, so the same person can't be added twice.
                row_available = [s for s in available if s["id"] not in chosen_so_far]
                options = ["— none —"] + [speaker_display(s) for s in row_available]

                col_sel, col_on, col_top = st.columns([3, 1, 3])
                with col_sel:
                    sel_name = st.selectbox(
                        "Speaker", options, key=f"blank_name_{pid}_{j}", label_visibility="collapsed"
                    )
                match = next((s for s in row_available if speaker_display(s) == sel_name), None)
                if match is not None:
                    sel_id = match["id"]
                    chosen_so_far.add(sel_id)
                    s_topics = topics_by_speaker_id.get(sel_id, [])
                    with col_on:
                        on_panel_new = st.checkbox(
                            "On Panel?", value=True, key=f"blank_onpanel_{pid}_{j}"
                        )
                    with col_top:
                        chosen_topics = st.multiselect(
                            "Topics", [t["topic"] for t in s_topics],
                            key=f"blank_topics_{pid}_{j}", label_visibility="collapsed"
                        )
                    blank_rows_data.append((sel_id, on_panel_new, chosen_topics, s_topics))

            bcols1, bcols2 = st.columns(2)
            with bcols1:
                if st.button("+ Add another speaker", key=f"more_rows_{pid}"):
                    st.session_state[extra_key] += 1
                    st.rerun()
            with bcols2:
                if blank_rows_data and st.button("Save new speakers", key=f"save_new_spk_{pid}"):
                    conn = get_connection()
                    seen_ids = set()
                    for sel_id, on_panel_new, chosen_topics, s_topics in blank_rows_data:
                        if sel_id in seen_ids:
                            continue
                        seen_ids.add(sel_id)
                        cur = conn.execute(
                            "INSERT INTO panel_speakers (panel_id, speaker_id, role) VALUES (?, ?, ?)",
                            (pid, sel_id, "panelist" if on_panel_new else "alternate")
                        )
                        panel_speaker_id = cur.lastrowid
                        for t in s_topics:
                            if t["topic"] in chosen_topics:
                                conn.execute(
                                    "INSERT INTO panel_speaker_topics "
                                    "(panel_speaker_id, speaker_topic_id) VALUES (?, ?)",
                                    (panel_speaker_id, t["id"])
                                )
                    conn.commit()
                    conn.close()

                    prev_count = st.session_state[extra_key]
                    for j in range(prev_count):
                        st.session_state.pop(f"blank_name_{pid}_{j}", None)
                        st.session_state.pop(f"blank_onpanel_{pid}_{j}", None)
                        st.session_state.pop(f"blank_topics_{pid}_{j}", None)
                    st.session_state[extra_key] = 1
                    st.rerun()

        # ── Rest of the editable fields ──────────────────────────────────────
        st.divider()

        cur_track_name = "— none —"
        if panel["track_id"]:
            cur_track_name = next((t["name"] for t in tracks if t["id"] == panel["track_id"]), "— none —")
        track_choice = st.selectbox(
            "Track", track_names, index=track_names.index(cur_track_name), key=f"pf_track_{key_suffix}"
        )

        priority = st.number_input(
            "Committee priority (optional, lower = higher priority)",
            min_value=0, step=1, value=panel["priority_ranking"] or 0,
            key=f"pf_priority_{key_suffix}"
        )

        cur_attendance = panel["expected_attendance"] or "— none —"
        attendance_choice = st.selectbox(
            "Expected attendance (used by the auto-scheduler)", ATTENDANCE_OPTIONS,
            index=ATTENDANCE_OPTIONS.index(cur_attendance) if cur_attendance in ATTENDANCE_OPTIONS else 0,
            key=f"pf_attendance_{key_suffix}"
        )

        conn = get_connection()
        other_panels = conn.execute(
            "SELECT id, title FROM panels WHERE id != ? ORDER BY title", (pid,)
        ).fetchall()
        conflict_rows = conn.execute(
            "SELECT panel_id_a, panel_id_b FROM panel_conflicts WHERE panel_id_a = ? OR panel_id_b = ?",
            (pid, pid)
        ).fetchall()
        conn.close()
        conflict_ids = {(r["panel_id_b"] if r["panel_id_a"] == pid else r["panel_id_a"]) for r in conflict_rows}
        title_by_other_id = {p["id"]: p["title"] for p in other_panels}
        current_conflict_titles = [title_by_other_id[i] for i in conflict_ids if i in title_by_other_id]

        conflict_choice = st.multiselect(
            "Don't schedule at the same time as",
            [p["title"] for p in other_panels],
            default=current_conflict_titles,
            key=f"pf_conflicts_{key_suffix}",
            help="The auto-scheduler will never place these panels in the same time slot."
        )

        committee_notes = st.text_area(
            "Committee notes", value=panel["committee_notes"] or "", key=f"pf_cnotes_{key_suffix}"
        )
        presentation_notes = st.text_area(
            "Presentation notes", value=panel["presentation_notes"] or "", key=f"pf_pnotes_{key_suffix}"
        )
        if show_admin_notes:
            admin_notes = st.text_area(
                "Admin notes", value=panel["admin_notes"] or "", key=f"pf_anotes_{key_suffix}"
            )

        if can_edit_all:
            status_choice = st.selectbox(
                "Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index(panel["status"]),
                key=f"pf_status_{key_suffix}"
            )
        else:
            status_choice = panel["status"]

        save_clicked = st.button("Save panel", key=f"pf_save_{key_suffix}")

        if save_clicked:
            if not title.strip():
                st.error("Title is required.")
            else:
                track_id = None
                if track_choice != "— none —":
                    track_id = next(t["id"] for t in tracks if t["name"] == track_choice)
                chosen_committee_id = committee_options[committee_choice] if can_edit_all else my_committee_id

                conn = get_connection()
                fields = {
                    "title": title.strip(),
                    "short_description": short_description.strip() or None,
                    "full_description": full_description.strip() or None,
                    "track_id": track_id,
                    "priority_ranking": priority or None,
                    "committee_notes": committee_notes.strip() or None,
                    "presentation_notes": presentation_notes.strip() or None,
                    "status": status_choice,
                    "expected_attendance": None if attendance_choice == "— none —" else attendance_choice,
                }
                if can_edit_all:
                    fields["committee_id"] = chosen_committee_id
                if show_admin_notes:
                    fields["admin_notes"] = admin_notes.strip() or None

                set_clause = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(f"UPDATE panels SET {set_clause} WHERE id = ?", (*fields.values(), pid))

                other_id_by_title = {p["title"]: p["id"] for p in other_panels}
                new_conflict_ids = {other_id_by_title[t] for t in conflict_choice}
                conn.execute("DELETE FROM panel_conflicts WHERE panel_id_a = ? OR panel_id_b = ?", (pid, pid))
                for other_id in new_conflict_ids:
                    add_panel_conflict(conn, pid, other_id)

                conn.commit()
                conn.close()
                st.success("Panel saved.")
                st.rerun()

        # ── Delete ────────────────────────────────────────────────────────────
        st.divider()
        if st.button("Delete panel", key=f"del_{pid}"):
            conn = get_connection()
            delete_panel(conn, pid)
            conn.commit()
            conn.close()
            st.session_state["panels_open_id"] = None
            st.rerun()
