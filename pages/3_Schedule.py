"""
3_Schedule.py
-------------
Build and edit the conference schedule.

Model:
  - schedule_slots is the grid itself: a bookable (date, room, start, end)
    row, independent of whether any panel occupies it. Admins define and
    edit this grid directly here (generate a starting set from the global
    defaults, then add/remove/adjust individual slots by hand).
  - schedule maps a panel onto a slot. Assignment here is manual — an
    automatic scheduler that fills slots from approved panels is a planned
    future addition, not this page.
"""

from datetime import datetime, timedelta

import streamlit as st
from database import get_connection, delete_schedule_slot
from auth import require_login, has_permission
from layout import widen_content

widen_content()
require_login()

if not has_permission("can_manage_schedule"):
    st.error("You don't have permission to access this page.")
    st.stop()

st.title("Schedule")

TIMES = [f"{h:02d}:{m:02d}" for h in range(6, 24) for m in (0, 30)]


def times_including(*values):
    """The standard 30-min time grid, plus any specific values that don't
    land on it (e.g. a slot generated from a 75-min duration ends at 10:15)
    so they're always selectable instead of crashing TIMES.index(...)."""
    extra = [v for v in values if v and v not in TIMES]
    return sorted(TIMES + extra)


def generate_slot_times(start_time, end_time, panel_duration, break_minutes, lunch_start, lunch_end):
    """Return a list of (slot_start, slot_end, is_lunch) for a day's schedule.
    The lunch window (if any) becomes its own real row instead of a gap, so
    a panel can still be placed there when one draws a big enough audience
    to be worth scheduling opposite lunch."""
    slots = []
    fmt = "%H:%M"
    current = datetime.strptime(start_time, fmt)
    day_end = datetime.strptime(end_time, fmt)
    panel_td = timedelta(minutes=panel_duration)
    break_td = timedelta(minutes=break_minutes)
    ls = datetime.strptime(lunch_start, fmt) if lunch_start else None
    le = datetime.strptime(lunch_end, fmt) if lunch_end else None
    lunch_inserted = False

    while current + panel_td <= day_end:
        slot_end = current + panel_td
        if ls and le and current < le and slot_end > ls:
            if not lunch_inserted:
                slots.append((ls.strftime(fmt), le.strftime(fmt), True))
                lunch_inserted = True
            current = le
            continue
        slots.append((current.strftime(fmt), slot_end.strftime(fmt), False))
        current = slot_end + break_td
    return slots


conn = get_connection()
days = conn.execute("SELECT * FROM conference_days ORDER BY day_order").fetchall()
config = conn.execute("SELECT * FROM conference_config WHERE id = 1").fetchone()
rooms = conn.execute("SELECT * FROM rooms ORDER BY name").fetchall()
conn.close()

if not days:
    st.info("No conference dates set up yet. Add them in Admin → Conference Days first.")
    st.stop()

if not rooms:
    st.warning("No rooms set up yet. Add them in Admin → Rooms before building the schedule.")

st.session_state.setdefault("schedule_editing_slot", None)

day_labels = [f"{d['day_name']} ({d['date']})" for d in days]
day_tabs = st.tabs(day_labels)

