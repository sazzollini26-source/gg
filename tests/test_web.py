"""Exercise the actual local HTTP API and its cross-site write protections."""

import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

from daydesk.store import Store
from daydesk.web import MAX_BODY_BYTES, make_server


class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))
        self.store.initialize()
        self.server = make_server(self.store, port=0)
        self.port = self.server.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.origin = f"http://{self.host}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.page = body.decode("utf-8")
        self.token = re.search(r'<meta name="csrf-token" content="([^"]+)">', self.page).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=None):
        values = {"Host": self.host}
        if method == "POST":
            values.update({"Origin": self.origin, "X-CSRF-Token": getattr(self, "token", ""), "Content-Type": "application/json"})
        if headers:
            for name, value in headers.items():
                if value is None:
                    values.pop(name, None)
                else:
                    values[name] = value
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=values)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def json_request(self, method, path, payload=None, **kwargs):
        status, headers, body = self.request(method, path, payload, **kwargs)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        return status, json.loads(body)

    def test_task_creation_completion_and_recurrence_with_real_store(self):
        status, created = self.json_request("POST", "/api/tasks", {
            "title": "Review project checklist", "category": "Projects",
            "due": "2026-10-03T09:00", "priority": 1, "recurrence": "weekly",
        })
        self.assertEqual(status, 200)
        identifier = created["result"]["id"]
        self.assertIsInstance(identifier, int)
        status, completed = self.json_request("POST", f"/api/tasks/{identifier}/complete", {})
        self.assertEqual(status, 200)
        self.assertTrue(completed["result"]["completed"])
        status, state = self.json_request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(len(state["tasks"]), 2)
        self.assertEqual(len([task for task in state["tasks"] if not task["completed"]]), 1)
        self.assertIn("Activity prep", state["templates"])
        status, _ = self.json_request("POST", f"/api/tasks/{identifier}/complete", {})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.store.tasks(include_completed=True)), 2)

    def test_other_forms_match_store_contract_and_exports_exist(self):
        status, _ = self.json_request("POST", "/api/config", {"timezone": "America/Los_Angeles", "name": "Example user"})
        self.assertEqual(status, 200)
        event_start = self.store.now().replace(hour=9, minute=0, second=0, microsecond=0)
        event_end = event_start.replace(hour=10, minute=30)
        status, event = self.json_request("POST", "/api/events", {
            "title": "Planning session", "start": event_start.replace(tzinfo=None).isoformat(),
            "end": event_end.replace(tzinfo=None).isoformat(), "location": "Meeting room",
        })
        self.assertEqual(status, 200)
        self.assertEqual(event["result"]["start"], event_start.isoformat())
        status, expense = self.json_request("POST", "/api/expenses", {"amount": "18.25", "category": "Travel", "day": "2026-10-03"})
        self.assertEqual(status, 200)
        self.assertEqual(expense["result"]["amount_cents"], 1825)
        status, work = self.json_request("POST", "/api/work", {
            "role": "Event setup", "start": "2026-10-03T07:30", "end": "2026-10-03T10:00", "rate": "15.00", "miles": 12.5,
        })
        self.assertEqual(status, 200)
        self.assertEqual(work["result"]["pay_cents"], 3750)
        status, checklist = self.json_request("POST", "/api/checklists", {"name": "Morning reset"})
        self.assertEqual(status, 200)
        self.assertGreater(len(checklist["result"]), 0)
        status, backup = self.json_request("POST", "/api/backup", {})
        self.assertEqual(status, 200)
        self.assertTrue(Path(backup["result"]).is_file())
        status, exports = self.json_request("POST", "/api/export", {})
        self.assertEqual(status, 200)
        self.assertTrue(all(Path(path).is_file() for path in exports["result"].values()))
        status, brief = self.json_request("GET", "/api/brief")
        self.assertEqual(status, 200)
        self.assertIn("Planning session", brief["markdown"])
        status, result = self.json_request("POST", "/api/run", {})
        self.assertEqual(status, 200)
        self.assertTrue(Path(result["result"]["path"]).is_file())

    def test_host_origin_and_csrf_block_cross_site_writes(self):
        cases = [
            {"Host": "evil.example"},
            {"Host": f"127.0.0.1:{self.port + 1}"},
            {"Origin": "https://evil.example"},
            {"Origin": "null"},
            {"Origin": None},
            {"X-CSRF-Token": "wrong"},
            {"X-CSRF-Token": "é"},
            {"X-CSRF-Token": None},
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                status, body = self.json_request("POST", "/api/tasks", {"title": "Should not be created"}, headers=headers)
                self.assertEqual(status, 403)
                self.assertFalse(body["ok"])
        self.assertEqual(self.store.tasks(), [])
        status, _ = self.json_request("GET", "/api/state", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_edit_and_delete_correct_records(self):
        _, created = self.json_request("POST", "/api/tasks", {"title": "Adjust my deadline"})
        identifier = created["result"]["id"]
        status, edited = self.json_request("POST", f"/api/tasks/{identifier}/edit", {"due": "2026-10-06T10:30"})
        self.assertEqual(status, 200)
        self.assertEqual(edited["result"]["due"], "2026-10-06T10:30:00+00:00")
        status, _ = self.json_request("POST", f"/api/tasks/{identifier}/delete", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.tasks(include_completed=True), [])
        samples = {
            "events": self.store.add_event("Mistyped event", "2026-10-03T08:00", "2026-10-03T09:00"),
            "expenses": self.store.add_expense("12.00", "Test"),
            "work_logs": self.store.add_work("2026-10-03T08:00", "2026-10-03T09:00", "Test"),
        }
        for kind, record in samples.items():
            with self.subTest(kind=kind):
                status, _ = self.json_request("POST", f"/api/{kind}/{record['id']}/delete", {})
                self.assertEqual(status, 200)
                self.assertEqual(self.store.snapshot()[kind], [])
        status, _ = self.json_request("POST", "/api/configuration/1/delete", {})
        self.assertEqual(status, 400)

    def test_invalid_payloads_and_file_paths_are_json_errors(self):
        for raw in [b"not json", b"[]", b'{"title":"Bad","priority":NaN}']:
            with self.subTest(raw=raw):
                status, body = self.json_request("POST", "/api/tasks", raw=raw)
                self.assertEqual(status, 400)
                self.assertFalse(body["ok"])
        status, _ = self.json_request("POST", "/api/tasks", {"title": "Unsupported field", "password": "no"})
        self.assertEqual(status, 400)
        status, _ = self.json_request("POST", "/api/tasks/nope/complete", {})
        self.assertEqual(status, 400)
        status, _ = self.json_request("POST", "/api/tasks/0/complete", {})
        self.assertEqual(status, 400)
        status, _ = self.json_request("POST", "/api/tasks", raw=b"x" * (MAX_BODY_BYTES + 1))
        self.assertEqual(status, 413)
        status, _ = self.json_request("POST", "/api/tasks", raw=b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        for path in ["/../daydesk.sqlite3", "/static/index.html", "/api/nope"]:
            status, body = self.json_request("GET", path)
            self.assertEqual(status, 404)
            self.assertFalse(body["ok"])

    def test_csp_session_token_and_no_cors(self):
        self.assertNotIn("__CSRF_TOKEN__", self.page)
        self.assertNotIn("__CSP_NONCE__", self.page)
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("script-src 'nonce-", headers["Content-Security-Policy"])
        self.assertNotIn("'unsafe-inline'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, _ = self.json_request("OPTIONS", "/api/tasks")
        self.assertEqual(status, 405)


if __name__ == "__main__":
    unittest.main()
