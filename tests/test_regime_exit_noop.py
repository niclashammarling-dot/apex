"""PRODUCTION_GAP row 32 (2026-10-18): live's REGIME exit closes a position whose sector
is off the Bayesian leaderboard AND whose ticker closed down 5 days in a row. The
leaderboard keeps the top LEADERBOARD_SIZE (12) active sectors and there are 11 active
sectors, so a held sector is off it only when it was moved to EXCLUDED_SECTORS while
held (live's one REGIME exit: SO, Utilities, excluded 05-07, closed 05-08). The engines
never enter an excluded sector, so the rule cannot fire there and is not ported. This
test is the trigger to port it: it fails the day the active universe outgrows the board."""
from backend.config import EXCLUDED_SECTORS
from backend.regime.regime_bayes import LEADERBOARD_SIZE
from backend.ticker_config import get_sectors


def test_leaderboard_covers_every_active_sector():
    active = [s for s in get_sectors() if s not in EXCLUDED_SECTORS]
    assert len(active) <= LEADERBOARD_SIZE, (
        f"{len(active)} active sectors > LEADERBOARD_SIZE {LEADERBOARD_SIZE}: live's REGIME exit can now "
        f"fire on a ranking drop; port it to engine_fast (PRODUCTION_GAP row 32)")