for day, tab in zip(days, day_tabs):
    with tab:
        conn = get_connection()
        slots = conn.execute("""
            SELECT ss.*, r.name AS room_name
            FROM schedule_slots ss LEFT JOIN rooms r ON r.id = ss.room_id
            WHERE ss.date = ?
            ORDER BY ss.start_time, r.name
        """, (day["date"],)).fetchall()
        assignments = conn.execute("""
            SELECT s.slot_id, s.panel_id, p.title AS panel_title
            FROM schedule s JOIN panels p ON p.id = s.panel_id
            WHERE s.slot_id IN (SELECT id FROM schedule_slots WHERE date = ?)
        """, (day["date"],)).fetchall()
        conn.close()

        panel_by_slot_id = {a["slot_id"]: a for a in assignments}

        # ── No slots yet: offer to bulk-generate from the global defaults ────
        if not slots:
            st.info("No time slots defined for this day yet.")
            with st.form(f"gen_slots_{day['id']}"):
                st.markdown("**Generate slots from defaults**")
                gc1, gc2 = st.columns(2)
                with gc1:
                    g_start = st.selectbox(
                        "Day start", TIMES,
                        index=TIMES.index(config["default_start_time"] or "09:00"),
                        key=f"gs_{day['id']}"
                    )
                    g_duration = st.number_input(
                        "Panel duration (min)", min_value=15, max_value=240,
                        value=config["default_panel_duration"] or 90, step=5, key=f"gd_{day['id']}"
                    )
                    max_concurrent = len(rooms) if rooms else 20
                    g_concurrent = st.number_input(
                        "Concurrent panels (rooms in use)", min_value=1, max_value=max_concurrent,
                        value=min(config["default_concurrent_panels"] or 3, max_concurrent),
                        key=f"gcc_{day['id']}"
                    )
                with gc2:
                    g_end = st.selectbox(
                        "Day end", TIMES,
                        index=TIMES.index(config["default_end_time"] or "17:30"),
                        key=f"ge_{day['id']}"
                    )
                    g_break = st.number_input(
                        "Break between panels (min)", min_value=0, max_value=60,
                        value=config["default_break_minutes"] or 15, step=5, key=f"gb_{day['id']}"
                    )
                    g_has_lunch = st.checkbox("Include a lunch break", key=f"glhas_{day['id']}")
                    lc1, lc2 = st.columns(2)
                    with lc1:
                        g_lunch_start = st.selectbox(
                            "Lunch start", TIMES, index=TIMES.index("12:00"), key=f"gls_{day['id']}"
                        )
                    with lc2:
                        g_lunch_duration = st.number_input(
                            "Lunch duration (min)", min_value=15, max_value=240, value=90, step=5,
                            key=f"gld_{day['id']}"
                        )
                    st.caption(
                        "Only used if \"Include a lunch break\" is checked. Lunch still appears as a "
                        "normal row in the grid below, so a panel can be placed there if needed."
                    )
                generate = st.form_submit_button("Generate slots")

            if generate:
                if not rooms:
                    st.error("Add at least one room in Admin first.")
                else:
                    if g_has_lunch:
                        g_lunch_end = (
                            datetime.strptime(g_lunch_start, "%H:%M") + timedelta(minutes=g_lunch_duration)
                        ).strftime("%H:%M")
                    else:
                        g_lunch_start = g_lunch_end = None
                    times = generate_slot_times(g_start, g_end, g_duration, g_break, g_lunch_start, g_lunch_end)
                    use_rooms = rooms[:g_concurrent]
                    conn = get_connection()
                    for s, e, _is_lunch in times:
                        for room in use_rooms:
                            conn.execute(
                                "INSERT INTO schedule_slots (date, room_id, start_time, end_time) "
                                "VALUES (?, ?, ?, ?)",
                                (day["date"], room["id"], s, e)
                            )
                    if g_lunch_start:
                        conn.execute(
                            "UPDATE conference_days SET lunch_start = ?, lunch_end = ? WHERE id = ?",
                            (g_lunch_start, g_lunch_end, day["id"])
                        )
                    conn.commit()
                    conn.close()
                    st.success(f"Generated {len(times)} time slot(s) × {len(use_rooms)} room(s).")
                    st.rerun()

        # ── Grid view ──────────────────────────────────────────────────────
        else:
            distinct_times = sorted({(s["start_time"], s["end_time"]) for s in slots})
            room_name_by_id = {}
            distinct_room_ids = []
            for s in slots:
                rid = s["room_id"]
                if rid not in room_name_by_id:
                    room_name_by_id[rid] = s["room_name"] or "No room"
                    distinct_room_ids.append(rid)
            distinct_room_ids.sort(key=lambda rid: room_name_by_id[rid])

            slot_by_time_room = {(s["start_time"], s["end_time"], s["room_id"]): s for s in slots}

            header_cols = st.columns([1.3] + [1] * len(distinct_room_ids))
            header_cols[0].markdown("**Time**")
            for i, rid in enumerate(distinct_room_ids):
                header_cols[i + 1].markdown(f"**{room_name_by_id[rid]}**")

            for (start, end) in distinct_times:
                row_cols = st.columns([1.3] + [1] * len(distinct_room_ids))
                is_lunch_row = (start, end) == (day["lunch_start"], day["lunch_end"])
                label = f"{start}–{end}" + ("  ·  Lunch" if is_lunch_row else "")
                row_cols[0].caption(label)
                for i, rid in enumerate(distinct_room_ids):
                    with row_cols[i + 1]:
                        slot = slot_by_time_room.get((start, end, rid))
                        if slot is None:
                            st.caption("—")
                        else:
                            assignment = panel_by_slot_id.get(slot["id"])
                            label = assignment["panel_title"] if assignment else "Empty"
                            if st.button(label, key=f"slotbtn_{slot['id']}", use_container_width=True):
                                st.session_state["schedule_editing_slot"] = slot["id"]
                                st.rerun()

            # ── Bulk-edit a row (all slots sharing a start time) ────────────
            st.divider()
            st.markdown("**Bulk-edit a time row**")
            time_labels = [f"{s}–{e}" for s, e in distinct_times]
            chosen_row = st.selectbox("Row", time_labels, key=f"row_choice_{day['id']}")
            orig_start, orig_end = distinct_times[time_labels.index(chosen_row)]

            row_times = times_including(orig_start)
            rb1, rb2 = st.columns(2)
            with rb1:
                with st.form(f"row_shift_{day['id']}"):
                    st.caption("Shift this row's start time (each slot keeps its own duration).")
                    new_row_start = st.selectbox(
                        "New start time", row_times, index=row_times.index(orig_start), key=f"rrs_{day['id']}"
                    )
                    apply_shift = st.form_submit_button("Shift row")
            with rb2:
                with st.form(f"row_duration_{day['id']}"):
                    st.caption("Or set the same duration for every slot in this row.")
                    row_duration = st.number_input(
                        "Duration (min)", min_value=15, max_value=240, value=90, step=5,
                        key=f"rrd_{day['id']}"
                    )
                    apply_duration = st.form_submit_button("Set row duration")

            if apply_shift:
                delta = datetime.strptime(new_row_start, "%H:%M") - datetime.strptime(orig_start, "%H:%M")
                conn = get_connection()
                row_slots = conn.execute(
                    "SELECT * FROM schedule_slots WHERE date = ? AND start_time = ?",
                    (day["date"], orig_start)
                ).fetchall()
                for rs in row_slots:
                    new_s = (datetime.strptime(rs["start_time"], "%H:%M") + delta).strftime("%H:%M")
                    new_e = (datetime.strptime(rs["end_time"], "%H:%M") + delta).strftime("%H:%M")
                    conn.execute(
                        "UPDATE schedule_slots SET start_time = ?, end_time = ? WHERE id = ?",
                        (new_s, new_e, rs["id"])
                    )
                conn.commit()
                conn.close()
                st.success("Row shifted.")
                st.rerun()

            if apply_duration:
                conn = get_connection()
                row_slots = conn.execute(
                    "SELECT * FROM schedule_slots WHERE date = ? AND start_time = ?",
                    (day["date"], orig_start)
                ).fetchall()
                for rs in row_slots:
                    new_e = (datetime.strptime(rs["start_time"], "%H:%M")
                             + timedelta(minutes=row_duration)).strftime("%H:%M")
                    conn.execute(
                        "UPDATE schedule_slots SET end_time = ? WHERE id = ?", (new_e, rs["id"])
                    )
                conn.commit()
                conn.close()
                st.success("Row duration updated.")
                st.rerun()

        # ── Add an individual slot ─────────────────────────────────────────
        st.divider()
        with st.form(f"add_slot_{day['id']}", clear_on_submit=True):
            st.markdown("**Add a slot**")
            ac1, ac2, ac3 = st.columns(3)
            with ac1:
                a_room = st.selectbox(
                    "Room", [r["name"] for r in rooms] if rooms else ["— add a room first —"],
                    key=f"as_room_{day['id']}"
                )
            with ac2:
                a_start = st.selectbox("Start", TIMES, index=TIMES.index("09:00"), key=f"as_start_{day['id']}")
            with ac3:
                a_end = st.selectbox("End", TIMES, index=TIMES.index("10:30"), key=f"as_end_{day['id']}")
            add_slot = st.form_submit_button("Add slot")

        if add_slot:
            if not rooms:
                st.error("Add a room in Admin first.")
            else:
                room_id = next(r["id"] for r in rooms if r["name"] == a_room)
                conn = get_connection()
                conn.execute(
                    "INSERT INTO schedule_slots (date, room_id, start_time, end_time) VALUES (?, ?, ?, ?)",
                    (day["date"], room_id, a_start, a_end)
                )
                conn.commit()
                conn.close()
                st.success("Slot added.")
                st.rerun()

        # ── Slot editor (opened by clicking a grid cell) ────────────────────
        editing_id = st.session_state["schedule_editing_slot"]
        if editing_id is not None:
            conn = get_connection()
            slot_row = conn.execute("SELECT * FROM schedule_slots WHERE id = ?", (editing_id,)).fetchone()
            conn.close()

            if slot_row is not None and slot_row["date"] == day["date"]:
                st.divider()
                st.markdown(f"### Edit slot — {slot_row['start_time']}–{slot_row['end_time']}")

                conn = get_connection()
                available_panels = conn.execute("""
                    SELECT p.id, p.title
                    FROM panels p
                    LEFT JOIN schedule s ON s.panel_id = p.id
                    WHERE s.id IS NULL OR s.slot_id = ?
                    ORDER BY p.title
                """, (editing_id,)).fetchall()
                current_assignment = conn.execute(
                    "SELECT * FROM schedule WHERE slot_id = ?", (editing_id,)
                ).fetchone()
                conn.close()

                room_names = [r["name"] for r in rooms]
                cur_room_name = next(
                    (r["name"] for r in rooms if r["id"] == slot_row["room_id"]), "— no room —"
                )
                room_choices = ["— no room —"] + room_names

                panel_choices = ["— empty —"] + [p["title"] for p in available_panels]
                cur_panel_title = "— empty —"
                if current_assignment:
                    match = next((p for p in available_panels if p["id"] == current_assignment["panel_id"]), None)
                    if match:
                        cur_panel_title = match["title"]

                editor_times = times_including(slot_row["start_time"], slot_row["end_time"])

                ec1, ec2, ec3 = st.columns(3)
                with ec1:
                    e_room = st.selectbox(
                        "Room", room_choices,
                        index=room_choices.index(cur_room_name) if cur_room_name in room_choices else 0,
                        key=f"e_room_{editing_id}"
                    )
                with ec2:
                    e_start = st.selectbox(
                        "Start", editor_times, index=editor_times.index(slot_row["start_time"]),
                        key=f"e_start_{editing_id}"
                    )
                with ec3:
                    e_end = st.selectbox(
                        "End", editor_times, index=editor_times.index(slot_row["end_time"]),
                        key=f"e_end_{editing_id}"
                    )

                e_panel = st.selectbox(
                    "Assigned panel", panel_choices,
                    index=panel_choices.index(cur_panel_title) if cur_panel_title in panel_choices else 0,
                    key=f"e_panel_{editing_id}"
                )

                save_col, delete_col, close_col = st.columns(3)
                with save_col:
                    save_slot = st.button("Save slot", key=f"save_slot_{editing_id}", use_container_width=True)
                with delete_col:
                    delete_slot = st.button("Delete slot", key=f"delete_slot_{editing_id}", use_container_width=True)
                with close_col:
                    close_editor = st.button("Close", key=f"close_slot_{editing_id}", use_container_width=True)

                if save_slot:
                    new_room_id = None
                    if e_room != "— no room —":
                        new_room_id = next(r["id"] for r in rooms if r["name"] == e_room)

                    conn = get_connection()
                    conn.execute(
                        "UPDATE schedule_slots SET room_id = ?, start_time = ?, end_time = ? WHERE id = ?",
                        (new_room_id, e_start, e_end, editing_id)
                    )
                    conn.execute("DELETE FROM schedule WHERE slot_id = ?", (editing_id,))
                    if e_panel != "— empty —":
                        panel_id = next(p["id"] for p in available_panels if p["title"] == e_panel)
                        conn.execute(
                            "INSERT INTO schedule (slot_id, panel_id) VALUES (?, ?)",
                            (editing_id, panel_id)
                        )
                    conn.commit()
                    conn.close()
                    st.success("Slot saved.")
                    st.session_state["schedule_editing_slot"] = None
                    st.rerun()

                if delete_slot:
                    conn = get_connection()
                    delete_schedule_slot(conn, editing_id)
                    conn.commit()
                    conn.close()
                    st.session_state["schedule_editing_slot"] = None
                    st.rerun()

                if close_editor:
                    st.session_state["schedule_editing_slot"] = None
                    st.rerun()
