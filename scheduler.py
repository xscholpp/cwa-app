"""
scheduler.py
------------
Automatic conference scheduler: fills empty schedule_slots with approved
panels, respecting speaker availability, avoiding back-to-back overload for
any one speaker, and steering away from thematically-competing panels
running at the same time. Locked assignments (schedule.locked = 1) are
never touched; a re-run after a speaker's availability changes will keep
everything else in place unless there's a real constraint reason to move it
("minimize disruption").

Deliberately has NO Streamlit import, so it can be exercised with plain
`python3` scripts against a scratch SQLite file — unlike database.py's
Turso path (which relies on st.cache_resource and needs a real Streamlit
ScriptRunContext), this module only ever receives an already-open `conn`.

All times are zero-padded 24-hour "HH:MM" strings (as stored everywhere
else in this app), which compare correctly with plain string operators, so
this module never needs to parse them into datetime.time.
"""

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    id: int
    date: str
    day_name: str
    day_order: int
    room_id: object
    room_capacity: object
    start_time: str
    end_time: str


@dataclass(frozen=True)
class PanelInfo:
    id: int
    title: str
    committee_id: object
    expected_attendance: object
    topics: frozenset
    panelist_speaker_ids: frozenset
    conflict_ids: frozenset


@dataclass(frozen=True)
class SpeakerAvail:
    speaker_id: int
    name: str
    arrival_day_order: object
    arrival_time: object
    departure_day_order: object
    departure_time: object
    blackouts: tuple  # (day_order, start_time, end_time)


@dataclass
class ScheduleProposal:
    assignments: dict   # panel_id -> slot_id, the full final proposed state (movable + locked)
    diff: dict          # {"unchanged": [...], "newly_placed": [...], "moved": [...]}
    unplaceable: list   # [(panel_id, panel_title, reason_str), ...]
    score: float


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_panels(conn, statuses):
    placeholders = ",".join("?" * len(statuses))
    panel_rows = conn.execute(
        f"SELECT id, title, committee_id, expected_attendance FROM panels WHERE status IN ({placeholders})",
        tuple(statuses)
    ).fetchall()
    if not panel_rows:
        return {}

    topics_by_panel = {}
    for row in conn.execute("SELECT panel_id, topic FROM panel_topics").fetchall():
        topics_by_panel.setdefault(row["panel_id"], set()).add(row["topic"].strip().lower())

    speakers_by_panel = {}
    for row in conn.execute(
        "SELECT panel_id, speaker_id FROM panel_speakers WHERE role = 'panelist'"
    ).fetchall():
        speakers_by_panel.setdefault(row["panel_id"], set()).add(row["speaker_id"])

    conflicts_by_panel = {}
    for row in conn.execute("SELECT panel_id_a, panel_id_b FROM panel_conflicts").fetchall():
        a, b = row["panel_id_a"], row["panel_id_b"]
        conflicts_by_panel.setdefault(a, set()).add(b)
        conflicts_by_panel.setdefault(b, set()).add(a)

    panels = {}
    for p in panel_rows:
        pid = p["id"]
        panels[pid] = PanelInfo(
            id=pid,
            title=p["title"],
            committee_id=p["committee_id"],
            expected_attendance=p["expected_attendance"],
            topics=frozenset(topics_by_panel.get(pid, ())),
            panelist_speaker_ids=frozenset(speakers_by_panel.get(pid, ())),
            conflict_ids=frozenset(conflicts_by_panel.get(pid, ())),
        )
    return panels


def _load_slots(conn, scope_dates):
    if scope_dates:
        placeholders = ",".join("?" * len(scope_dates))
        where, params = f"WHERE ss.date IN ({placeholders})", tuple(scope_dates)
    else:
        where, params = "", ()
    rows = conn.execute(f"""
        SELECT ss.id, ss.date, ss.room_id, r.capacity AS room_capacity,
               ss.start_time, ss.end_time, cd.day_name, cd.day_order
        FROM schedule_slots ss
        LEFT JOIN rooms r ON r.id = ss.room_id
        JOIN conference_days cd ON cd.date = ss.date
        {where}
    """, params).fetchall()
    return {
        r["id"]: Slot(
            id=r["id"], date=r["date"], day_name=r["day_name"], day_order=r["day_order"],
            room_id=r["room_id"], room_capacity=r["room_capacity"],
            start_time=r["start_time"], end_time=r["end_time"],
        )
        for r in rows
    }


