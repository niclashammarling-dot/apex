"""
The dev server's proxy error names the missing backend (2026-11-14: on a Saturday the page answered only "proxy
error"; the serving backend runs in the weekday market windows only). The handler runs under node with a stand-in
response; there is no JS test runner in frontend/package.json. Verified once by hand against a real vite with
nothing on :8000 (502 with the detail on /api/ops/pyramid and /health).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "frontend" / "proxyError.js"

_HARNESS = """
import { proxyErrorHandler } from %s;
const [port, code, sent, ws] = process.argv.slice(1);
const out = { status: null, headers: null, body: null };
const res = {
  headersSent: sent === "1", writableEnded: false,
  writeHead(s, h) { out.status = s; out.headers = h; return this; },
  end(b) { out.body = b ?? null; },
};
if (ws !== "1") res.req = { url: "/api/ops/pyramid" };
const err = Object.assign(new Error("connect"), code === "-" ? {} : { code });
proxyErrorHandler(port)(err, {}, res);
console.log(JSON.stringify(out));
"""


def _run(port: str, code: str, sent: bool = False, ws: bool = False) -> dict:
    node = shutil.which("node")
    assert node, "node is on ubuntu-latest and on the host; without it this test cannot run the handler"
    script = _HARNESS % json.dumps(MODULE.as_uri())
    p = subprocess.run([node, "--input-type=module", "-e", script, port, code, "1" if sent else "0", "1" if ws else "0"],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_no_backend_on_8000_names_the_market_window_and_the_8001_path():
    out = _run("8000", "ECONNREFUSED")
    detail = json.loads(out["body"])["detail"]
    assert out["status"] == 502 and out["headers"]["Content-Type"] == "application/json"
    assert "market windows" in detail and "--port 8001" in detail and "APEX_API_PORT=8001" in detail
    assert "never on :8000" in detail


def test_no_dev_backend_names_its_own_port():
    detail = json.loads(_run("8001", "ECONNREFUSED")["body"])["detail"]
    assert detail.startswith("no backend on :8001") and "--port 8001" in detail and "market" not in detail


def test_another_failure_reports_its_code_without_the_hint():
    detail = json.loads(_run("8000", "ECONNRESET")["body"])["detail"]
    assert detail == "proxy to :8000 failed (ECONNRESET)"
    assert json.loads(_run("8000", "-")["body"])["detail"] == "proxy to :8000 failed (unknown error)"


def test_an_answered_response_and_a_websocket_are_left_to_vite():
    assert _run("8000", "ECONNREFUSED", sent=True) == {"status": None, "headers": None, "body": None}
    assert _run("8000", "ECONNREFUSED", ws=True) == {"status": None, "headers": None, "body": None}


def test_both_proxied_paths_carry_the_handler():
    src = (ROOT / "frontend" / "vite.config.js").read_text(encoding="utf-8")
    assert 'import { proxyErrorHandler } from "./proxyError.js"' in src
    assert 'proxy.on("error", proxyErrorHandler(apiPort))' in src
    assert re.search(r'"/api":\s*backend,', src) and re.search(r'"/health":\s*backend,', src)
