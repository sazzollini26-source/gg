import contextlib
import io
import json
import tempfile
import unittest

from daydesk.cli import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--home", self.tmp.name, *args])
        return code, out.getvalue(), err.getvalue()

    def test_first_run_and_task_lifecycle(self):
        self.assertEqual(self.call("init")[0], 0)
        code, out, _ = self.call("task", "add", "Review notes", "--priority", "1")
        self.assertEqual(code, 0)
        task_id = json.loads(out)["id"]
        self.assertEqual(self.call("task", "done", str(task_id))[0], 0)
        self.assertEqual(json.loads(self.call("task", "list")[1]), [])
        self.assertEqual(self.call("run")[0], 0)

    def test_validation_error_returns_nonzero_without_traceback(self):
        code, out, err = self.call("expense", "add", "NaN", "--category", "Food")
        self.assertEqual(code, 1)
        self.assertIn("Daydesk:", err)
        self.assertNotIn("Traceback", err)

    def test_watcher_once_and_csv_exports(self):
        self.assertEqual(self.call("watch", "--once")[0], 0)
        code, out, _ = self.call("export")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out))


if __name__ == "__main__":
    unittest.main()