def _load_speakers_with_availability(conn):
    name_to_order = {
        r["day_name"]: r["day_order"]
        for r in conn.execute("SELECT day_name, day_order FROM conference_days").fetchall()
    }

    blackouts_by_speaker = {}
    for row in conn.execute(
        "SELECT speaker_id, day, start_time, end_time FROM speaker_availability"
    ).fetchall():
        order = name_to_order.get(row["day"])
        if order is None:
            continue
        blackouts_by_speaker.setdefault(row["speaker_id"], []).append(
            (order, row["start_time"], row["end_time"])
        )

    speakers = {}
    for r in conn.execute(
        "SELECT id, name, arrival_day, arrival_time, departure_day, departure_time FROM speakers"
    ).fetchall():
        sid = r["id"]
        speakers[sid] = SpeakerAvail(
            speaker_id=sid,
            name=r["name"],
            arrival_day_order=name_to_order.get(r["arrival_day"]),
            arrival_time=r["arrival_time"],
            departure_day_order=name_to_order.get(r["departure_day"]),
            departure_time=r["departure_time"],
            blackouts=tuple(blackouts_by_speaker.get(sid, ())),
        )
    return speakers


def _load_current_schedule(conn, scope_dates):
    """panel_id -> (slot_id, locked_bool) for EVERY currently-scheduled panel
    within scope_dates, regardless of its status — a panel outside the
    auto-scheduler's eligible statuses (e.g. still 'draft') is still
    physically occupying a slot and must still block that slot."""
    if scope_dates:
        placeholders = ",".join("?" * len(scope_dates))
        where, params = f"WHERE ss.date IN ({placeholders})", tuple(scope_dates)
    else:
        where, params = "", ()
    rows = conn.execute(f"""
        SELECT s.panel_id, s.slot_id, s.locked
        FROM schedule s JOIN schedule_slots ss ON ss.id = s.slot_id
        {where}
    """, params).fetchall()
    return {r["panel_id"]: (r["slot_id"], bool(r["locked"])) for r in rows}


def build_day_row_index(slots):
    """date -> {(start_time, end_time): row_index}, matching the same
    distinct-time-row grid rendered on the Schedule page. Two panels are
    considered "back-to-back" if their rows are adjacent in this index —
    real slot rows already carry a break in them, so adjacency is about
    the row grid, not literal touching clock times."""
    by_date = {}
    for s in slots.values():
        by_date.setdefault(s.date, set()).add((s.start_time, s.end_time))
    return {date: {t: i for i, t in enumerate(sorted(times))} for date, times in by_date.items()}


