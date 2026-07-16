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

from datetime import datetime, timedelta, time as dtime

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


def to_time(hhmm):
    return datetime.strptime(hhmm, "%H:%M").time()


def to_str(t):
    return t.strftime("%H:%M")


def to_12h_display(hhmm):
    t = to_time(hhmm)
    h12 = t.hour % 12 or 12
    return f"{h12}:{t.minute:02d}"


def parse_time_text(raw, fallback_meridiem):
    """Parse a quick-to-type time string into a datetime.time, or None if it
    can't be parsed. Accepts an optional colon and an optional trailing
    a/p/am/pm marker (case-insensitive) — '930a', '9:30am', '215p', '9:30'
    (uses fallback_meridiem), and '14:30'/'1430' (always read as 24-hour,
    marker or not, since 13-23 isn't a valid 12-hour hour) all work."""
    if not raw:
        return None
    s = raw.strip().lower().replace(" ", "")
    if not s:
        return None

    meridiem = None
    for suffix, mer in (("am", "AM"), ("pm", "PM"), ("a", "AM"), ("p", "PM")):
        if s.endswith(suffix):
            meridiem = mer
            s = s[: -len(suffix)]
            break

    if ":" in s:
        hh_s, mm_s = s.split(":", 1)
    elif len(s) > 2:
        hh_s, mm_s = s[:-2], s[-2:]
    else:
        hh_s, mm_s = s, "0"

    if not hh_s.isdigit() or not mm_s.isdigit():
        return None
    hh, mm = int(hh_s), int(mm_s)
    if not (0 <= mm < 60):
        return None

    if meridiem is None:
        if not (0 <= hh <= 23):
            return None
        hour24 = ((hh % 12) + (12 if fallback_meridiem == "PM" else 0)) if hh <= 12 else hh
    else:
        if not (1 <= hh <= 12):
            return None
        hour24 = (hh % 12) + (12 if meridiem == "PM" else 0)

    return dtime(hour24, mm)


def time_field(label, key, default_hhmm):
    """Typable time entry: a plain text box (accepts '930a', '2:15 pm',
    '14:30', or just '930' using the AM/PM toggle below) plus a click
    toggle for when the text alone doesn't specify a meridiem — typing
    a/p in the text always wins over the toggle. Returns a 24-hour
    'HH:MM' string. Built from text_input + segmented_control rather than
    st.time_input so it's a real text box, not a dropdown/scroll picker,
    and both widgets are form-safe (unlike a plain st.button)."""
    text_key = f"{key}_text"
    mer_key = f"{key}_mer"

    if text_key not in st.session_state:
        st.session_state[text_key] = to_12h_display(default_hhmm)
    if mer_key not in st.session_state:
        st.session_state[mer_key] = "AM" if to_time(default_hhmm).hour < 12 else "PM"

    tcol, mcol = st.columns([2, 1])
    with tcol:
        raw = st.text_input(label, key=text_key)
    with mcol:
        st.write("")
        meridiem = st.segmented_control(
            "AM/PM", ["AM", "PM"], key=mer_key, label_visibility="collapsed"
        )

    fallback = meridiem if meridiem in ("AM", "PM") else "AM"
    parsed = parse_time_text(raw, fallback) or to_time(default_hhmm)
    return to_str(parsed)


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


