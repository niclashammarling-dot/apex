"""Stand-in for uvicorn in tests/test_session_heartbeat.py: serves /health on
--port after FAKE_DELAY seconds, scheduler_owner from FAKE_OWNER ("1" = true)."""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])
time.sleep(float(os.environ.get("FAKE_DELAY", "0")))
body = json.dumps({"status": "ok", "scheduler_owner": os.environ.get("FAKE_OWNER") == "1"},
                  separators=(",", ":")).encode()


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", port), H).serve_forever()
