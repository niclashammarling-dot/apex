"""
CRITICAL ledger (2026-11-21, minimal form: build-scope items 1 and 3; riktning, Niclas 2026-11-20: "Bunt-fixen i
minsta form, punkt 1 och 3, så att nästa fredag inte tappar något"). Vault record:
raw/notes/2026-11/2026-11-20-apex-weekend-bundle-misses-criticals-that-cleared-before-friday.md § Build scope.

The weekend bundle read only Friday's run, so a CRITICAL that cleared mid-week reached no bundle: CHECK 81 on
11-17/18 (L6 REVIEW failing closed on live candidates) cleared before Friday and nobody acted on it. Niclas
2026-11-20: "criticals stannar i listan tills lördagens bundle agerar på dom … Action är lika viktig."

Writer: every nightly run (publish_state, after route_criticals) opens each CRITICAL key the first night it appears
and adds each night it is seen; a night without it records `cleared` and leaves the item open. Clearing is not an
action. An item leaves only by an action (R119: fixed, scoped or dated), recorded with a reference by `dispose`.
Reader: the weekend bundle opens with the open items (audit/bundle.py act_items), carried bundle to bundle.

Key: check|where, not the routing key check|where|text. A CRITICAL's text carries counts and dates (CHECK 81:
"6 … rows … 11-17 SPG"), so a text key would open a new item for every night's count, while the action is taken
per check and place (the 11-21 dispositions were per check: R124 for CHECK 81, R125 for CHECK 71). The latest text
is kept on the item.

Not in the minimal form, dated at the 11-21 close: runtime CRITICALs from backend/alerts._dispatch, the reference
checks on dispose (commit exists, INDEX title exists), the Monday-open sub-check, the Test tab port.

CLI:
  python -m audit.critical_ledger show
  python -m audit.critical_ledger seed --since YYYY-MM-DD        (nights from origin/audit-state:latest.json)
  python -m audit.critical_ledger dispose KEY --action fixed|scoped|dated --ref REF [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO   = Path(__file__).resolve().parent.parent
LEDGER = REPO / "audit/state/critical_ledger.json"
NY     = ZoneInfo("America/New_York")
ACTIONS = ("fixed", "scoped", "dated")          # R119: no "accepted"; a false positive is fixed in its check


def load(path: Path | None = None) -> dict:
    try:
        return json.loads((path or LEDGER).read_text())
    except (FileNotFoundError, ValueError):
        return {"items": {}, "disposed": []}


def save(led: dict, path: Path | None = None) -> None:
    path = path or LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(led, indent=1))


def key_of(f) -> str:
    return f"{f[0]}|{f[3]}"


def update(findings: list, night: str, path: Path | None = None) -> dict:
    """Apply one nightly run's findings (latest.json rows: check, name, severity, where, text) for `night` (ET date)."""
    led = load(path)
    items = led.setdefault("items", {})
    led.setdefault("disposed", [])
    tonight = {key_of(f): f for f in findings if len(f) >= 5 and f[2] == "CRITICAL"}
    for k, f in tonight.items():
        it = items.get(k)
        if it is None:
            items[k] = {"check": f[0], "name": f[1], "where": f[3], "text": f[4],
                        "first_seen": night, "nights": [night], "cleared": None}
        else:
            if night not in it["nights"]:
                it["nights"].append(night)
                it["nights"].sort()
            it["text"], it["cleared"] = f[4], None
    for k, it in items.items():
        if k not in tonight and it.get("cleared") is None and it["nights"] and it["nights"][-1] < night:
            it["cleared"] = night
    led["updated"] = night
    save(led, path)
    return led


def open_items(led: dict | None = None, path: Path | None = None) -> list[dict]:
    led = led if led is not None else load(path)
    return [dict(it, key=k) for k, it in sorted(led.get("items", {}).items(), key=lambda kv: kv[1]["first_seen"])]


def dispose(key: str, action: str, ref: str, date: str | None = None, today: str | None = None,
            path: Path | None = None) -> dict:
    """Close an open item by an action with a reference (R119). Refuses without a ref, an unknown action, a dated
    action without a date, or a key that is not open. The item moves to `disposed`; if the same key fires again
    it opens as a new item."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, not {action!r}")
    if not (ref or "").strip():
        raise ValueError("a disposition needs a reference (commit, scope note or INDEX title, R-id)")
    if action == "dated":
        if not date:
            raise ValueError("a dated action needs --date (the day the action happens)")
        datetime.fromisoformat(date)
    led = load(path)
    it = led.get("items", {}).pop(key, None)
    if it is None:
        raise KeyError(f"no open ledger item {key!r}")
    today = today or datetime.now(NY).date().isoformat()
    led.setdefault("disposed", []).append(dict(it, key=key, action=action, ref=ref, date=date, disposed_on=today))
    save(led, path)
    return led


def _night_of(payload: dict) -> str | None:
    ts = payload.get("generated_at")
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(NY).date().isoformat()


def seed(since: str, branch: str = "origin/audit-state", path: Path | None = None) -> list[str]:
    """Replay the published nightly states since `since` into the ledger, oldest first (the build's first firing)."""
    log = subprocess.run(["git", "-C", str(REPO), "log", "--reverse", "--format=%H", f"--since={since}", branch,
                          "--", "latest.json"], capture_output=True, text=True, check=True).stdout.split()
    nights = []
    for sha in log:
        raw = subprocess.run(["git", "-C", str(REPO), "show", f"{sha}:latest.json"], capture_output=True, text=True)
        try:
            payload = json.loads(raw.stdout)
        except ValueError:
            continue
        night = _night_of(payload)
        if not night or night < since:
            continue
        update(payload.get("findings", []), night, path)
        nights.append(night)
    return nights


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m audit.critical_ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    s = sub.add_parser("seed"); s.add_argument("--since", required=True)
    d = sub.add_parser("dispose"); d.add_argument("key"); d.add_argument("--action", required=True, choices=ACTIONS)
    d.add_argument("--ref", required=True); d.add_argument("--date")
    a = ap.parse_args(argv)
    if a.cmd == "seed":
        print(f"seeded nights: {', '.join(seed(a.since)) or 'none'}")
    elif a.cmd == "dispose":
        dispose(a.key, a.action, a.ref, a.date)
        print(f"disposed {a.key}: {a.action} ({a.ref})")
    for it in open_items():
        state = "standing" if it["cleared"] is None else f"cleared {it['cleared']}"
        print(f"OPEN  {it['key']}  since {it['first_seen']}  nights {len(it['nights'])}  {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
