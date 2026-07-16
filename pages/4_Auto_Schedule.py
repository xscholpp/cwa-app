"""
4_Auto_Schedule.py
------------------
Automatically fills the schedule from approved panels, speaker availability,
and manual scheduling preferences (expected attendance, marked conflicts).
Locked assignments (set in the Schedule page's slot editor) are never
touched. Always shows a preview before writing anything — nothing is
applied until the admin confirms.
"""

import streamlit as st
from database import get_connection
from scheduler import propose_schedule, apply_schedule_proposal
from auth import require_login, has_permission
from layout import widen_content

widen_content()
require_login()

if not has_permission("can_manage_schedule"):
    st.error("You don't have permission to access this page.")
    st.stop()

st.title("Auto Schedule")
st.caption(
    "Fills empty schedule slots with approved panels, respecting speaker availability, "
    "avoiding back-to-back overload, and steering competing panels apart. Locked "
    "assignments (set in the Schedule page) are never moved."
)

conn = get_connection()
days = conn.execute("SELECT * FROM conference_days ORDER BY day_order").fetchall()
conn.close()

if not days:
    st.info("No conference dates set up yet. Add them in Admin → Conference Days first.")
    st.stop()

day_labels = [f"{d['day_name']} ({d['date']})" for d in days]
chosen_labels = st.multiselect("Days to include", day_labels, default=day_labels)
chosen_dates = [d["date"] for d, lbl in zip(days, day_labels) if lbl in chosen_labels]

minimize_changes = st.checkbox("Minimize changes to current schedule", value=True)
st.caption(
    "When checked, panels already placed keep their slot unless there's a real reason "
    "to move them (a new conflict, an availability change). Uncheck to fully "
    "re-optimize from scratch."
)

if st.button("Run scheduler", type="primary"):
    if not chosen_dates:
        st.error("Choose at least one day.")
    else:
        conn = get_connection()
        with st.spinner("Scheduling..."):
            proposal = propose_schedule(
                conn, scope_dates=chosen_dates, minimize_disruption=minimize_changes
            )
        conn.close()
        st.session_state["auto_schedule_proposal"] = proposal


@st.dialog("Apply proposed schedule?")
def confirm_apply_schedule_dialog(proposal):
    n_changed = len(proposal.diff["newly_placed"]) + len(proposal.diff["moved"])
    st.warning(
        f"This will update **{n_changed} panel assignment(s)** in the schedule. "
        f"Locked and unchanged assignments are left untouched. This can't be undone."
    )
    cancel_col, confirm_col = st.columns(2)
    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.rerun()
    with confirm_col:
        if st.button("Yes, apply this schedule", type="primary", use_container_width=True):
            conn = get_connection()
            apply_schedule_proposal(conn, proposal)
            conn.close()
            st.session_state.pop("auto_schedule_proposal", None)
            st.success("Schedule updated.")
            st.rerun()


proposal = st.session_state.get("auto_schedule_proposal")
if proposal:
    st.divider()
    st.subheader("Proposed changes")
    st.caption(
        f"{len(proposal.diff['unchanged'])} unchanged · "
        f"{len(proposal.diff['newly_placed'])} newly scheduled · "
        f"{len(proposal.diff['moved'])} moved · "
        f"{len(proposal.unplaceable)} could not be scheduled"
    )

    if proposal.diff["newly_placed"]:
        st.markdown("**Newly scheduled**")
        for row in proposal.diff["newly_placed"]:
            st.markdown(f"- {row['panel_title']} → {row['new_slot_desc']}")

    if proposal.diff["moved"]:
        st.markdown("**Moved**")
        for row in proposal.diff["moved"]:
            st.markdown(f"- {row['panel_title']}: {row['old_slot_desc']} → {row['new_slot_desc']}")

    if proposal.unplaceable:
        st.markdown("**Could not be scheduled**")
        for panel_id, title, reason in proposal.unplaceable:
            st.markdown(f"- {title}: {reason}")

    n_changed = len(proposal.diff["newly_placed"]) + len(proposal.diff["moved"])
    if n_changed == 0:
        st.success("Nothing to change — the current schedule is already optimal.")
    elif st.button("Apply proposed schedule", type="primary"):
        confirm_apply_schedule_dialog(proposal)
