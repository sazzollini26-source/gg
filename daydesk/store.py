"""Validated, local SQLite storage for Daydesk.

All datetimes are stored with a UTC offset. Naive input is interpreted in the
configured timezone. Money is stored in integer cents, never binary floats.
"""

from __future__ import annotations

import csv
import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_CONFIG = {
    "name": "You",
    "timezone": "UTC",
    "brief_time": "07:00",
    "categories": ["Work", "Home", "Projects", "Personal"],
}

CHECKLISTS = {
    "Morning reset": [
        "Review today's calendar and deadlines",
        "Choose the day's three priorities",
        "Review notes and information needed today",
        "Check time needed for the first commitment",
    ],
    "Event setup": [
        "Confirm the event time and location",
        "Review the event plan",
        "Check materials and equipment",
        "Confirm arrangements with participants",
        "Record any outstanding issues",
    ],
    "Activity prep": [
        "Confirm the activity time and location",
        "Gather the items needed",
        "Review instructions and plans",
        "Check travel or connection details",
        "Record notes and follow-up tasks",
    ],
    "Weekly reset": [
        "Review completed tasks and open follow-ups",
        "Organize notes and files",
        "Choose priorities for the coming week",
        "Review project progress",
        "Check next week's commitments",
    ],
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS configuration (
    id INTEGER PRIMARY KEY CHECK (id = 1), value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, category TEXT NOT NULL, due TEXT,
    priority INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 3),
    recurrence TEXT NOT NULL CHECK (recurrence IN ('none', 'daily', 'weekly')),
    notes TEXT NOT NULL DEFAULT '', completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, completed_at TEXT,
    recurrence_time TEXT,
    parent_id INTEGER UNIQUE REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS tasks_open_due ON tasks(completed, due);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
    start TEXT NOT NULL, end TEXT NOT NULL, location TEXT NOT NULL,
    category TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, amount_cents INTEGER NOT NULL,
    category TEXT NOT NULL, description TEXT NOT NULL,
    date TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, start TEXT NOT NULL, end TEXT NOT NULL,
    role TEXT NOT NULL, miles REAL NOT NULL, rate_cents INTEGER NOT NULL,
    duration_minutes REAL NOT NULL, pay_cents INTEGER NOT NULL,
    notes TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def _private(path: Path, mode: int) -> None:
    """Restrict local permissions on systems that support POSIX permissions."""
    try:
        path.chmod(mode)
    except OSError:
        pass


def _text(value: Any, field: str, *, optional: bool = False, limit: int = 5000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if not optional and not value:
        raise ValueError(f"{field} cannot be empty")
    if len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} must be at most {limit} characters without null bytes")
    return value


def _cents(value: Any, field: str, *, allow_zero: bool = False, maximum: str = "1000000") -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a decimal number")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field} must be a decimal number") from None
    if not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0):
        raise ValueError(f"{field} must be {'nonnegative' if allow_zero else 'positive'}")
    if amount > Decimal(maximum):
        raise ValueError(f"{field} must not exceed {maximum}")
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount != rounded:
        raise ValueError(f"{field} must have at most two decimal places")
    return int(rounded * 100)


def _date(value: str | date, field: str = "date") -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{field} must use YYYY-MM-DD")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{field} must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be a valid date") from None


def _localize(value: datetime, zone: ZoneInfo, *, allow_gap: bool = False) -> datetime:
    """Resolve a local clock reading, including DST transitions.

    Ambiguous clock readings without an offset use the first occurrence. A
    recurrence that falls in a spring gap is advanced by the gap; its separate
    anchor clock is retained so following occurrences return to that clock.
    """
    candidate = value.replace(tzinfo=zone)
    roundtrip = candidate.astimezone(timezone.utc).astimezone(zone)
    if roundtrip.replace(tzinfo=None) != value.replace(tzinfo=None):
        if allow_gap:
            return roundtrip
        raise ValueError("This local time does not exist because of daylight saving time; choose another time")
    return candidate


