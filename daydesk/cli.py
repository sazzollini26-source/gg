"""Command-line entry point, scheduled runs, and a local web dashboard."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from . import __version__
from .briefing import alerts_for, render_brief, run_daily
from .scheduling import generate_schedule
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daydesk: automate your daily brief, task reminders, checklists, and backups.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("DAYDESK_HOME", str(Path.home() / ".daydesk"))), help="Private data folder (default: ~/.daydesk or DAYDESK_HOME)")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("init", help="Create a private empty workspace")
    serve = subs.add_parser("serve", help="Open your local dashboard")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="Open the browser automatically")
    subs.add_parser("brief", help="Print today's plan")
    subs.add_parser("run", help="Save today's brief and create a daily backup")
    watch = subs.add_parser("watch", help="Run daily routines and check reminders while this process stays open")
    watch.add_argument("--interval", type=int, default=60, help="Seconds between checks (1–3600)")
    watch.add_argument("--once", action="store_true", help="Check once and exit")
    task = subs.add_parser("task", help="Create, list, and complete tasks").add_subparsers(dest="action", required=True)
    add = task.add_parser("add")
    add.add_argument("title")
    add.add_argument("--category", default="Personal")
    add.add_argument("--due", help="Local date-time, e.g. 2026-10-04T17:00")
    add.add_argument("--priority", type=int, choices=[1, 2, 3], default=2, help="1 high, 2 normal, 3 low")
    add.add_argument("--repeat", choices=["none", "daily", "weekly"], default="none")
    add.add_argument("--notes", default="")
    listing = task.add_parser("list")
    listing.add_argument("--all", action="store_true", help="Include completed tasks")
    done = task.add_parser("done")
    done.add_argument("id", type=int)
    edit = task.add_parser("edit", help="Correct or reschedule an open task")
    edit.add_argument("id", type=int)
    edit.add_argument("--title")
    edit.add_argument("--category")
    due_options = edit.add_mutually_exclusive_group()
    due_options.add_argument("--due")
    due_options.add_argument("--clear-due", action="store_true")
    edit.add_argument("--priority", type=int, choices=[1, 2, 3])
    edit.add_argument("--repeat", dest="recurrence", choices=["none", "daily", "weekly"])
    edit.add_argument("--notes")
    task.add_parser("delete", help="Delete a task").add_argument("id", type=int)
    event = subs.add_parser("event", help="Create and list commitments").add_subparsers(dest="action", required=True)
    add = event.add_parser("add")
    add.add_argument("title")
    add.add_argument("--start", required=True)
    add.add_argument("--end", required=True)
    add.add_argument("--location", default="")
    add.add_argument("--category", default="Personal")
    event.add_parser("list")
    event.add_parser("delete", help="Remove an event").add_argument("id", type=int)
    expense = subs.add_parser("expense", help="Log and list spending").add_subparsers(dest="action", required=True)
    add = expense.add_parser("add")
    add.add_argument("amount", help="Dollars, e.g. 12.50")
    add.add_argument("--category", required=True)
    add.add_argument("--description", default="")
    add.add_argument("--day", help="YYYY-MM-DD, default today")
    expense.add_parser("list")
    expense.add_parser("delete", help="Remove an incorrect expense").add_argument("id", type=int)
    work = subs.add_parser("work", help="Calculate work hours, gross pay, and mileage").add_subparsers(dest="action", required=True)
    add = work.add_parser("add")
    add.add_argument("--start", required=True)
    add.add_argument("--end", required=True)
    add.add_argument("--role", required=True)
    add.add_argument("--miles", default="0")
    add.add_argument("--rate", default="0", help="Hourly pay in dollars")
    add.add_argument("--notes", default="")
    work.add_parser("list")
    work.add_parser("delete", help="Remove an incorrect work log").add_argument("id", type=int)
    checklist = subs.add_parser("checklist", help="Apply reusable daily, activity, and event routines").add_subparsers(dest="action", required=True)
    checklist.add_parser("list")
    apply = checklist.add_parser("apply")
    apply.add_argument("name")
    config = subs.add_parser("config", help="View or change your profile and local reminder time")
    config.add_argument("--name")
    config.add_argument("--timezone")
    config.add_argument("--brief-time", dest="brief_time")
    subs.add_parser("backup", help="Create a consistent SQLite data backup")
    subs.add_parser("export", help="Export Excel-compatible CSV files")
    schedule = subs.add_parser("schedule", help="Generate OS scheduler files for you to review and enable")
    schedule.add_argument("--platform", choices=["macos", "linux", "windows"],
                          default="macos" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux")
    return parser


def _output(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def watch(store, interval: int = 60, once: bool = False) -> None:
    if not 1 <= interval <= 3600:
        raise ValueError("Interval must be between 1 and 3600 seconds")
    result = run_daily(store)
    print(f"Daily brief: {result['path']}", flush=True)
    print("Watching locally. Keep this process open; press Ctrl+C to stop.", flush=True)
    last_scheduled_day = None
    seen_alerts = set()
    seen_day = None
    while True:
        snapshot = store.snapshot()
        now = store.now()
        today = snapshot["today"]
        if seen_day != today:
            seen_alerts.clear()
            seen_day = today
        if now.strftime("%H:%M") >= snapshot["config"]["brief_time"] and last_scheduled_day != today:
            result = run_daily(store)
            last_scheduled_day = today
            print(f"[{now:%H:%M}] Updated daily brief: {result['path']}", flush=True)
        for alert in alerts_for(snapshot):
            if alert["id"] not in seen_alerts:
                print(f"[{now:%H:%M}] {alert['message']}", flush=True)
                seen_alerts.add(alert["id"])
        if once:
            return
        time.sleep(interval)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        store = Store(args.home.expanduser().resolve())
        store.initialize()
        if args.command == "init":
            print(f"Daydesk is ready. Private data: {store.home}\nStart: python3 -m daydesk serve --open")
        elif args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise ValueError("Port must be 1–65535")
            from .web import serve
            serve(store, port=args.port, open_browser=args.open)
        elif args.command == "brief":
            print(render_brief(store.snapshot()))
        elif args.command == "run":
            _output(run_daily(store))
        elif args.command == "watch":
            watch(store, args.interval, args.once)
        elif args.command == "task":
            if args.action == "add":
                _output(store.add_task(args.title, category=args.category, due=args.due, priority=args.priority, recurrence=args.repeat, notes=args.notes))
            elif args.action == "done":
                _output(store.complete_task(args.id))
            elif args.action == "edit":
                changes = {field: getattr(args, field) for field in ["title", "category", "due", "priority", "recurrence", "notes"] if getattr(args, field) is not None}
                if args.clear_due:
                    changes["due"] = None
                _output(store.update_task(args.id, **changes))
            elif args.action == "delete":
                _output(store.delete_record("tasks", args.id))
            else:
                _output(store.tasks(include_completed=args.all))
        elif args.command == "event":
            if args.action == "add":
                _output(store.add_event(args.title, start=args.start, end=args.end, location=args.location, category=args.category))
            elif args.action == "delete":
                _output(store.delete_record("events", args.id))
            else:
                _output(store.events())
        elif args.command == "expense":
            if args.action == "add":
                _output(store.add_expense(args.amount, category=args.category, description=args.description, day=args.day))
            elif args.action == "delete":
                _output(store.delete_record("expenses", args.id))
            else:
                _output(store.expenses())
        elif args.command == "work":
            if args.action == "add":
                _output(store.add_work(args.start, args.end, args.role, miles=args.miles, rate=args.rate, notes=args.notes))
            elif args.action == "delete":
                _output(store.delete_record("work_logs", args.id))
            else:
                _output(store.work_logs())
        elif args.command == "checklist":
            _output(store.checklist_templates() if args.action == "list" else store.instantiate_checklist(args.name))
        elif args.command == "config":
            changes = {k: getattr(args, k) for k in ["name", "timezone", "brief_time"] if getattr(args, k) is not None}
            if changes:
                store.update_config(**changes)
            _output(store.config())
        elif args.command == "backup":
            print(store.backup())
        elif args.command == "export":
            _output(store.export_csv())
        elif args.command == "schedule":
            _output(generate_schedule(store, args.platform))
        return 0
    except ZoneInfoNotFoundError:
        print("Timezone data is missing. Run: python -m pip install tzdata, then try again.", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(f"Daydesk: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nDaydesk stopped.")
        return 0
