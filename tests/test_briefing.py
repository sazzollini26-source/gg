import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from daydesk.briefing import alerts_for, focus_tasks, render_brief, run_daily
from daydesk.scheduling import generate_schedule
from daydesk.store import Store


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.store.initialize()
        # Use a DST-observing test timezone for the fixed timestamp fixtures.
        self.store.update_config(timezone="America/New_York")

    def snapshot(self):
        result = self.store.snapshot(day="2026-10-03")
        result["now"] = "2026-10-03T09:00:00-04:00"
        return result

    def test_focus_prioritizes_overdue_then_high_priority_and_excludes_future(self):
        self.store.add_task("Overdue", due="2026-10-02T17:00", priority=3)
        self.store.add_task("Future", due="2026-10-05T17:00", priority=1)
        self.store.add_task("Normal", priority=2)
        self.store.add_task("Important", priority=1)
        self.assertEqual([t["title"] for t in focus_tasks(self.snapshot())], ["Overdue", "Important", "Normal"])

    def test_report_events_conflicts_and_work_totals(self):
        self.store.add_event("Planning", "2026-10-03T10:00", "2026-10-03T12:00")
        self.store.add_event("Review", "2026-10-03T11:00", "2026-10-03T12:30")
        self.store.add_work("2026-10-03T08:00", "2026-10-03T10:30", "Staff", rate="30", miles=12)
        self.store.add_expense("12.50", "Food", day="2026-10-03")
        brief = render_brief(self.snapshot())
        self.assertIn("Planning overlaps Review", brief)
        self.assertIn("2.50 hours", brief)
        self.assertIn("$75.00", brief)
        self.assertIn("$12.50", brief)

    def test_alerts_do_not_notify_completed_tasks_or_distant_events(self):
        completed = self.store.add_task("Done", due="2026-10-03T08:00")
        self.store.complete_task(completed["id"])
        self.store.add_task("Overdue", due="2026-10-03T08:30")
        self.store.add_task("Due soon", due="2026-10-03T18:00")
        self.store.add_event("Next meeting", "2026-10-03T09:20", "2026-10-03T10:00")
        self.store.add_event("Later", "2026-10-03T18:00", "2026-10-03T19:00")
        self.assertEqual([a["title"] for a in alerts_for(self.snapshot())], ["Overdue", "Due soon", "Next meeting"])

    def test_run_is_idempotent_for_backup_and_updates_brief(self):
        first = run_daily(self.store)
        self.assertTrue(first["generated"])
        self.assertTrue(Path(first["backup"]).is_file())
        self.store.add_task("New priority", priority=1)
        second = run_daily(self.store)
        self.assertFalse(second["generated"])
        self.assertIsNone(second["backup"])
        self.assertIn("New priority", Path(second["path"]).read_text())
        with sqlite3.connect(first["backup"]) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_fall_dst_alerts_use_elapsed_time_and_conflicts_use_instants(self):
        self.store.add_task("Second-hour deadline", due="2026-11-01T01:30:00-05:00")
        self.store.add_event("First hour", "2026-11-01T01:10:00-04:00", "2026-11-01T01:40:00-04:00")
        self.store.add_event("Second hour", "2026-11-01T01:20:00-05:00", "2026-11-01T01:50:00-05:00")
        snapshot = self.store.snapshot(day="2026-11-01")
        snapshot["now"] = "2026-11-01T01:45:00-04:00"
        alerts = alerts_for(snapshot)
        self.assertEqual([(a["title"], a["kind"]) for a in alerts], [("Second-hour deadline", "deadline")])
        self.assertNotIn("overlaps", render_brief(snapshot))

    def test_removed_daily_backup_is_recreated(self):
        first = run_daily(self.store)
        Path(first["backup"]).unlink()
        second = run_daily(self.store)
        self.assertTrue(Path(second["backup"]).is_file())

    def test_schedule_files_are_generated_without_installation(self):
        import plistlib
        self.store.update_config(brief_time="07:15")
        mac = generate_schedule(self.store, "macos")
        payload = plistlib.loads(Path(mac["path"]).read_bytes())
        self.assertEqual(payload["StartCalendarInterval"], {"Hour": 7, "Minute": 15})
        self.assertIn(str(self.store.home), payload["ProgramArguments"])
        self.assertFalse(mac["installed"])
        linux = generate_schedule(self.store, "linux")
        self.assertIn("15 7 * * *", Path(linux["path"]).read_text())
        windows = generate_schedule(self.store, "windows")
        self.assertIn('"-m" "daydesk"', Path(windows["path"]).read_text())

    def test_windows_schedule_quotes_paths_containing_shell_characters(self):
        store = Store(Path(self.tmp.name) / "data&work!")
        store.initialize()
        result = generate_schedule(store, "windows")
        script = Path(result["path"]).read_text()
        self.assertIn('"' + str(store.home) + '"', script)
        self.assertIn("setlocal DisableDelayedExpansion", script)


if __name__ == "__main__":
    unittest.main()
