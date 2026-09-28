"""Standard-library HTTP API for the visual debugger."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .debugger import source_hash
from .parser import LexEduError, ParseEduError
from .store import SessionStore, StoreError

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "frontend")


def make_handler(store: SessionStore):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EDLDebugger/0.1"

        def log_message(self, fmt, *args):  # quieter test runs
            pass

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StoreError(f"invalid JSON body: {exc}")
            return data if isinstance(data, dict) else {}

        def do_GET(self):
            try:
                if self.path == "/api/sessions":
                    self._send_json(200, {"sessions": store.list_sessions()})
                    return
                if self.path.startswith("/api/sessions/"):
                    session_id = self.path.split("/", 3)[3]
                    session = store.require(session_id)
                    state = session.state()
                    state["source"] = session.source
                    self._send_json(200, state)
                    return
                self._send_json(404, {"error": "not found"})
            except StoreError as exc:
                self._send_json(404, {"error": str(exc)})

        def do_POST(self):
            try:
                data = self._read_json()
                if self.path == "/api/sessions":
                    source = data.get("source", "")
                    if not isinstance(source, str):
                        raise StoreError("source must be a string")
                    session = store.create(source)
                    self._send_json(201, session.state())
                    return
                if self.path.startswith("/api/sessions/"):
                    parts = self.path.strip("/").split("/")
                    # /api/sessions/{id}/{action}
                    if len(parts) != 4:
                        raise StoreError("unknown endpoint")
                    session_id, action = parts[2], parts[3]
                    session = store.require(session_id)
                    if action == "check-source":
                        source = data.get("source", session.source)
                        matches = source_hash(source) == session.src_hash
                        self._send_json(200, {"matches": matches, "srcHash": session.src_hash})
                        return
                    if "source" in data:
                        store.assert_source_matches(session, data["source"])
                    if action in ("stepInto", "stepOver", "continue"):
                        store.apply_action(session, action)
                    elif action in ("breakpoints",):
                        line = data.get("line")
                        verb = data.get("verb", "set")
                        store.apply_action(session, "setBreakpoint" if verb == "set" else "clearBreakpoint", {"line": line})
                    else:
                        raise StoreError(f"unknown action {action!r}")
                    self._send_json(200, session.state())
                    return
                self._send_json(404, {"error": "not found"})
            except StoreError as exc:
                self._send_json(400, {"error": str(exc)})
            except (LexEduError, ParseEduError) as exc:
                self._send_json(400, {"error": f"syntax error: {exc}"})

    return Handler


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="EDL visual debugger backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sessions"))
    args = parser.parse_args()

    store = SessionStore(args.data)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    print(f"EDL debugger backend listening on http://{args.host}:{args.port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
