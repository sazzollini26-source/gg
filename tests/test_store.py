from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from zoneinfo import ZoneInfo

from daydesk.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "data")
        self.store.initialize()

    def test_initialization_has_no_invented_tasks_and_config_is_independent(self) -> None:
        self.assertEqual(self.store.tasks(), [])
        config = self.store.config()
        self.assertEqual(config["name"], "You")
        self.assertEqual(config["timezone"], "UTC")
        self.assertEqual(config["categories"], ["Work", "Home", "Projects", "Personal"])
        config["categories"].append("Changed outside store")
        self.assertNotIn("Changed outside store", self.store.config()["categories"])
        self.store.update_config(name="Example user", brief_time="06:30")
        self.store.initialize()
        self.assertEqual(self.store.config()["name"], "Example user")

    def test_invalid_config_does_not_overwrite_valid_configuration(self) -> None:
        original = self.store.config()
        for changes in ({"name": " "}, {"timezone": "Made/Up"}, {"brief_time": "25:00"},
                        {"brief_time": "7:00"}, {"categories": []},
                        {"categories": ["Work", "work"]}, {"unexpected": 3}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.update_config(**changes)
            self.assertEqual(self.store.config(), original)

    def test_task_completion_is_idempotent_and_persistent(self) -> None:
        task = self.store.add_task("Submit report", category="Work", due="2026-10-03T17:00", priority=1)
        self.assertFalse(task["completed"])
        completed = self.store.complete_task(task["id"], now="2026-10-03T16:00")
        repeated = self.store.complete_task(task["id"], now="2026-10-04T16:00")
        self.assertEqual(completed, repeated)
        self.assertEqual(self.store.tasks(), [])
        reopened = Store(self.store.home)
        self.assertEqual(len(reopened.tasks(include_completed=True)), 1)
        self.assertTrue(reopened.tasks(include_completed=True)[0]["completed"])

    def test_daily_recurrence_preserves_wall_time_across_spring_dst(self) -> None:
        self.store.update_config(timezone="America/New_York")  # Synthetic DST test zone.
        task = self.store.add_task("Morning reset", due="2026-03-07T08:00", recurrence="daily")
        self.store.complete_task(task["id"], now="2026-03-07T08:10")
        next_task = self.store.tasks()[0]
        self.assertEqual(next_task["due"], "2026-03-08T08:00:00-04:00")
        self.store.complete_task(task["id"], now="2026-03-08T09:00")
        self.assertEqual(len(self.store.tasks()), 1)
        self.assertEqual(len(self.store.tasks(include_completed=True)), 2)

    def test_weekly_recurrence_preserves_wall_time_across_fall_dst(self) -> None:
        self.store.update_config(timezone="America/New_York")  # Synthetic DST test zone.
        task = self.store.add_task("Review project progress", due="2026-10-25T09:00", recurrence="weekly")
        self.store.complete_task(task["id"], now="2026-10-25T09:30")
        self.assertEqual(self.store.tasks()[0]["due"], "2026-11-01T09:00:00-05:00")

    def test_spring_gap_shifts_one_occurrence_and_retains_original_clock(self) -> None:
        self.store.update_config(timezone="America/New_York")  # Synthetic DST test zone.
        task = self.store.add_task("Overnight reminder", due="2026-03-07T02:30", recurrence="daily")
        self.store.complete_task(task["id"], now="2026-03-07T02:40")
        shifted = self.store.tasks()[0]
        self.assertEqual(shifted["due"], "2026-03-08T03:30:00-04:00")
        self.store.complete_task(shifted["id"], now="2026-03-08T03:40")
        self.assertEqual(self.store.tasks()[0]["due"], "2026-03-09T02:30:00-04:00")

    def test_late_completion_skips_missed_occurrences(self) -> None:
        task = self.store.add_task("Read notes", due="2026-10-01T08:00", recurrence="daily")
        self.store.complete_task(task["id"], now="2026-10-03T12:00")
        self.assertEqual(self.store.tasks()[0]["due"], "2026-10-04T08:00:00+00:00")
        self.assertEqual(len(self.store.tasks(include_completed=True)), 2)

    def test_concurrent_completion_only_creates_one_recurrence(self) -> None:
        task = self.store.add_task("Daily task", due="2026-10-03T08:00", recurrence="daily")
        errors = []
        barrier = threading.Barrier(2)

        def complete() -> None:
            try:
                another = Store(self.store.home)
                another.initialize()
                barrier.wait(timeout=5)
                another.complete_task(task["id"], now="2026-10-03T09:00")
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=complete) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.tasks()), 1)
        self.assertEqual(len(self.store.tasks(include_completed=True)), 2)

    def test_invalid_tasks_and_missing_ids(self) -> None:
        self.store.update_config(timezone="America/New_York")  # Exercises a nonexistent DST time.
        for fields in ({"title": ""}, {"title": "x", "priority": 0},
                       {"title": "x", "priority": True}, {"title": "x", "due": "2026-10-03"},
                       {"title": "x", "recurrence": "daily"},
                       {"title": "x", "recurrence": "monthly"},
                       {"title": "x", "due": "2026-03-08T02:30"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.store.add_task(**fields)
        with self.assertRaises(KeyError):
            self.store.complete_task(9999)
        with self.assertRaises(ValueError):
            self.store.complete_task(True)
        self.assertEqual(self.store.tasks(), [])

    def test_task_order_uses_instants_then_priority_with_undated_last(self) -> None:
        undated = self.store.add_task("Undated high priority", priority=1)
        late = self.store.add_task("Late", due="2026-11-01T01:30-05:00", priority=1)
        early_low = self.store.add_task("Early low", due="2026-11-01T01:30-04:00", priority=3)
        early_high = self.store.add_task("Early high", due="2026-11-01T01:30-04:00", priority=1)
        self.assertEqual([task["id"] for task in self.store.tasks()], [early_high["id"], early_low["id"], late["id"], undated["id"]])

    def test_event_overnight_and_absolute_time_order(self) -> None:
        self.store.update_config(timezone="America/New_York")  # Synthetic DST test zone.
        overnight = self.store.add_event("Overnight event", "2026-10-03T23:00", "2026-10-04T00:30", location="Meeting room")
        self.assertEqual(overnight["end"], "2026-10-04T00:30:00-04:00")
        # Repeated 01:30 occurs one hour apart during the fall transition.
        fall = self.store.add_event("Late coverage", "2026-11-01T01:30-04:00", "2026-11-01T01:30-05:00")
        self.assertEqual(fall["start"], "2026-11-01T01:30:00-04:00")
        with self.assertRaises(ValueError):
            self.store.add_event("Bad", "2026-10-03T12:00", "2026-10-03T11:00")
        with self.assertRaises(ValueError):
            self.store.add_event("Bad", "2026-10-03T12:00", "2026-10-03T12:00")

    def test_currency_stores_exact_cents_and_rejects_invalid_values(self) -> None:
        one = self.store.add_expense("0.10", "Food", day="2026-10-03")
        two = self.store.add_expense(Decimal("0.20"), "Food", day="2026-10-03")
        self.assertEqual(one["amount_cents"] + two["amount_cents"], 30)
        for value in ("0", "-1", "1.005", "NaN", "Infinity", "1000000.01", True, "words"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.add_expense(value, "Food")
        with self.assertRaises(ValueError):
            self.store.add_expense("1", "Food", day="2026-02-30")

    def test_work_overnight_pay_miles_and_dst_elapsed_hours(self) -> None:
        self.store.update_config(timezone="America/New_York")  # Synthetic DST test zone.
        shift = self.store.add_work("2026-10-03T22:00", "2026-10-04T01:30", "Event support", miles="22.5", rate="15.25")
        self.assertEqual(shift["duration_minutes"], 210)
        self.assertEqual(shift["pay_cents"], 5338)
        self.assertEqual(shift["miles"], 22.5)
        spring = self.store.add_work("2026-03-08T01:00", "2026-03-08T04:00", "Overnight", rate="10")
        self.assertEqual(spring["duration_minutes"], 120)
        self.assertEqual(spring["pay_cents"], 2000)
        fall = self.store.add_work("2026-11-01T01:30-04:00", "2026-11-01T01:30-05:00", "Coverage", rate="10")
        self.assertEqual(fall["duration_minutes"], 60)
        for fields in ({"miles": -1}, {"miles": "NaN"}, {"rate": -2}, {"rate": "2.001"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.store.add_work("2026-10-03T08:00", "2026-10-03T09:00", "Work", **fields)
        with self.assertRaises(ValueError):
            self.store.add_work("2026-10-03T22:00", "2026-10-03T01:00", "Work")

    def test_rescheduled_task_persists_and_recurrence_uses_new_clock(self) -> None:
        self.store.update_config(timezone="America/New_York")  # Synthetic DST test zone.
        original = self.store.add_task("Morning review", due="2026-03-07T08:00", recurrence="daily")
        updated = self.store.update_task(original["id"], title="Review project notes", category="Projects",
                                         due="2026-03-07T09:30", priority=1, notes="Bring project checklist")
        self.assertEqual(updated["created_at"], original["created_at"])
        self.assertEqual(updated["due"], "2026-03-07T09:30:00-05:00")
        reopened = Store(self.store.home)
        self.assertEqual(reopened.tasks()[0], updated)
        reopened.complete_task(updated["id"], now="2026-03-07T09:45")
        self.assertEqual(reopened.tasks()[0]["due"], "2026-03-08T09:30:00-04:00")
        with self.assertRaises(ValueError):
            reopened.update_task(updated["id"], title="Cannot rewrite completed history")

    def test_update_validation_is_atomic_and_removing_recurrence_allows_no_deadline(self) -> None:
        self.store.update_config(timezone="America/New_York")  # Exercises a nonexistent DST time.
        original = self.store.add_task("Daily task", due="2026-10-03T08:00", recurrence="daily")
        for fields in ({"due": None}, {"priority": 0}, {"recurrence": "monthly"},
                       {"title": ""}, {"due": "2026-03-08T02:30"}, {"created_at": "new"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.store.update_task(original["id"], **fields)
            self.assertEqual(self.store.tasks()[0], original)
        updated = self.store.update_task(original["id"], due=None, recurrence="none")
        self.assertIsNone(updated["due"])
        self.assertEqual(updated["recurrence"], "none")
        self.assertEqual(updated["created_at"], original["created_at"])
        with self.assertRaises(KeyError):
            self.store.update_task(99999, title="Missing")
        with self.assertRaises(ValueError):
            self.store.update_task(True, title="Invalid id")

    def test_delete_removes_only_selected_record_and_validates_ids_and_tables(self) -> None:
        first = self.store.add_expense("12", "Food")
        second = self.store.add_expense("18", "Travel")
        task = self.store.add_task("Same integer id in another table")
        self.assertEqual(self.store.delete_record("expenses", first["id"]), {"kind": "expenses", "id": first["id"]})
        self.assertEqual([expense["id"] for expense in self.store.expenses()], [second["id"]])
        self.assertEqual(self.store.tasks()[0]["id"], task["id"])
        with self.assertRaises(KeyError):
            self.store.delete_record("expenses", first["id"])
        for kind, record_id in (("configuration", 1), ("tasks; DROP TABLE tasks", 1), ("tasks", True), ("tasks", -1), ("tasks", "1")):
            with self.subTest(kind=kind, record_id=record_id), self.assertRaises(ValueError):
                self.store.delete_record(kind, record_id)
        event = self.store.add_event("Mistaken event", "2026-10-03T09:00", "2026-10-03T10:00")
        work = self.store.add_work("2026-10-03T09:00", "2026-10-03T10:00", "Mistaken log")
        self.store.delete_record("events", event["id"])
        self.store.delete_record("work_logs", work["id"])
        self.assertEqual(self.store.events(), [])
        self.assertEqual(self.store.work_logs(), [])

    def test_deleting_completed_recurring_parent_keeps_successor_usable(self) -> None:
        parent = self.store.add_task("Recurring task", due="2026-10-03T08:00", recurrence="daily")
        self.store.complete_task(parent["id"], now="2026-10-03T09:00")
        successor = self.store.tasks()[0]
        self.store.delete_record("tasks", parent["id"])
        self.assertEqual(self.store.tasks(include_completed=True), [successor])
        self.store.complete_task(successor["id"], now="2026-10-04T09:00")
        self.assertEqual(self.store.tasks()[0]["due"], "2026-10-05T08:00:00+00:00")

    def test_backup_is_consistent_separate_database_and_not_overwritten(self) -> None:
        self.store.add_task("Keep this record")
        backup = self.store.backup()
        other_backup = self.store.backup()
        self.assertNotEqual(backup, other_backup)
        self.assertEqual(backup.parent, self.store.home / "backups")
        with sqlite3.connect(backup) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT title FROM tasks").fetchone()[0], "Keep this record")
        self.store.add_task("Created after backup")
        with sqlite3.connect(backup) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)

    def test_csv_export_preserves_rows_and_disarms_formula_cells(self) -> None:
        self.store.add_task("=HYPERLINK(\"https://example.test\")", notes="  @SUM(1,2)")
        self.store.add_expense("3.25", "Food", description="+Danger")
        paths = self.store.export_csv()
        self.assertEqual(set(paths), {"tasks", "events", "expenses", "work_logs"})
        with paths["tasks"].open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
        self.assertTrue(row["title"].startswith("'="))
        self.assertTrue(row["notes"].startswith("'@"))
        with paths["expenses"].open(newline="", encoding="utf-8") as handle:
            expense = next(csv.DictReader(handle))
        self.assertEqual(expense["amount_cents"], "325")
        self.assertEqual(expense["description"], "'+Danger")
        with paths["events"].open(newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])
        self.assertNotEqual(paths["tasks"], self.store.export_csv()["tasks"])

    def test_snapshot_uses_selected_day_and_aware_now(self) -> None:
        snapshot = self.store.snapshot("2026-10-05")
        self.assertEqual(snapshot["today"], "2026-10-05")
        self.assertIsNotNone(datetime.fromisoformat(snapshot["now"]).tzinfo)
        self.assertIsInstance(self.store.now().tzinfo, ZoneInfo)
        self.assertEqual(set(snapshot), {"config", "tasks", "events", "expenses", "work_logs", "today", "now"})

    def test_checklist_template_instantiation_creates_only_requested_undated_tasks(self) -> None:
        templates = self.store.checklist_templates()
        self.assertEqual(len(templates), 4)
        templates["Morning reset"].clear()
        self.assertTrue(self.store.checklist_templates()["Morning reset"])
        records = self.store.instantiate_checklist("Event setup")
        self.assertEqual(len(records), len(templates["Event setup"]))
        self.assertTrue(all(task["due"] is None and task["category"] == "Projects" for task in records))
        with self.assertRaises(KeyError):
            self.store.instantiate_checklist("Made up")


if __name__ == "__main__":
    unittest.main()