@st.dialog("Edit slot")
def edit_slot_dialog(slot_id):
    conn = get_connection()
    slot_row = conn.execute("SELECT * FROM schedule_slots WHERE id = ?", (slot_id,)).fetchone()
    if slot_row is None:
        conn.close()
        st.info("This slot no longer exists.")
        return

    rooms = conn.execute("SELECT * FROM rooms ORDER BY name").fetchall()
    available_panels = conn.execute("""
        SELECT p.id, p.title
        FROM panels p
        LEFT JOIN schedule s ON s.panel_id = p.id
        WHERE s.id IS NULL OR s.slot_id = ?
        ORDER BY p.title
    """, (slot_id,)).fetchall()
    current_assignment = conn.execute(
        "SELECT * FROM schedule WHERE slot_id = ?", (slot_id,)
    ).fetchone()
    conn.close()

    room_names = [r["name"] for r in rooms]
    cur_room_name = next((r["name"] for r in rooms if r["id"] == slot_row["room_id"]), "— no room —")
    room_choices = ["— no room —"] + room_names

    panel_choices = ["— empty —"] + [p["title"] for p in available_panels]
    cur_panel_title = "— empty —"
    if current_assignment:
        match = next((p for p in available_panels if p["id"] == current_assignment["panel_id"]), None)
        if match:
            cur_panel_title = match["title"]

    e_room = st.selectbox(
        "Room", room_choices,
        index=room_choices.index(cur_room_name) if cur_room_name in room_choices else 0
    )
    tc1, tc2 = st.columns(2)
    with tc1:
        e_start = time_field("Start", f"e_start_{slot_id}", slot_row["start_time"])
    with tc2:
        e_end = time_field("End", f"e_end_{slot_id}", slot_row["end_time"])

    e_panel = st.selectbox(
        "Assigned panel", panel_choices,
        index=panel_choices.index(cur_panel_title) if cur_panel_title in panel_choices else 0
    )

    save_col, delete_col = st.columns(2)
    with save_col:
        save_slot = st.button("Save slot", use_container_width=True)
    with delete_col:
        delete_slot = st.button("Delete slot", use_container_width=True)

    if save_slot:
        if to_time(e_end) <= to_time(e_start):
            st.error("End time must be after start time.")
        else:
            new_room_id = None
            if e_room != "— no room —":
                new_room_id = next(r["id"] for r in rooms if r["name"] == e_room)

            conn = get_connection()
            conn.execute(
                "UPDATE schedule_slots SET room_id = ?, start_time = ?, end_time = ? WHERE id = ?",
                (new_room_id, e_start, e_end, slot_id)
            )
            conn.execute("DELETE FROM schedule WHERE slot_id = ?", (slot_id,))
            if e_panel != "— empty —":
                panel_id = next(p["id"] for p in available_panels if p["title"] == e_panel)
                conn.execute("INSERT INTO schedule (slot_id, panel_id) VALUES (?, ?)", (slot_id, panel_id))
            conn.commit()
            conn.close()
            st.rerun()

    if delete_slot:
        conn = get_connection()
        delete_schedule_slot(conn, slot_id)
        conn.commit()
        conn.close()
        st.rerun()


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
                    g_start = time_field(
                        "Day start", f"gs_{day['id']}", config["default_start_time"] or "09:00"
                    )
                    g_duration = st.number_input(
                        "Panel duration (min)", min_value=1, max_value=240,
                        value=config["default_panel_duration"] or 90, step=5, key=f"gd_{day['id']}"
                    )
                with gc2:
                    g_end = time_field(
                        "Day end", f"ge_{day['id']}", config["default_end_time"] or "17:30"
                    )
                    g_break = st.number_input(
                        "Break between panels (min)", min_value=0, max_value=60,
                        value=config["default_break_minutes"] or 15, step=5, key=f"gb_{day['id']}"
                    )

                if rooms:
                    default_n = min(config["default_concurrent_panels"] or 3, len(rooms))
                    g_rooms = st.multiselect(
                        "Rooms to generate for", [r["name"] for r in rooms],
                        default=[r["name"] for r in rooms[:default_n]], key=f"gr_{day['id']}"
                    )
                else:
                    g_rooms = []
                    st.caption("Add rooms in Admin first.")

                g_has_lunch = st.checkbox("Include a lunch break", key=f"glhas_{day['id']}")
                lc1, lc2 = st.columns(2)
                with lc1:
                    g_lunch_start = time_field("Lunch start", f"gls_{day['id']}", "12:00")
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
                if not g_rooms:
                    st.error("Choose at least one room.")
                elif to_time(g_end) <= to_time(g_start):
                    st.error("Day end must be after day start.")
                else:
                    if g_has_lunch:
                        lunch_start_str = g_lunch_start
                        lunch_end_str = (
                            datetime.combine(datetime.today(), to_time(g_lunch_start))
                            + timedelta(minutes=g_lunch_duration)
                        ).strftime("%H:%M")
                    else:
                        lunch_start_str = lunch_end_str = None
                    times = generate_slot_times(
                        g_start, g_end, g_duration, g_break, lunch_start_str, lunch_end_str
                    )
                    use_rooms = [r for r in rooms if r["name"] in g_rooms]
                    conn = get_connection()
                    for s, e, _is_lunch in times:
                        for room in use_rooms:
                            conn.execute(
                                "INSERT INTO schedule_slots (date, room_id, start_time, end_time) "
                                "VALUES (?, ?, ?, ?)",
                                (day["date"], room["id"], s, e)
                            )
                    if lunch_start_str:
                        conn.execute(
                            "UPDATE conference_days SET lunch_start = ?, lunch_end = ? WHERE id = ?",
                            (lunch_start_str, lunch_end_str, day["id"])
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

            st.caption("Click any slot to open it in a popup for editing.")
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
                            btn_label = assignment["panel_title"] if assignment else "Empty"
                            if st.button(btn_label, key=f"slotbtn_{slot['id']}", use_container_width=True):
                                edit_slot_dialog(slot["id"])

            # ── Bulk-edit a row (all slots sharing a start time) ────────────
            st.divider()
            st.markdown("**Bulk-edit a time row**")
            time_labels = [f"{s}–{e}" for s, e in distinct_times]
            chosen_row = st.selectbox("Row", time_labels, key=f"row_choice_{day['id']}")
            orig_start, orig_end = distinct_times[time_labels.index(chosen_row)]

            rb1, rb2 = st.columns(2)
            with rb1:
                with st.form(f"row_shift_{day['id']}"):
                    st.caption("Shift this row's start time (each slot keeps its own duration).")
                    new_row_start = time_field(
                        "New start time", f"rrs_{day['id']}_{orig_start}", orig_start
                    )
                    apply_shift = st.form_submit_button("Shift row")
            with rb2:
                with st.form(f"row_duration_{day['id']}"):
                    st.caption("Or set the same duration for every slot in this row.")
                    row_duration = st.number_input(
                        "Duration (min)", min_value=1, max_value=240, value=90, step=5,
                        key=f"rrd_{day['id']}"
                    )
                    apply_duration = st.form_submit_button("Set row duration")

            if apply_shift:
                delta = datetime.combine(datetime.today(), to_time(new_row_start)) - datetime.combine(
                    datetime.today(), to_time(orig_start)
                )
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
                a_start = time_field("Start", f"as_start_{day['id']}", "09:00")
            with ac3:
                a_end = time_field("End", f"as_end_{day['id']}", "10:30")
            add_slot = st.form_submit_button("Add slot")

        if add_slot:
            if not rooms:
                st.error("Add a room in Admin first.")
            elif to_time(a_end) <= to_time(a_start):
                st.error("End time must be after start time.")
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
