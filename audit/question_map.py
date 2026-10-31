"""
The question map (audit/QUESTIONS.md) read into the pyramid (Niclas 2026-10-21: "underhållas
automatiskt utifrån det arbete vi gör … siffrorna ska komma ur vårt arbete med att kartlägga hur vi
får svar på alla våra frågor").

Each question's references are resolved against the registers they name, each row gets a state read
from its register, and each question rolls up to answered / partial / open. Nothing here holds a
state of its own: the map holds the questions, the registers hold the rows.

Row states (QUESTIONS.md § Derivation):
  S        signal register first status word; adopted/discarded answered
  GAP      PRODUCTION_GAP parity column first word; yes/closed/n/a answered
  CHECK    audit/state/latest.json: evaluated with no WARNING/CRITICAL finding answered
  PARAM    PARAMETERS.json kind; chosen answered until its review date passes
  M        never answered
An unreadable register raises RegisterUnread: a pyramid of zeros is never shown for a broken reader.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAP = REPO / "audit" / "QUESTIONS.md"
SIGNALS = REPO / "backend" / "backtest" / "SIGNAL_REGISTER.md"
GAPS = REPO / "backend" / "backtest" / "PRODUCTION_GAP.md"
CHECKS = REPO / "audit" / "CHECKS.md"
STATE = REPO / "audit" / "state" / "latest.json"
PARAMS = REPO / "audit" / "PARAMETERS.json"
VAULT_NOTES = Path("/mnt/c/Users/Nix/Niclas-KB/raw/notes")

# Card verdicts (2026-10-31, pyramid ruling A of 2026-10-24): one word in the card's frontmatter. A question whose
# cards all carry an unanswered verdict is a break in the chain, shown as such.
CARD_VERDICTS = {"supported", "not-supported", "reverses", "unanswered", "pending", "instrument"}
CARD_UNANSWERED = {"unanswered", "pending"}

S_ANSWERED = {"adopted", "discarded"}
GAP_ANSWERED = {"yes", "closed", "n/a"}
REF = re.compile(r"^(S\d+|GAP-\d+b?|CHECK-\d+|CHECK-\*|PARAM-(?:money|ops)|Q-[A-Z]+|G\d|O3|M\d+)$")


class RegisterUnread(Exception):
    pass


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _first_word(s: str) -> str:
    m = re.match(r"[*_`\s]*([A-Za-z/-]+)", s)
    return m.group(1).lower() if m else ""


def parse_map(text: str) -> dict:
    """Questions (top and layers) and missing rows, keyed by id, in file order."""
    qs, missing, section = {}, {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if not line.startswith("| ") or line.startswith("| id ") or line.startswith("|---"):
            continue
        c = _cells(line)
        if section == "top":
            qid, q, up, thr, refs = c[:5]
        elif section == "layers":
            (qid, q, up, refs), thr = c[:4], None
        elif section == "missing rows":
            missing[c[0]] = {"what": c[1], "needed_by": c[2], "where": c[3]}
            continue
        else:
            continue
        qs[qid] = {"question": q, "answers": [x.strip() for x in up.split(",") if x.strip() not in ("", "—")],
                   "threshold": thr, "refs": [x.strip() for x in refs.split(",") if x.strip()]}
    if not qs:
        raise RegisterUnread(f"{MAP}: no questions parsed")
    return {"questions": qs, "missing": missing}


def signal_states(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        c = _cells(line) if line.startswith("| S") else []
        if c and re.fullmatch(r"S\d+", c[0]):
            out[c[0]] = _first_word(c[4])
    if not out:
        raise RegisterUnread(f"{SIGNALS}: no S-rows parsed")
    return out


def gap_states(text: str) -> dict[str, str]:
    """Numbered rows of § Live gate path; the parity column is the fourth."""
    out = {}
    section = text.split("## Live gate path", 1)[-1].split("\n## ", 1)[0] if "## Live gate path" in text else ""
    for line in section.splitlines():
        c = _cells(line) if line.startswith("| ") else []
        if c and re.fullmatch(r"\d+b?", c[0]) and len(c) >= 4:
            out[f"GAP-{c[0]}"] = _first_word(c[3])
    if not out:
        raise RegisterUnread(f"{GAPS}: no numbered rows parsed")
    return out


def check_ids(text: str) -> list[int]:
    """Active CHECK ids from the registry (the Retired section is left out)."""
    active = text.split("## Retired")[0]
    ids = [int(m.group(1)) for m in re.finditer(r"^\| (\d+) \|", active, re.M)]
    if not ids:
        raise RegisterUnread(f"{CHECKS}: no CHECK rows parsed")
    return ids


def check_states(state: dict, ids: list[int]) -> dict[str, str]:
    evaluated = {int(x) for x in state.get("evaluated", [])}
    skipped = {int(x[0] if isinstance(x, list) else x) for x in state.get("skipped", [])}
    worst: dict[int, str] = {}
    rank = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
    for f in state.get("findings", []):
        n, sev = int(f[0]), f[2]
        if rank.get(sev, 0) > rank.get(worst.get(n, "INFO"), 0):
            worst[n] = sev
    out = {}
    for n in ids:
        if n in worst:
            out[f"CHECK-{n}"] = worst[n].lower()
        elif n in skipped:
            out[f"CHECK-{n}"] = "skipped"
        elif n in evaluated:
            out[f"CHECK-{n}"] = "clean"
        else:
            out[f"CHECK-{n}"] = "not run"
    return out


def param_states(params: dict, today: date) -> dict[str, tuple[str, str]]:
    """{name: (tier, state)}; state is the kind, or 'chosen, review passed'."""
    out = {}
    for name, p in params.items():
        kind = p.get("kind", "")
        if kind == "chosen" and p.get("review") and p["review"] < today.isoformat():
            kind = "chosen, review passed"
        out[name] = (p.get("tier", ""), kind)
    return out


def kind_of(ref: str) -> str:
    return "PARAM" if ref.startswith("PARAM:") else re.match(r"[A-Z]+", ref).group(0)


def answered(ref: str, state: str) -> bool:
    if ref.startswith("S"):
        return state in S_ANSWERED
    if ref.startswith("GAP-"):
        return state in GAP_ANSWERED
    if ref.startswith("CHECK-"):
        return state == "clean"
    if ref.startswith("PARAM:"):
        return state != "chosen, review passed"
    return False


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def card_index(notes: Path = VAULT_NOTES) -> dict:
    """Test cards by the question they answer (2026-10-31). Niclas 2026-10-24: "pyramiden visar att varje lager samlar
    viss information för att svara på en fråga. Detta behöver kopplas från botten till toppen"; ruled A: every card's
    frontmatter carries `answers:` (Q-/G- ids, comma-separated or a [list]), `delivers:` (one line, what it gives the
    layer above), `verdict:` (one word of CARD_VERDICTS) and `next:` (the card or question it points to). Read from the
    cards themselves, no hand-typed numbers. Returns {"cards": {qid: [card]}, "invalid": [...]}; an unreachable vault
    raises RegisterUnread, never an empty map."""
    if not notes.is_dir():
        raise RegisterUnread(f"vault notes not reachable at {notes} (cards unread)")
    by_q: dict[str, list[dict]] = {}
    invalid = []
    for f in sorted(notes.rglob("*.md")):
        try:
            fm = _frontmatter(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if "answers" not in fm:
            continue
        ids = [x.strip() for x in fm["answers"].strip("[]").split(",") if x.strip()]
        card = {"slug": f.stem, "verdict": fm.get("verdict", ""), "delivers": fm.get("delivers", "").strip('"'),
                "next": fm.get("next", "").strip('"'), "date": fm.get("date", "")}
        bad = [i for i in ids if not REF.match(i) or not i.startswith(("Q-", "G", "O3"))]
        if card["verdict"] not in CARD_VERDICTS or not ids or bad or not card["delivers"]:
            invalid.append({"slug": f.stem, "why": f"verdict {card['verdict']!r}, answers {ids}, delivers "
                                                   f"{'set' if card['delivers'] else 'missing'}"})
            continue
        for i in ids:
            by_q.setdefault(i, []).append(card)
    for cards in by_q.values():
        cards.sort(key=lambda c: c["date"], reverse=True)
    return {"cards": by_q, "invalid": invalid}


def derive(m: dict, rows: dict[str, str], checks_all: list[str], params: dict[str, tuple[str, str]]) -> dict:
    """Roll every question up from its rows. rows: {ref: state} for S, GAP and CHECK."""
    qs = m["questions"]
    named = {r for q in qs.values() for r in q["refs"] if r.startswith("CHECK-") and r != "CHECK-*"}
    unresolved = []

    def expand(ref: str) -> list[tuple[str, str]]:
        if ref == "CHECK-*":
            return [(c, rows[c]) for c in checks_all if c not in named]
        if ref.startswith("PARAM-"):
            tier = ref.split("-", 1)[1]
            return [(f"PARAM:{n}", k) for n, (t, k) in params.items() if t == tier]
        if ref in rows:
            return [(ref, rows[ref])]
        if ref in m["missing"]:
            return [(ref, "missing")]
        unresolved.append(ref)
        # A GAP row added on port-regime is not in master's PRODUCTION_GAP until the merge (M6).
        return [(ref, "port-regime" if ref.startswith("GAP-") else "unresolved")]

    out: dict[str, dict] = {}

    def roll(qid: str, seen: tuple = ()) -> dict:
        if qid in out:
            return out[qid]
        q = qs[qid]
        leaves, children = [], []
        for ref in q["refs"]:
            if ref in qs:
                if ref in seen:
                    raise RegisterUnread(f"question map cycle through {ref}")
                children.append(ref)
                roll(ref, seen + (qid,))
            else:
                leaves += expand(ref)
        counts: dict[str, int] = {}
        for ref, st in leaves:
            key = f"{kind_of(ref)}:{st}"
            counts[key] = counts.get(key, 0) + 1
        score = {"answered": 1.0, "partial": 0.5, "open": 0.0}
        flags = [float(answered(r, s)) for r, s in leaves] + [score[out[c]["state"]] for c in children]
        state = "open" if not flags or not any(flags) else "answered" if all(f == 1.0 for f in flags) else "partial"
        scored = q["threshold"] is None or q["threshold"].lower().startswith("set")
        out[qid] = {"id": qid, "question": q["question"], "answers": q["answers"], "threshold": q["threshold"],
                    "scored": scored, "state": state, "children": children, "counts": counts,
                    "rows": [{"ref": r, "state": s, "answered": answered(r, s)} for r, s in leaves],
                    "missing": [r for r, s in leaves if s == "missing"]}
        return out[qid]

    for qid in qs:
        roll(qid)
    breaks = [{"id": k, "what": v["what"], "needed_by": v["needed_by"]} for k, v in m["missing"].items()]
    return {"questions": out, "breaks": breaks, "unresolved": sorted(set(unresolved))}


def build(today: date | None = None) -> dict:
    """Read every register and derive. Raises RegisterUnread on any unreadable source."""
    today = today or date.today()
    for p in (MAP, SIGNALS, GAPS, CHECKS, PARAMS):
        if not p.exists():
            raise RegisterUnread(f"{p} missing")
    m = parse_map(MAP.read_text(encoding="utf-8"))
    ids = check_ids(CHECKS.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    rows = {**signal_states(SIGNALS.read_text(encoding="utf-8")), **gap_states(GAPS.read_text(encoding="utf-8")),
            **check_states(state, ids)}
    params = param_states(json.loads(PARAMS.read_text())["parameters"], today)
    out = derive(m, rows, [f"CHECK-{n}" for n in ids], params)
    try:
        ci = card_index()
        for qid, q in out["questions"].items():
            cards = ci["cards"].get(qid, [])
            q["cards"] = cards
            q["card_break"] = bool(cards) and all(c["verdict"] in CARD_UNANSWERED for c in cards)
        out["cards_unread"] = None
        out["cards_invalid"] = ci["invalid"]
        out["cards_unmapped"] = sorted(set(ci["cards"]) - set(out["questions"]))
    except RegisterUnread as e:   # the pyramid still renders from the registers; the missing cards say so
        out["cards_unread"] = str(e)
    out["as_of"] = {"checks_run": state.get("generated_at"), "derived": today.isoformat(),
                    "checks_state_present": bool(state)}
    return out