def _datetime(value: str | datetime, zone: ZoneInfo, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            raise ValueError(f"{field} must use an ISO date and time, for example 2026-10-03T09:00")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{field} must be a valid ISO date and time") from None
    else:
        raise ValueError(f"{field} must be an ISO date and time")
    try:
        if parsed.tzinfo is None:
            return _localize(parsed, zone)
        return parsed.astimezone(zone)
    except OverflowError:
        raise ValueError(f"{field} is outside the supported calendar range") from None


def _next_occurrence(previous_due: datetime, anchor_time: str, interval: int, completed_at: datetime, zone: ZoneInfo) -> datetime:
    try:
        next_date = previous_due.date() + timedelta(days=interval)
        next_due = _localize(datetime.fromisoformat(f"{next_date.isoformat()}T{anchor_time}"), zone, allow_gap=True)
        # Skip missed occurrences without changing the recurrence's clock.
        if next_due.timestamp() <= completed_at.timestamp():
            days_elapsed = max(0, (completed_at.date() - next_date).days)
            next_date += timedelta(days=(days_elapsed // interval) * interval)
            next_due = _localize(datetime.fromisoformat(f"{next_date.isoformat()}T{anchor_time}"), zone, allow_gap=True)
            while next_due.timestamp() <= completed_at.timestamp():
                next_date += timedelta(days=interval)
                next_due = _localize(datetime.fromisoformat(f"{next_date.isoformat()}T{anchor_time}"), zone, allow_gap=True)
        return next_due
    except OverflowError:
        raise ValueError("The next recurring task would be outside the supported calendar range") from None


def _valid_config(config: dict[str, Any]) -> dict[str, Any]:
    result = dict(config)
    result["name"] = _text(result["name"], "name", limit=120)
    result["timezone"] = _text(result["timezone"], "timezone", limit=120)
    try:
        ZoneInfo(result["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone must be an installed IANA timezone, such as UTC. If timezone data is missing, run: python -m pip install tzdata") from None
    brief_time = result["brief_time"]
    if not isinstance(brief_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", brief_time):
        raise ValueError("brief_time must use 24-hour HH:MM")
    categories = result["categories"]
    if not isinstance(categories, list) or not 1 <= len(categories) <= 50:
        raise ValueError("categories must be a list of 1 to 50 names")
    categories = [_text(category, "category", limit=80) for category in categories]
    if len(set(category.casefold() for category in categories)) != len(categories):
        raise ValueError("categories must not contain duplicate names")
    result["categories"] = categories
    return result


def _task(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["completed"] = bool(result["completed"])
    result.pop("parent_id", None)
    result.pop("recurrence_time", None)
    return result


def _safe_csv(value: Any) -> Any:
    """Keep spreadsheet applications from interpreting text as a formula."""
    if isinstance(value, str):
        stripped = value.lstrip()
        if (stripped and stripped[0] in "=+-@") or value.startswith(("\t", "\r", "\n")):
            return "'" + value
    return value


class Store:
    """A connection-per-operation store under an explicitly chosen data folder."""

    def __init__(self, home: Path):
        self.home = Path(home).expanduser().resolve()
        self.db_path = self.home / "daydesk.sqlite3"
        self._initialized = False

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        _private(self.home, 0o700)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO configuration(id, value) VALUES (1, ?)",
                (json.dumps(_valid_config(DEFAULT_CONFIG)),),
            )
        _private(self.db_path, 0o600)
        for suffix in ("-wal", "-shm"):
            if Path(str(self.db_path) + suffix).exists():
                _private(Path(str(self.db_path) + suffix), 0o600)
        self._initialized = True

    def _ensure(self) -> None:
        if not self._initialized:
            self.initialize()

    def config(self) -> dict[str, Any]:
        self._ensure()
        with self._connection() as connection:
            row = connection.execute("SELECT value FROM configuration WHERE id = 1").fetchone()
        return json.loads(row["value"])

    def update_config(self, **fields: Any) -> dict[str, Any]:
        unknown = set(fields) - set(DEFAULT_CONFIG)
        if unknown:
            raise ValueError("Unknown configuration field(s): " + ", ".join(sorted(unknown)))
        self._ensure()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = json.loads(connection.execute("SELECT value FROM configuration WHERE id = 1").fetchone()["value"])
            updated = _valid_config({**current, **fields})
            connection.execute("UPDATE configuration SET value = ? WHERE id = 1", (json.dumps(updated),))
        return updated

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.config()["timezone"]))

    def _zone(self) -> ZoneInfo:
        return ZoneInfo(self.config()["timezone"])

    def add_task(
        self,
        title: str,
        category: str = "Personal",
        due: str | datetime | None = None,
        priority: int = 2,
        recurrence: str = "none",
        notes: str = "",
    ) -> dict[str, Any]:
        title = _text(title, "title", limit=500)
        category = _text(category, "category", limit=80)
        notes = _text(notes, "notes", optional=True)
        if isinstance(priority, bool) or not isinstance(priority, int) or priority not in (1, 2, 3):
            raise ValueError("priority must be 1 (high), 2 (normal), or 3 (low)")
        if recurrence not in ("none", "daily", "weekly"):
            raise ValueError("recurrence must be none, daily, or weekly")
        if recurrence != "none" and due is None:
            raise ValueError("A recurring task needs a due date and time")
        parsed_due = _datetime(due, self._zone(), "due") if due is not None else None
        created_at = self.now().isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks(title, category, due, priority, recurrence, notes, created_at, recurrence_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (title, category, parsed_due.isoformat() if parsed_due else None, priority,
                 recurrence, notes, created_at, parsed_due.time().isoformat() if parsed_due else None),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _task(row)

    def tasks(self, include_completed: bool = False) -> list[dict[str, Any]]:
        self._ensure()
        with self._connection() as connection:
            query = "SELECT * FROM tasks" + ("" if include_completed else " WHERE completed = 0")
            rows = connection.execute(query).fetchall()
        tasks = [_task(row) for row in rows]
        tasks.sort(key=lambda item: (
            item["completed"], item["due"] is None,
            datetime.fromisoformat(item["due"]).timestamp() if item["due"] else float("inf"),
            item["priority"], item["id"],
        ))
        return tasks

    def update_task(self, task_id: int, **fields: Any) -> dict[str, Any]:
        """Correct an open task without rewriting its history."""
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
            raise ValueError("task id must be a positive integer")
        allowed = {"title", "category", "due", "priority", "recurrence", "notes"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError("Unknown task field(s): " + ", ".join(sorted(unknown)))
        zone = self._zone()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Task {task_id} does not exist")
            if row["completed"]:
                raise ValueError("Completed tasks cannot be edited; add a new task instead")
            updated = {**dict(row), **fields}
            updated["title"] = _text(updated["title"], "title", limit=500)
            updated["category"] = _text(updated["category"], "category", limit=80)
            updated["notes"] = _text(updated["notes"], "notes", optional=True)
            priority = updated["priority"]
            if isinstance(priority, bool) or not isinstance(priority, int) or priority not in (1, 2, 3):
                raise ValueError("priority must be 1 (high), 2 (normal), or 3 (low)")
            if updated["recurrence"] not in ("none", "daily", "weekly"):
                raise ValueError("recurrence must be none, daily, or weekly")
            if updated["recurrence"] != "none" and updated["due"] is None:
                raise ValueError("A recurring task needs a due date and time")
            if "due" in fields:
                parsed_due = _datetime(fields["due"], zone, "due") if fields["due"] is not None else None
                updated["due"] = parsed_due.isoformat() if parsed_due else None
                updated["recurrence_time"] = parsed_due.time().isoformat() if parsed_due else None
            connection.execute(
                "UPDATE tasks SET title = ?, category = ?, due = ?, priority = ?, recurrence = ?, notes = ?, recurrence_time = ? WHERE id = ?",
                (updated["title"], updated["category"], updated["due"], updated["priority"],
                 updated["recurrence"], updated["notes"], updated["recurrence_time"], task_id),
            )
            result = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(result)

    def delete_record(self, kind: str, record_id: int) -> dict[str, Any]:
        """Delete one explicitly identified record; keep recurrence successors."""
        if not isinstance(kind, str) or kind not in ("tasks", "events", "expenses", "work_logs"):
            raise ValueError("kind must be tasks, events, expenses, or work_logs")
        if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
            raise ValueError("record id must be a positive integer")
        self._ensure()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # kind is restricted to the literal table names above.
            row = connection.execute(f"SELECT id FROM {kind} WHERE id = ?", (record_id,)).fetchone()
            if row is None:
                raise KeyError(f"Record {record_id} does not exist in {kind}")
            if kind == "tasks":
                connection.execute("UPDATE tasks SET parent_id = NULL WHERE parent_id = ?", (record_id,))
            connection.execute(f"DELETE FROM {kind} WHERE id = ?", (record_id,))
        return {"kind": kind, "id": record_id}

    def complete_task(self, task_id: int, now: str | datetime | None = None) -> dict[str, Any]:
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
            raise ValueError("task id must be a positive integer")
        zone = self._zone()
        completed_at = _datetime(now, zone, "completion time") if now is not None else self.now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Task {task_id} does not exist")
            if row["completed"]:
                return _task(row)
            connection.execute("UPDATE tasks SET completed = 1, completed_at = ? WHERE id = ?", (completed_at.isoformat(), task_id))
            if row["recurrence"] != "none":
                previous_due = datetime.fromisoformat(row["due"]).astimezone(zone)
                interval = 1 if row["recurrence"] == "daily" else 7
                anchor_time = row["recurrence_time"] or previous_due.time().isoformat()
                next_due = _next_occurrence(previous_due, anchor_time, interval, completed_at, zone)
                connection.execute(
                    "INSERT OR IGNORE INTO tasks(title, category, due, priority, recurrence, notes, created_at, recurrence_time, parent_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row["title"], row["category"], next_due.isoformat(), row["priority"], row["recurrence"],
                     row["notes"], completed_at.isoformat(), anchor_time, task_id),
                )
            result = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(result)

    def add_event(
        self, title: str, start: str | datetime, end: str | datetime,
        location: str = "", category: str = "Personal",
    ) -> dict[str, Any]:
        title = _text(title, "title", limit=500)
        location = _text(location, "location", optional=True, limit=500)
        category = _text(category, "category", limit=80)
        zone = self._zone()
        start_dt, end_dt = _datetime(start, zone, "start"), _datetime(end, zone, "end")
        if end_dt.timestamp() <= start_dt.timestamp():
            raise ValueError("end must be after start; overnight events need the next date")
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO events(title, start, end, location, category, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (title, start_dt.isoformat(), end_dt.isoformat(), location, category, self.now().isoformat()),
            )
            row = connection.execute("SELECT * FROM events WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def events(self) -> list[dict[str, Any]]:
        self._ensure()
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM events").fetchall()
        return sorted((dict(row) for row in rows), key=lambda item: (datetime.fromisoformat(item["start"]).timestamp(), item["id"]))

    def add_expense(
        self, amount: str | Decimal, category: str, description: str = "", day: str | date | None = None,
    ) -> dict[str, Any]:
        amount_cents = _cents(amount, "amount")
        category = _text(category, "category", limit=80)
        description = _text(description, "description", optional=True)
        current = self.now()
        expense_date = _date(day) if day is not None else current.date()
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO expenses(amount_cents, category, description, date, created_at) VALUES (?, ?, ?, ?, ?)",
                (amount_cents, category, description, expense_date.isoformat(), current.isoformat()),
            )
            row = connection.execute("SELECT * FROM expenses WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def expenses(self) -> list[dict[str, Any]]:
        self._ensure()
        with self._connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM expenses ORDER BY date, id").fetchall()]

    def add_work(
        self, start: str | datetime, end: str | datetime, role: str,
        miles: int | float | str = 0, rate: int | float | str = 0, notes: str = "",
    ) -> dict[str, Any]:
        role = _text(role, "role", limit=200)
        notes = _text(notes, "notes", optional=True)
        rate_cents = _cents(rate, "hourly rate", allow_zero=True, maximum="10000")
        try:
            distance = Decimal(str(miles))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("miles must be a nonnegative decimal number") from None
        if isinstance(miles, bool) or not distance.is_finite() or distance < 0 or distance > 10000:
            raise ValueError("miles must be between 0 and 10000")
        zone = self._zone()
        start_dt, end_dt = _datetime(start, zone, "start"), _datetime(end, zone, "end")
        seconds = Decimal(str(end_dt.timestamp() - start_dt.timestamp()))
        if seconds <= 0:
            raise ValueError("end must be after start; overnight shifts need the next date")
        if seconds > 7 * 24 * 3600:
            raise ValueError("A single work log cannot exceed 7 days")
        duration_minutes = float(seconds / 60)
        pay_cents = int((Decimal(rate_cents) * seconds / 3600).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO work_logs(start, end, role, miles, rate_cents, duration_minutes, pay_cents, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (start_dt.isoformat(), end_dt.isoformat(), role, float(distance), rate_cents, duration_minutes,
                 pay_cents, notes, self.now().isoformat()),
            )
            row = connection.execute("SELECT * FROM work_logs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def work_logs(self) -> list[dict[str, Any]]:
        self._ensure()
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM work_logs").fetchall()
        return sorted((dict(row) for row in rows), key=lambda item: (datetime.fromisoformat(item["start"]).timestamp(), item["id"]))

    def snapshot(self, day: str | date | None = None) -> dict[str, Any]:
        current = self.now()
        selected_day = _date(day) if day is not None else current.date()
        return {
            "config": self.config(), "tasks": self.tasks(include_completed=True),
            "events": self.events(), "expenses": self.expenses(), "work_logs": self.work_logs(),
            "today": selected_day.isoformat(), "now": current.isoformat(),
        }

    def checklist_templates(self) -> dict[str, list[str]]:
        return {name: list(items) for name, items in CHECKLISTS.items()}

    def instantiate_checklist(self, name: str) -> list[dict[str, Any]]:
        name = _text(name, "checklist name", limit=120)
        if name not in CHECKLISTS:
            raise KeyError(f"Unknown checklist: {name}")
        category = {"Event setup": "Projects", "Activity prep": "Personal", "Weekly reset": "Personal"}.get(name, "Personal")
        self._ensure()
        current = self.now().isoformat()
        with self._connection() as connection:
            result = []
            for title in CHECKLISTS[name]:
                cursor = connection.execute(
                    "INSERT INTO tasks(title, category, priority, recurrence, notes, created_at) VALUES (?, ?, 2, 'none', ?, ?)",
                    (title, category, f"Checklist: {name}", current),
                )
                result.append(_task(connection.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()))
        return result

    def backup(self) -> Path:
        self._ensure()
        directory = self.home / "backups"
        directory.mkdir(mode=0o700, exist_ok=True)
        _private(directory, 0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = directory / f"daydesk-{stamp}.sqlite3"
        # Exclusive creation avoids replacing any existing backup.
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with self._connection() as source:
                destination = sqlite3.connect(path)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        _private(path, 0o600)
        return path

    def export_csv(self) -> dict[str, Path]:
        self._ensure()
        directory = self.home / "exports" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory.mkdir(parents=True, mode=0o700)
        _private(directory.parent, 0o700)
        _private(directory, 0o700)
        columns = {
            "tasks": ["id", "title", "category", "due", "priority", "recurrence", "notes", "completed", "created_at", "completed_at"],
            "events": ["id", "title", "start", "end", "location", "category", "created_at"],
            "expenses": ["id", "amount_cents", "category", "description", "date", "created_at"],
            "work_logs": ["id", "start", "end", "role", "miles", "rate_cents", "duration_minutes", "pay_cents", "notes", "created_at"],
        }
        data = {"tasks": self.tasks(include_completed=True), "events": self.events(), "expenses": self.expenses(), "work_logs": self.work_logs()}
        paths = {}
        for name, records in data.items():
            path = directory / f"{name}.csv"
            with path.open("x", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns[name])
                writer.writeheader()
                for record in records:
                    writer.writerow({key: _safe_csv(record.get(key)) for key in columns[name]})
            _private(path, 0o600)
            paths[name] = path
        return paths
