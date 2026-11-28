"""One config builder per book for the tests: the tracked file, then the test's own values (2026-11-28).

Vault: raw/notes/2026-11/2026-11-28-apex-test-config-builder-from-tracked-files-scope.md (Niclas: "en enda byggare som
utgår från den versionshanterade filen"). Since R150 b every money-path reader subscripts, so a config handed to the
code must be complete. Until this builder, tests made their own partial dicts and, for one evening, tests/cfg_complete.py
filled the gaps with the values the removed .get() fallbacks gave (VIX 25.0, the DEMO_ constants), which kept M12's
second values alive in the suite. Now a test starts from what production reads, data/live_config.json or
data/demo_config.json through the book's own _KEYS, and writes out only the values it asserts on.

Measured before the port (2026-11-28, apex f6f77b8): 0 of 442 tests in the 24 files that built configs depend on a value
they do not state; the fallbacks, the live file and every filled value moved all gave 442 passed, and a planted test
that depended on a filled value failed under both. tests/test_book_cfg.py guards that no test hands the code a dict
literal as a config again.
"""
import json
from pathlib import Path

from backend import demo_config, live_config

ROOT = Path(__file__).resolve().parents[1]


def _book(name: str, keys: list[str]) -> dict:
    stored = json.loads((ROOT / "data" / f"{name}_config.json").read_text())
    return {k: stored[k] for k in keys}


def live_cfg(cfg: dict | None = None, **overrides) -> dict:
    """data/live_config.json's runtime keys, then cfg, then overrides."""
    return {**_book("live", live_config._KEYS), **(cfg or {}), **overrides}


def demo_cfg(cfg: dict | None = None, **overrides) -> dict:
    """data/demo_config.json's runtime keys, then cfg, then overrides."""
    return {**_book("demo", demo_config._KEYS), **(cfg or {}), **overrides}
