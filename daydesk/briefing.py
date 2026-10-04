"""Turn user-entered information into a practical daily plan and local backups."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def _stamp(value: str | None, timezone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=timezone) if result.tzinfo is None else result.astimezone(timezone)


def _clean(value: object) -> str:
    # Reports are plain Markdown, and user-entered newlines should not become headings.
    return " ".join(str(value).split()).replace("<", "&lt;").replace(">", "&gt;")


def focus_tasks(snapshot: dict) -> list[dict]:
    timezone = ZoneInfo(snapshot["config"]["timezone"])
    today = datetime.fromisoformat(snapshot["today"]).date()
    pending = [t for t in snapshot["tasks"] if not t["completed"]]
    relevant = [t for t in pending if not t["due"] or _stamp(t["due"], timezone).date() <= today]
    # An overdue deadline comes first, then priority, then deadline. Future tasks stay out of today's focus.
    relevant.sort(key=lambda t: (
        0 if t["due"] and _stamp(t["due"], timezone).date() < today else 1,
        t["priority"], _stamp(t["due"], timezone).timestamp() if t["due"] else float("inf"), t["id"],
    ))
    return relevant[:3]


def alerts_for(snapshot: dict) -> list[dict]:
    timezone = ZoneInfo(snapshot["config"]["timezone"])
    now = _stamp(snapshot["now"], timezone)
    alerts = []
    for task in snapshot["tasks"]:
        due = _stamp(task["due"], timezone)
        if task["completed"] or due is None:
            continue
        if due.timestamp() < now.timestamp():
            alerts.append({"id": f"task:{task['id']}:overdue", "kind": "overdue", "title": task["title"],
                           "message": f"Overdue: {task['title']} (due {due:%b %d, %I:%M %p})"})
        elif due.timestamp() <= now.timestamp() + 24 * 3600:
            alerts.append({"id": f"task:{task['id']}:soon", "kind": "deadline", "title": task["title"],
                           "message": f"Due within 24 hours: {task['title']} ({due:%I:%M %p})"})
    for event in snapshot["events"]:
        start = _stamp(event["start"], timezone)
        end = _stamp(event["end"], timezone)
        if now.timestamp() <= start.timestamp() <= now.timestamp() + 30 * 60:
            alerts.append({"id": f"event:{event['id']}:soon", "kind": "event", "title": event["title"],
                           "message": f"Starting soon: {event['title']} at {start:%I:%M %p}"})
        elif start.timestamp() < now.timestamp() < end.timestamp():
            alerts.append({"id": f"event:{event['id']}:active", "kind": "event", "title": event["title"],
                           "message": f"In progress: {event['title']}"})
    return alerts


def render_brief(snapshot: dict) -> str:
    timezone = ZoneInfo(snapshot["config"]["timezone"])
    day = datetime.fromisoformat(snapshot["today"]).date()
    now = _stamp(snapshot["now"], timezone)
    relevant_events = sorted(
        [e for e in snapshot["events"]
         if _stamp(e["start"], timezone).date() <= day <= _stamp(e["end"], timezone).date()
         and not (_stamp(e["end"], timezone).date() == day and _stamp(e["end"], timezone).time().isoformat() == "00:00:00")],
        key=lambda e: _stamp(e["start"], timezone).timestamp(),
    )
    pending = [t for t in snapshot["tasks"] if not t["completed"]]
    overdue = [t for t in pending if t["due"] and _stamp(t["due"], timezone).date() < day]
    due_today = [t for t in pending if t["due"] and _stamp(t["due"], timezone).date() == day]
    focus = focus_tasks(snapshot)
    name = _clean(snapshot['config']['name'])
    title = "Your daily brief" if name == "You" else f"{name}'s daily brief"
    lines = [f"# {title}", "",
             f"{day:%A, %B %d, %Y} · {snapshot['config']['timezone']}",
             f"Updated {now:%I:%M %p} · Based on information you entered", "",
             "## Your three priorities", ""]
    for t in focus:
        suffix = f" · due {_stamp(t['due'], timezone):%b %d, %I:%M %p}" if t["due"] else ""
        lines.append(f"- [ ] {_clean(t['title'])} · {_clean(t['category'])}{suffix}")
    if not focus:
        lines.append("No tasks due today. Add your next priority or apply a checklist.")
    lines += ["", "## Schedule", ""]
    for e in relevant_events:
        start, end = _stamp(e["start"], timezone), _stamp(e["end"], timezone)
        where = f" · {_clean(e['location'])}" if e["location"] else ""
        # Include dates to make cross-midnight events unambiguous.
        lines.append(f"- {start:%b %d %I:%M %p} – {end:%b %d %I:%M %p}: {_clean(e['title'])}{where}")
    if not relevant_events:
        lines.append("No events entered for today.")
    conflicts = []
    for i, first in enumerate(relevant_events):
        for second in relevant_events[i + 1:]:
            if _stamp(second["start"], timezone).timestamp() < _stamp(first["end"], timezone).timestamp():
                conflicts.append(f"- {_clean(first['title'])} overlaps {_clean(second['title'])}.")
    if conflicts:
        lines += ["", "### Schedule conflicts", "", *conflicts]
    lines += ["", "## Deadlines", "", f"{len(overdue)} overdue · {len(due_today)} due today", ""]
    for t in overdue + due_today:
        lines.append(f"- {_clean(t['title'])} · {_stamp(t['due'], timezone):%b %d, %I:%M %p}")
    if not overdue and not due_today:
        lines.append("No outstanding deadlines today.")
    spending = sum(e["amount_cents"] for e in snapshot["expenses"] if e["date"] == day.isoformat())
    month_spending = sum(e["amount_cents"] for e in snapshot["expenses"] if e["date"].startswith(day.strftime("%Y-%m")))
    monday = day - timedelta(days=day.weekday())
    logs = [w for w in snapshot["work_logs"] if monday <= _stamp(w["start"], timezone).date() <= day]
    hours = sum(w["duration_minutes"] for w in logs) / 60
    pay = sum(w["pay_cents"] for w in logs) / 100
    miles = sum(w["miles"] for w in logs)
    lines += ["", "## Money and work", "", f"- Expenses entered today: ${spending / 100:,.2f}",
              f"- Expenses entered this month: ${month_spending / 100:,.2f}",
              f"- Work entered this week: {hours:.2f} hours · estimated gross pay ${pay:,.2f} · {miles:g} miles", "",
              "## End-of-day reset", "", "- [ ] Finish or reschedule outstanding tasks.",
              "- [ ] Log work hours, mileage, and expenses.", "- [ ] Enter tomorrow's commitments.", "",
              "Work totals include shifts by their start date. Pay excludes taxes, overtime, and reimbursements.", ""]
    return "\n".join(lines)


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=".daydesk-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


@contextmanager
def _run_lock(home: Path):
    """Serialize overlapping scheduler/CLI/web runs without third-party packages."""
    path = home / "automation.lock"
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def run_daily(store) -> dict:
    store.initialize()
    with _run_lock(store.home):
        snapshot = store.snapshot()
        day = snapshot["today"]
        path = store.home / "briefs" / f"{day}.md"
        state_path = store.home / "automation.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("Automation state must be a JSON object")
        except FileNotFoundError:
            state = {}
        generated = not path.exists()
        backup = None
        # Verify the previous file still exists: removing a backup should not prevent a new one.
        previous_backup = state.get("backup_path")
        if state.get("backup_day") != day or not previous_backup or not Path(previous_backup).is_file():
            backup = str(store.backup())
            state.update(backup_day=day, backup_path=backup)
        atomic_text(path, render_brief(snapshot))
        atomic_text(state_path, json.dumps(state, indent=2) + "\n")
        return {"path": str(path), "backup": backup, "generated": generated,
                "alerts": alerts_for(snapshot), "focus": focus_tasks(snapshot)}
