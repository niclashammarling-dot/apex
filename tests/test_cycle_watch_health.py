"""cycle_watch reads /health as JSON, whatever the body's length (2026-10-05).

`process_state` read the first 500 bytes. The body grew to 989 bytes (12
scheduler jobs listed before `scheduler_owner`), so the key fell outside the
window and every watch line on 10-05 said `scheduler_owner=false` while /health
said true. The fixture is the real body captured from the serving backend that
day — the positive control: it fails against the 500-byte read.
"""
import http.server
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cycle_watch as cw

REAL_BODY = (Path(__file__).parent / "fixtures" / "health_2026-10-05.json").read_bytes()


@pytest.fixture
def serve():
    servers = []

    def start(body: bytes) -> int:
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]

    yield start
    for s in servers:
        s.shutdown()


def test_fixture_is_the_shape_that_broke_the_500_byte_read():
    assert len(REAL_BODY) > 500
    assert REAL_BODY.index(b'"scheduler_owner"') > 500
    assert json.loads(REAL_BODY)["scheduler_owner"] is True


def test_real_body_over_500_bytes_reads_owner_true(serve):
    assert "scheduler_owner=true" in cw.process_state(serve(REAL_BODY))


def test_owner_false_reads_false(serve):
    d = json.loads(REAL_BODY)
    d["scheduler_owner"] = False
    assert "scheduler_owner=false" in cw.process_state(serve(json.dumps(d).encode()))


def test_compact_json_reads_true(serve):
    body = json.dumps(json.loads(REAL_BODY), separators=(",", ":")).encode()
    assert "scheduler_owner=true" in cw.process_state(serve(body))


def test_non_json_body_is_named_not_read_as_false(serve):
    out = cw.process_state(serve(b"<html>proxy error</html>"))
    assert "scheduler_owner=" not in out
    assert "not JSON" in out