def _compute_room_capacity_tiers(slots):
    """room_id -> 'low'/'medium'/'high', bucketing rooms.capacity into
    terciles across whatever rooms are in scope. Purely a tie-break signal."""
    caps = sorted({s.room_capacity for s in slots.values() if s.room_capacity is not None})
    if not caps:
        return {}
    n = len(caps)
    low_max = caps[n // 3] if n >= 3 else caps[0]
    high_min = caps[(2 * n) // 3] if n >= 3 else caps[-1]
    tier = {}
    for s in slots.values():
        if s.room_id is None or s.room_capacity is None:
            continue
        if s.room_capacity <= low_max:
            tier[s.room_id] = "low"
        elif s.room_capacity >= high_min:
            tier[s.room_id] = "high"
        else:
            tier[s.room_id] = "medium"
    return tier


# ── Hard constraints ──────────────────────────────────────────────────────────

def speaker_available_for_slot(spk, slot):
    if spk.arrival_day_order is not None and slot.day_order < spk.arrival_day_order:
        return False
    if spk.departure_day_order is not None and slot.day_order > spk.departure_day_order:
        return False
    if (spk.arrival_day_order is not None and slot.day_order == spk.arrival_day_order
            and spk.arrival_time and slot.start_time < spk.arrival_time):
        return False
    if (spk.departure_day_order is not None and slot.day_order == spk.departure_day_order
            and spk.departure_time and slot.end_time > spk.departure_time):
        return False
    for (bd_order, b_start, b_end) in spk.blackouts:
        if bd_order == slot.day_order and slot.start_time < b_end and b_start < slot.end_time:
            return False
    return True


def violates_hard_constraints(panel_id, slot_id, assignment, panels, slots, speakers,
                               row_index, locked_slot_ids):
    """Would placing `panel_id` at `slot_id` break a hard rule, given the rest
    of `assignment` (which must NOT already contain panel_id)?"""
    if slot_id in locked_slot_ids:
        return True

    slot = slots[slot_id]
    panel = panels[panel_id]

    for spk_id in panel.panelist_speaker_ids:
        spk = speakers.get(spk_id)
        if spk and not speaker_available_for_slot(spk, slot):
            return True

    this_row = row_index[slot.date][(slot.start_time, slot.end_time)]
    for spk_id in panel.panelist_speaker_ids:
        other_rows = []
        for pid, sid in assignment.items():
            if pid == panel_id:
                continue
            other_panel = panels.get(pid)
            if not other_panel or spk_id not in other_panel.panelist_speaker_ids:
                continue
            other_slot = slots[sid]
            if other_slot.date != slot.date:
                continue
            if other_slot.start_time < slot.end_time and slot.start_time < other_slot.end_time:
                return True  # literal time overlap: double-booked
            other_rows.append(row_index[other_slot.date][(other_slot.start_time, other_slot.end_time)])
        rows_set = set(other_rows) | {this_row}
        if any((r in rows_set and (r + 1) in rows_set and (r + 2) in rows_set) for r in rows_set):
            return True  # 3 consecutive rows for the same speaker — hard blocked
    return False


# ── Soft objective ────────────────────────────────────────────────────────────

CONFLICT_PENALTY = 10000
BACK_TO_BACK_PENALTY = 400
DISRUPTION_PENALTY = 250
HIGH_ATTENDANCE_CLASH_PENALTY = 150
SAME_COMMITTEE_PENALTY = 100
SHARED_TOPIC_PENALTY = 75
SHARED_TOPIC_CAP = 3
CAPACITY_MISMATCH_PENALTY = 10


def score_assignment(assignment, panels, slots, row_index, original_assignment, room_capacity_tier):
    penalty = 0.0

    simultaneous = {}
    for pid, sid in assignment.items():
        s = slots[sid]
        key = (s.date, row_index[s.date][(s.start_time, s.end_time)])
        simultaneous.setdefault(key, []).append(pid)

    for ids in simultaneous.values():
        n = len(ids)
        for i in range(n):
            for j in range(i + 1, n):
                pa, pb = panels[ids[i]], panels[ids[j]]
                if pb.id in pa.conflict_ids:
                    penalty += CONFLICT_PENALTY
                if pa.expected_attendance == "high" and pb.expected_attendance == "high":
                    penalty += HIGH_ATTENDANCE_CLASH_PENALTY
                if pa.committee_id is not None and pa.committee_id == pb.committee_id:
                    penalty += SAME_COMMITTEE_PENALTY
                shared = len(pa.topics & pb.topics)
                if shared:
                    penalty += SHARED_TOPIC_PENALTY * min(shared, SHARED_TOPIC_CAP)

    speaker_rows = {}
    for pid, sid in assignment.items():
        s = slots[sid]
        r = row_index[s.date][(s.start_time, s.end_time)]
        for spk_id in panels[pid].panelist_speaker_ids:
            speaker_rows.setdefault(spk_id, {}).setdefault(s.date, set()).add(r)
    for by_date in speaker_rows.values():
        for rows in by_date.values():
            for r in rows:
                if (r + 1) in rows:
                    penalty += BACK_TO_BACK_PENALTY

    for pid, sid in assignment.items():
        orig = original_assignment.get(pid)
        if orig is not None and orig != sid:
            penalty += DISRUPTION_PENALTY

    for pid, sid in assignment.items():
        slot = slots[sid]
        want = panels[pid].expected_attendance
        tier = room_capacity_tier.get(slot.room_id)
        if want and tier and want != tier:
            penalty += CAPACITY_MISMATCH_PENALTY

    return penalty


# ── Unplaceable-panel diagnostics ─────────────────────────────────────────────

def _unplaceable_reason(panel, slots, speakers, open_slot_ids):
    if not open_slot_ids:
        return "No open slots remain in the selected date range."
    availability_fail = 0
    for sid in open_slot_ids:
        slot = slots[sid]
        if any(
            not speaker_available_for_slot(speakers[spk_id], slot)
            for spk_id in panel.panelist_speaker_ids if spk_id in speakers
        ):
            availability_fail += 1
    if availability_fail == len(open_slot_ids) and panel.panelist_speaker_ids:
        names = ", ".join(
            speakers[s].name for s in panel.panelist_speaker_ids if s in speakers
        )
        return f"Every remaining open slot falls outside the availability of: {names}."
    return "Every remaining open slot would double- or triple-book a panelist already scheduled elsewhere."


# ── Diff building ─────────────────────────────────────────────────────────────

def _slot_desc(slots, sid):
    s = slots[sid]
    room = f" (room {s.room_id})" if s.room_id else ""
    return f"{s.date} {s.start_time}-{s.end_time}{room}"


def _build_diff(assignment, current, panels, slots):
    unchanged, newly_placed, moved = [], [], []
    for pid, sid in assignment.items():
        if pid not in panels:
            continue
        title = panels[pid].title
        prev = current.get(pid)
        if prev is None:
            newly_placed.append({"panel_id": pid, "panel_title": title, "new_slot_desc": _slot_desc(slots, sid)})
        else:
            prev_sid, _locked = prev
            if prev_sid == sid:
                unchanged.append({"panel_id": pid, "panel_title": title, "slot_desc": _slot_desc(slots, sid)})
            else:
                moved.append({
                    "panel_id": pid, "panel_title": title,
                    "old_slot_desc": _slot_desc(slots, prev_sid), "new_slot_desc": _slot_desc(slots, sid),
                })
    return {"unchanged": unchanged, "newly_placed": newly_placed, "moved": moved}


# ── Public API ────────────────────────────────────────────────────────────────

def propose_schedule(conn, scope_dates=None, statuses=("approved", "to be presented"),
                      minimize_disruption=True, iterations=8000, seed=None):
    rng = random.Random(seed)

    panels = _load_panels(conn, statuses)
    slots = _load_slots(conn, scope_dates)
    speakers = _load_speakers_with_availability(conn)
    current = _load_current_schedule(conn, scope_dates)

    row_index = build_day_row_index(slots)
    room_capacity_tier = _compute_room_capacity_tiers(slots)
    assignable_slot_ids = {sid for sid, s in slots.items() if s.room_id is not None}

    # A slot is off-limits to every OTHER managed panel if it's occupied by a
    # locked assignment, or by a panel outside this run's eligible statuses
    # (e.g. still a draft) — either way it isn't this run's to move.
    locked_slot_ids = set()
    locked_assignment = {}
    for pid, (sid, locked) in current.items():
        if locked or pid not in panels:
            locked_slot_ids.add(sid)
        if locked and pid in panels:
            locked_assignment[pid] = sid

    movable_panel_ids = [pid for pid in panels if pid not in locked_assignment]
    assignment = dict(locked_assignment)
    original_assignment = {}
    open_slot_ids = assignable_slot_ids - locked_slot_ids

    if minimize_disruption:
        for pid in movable_panel_ids:
            if pid in current:
                sid, _locked = current[pid]
                if sid in open_slot_ids:
                    assignment[pid] = sid
                    original_assignment[pid] = sid
                    open_slot_ids.discard(sid)

    def feasible_slots_for(pid, candidate_slots):
        return [
            sid for sid in candidate_slots
            if not violates_hard_constraints(pid, sid, assignment, panels, slots, speakers,
                                              row_index, locked_slot_ids)
        ]

    # ---- Construction: most-constrained-panel-first ----
    remaining = [pid for pid in movable_panel_ids if pid not in assignment]
    unplaceable = []
    while remaining:
        feas_by_pid = {pid: feasible_slots_for(pid, open_slot_ids) for pid in remaining}
        candidates = [pid for pid in remaining if feas_by_pid[pid]]
        if not candidates:
            break
        pid = min(candidates, key=lambda p: len(feas_by_pid[p]))
        best_sid, best_score = None, None
        for sid in feas_by_pid[pid]:
            trial = dict(assignment)
            trial[pid] = sid
            sc = score_assignment(trial, panels, slots, row_index, original_assignment, room_capacity_tier)
            if best_score is None or sc < best_score:
                best_score, best_sid = sc, sid
        assignment[pid] = best_sid
        open_slot_ids.discard(best_sid)
        remaining.remove(pid)

    for pid in remaining:
        unplaceable.append((pid, panels[pid].title, _unplaceable_reason(panels[pid], slots, speakers, open_slot_ids)))

    # ---- Simulated annealing over movable, currently-placed panels ----
    placed_movable = [pid for pid in movable_panel_ids if pid in assignment]

    if len(placed_movable) >= 2 or (len(placed_movable) >= 1 and open_slot_ids):
        current_score = score_assignment(assignment, panels, slots, row_index, original_assignment, room_capacity_tier)
        best_assignment, best_score = dict(assignment), current_score

        T0, cooling_rate, min_T = 500.0, 0.999, 0.5
        stall, stall_limit = 0, max(1000, iterations // 8)

        for i in range(iterations):
            T = max(min_T, T0 * (cooling_rate ** i))
            if T <= min_T or stall >= stall_limit:
                break

            do_swap = len(placed_movable) >= 2 and rng.random() < 0.7
            if do_swap:
                pa, pb = rng.sample(placed_movable, 2)
                sid_a, sid_b = assignment[pa], assignment[pb]
                rest = {k: v for k, v in assignment.items() if k not in (pa, pb)}
                if (violates_hard_constraints(pa, sid_b, {**rest, pb: sid_a}, panels, slots, speakers, row_index, locked_slot_ids)
                        or violates_hard_constraints(pb, sid_a, {**rest, pa: sid_b}, panels, slots, speakers, row_index, locked_slot_ids)):
                    stall += 1
                    continue
                trial = dict(assignment)
                trial[pa], trial[pb] = sid_b, sid_a
            else:
                pa = rng.choice(placed_movable)
                targets = list(open_slot_ids) + [assignment[pa]]
                new_sid = rng.choice(targets)
                if new_sid == assignment[pa]:
                    stall += 1
                    continue
                rest = {k: v for k, v in assignment.items() if k != pa}
                if violates_hard_constraints(pa, new_sid, rest, panels, slots, speakers, row_index, locked_slot_ids):
                    stall += 1
                    continue
                trial = dict(assignment)
                trial[pa] = new_sid

            trial_score = score_assignment(trial, panels, slots, row_index, original_assignment, room_capacity_tier)
            delta = trial_score - current_score
            if delta <= 0 or rng.random() < math.exp(-delta / T):
                if not do_swap:
                    open_slot_ids.discard(new_sid)
                    open_slot_ids.add(assignment[pa])
                assignment = trial
                current_score += delta
                if current_score < best_score:
                    best_assignment, best_score, stall = dict(assignment), current_score, 0
                else:
                    stall += 1
            else:
                stall += 1

        assignment = best_assignment

    # Final retry: a slot may have freed up during the search.
    still_unplaceable = []
    for pid, title, reason in unplaceable:
        feas = feasible_slots_for(pid, open_slot_ids)
        if feas:
            sid = min(
                feas,
                key=lambda s: score_assignment({**assignment, pid: s}, panels, slots, row_index,
                                                original_assignment, room_capacity_tier)
            )
            assignment[pid] = sid
            open_slot_ids.discard(sid)
        else:
            still_unplaceable.append((pid, title, reason))

    diff = _build_diff(assignment, current, panels, slots)
    final_score = score_assignment(assignment, panels, slots, row_index, original_assignment, room_capacity_tier)
    return ScheduleProposal(assignments=assignment, diff=diff, unplaceable=still_unplaceable, score=final_score)


def apply_schedule_proposal(conn, proposal: ScheduleProposal):
    """Writes a proposal to the schedule table. Only touches panels whose
    slot actually changed (newly placed or moved) — unchanged and locked
    assignments are left completely alone."""
    changed_pids = {row["panel_id"] for row in proposal.diff["newly_placed"]} | \
                   {row["panel_id"] for row in proposal.diff["moved"]}
    for pid in changed_pids:
        new_sid = proposal.assignments[pid]
        conn.execute("DELETE FROM schedule WHERE slot_id = ?", (new_sid,))
        conn.execute("DELETE FROM schedule WHERE panel_id = ?", (pid,))
        conn.execute("INSERT INTO schedule (slot_id, panel_id, locked) VALUES (?, ?, 0)", (new_sid, pid))
    conn.commit()
