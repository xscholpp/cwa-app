"""
0_Admin.py
----------
Admin section: manage users, conference days, committees, tracks, and rooms.
Only accessible to users with admin permissions.
"""

import streamlit as st
from database import get_connection
from auth import (
    require_login, has_permission, get_current_user,
    create_user, PRESETS, PERMISSION_LABELS, PERMISSIONS,
    apply_preset_to_session,
)

require_login()

# Block access if user has no admin permissions at all
if not has_permission("can_manage_users") and not has_permission("can_manage_admin_settings"):
    st.error("You don't have permission to access this page.")
    st.stop()

st.title("Admin")

# Build tab list based on what this user can access
tab_names = []
if has_permission("can_manage_users"):
    tab_names.append("Users")
if has_permission("can_manage_admin_settings"):
    tab_names.extend(["Conference Days", "Committees", "Tracks", "Rooms"])

tabs = st.tabs(tab_names)
tab_index = 0


# ══════════════════════════════════════════════════════════════════════════════
# USERS TAB
# ══════════════════════════════════════════════════════════════════════════════
if has_permission("can_manage_users"):
    with tabs[tab_index]:
        tab_index += 1

        conn = get_connection()
        users = conn.execute("""
            SELECT u.*, c.name AS committee_name
            FROM users u
            LEFT JOIN committees c ON u.committee_id = c.id
            ORDER BY u.display_name
        """).fetchall()
        committees = conn.execute("SELECT id, name FROM committees ORDER BY name").fetchall()
        conn.close()

        committee_options = {c["name"]: c["id"] for c in committees}
        current_user = get_current_user()

        # ── List existing users ───────────────────────────────────────────────
        st.subheader("All users")
        if not users:
            st.info("No users yet.")
        else:
            for u in users:
                label = f"{u['display_name']}  (@{u['username']})"
                if u["committee_name"]:
                    label += f"  ·  {u['committee_name']}"
                with st.expander(label):
                    # Show which permissions are on
                    active = [PERMISSION_LABELS[p] for p in PERMISSIONS if u[p]]
                    st.markdown("**Active permissions:**")
                    if active:
                        for perm in active:
                            st.markdown(f"- {perm}")
                    else:
                        st.markdown("None")

                    # Edit permissions inline
                    with st.form(f"edit_user_{u['id']}"):
                        st.markdown("**Edit permissions:**")
                        new_perms = {
                            perm: st.checkbox(label, value=bool(u[perm]), key=f"ep_{u['id']}_{perm}")
                            for perm, label in PERMISSION_LABELS.items()
                        }
                        save = st.form_submit_button("Save changes")

                    if save:
                        conn = get_connection()
                        conn.execute("""
                            UPDATE users SET
                                can_manage_users          = ?,
                                can_manage_admin_settings = ?,
                                can_view_all_panels       = ?,
                                can_edit_all_panels       = ?,
                                can_manage_speakers       = ?,
                                can_manage_schedule       = ?
                            WHERE id = ?
                        """, (
                            int(new_perms["can_manage_users"]),
                            int(new_perms["can_manage_admin_settings"]),
                            int(new_perms["can_view_all_panels"]),
                            int(new_perms["can_edit_all_panels"]),
                            int(new_perms["can_manage_speakers"]),
                            int(new_perms["can_manage_schedule"]),
                            u["id"],
                        ))
                        conn.commit()
                        conn.close()
                        # Refresh session state if the admin just edited their own account
                        if current_user and current_user["id"] == u["id"]:
                            for perm in PERMISSIONS:
                                st.session_state["user"][perm] = int(new_perms[perm])
                        st.success("Permissions updated.")
                        st.rerun()

                    st.divider()
                    if current_user and u["id"] == current_user["id"]:
                        st.caption("You cannot delete your own account.")
                    else:
                        if st.button("Delete user", key=f"del_user_{u['id']}"):
                            conn = get_connection()
                            conn.execute("DELETE FROM users WHERE id = ?", (u["id"],))
                            conn.commit()
                            conn.close()
                            st.rerun()

        # ── Add new user ──────────────────────────────────────────────────────
        st.divider()
        st.subheader("Add new user")

        # Initialize preset session state on first render
        if "add_user_preset" not in st.session_state:
            st.session_state["add_user_preset"] = "committee_leader"
            apply_preset_to_session()

        st.selectbox(
            "Start from a preset — adjustable below",
            options=list(PRESETS.keys()),
            format_func=lambda x: PRESETS[x]["label"],
            key="add_user_preset",
            on_change=apply_preset_to_session,
        )

        with st.form("add_user_form"):
            col1, col2, col3 = st.columns(3)
            with col1:
                display_name = st.text_input("Display name *")
            with col2:
                username = st.text_input("Username *")
            with col3:
                password = st.text_input("Password *", type="password")

            st.markdown("**Permissions** (auto-filled from preset — adjust as needed):")
            new_perms = {
                perm: st.checkbox(label, key=f"new_perm_{perm}")
                for perm, label in PERMISSION_LABELS.items()
            }

            st.markdown("**Own committee** — panel editing scope for Committee Leaders:")
            if committee_options:
                committee_choice = st.selectbox(
                    "If this user cannot edit all panels, they can add/edit panels for this committee only:",
                    options=["— none —"] + list(committee_options.keys()),
                )
            else:
                st.caption("No committees exist yet — add them in the Committees tab first, then assign here.")
                committee_choice = "— none —"

            submitted = st.form_submit_button("Create user")

        if submitted:
            if not all([display_name.strip(), username.strip(), password]):
                st.error("Display name, username, and password are all required.")
            else:
                cid = committee_options.get(committee_choice) if committee_choice != "— none —" else None
                ok, err = create_user(username, display_name, password, new_perms, cid)
                if ok:
                    st.success(f"User '{display_name.strip()}' created.")
                    st.rerun()
                else:
                    if "UNIQUE" in str(err):
                        st.error(f"The username '{username.strip()}' is already taken.")
                    else:
                        st.error(f"Error: {err}")


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN SETTINGS TABS (Conference Days, Committees, Tracks, Rooms)
# ══════════════════════════════════════════════════════════════════════════════
if has_permission("can_manage_admin_settings"):

    # ── Conference Days ───────────────────────────────────────────────────────
    with tabs[tab_index]:
        tab_index += 1
        from datetime import date, timedelta, datetime

        def generate_slots(start_time, end_time, panel_duration, break_minutes, lunch_start, lunch_end):
            """Return a list of (slot_start, slot_end) strings for a day's schedule."""
            slots = []
            fmt = "%H:%M"
            current = datetime.strptime(start_time, fmt)
            day_end  = datetime.strptime(end_time,   fmt)
            panel_td = timedelta(minutes=panel_duration)
            break_td = timedelta(minutes=break_minutes)
            ls = datetime.strptime(lunch_start, fmt) if lunch_start else None
            le = datetime.strptime(lunch_end,   fmt) if lunch_end   else None

            while current + panel_td <= day_end:
                slot_end = current + panel_td
                if ls and le and current < le and slot_end > ls:
                    current = le
                    continue
                slots.append((current.strftime(fmt), slot_end.strftime(fmt)))
                current = slot_end + break_td
            return slots

        TIMES = [f"{h:02d}:{m:02d}" for h in range(6, 24) for m in (0, 30)]

        conn = get_connection()
        config = conn.execute("SELECT * FROM conference_config WHERE id = 1").fetchone()
        days   = conn.execute("SELECT * FROM conference_days ORDER BY day_order").fetchall()
        conn.close()

        # ── Global defaults ───────────────────────────────────────────────────
        st.subheader("Global schedule defaults")
        st.caption("These apply to every day unless a day overrides them.")

        with st.form("config_form"):
            gc1, gc2, gc3 = st.columns(3)
            with gc1:
                cfg_duration    = st.number_input("Panel duration (min)", min_value=15, max_value=240,
                                                   value=config["default_panel_duration"] or 90, step=5)
                cfg_break       = st.number_input("Break between panels (min)", min_value=0, max_value=60,
                                                   value=config["default_break_minutes"] or 15, step=5)
            with gc2:
                cfg_concurrent  = st.number_input("Concurrent panels (rooms in use)", min_value=1, max_value=20,
                                                   value=config["default_concurrent_panels"] or 3)
            with gc3:
                cfg_start = st.selectbox("Default day start", TIMES,
                                          index=TIMES.index(config["default_start_time"] or "09:00"))
                cfg_end   = st.selectbox("Default day end",   TIMES,
                                          index=TIMES.index(config["default_end_time"]   or "17:30"))
            save_config = st.form_submit_button("Save defaults")

        if save_config:
            conn = get_connection()
            conn.execute("""
                UPDATE conference_config SET
                    default_panel_duration    = ?,
                    default_break_minutes     = ?,
                    default_concurrent_panels = ?,
                    default_start_time        = ?,
                    default_end_time          = ?
                WHERE id = 1
            """, (cfg_duration, cfg_break, cfg_concurrent, cfg_start, cfg_end))
            conn.commit()
            conn.close()
            st.success("Defaults saved.")
            st.rerun()

        # ── Add conference days ───────────────────────────────────────────────
        st.divider()
        st.subheader("Conference dates")

        with st.form("add_days_form"):
            date_range = st.date_input(
                "Select conference dates",
                value=(),
                help="Click a start date then an end date to select a range, or pick individual dates."
            )
            add_days = st.form_submit_button("Add selected dates")

        if add_days:
            # date_input returns a single date or a tuple of (start, end)
            if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
                start_d, end_d = date_range
                selected = [start_d + timedelta(days=i) for i in range((end_d - start_d).days + 1)]
            elif isinstance(date_range, date):
                selected = [date_range]
            else:
                selected = list(date_range) if date_range else []

            conn = get_connection()
            added = 0
            for d in selected:
                next_order = conn.execute(
                    "SELECT COALESCE(MAX(day_order) + 1, 0) FROM conference_days"
                ).fetchone()[0]
                try:
                    conn.execute("""
                        INSERT INTO conference_days
                            (date, day_name, day_order, start_time, end_time, concurrent_panels)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        d.isoformat(),
                        d.strftime("%A"),        # "Monday", "Tuesday", etc.
                        next_order,
                        config["default_start_time"] or "09:00",
                        config["default_end_time"]   or "17:30",
                        config["default_concurrent_panels"] or 3,
                    ))
                    added += 1
                except Exception:
                    pass  # skip duplicate dates
            conn.commit()
            conn.close()
            if added:
                st.success(f"{added} day(s) added.")
                st.rerun()
            else:
                st.warning("Those dates are already added.")

        # ── Per-day configuration ─────────────────────────────────────────────
        if not days:
            st.info("No conference dates added yet. Use the date picker above.")
        else:
            st.markdown("---")
            for day in days:
                date_label = f"{day['day_name']}, {day['date']}"
                with st.expander(date_label):
                    with st.form(f"day_form_{day['id']}"):
                        dc1, dc2, dc3 = st.columns(3)
                        with dc1:
                            d_start = st.selectbox("Start time", TIMES,
                                                    index=TIMES.index(day["start_time"] or "09:00"),
                                                    key=f"ds_{day['id']}")
                            d_end   = st.selectbox("End time",   TIMES,
                                                    index=TIMES.index(day["end_time"]   or "17:30"),
                                                    key=f"de_{day['id']}")
                        with dc2:
                            d_concurrent = st.number_input("Concurrent panels", min_value=1, max_value=20,
                                                            value=day["concurrent_panels"] or 3,
                                                            key=f"dc_{day['id']}")
                        with dc3:
                            d_lunch_start = st.selectbox("Lunch start (optional)",
                                                          ["— none —"] + TIMES,
                                                          index=(TIMES.index(day["lunch_start"]) + 1
                                                                 if day["lunch_start"] in TIMES else 0),
                                                          key=f"dls_{day['id']}")
                            d_lunch_end   = st.selectbox("Lunch end (optional)",
                                                          ["— none —"] + TIMES,
                                                          index=(TIMES.index(day["lunch_end"]) + 1
                                                                 if day["lunch_end"] in TIMES else 0),
                                                          key=f"dle_{day['id']}")
                        save_day = st.form_submit_button("Save day settings")

                    if save_day:
                        ls = d_lunch_start if d_lunch_start != "— none —" else None
                        le = d_lunch_end   if d_lunch_end   != "— none —" else None
                        conn = get_connection()
                        conn.execute("""
                            UPDATE conference_days
                            SET start_time=?, end_time=?, concurrent_panels=?, lunch_start=?, lunch_end=?
                            WHERE id=?
                        """, (d_start, d_end, d_concurrent, ls, le, day["id"]))
                        conn.commit()
                        conn.close()
                        st.success("Day updated.")
                        st.rerun()

                    # Slot preview
                    dur  = config["default_panel_duration"] or 90
                    brk  = config["default_break_minutes"]  or 15
                    ls_v = day["lunch_start"] if day["lunch_start"] else None
                    le_v = day["lunch_end"]   if day["lunch_end"]   else None
                    slots = generate_slots(
                        day["start_time"] or "09:00",
                        day["end_time"]   or "17:30",
                        dur, brk, ls_v, le_v
                    )
                    conc = day["concurrent_panels"] or 3
                    st.markdown(f"**Schedule preview** — {len(slots)} slots × {conc} concurrent = "
                                f"**{len(slots) * conc} total panel spots**")
                    for s, e in slots:
                        st.markdown(f"- {s} – {e}")

                    st.divider()
                    if st.button("Remove this day", key=f"del_day_{day['id']}"):
                        conn = get_connection()
                        conn.execute("DELETE FROM conference_days WHERE id = ?", (day["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()

    # ── Committees ────────────────────────────────────────────────────────────
    with tabs[tab_index]:
        tab_index += 1
        st.subheader("Committees")

        conn = get_connection()
        committees = conn.execute("SELECT * FROM committees ORDER BY name").fetchall()
        conn.close()

        if not committees:
            st.info("No committees added yet.")
        else:
            for c in committees:
                with st.expander(c["name"]):
                    st.markdown(f"**Description:** {c['description'] or '—'}")
                    conn = get_connection()
                    panel_count = conn.execute(
                        "SELECT COUNT(*) FROM panels WHERE committee_id = ?", (c["id"],)
                    ).fetchone()[0]
                    conn.close()
                    st.markdown(f"**Panels:** {panel_count}")
                    if st.button("Delete committee", key=f"del_comm_{c['id']}"):
                        conn = get_connection()
                        conn.execute("DELETE FROM committees WHERE id = ?", (c["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()

        st.divider()
        with st.form("add_committee_form", clear_on_submit=True):
            c_name = st.text_input("Committee name *")
            c_desc = st.text_area("Description", height=80)
            submitted = st.form_submit_button("Add committee")
        if submitted:
            if not c_name.strip():
                st.error("Name is required.")
            else:
                conn = get_connection()
                try:
                    conn.execute(
                        "INSERT INTO committees (name, description) VALUES (?, ?)",
                        (c_name.strip(), c_desc.strip() or None)
                    )
                    conn.commit()
                    st.rerun()
                except Exception:
                    st.error(f"A committee named '{c_name.strip()}' already exists.")
                finally:
                    conn.close()

    # ── Tracks ────────────────────────────────────────────────────────────────
    with tabs[tab_index]:
        tab_index += 1
        st.subheader("Tracks")
        st.caption("Named conference tracks, e.g. 'Global Economy', 'Environment'.")

        conn = get_connection()
        tracks = conn.execute("SELECT * FROM tracks ORDER BY name").fetchall()
        conn.close()

        if not tracks:
            st.info("No tracks added yet.")
        else:
            for t in tracks:
                col1, col2 = st.columns([6, 1])
                with col1:
                    st.markdown(f"**{t['name']}**")
                with col2:
                    if st.button("Remove", key=f"del_track_{t['id']}"):
                        conn = get_connection()
                        conn.execute("DELETE FROM tracks WHERE id = ?", (t["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()

        st.divider()
        with st.form("add_track_form", clear_on_submit=True):
            t_name = st.text_input("Track name *")
            submitted = st.form_submit_button("Add track")
        if submitted:
            if not t_name.strip():
                st.error("Track name is required.")
            else:
                conn = get_connection()
                try:
                    conn.execute("INSERT INTO tracks (name) VALUES (?)", (t_name.strip(),))
                    conn.commit()
                    st.rerun()
                except Exception:
                    st.error(f"A track named '{t_name.strip()}' already exists.")
                finally:
                    conn.close()

    # ── Rooms ─────────────────────────────────────────────────────────────────
    with tabs[tab_index]:
        tab_index += 1
        st.subheader("Rooms")

        conn = get_connection()
        rooms = conn.execute("SELECT * FROM rooms ORDER BY name").fetchall()
        conn.close()

        if not rooms:
            st.info("No rooms added yet.")
        else:
            for r in rooms:
                col1, col2 = st.columns([6, 1])
                with col1:
                    cap = f"  ·  Capacity: {r['capacity']}" if r["capacity"] else ""
                    st.markdown(f"**{r['name']}**{cap}")
                with col2:
                    if st.button("Remove", key=f"del_room_{r['id']}"):
                        conn = get_connection()
                        conn.execute("DELETE FROM rooms WHERE id = ?", (r["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()

        st.divider()
        with st.form("add_room_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                r_name = st.text_input("Room name *")
            with col2:
                r_cap = st.number_input("Capacity (optional)", min_value=0, value=0)
            submitted = st.form_submit_button("Add room")
        if submitted:
            if not r_name.strip():
                st.error("Room name is required.")
            else:
                conn = get_connection()
                try:
                    conn.execute(
                        "INSERT INTO rooms (name, capacity) VALUES (?, ?)",
                        (r_name.strip(), r_cap if r_cap > 0 else None)
                    )
                    conn.commit()
                    st.rerun()
                except Exception:
                    st.error(f"A room named '{r_name.strip()}' already exists.")
                finally:
                    conn.close()
