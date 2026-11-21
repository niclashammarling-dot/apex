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

Key: check|where plus an episode (riktning, Niclas 2026-11-21), not the routing key check|where|text. A text key
would open a new item for every night's count (CHECK 81: "6 … rows … 11-17 SPG"); check|where alone would merge
separate runs of the alarm, so a CRITICAL that cleared and came back weeks later for another reason would land
under a decision already taken. An episode opens when the check goes CRITICAL after a run without it; a disposition
applies to its episode; the latest text is kept per episode.

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
    """The check and place a CRITICAL belongs to (the base of its episodes)."""
    return f"{f[0]}|{f[3]}"


def _standing(items: dict, base: str) -> str | None:
    """The episode of `base` that has not cleared, if any (at most one by construction)."""
    for k, it in items.items():
        if it.get("base") == base and it.get("cleared") is None:
            return k
    return None


def update(findings: list, night: str, path: Path | None = None) -> dict:
    """Apply one nightly run's findings (latest.json rows: check, name, severity, where, text) for `night` (ET date).

    Episodes (riktning, Niclas 2026-11-21): an item is check|place plus an episode. An episode opens the night the
    check goes CRITICAL after a run without the alarm (or for the first time) and continues while each run carries
    it; the latest text is kept per episode. A run without it records `cleared`, and a later return opens a new
    episode, so a new problem never lands under a decision already taken. A disposition applies to its episode:
    while that episode stands, nights are added and the decision holds; a dated disposition whose date has passed
    while the episode still stands reopens it."""
    led = load(path)
    items = led.setdefault("items", {})
    led.setdefault("disposed", [])                     # pre-episode ledgers: kept as history, never read back
    tonight = {key_of(f): f for f in findings if len(f) >= 5 and f[2] == "CRITICAL"}
    for base, f in tonight.items():
        k = _standing(items, base)
        if k is None:
            k = f"{base}|{night}"
            items[k] = {"base": base, "check": f[0], "name": f[1], "where": f[3], "text": f[4],
                        "first_seen": night, "nights": [night], "cleared": None, "disposition": None}
        else:
            it = items[k]
            if night not in it["nights"]:
                it["nights"].append(night)
                it["nights"].sort()
            it["text"] = f[4]
            d = it.get("disposition")
            if d and d.get("action") == "dated" and d.get("date") and d["date"] < night:
                it.setdefault("history", []).append(dict(d, reopened=night))
                it["disposition"] = None
    for k, it in items.items():
        if it.get("cleared") is None and it.get("base") not in tonight and it["nights"] and it["nights"][-1] < night:
            it["cleared"] = night
    led["updated"] = night
    save(led, path)
    return led


def open_items(led: dict | None = None, path: Path | None = None) -> list[dict]:
    """Episodes with no disposition, oldest first."""
    led = led if led is not None else load(path)
    return [dict(it, key=k) for k, it in sorted(led.get("items", {}).items(), key=lambda kv: kv[1]["first_seen"])
            if not it.get("disposition")]


def dispose(key: str, action: str, ref: str, date: str | None = None, today: str | None = None,
            path: Path | None = None) -> dict:
    """Record an action with a reference on one open episode (R119). Refuses without a ref, an unknown action, a
    dated action without a date, or a key that is not an open episode. The episode keeps its nights."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, not {action!r}")
    if not (ref or "").strip():
        raise ValueError("a disposition needs a reference (commit, scope note or INDEX title, R-id)")
    if action == "dated":
        if not date:
            raise ValueError("a dated action needs --date (the day the action happens)")
        datetime.fromisoformat(date)
    led = load(path)
    it = led.get("items", {}).get(key)
    if it is None or it.get("disposition"):
        raise KeyError(f"no open ledger episode {key!r}")
    today = today or datetime.now(NY).date().isoformat()
    it["disposition"] = {"action": action, "ref": ref, "date": date, "disposed_on": today}
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
