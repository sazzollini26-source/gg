"""Private, dependency-free web dashboard served only on this computer."""

from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .store import Store

MAX_BODY_BYTES = 64 * 1024


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def make_server(store: Store, port: int = 8765) -> ThreadingHTTPServer:
    """Make a loopback HTTP server. A port of zero selects a free port."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Port must be an integer between 0 and 65535.")
    csrf_token = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    template = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    page = template.replace("__CSRF_TOKEN__", csrf_token).replace("__CSP_NONCE__", nonce).encode("utf-8")
    lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "DayDesk"
        sys_version = ""

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format: str, *args: Any) -> None:
            # Calendar titles and other private details should not end up in logs.
            return

        def _send(self, status: int, content: bytes, content_type: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Content-Security-Policy", "; ".join([
                "default-src 'none'", f"script-src 'nonce-{nonce}'",
                f"style-src 'nonce-{nonce}'", "connect-src 'self'", "img-src data:",
                "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'",
            ]))
            self.end_headers()
            self.wfile.write(content)

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, default=_json_default, allow_nan=False).encode("utf-8")
            self._send(status, body)

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"ok": False, "error": message})

        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            self.close_connection = True
            self._error(code, message or "Invalid HTTP request.")

        def _host(self) -> str | None:
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1:
                self._error(403, "A single local Host header is required.")
                return None
            host = hosts[0]
            bound_port = self.server.server_address[1]
            if host not in {f"127.0.0.1:{bound_port}", f"localhost:{bound_port}", f"[::1]:{bound_port}"}:
                self._error(403, "This dashboard only accepts requests to its local address.")
                return None
            return host

        def _write_authorized(self, host: str) -> bool:
            origins = self.headers.get_all("Origin", [])
            # Require Origin even for scripts: use the CLI for non-browser writes.
            if len(origins) != 1 or origins[0] != f"http://{host}":
                self._error(403, "A same-origin request is required.")
                return False
            tokens = self.headers.get_all("X-CSRF-Token", [])
            if len(tokens) != 1 or not secrets.compare_digest(tokens[0].encode("utf-8"), csrf_token.encode("ascii")):
                self._error(403, "Invalid session token. Reload the dashboard and try again.")
                return False
            return True

        def _read_json(self) -> dict[str, Any] | None:
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                self._error(400, "Transfer encoding is not supported.")
                return None
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                self._error(400, "A valid Content-Length is required.")
                return None
            if len(lengths[0]) > 10:
                self.close_connection = True
                self._error(413, "Request is too large (maximum 64 KiB).")
                return None
            length = int(lengths[0])
            if length > MAX_BODY_BYTES:
                self.close_connection = True
                self._error(413, "Request is too large (maximum 64 KiB).")
                return None
            if self.headers.get_content_type() != "application/json":
                self._error(415, "Send application/json.")
                return None
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete request body.")

                def reject_constant(value: str) -> None:
                    raise ValueError("JSON numbers must be finite.")

                payload = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
                if not isinstance(payload, dict):
                    raise ValueError("The request body must be a JSON object.")
                return payload
            except (ValueError, UnicodeError, TimeoutError) as exc:
                self._error(400, f"Invalid JSON: {exc}")
                return None

        def do_GET(self) -> None:
            if self._host() is None:
                return
            try:
                with lock:
                    if self.path == "/":
                        self._send(200, page, "text/html; charset=utf-8")
                    elif self.path == "/api/state":
                        state = store.snapshot()
                        state["templates"] = store.checklist_templates()
                        self._json(200, state)
                    elif self.path == "/api/brief":
                        from .briefing import render_brief
                        self._json(200, {"markdown": render_brief(store.snapshot())})
                    else:
                        self._error(404, "Page not found.")
            except (ValueError, TypeError, KeyError) as exc:
                self._error(400, str(exc))
            except Exception:
                self._error(500, "The dashboard could not read your data. Try again or check your data with the CLI.")

        def do_POST(self) -> None:
            host = self._host()
            if host is None or not self._write_authorized(host):
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                with lock:
                    if self.path == "/api/tasks":
                        result = store.add_task(**payload)
                    elif self.path.startswith("/api/tasks/") and self.path.endswith("/complete"):
                        identifier = self.path.removeprefix("/api/tasks/").removesuffix("/complete")
                        if not identifier or "/" in identifier or payload:
                            raise ValueError("Choose a task and send an empty JSON object.")
                        result = store.complete_task(int(identifier))
                    elif self.path.startswith("/api/tasks/") and self.path.endswith("/edit"):
                        identifier = self.path.removeprefix("/api/tasks/").removesuffix("/edit")
                        if not identifier.isascii() or not identifier.isdigit():
                            raise ValueError("Choose a valid task id.")
                        result = store.update_task(int(identifier), **payload)
                    elif self.path.endswith("/delete"):
                        parts = self.path.split("/")
                        if (len(parts) != 5 or parts[1] != "api" or
                                parts[2] not in {"tasks", "events", "expenses", "work_logs"} or
                                not parts[3].isascii() or not parts[3].isdigit() or payload):
                            raise ValueError("Choose a valid record and send an empty JSON object.")
                        result = store.delete_record(parts[2], int(parts[3]))
                    elif self.path == "/api/events":
                        result = store.add_event(**payload)
                    elif self.path == "/api/expenses":
                        result = store.add_expense(**payload)
                    elif self.path == "/api/work":
                        result = store.add_work(**payload)
                    elif self.path == "/api/config":
                        result = store.update_config(**payload)
                    elif self.path == "/api/checklists":
                        if set(payload) != {"name"} or not isinstance(payload["name"], str):
                            raise ValueError("Choose a checklist name.")
                        result = store.instantiate_checklist(payload["name"])
                    elif self.path in {"/api/run", "/api/backup", "/api/export"}:
                        if payload:
                            raise ValueError("Send an empty JSON object for this action.")
                        if self.path == "/api/run":
                            from .briefing import run_daily
                            result = run_daily(store)
                        elif self.path == "/api/backup":
                            result = store.backup()
                        else:
                            result = store.export_csv()
                    else:
                        self._error(404, "Action not found.")
                        return
                    self._json(200, {"ok": True, "result": result})
            except (ValueError, TypeError, KeyError) as exc:
                self._error(400, str(exc))
            except Exception:
                self._error(500, "The action could not be completed. Your data may need attention; try again or use the CLI.")

        def _unsupported(self) -> None:
            if self._host() is not None:
                self._error(405, "This method is not supported.")

        do_HEAD = _unsupported
        do_OPTIONS = _unsupported
        do_PUT = _unsupported
        do_PATCH = _unsupported
        do_DELETE = _unsupported

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def serve(store: Store, port: int = 8765, open_browser: bool = False) -> None:
    """Run the dashboard until interrupted. No internet connection is needed."""
    server = make_server(store, port)
    address = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"DayDesk is ready at {address}")
    print("Keep this terminal open. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDayDesk stopped.")
    finally:
        server.server_close()
