"""
regime_bayes.py — Sector regime leaderboard and portfolio allocation engine.

Preparatory module that runs before the lock chain. Determines how much of
the portfolio is allocated to each sector before any trading decisions are made.

Two-stage calculation:
  1. Regime posterior — Bayesian update from four independent signals
  2. Adjusted score   — sector aggregate score × regime posterior
  3. Leaderboard      — top 5 sectors ranked by adjusted score
  4. Allocation       — proportional split among qualifiers (adjusted > 0.5)

The allocation output is consumed by:
  - Lock chain: determines how many tickers can be traded per sector
  - Lock 4 (Claude): full regime context for final approval
  - Ticker allocation: sector % split proportionally by individual signal scores

Ticker-level thresholds (Lock 1) are sector-specific and handled downstream.
The regime module only knows about sectors, not individual tickers.

Usage:
    from backend.regime.regime_bayes import RegimeBayes, regime_context_for_claude

    rb = RegimeBayes(sectors_cfg, sector_etf_map, transition_priors)
    result = rb.update(today, raw_data, sector_snapshots, ipo_shares)
    allocation = result.allocation      # {sector: pct} summing to 1.0
    leaderboard = result.leaderboard    # top 5 ranked sectors
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd
from loguru import logger


# ── Constants ─────────────────────────────────────────────────────────────────

LEADERBOARD_SIZE      = 5      # number of sectors tracked
ALLOCATION_THRESHOLD  = 0.5    # adjusted score floor for allocation eligibility
TICKER_RECOVERY_DAYS  = 5      # consecutive days for ticker recovery signal
RS_WINDOW_DAYS        = 5      # lookback window for RS divergence


# ── Likelihood ratio functions ────────────────────────────────────────────────

def _lr_ticker_count(recovering: int, total: int) -> float:
    """
    Exponential doubling per recovering ticker.
    First ticker: +0.2, second: +0.4, third: +0.8 etc.
    Zero recovering = neutral (1.0). No cap.
    """
    if total == 0 or recovering == 0:
        return 1.0
    lr = sum(0.2 * (2 ** i) for i in range(recovering))
    return round(1.0 + lr, 3)


def _lr_etf_consecutive_days(consecutive_positive_days: int) -> float:
    """
    +0.1 per consecutive positive ETF day.
    Reaches 2.0 at day 10, 3.0 at day 20 (true MA20 confirmation).
    Zero days = neutral (1.0). No cap.
    """
    return round(1.0 + (consecutive_positive_days * 0.1), 3)


def _lr_rs_divergence(start_score: float, end_score: float, decline_days: int) -> float:
    """
    RS divergence — percentage drop in leader × exponential consecutive decline days.
    Mirrors ticker count formula — small short drops stay quiet,
    large sustained drops compound into strong conviction.
    Zero decline days or non-declining leader = neutral (1.0). No cap.
    """
    if start_score <= 0 or decline_days == 0:
        return 1.0
    magnitude_drop = (start_score - end_score) / start_score
    if magnitude_drop <= 0:
        return 1.0
    lr = 1.0 + (magnitude_drop * (2 ** (decline_days - 1)) * 0.2)
    return round(lr, 3)


def _lr_ipo_cluster(sector_ipo_share: float) -> float:
    """
    LR for IPO sector share — sector's proportion of total market IPOs
    over the last 30 days.

    sector_ipo_share: sector_ipos / total_market_ipos (0.0 to 1.0)
    Computed and normalized upstream by ipo_sentiment.py.

    Zero share when market is active = mild negative (capital absent).
    Zero share when no market IPOs = neutral (handled upstream, pass 1/n_sectors).
    Exponential: +0.2 per 10% share increment — mirrors other signal scales.
    50% share in one sector is rare and warrants the strong compounding signal.
    """
    if sector_ipo_share == 0:
        return 0.8   # sector absent from active IPO market — mild negative
    steps = round(sector_ipo_share * 10)   # 10% share = 1 step, 30% = 3 steps
    lr = sum(0.2 * (2 ** i) for i in range(steps))
    return round(1.0 + lr, 3)


# ── Bayesian update ───────────────────────────────────────────────────────────

def _bayesian_update(prior: float, likelihood_ratio: float) -> float:
    """
    Update a probability using a likelihood ratio.
    posterior = LR × prior / (LR × prior + (1 − prior))
    """
    if likelihood_ratio <= 0:
        return prior
    numerator   = likelihood_ratio * prior
    denominator = numerator + (1.0 - prior)
    return numerator / denominator if denominator > 0 else prior


def _apply_signals(
    prior: float,
    lr_ticker: float,
    lr_etf: float,
    lr_rs: float,
    lr_ipo: float,
) -> dict:
    """
    Apply all four likelihood ratios sequentially.
    Returns intermediate and final posteriors for full transparency.
    """
    p0 = prior
    p1 = _bayesian_update(p0, lr_ticker)
    p2 = _bayesian_update(p1, lr_etf)
    p3 = _bayesian_update(p2, lr_rs)
    p4 = _bayesian_update(p3, lr_ipo)

    return {
        "prior":         round(p0, 4),
        "after_tickers": round(p1, 4),
        "after_etf":     round(p2, 4),
        "after_rs":      round(p3, 4),
        "after_ipo":     round(p4, 4),
        "posterior":     round(p4, 4),
        "lr_ticker":     round(lr_ticker, 3),
        "lr_etf":        round(lr_etf, 3),
        "lr_rs":         round(lr_rs, 3),
        "lr_ipo":        round(lr_ipo, 3),
    }


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SectorEntry:
    """Full state for one sector in the leaderboard."""
    sector:          str
    aggregate_score: float        # from sector_snapshots (momentum + volume + RSI avg)
    posterior:       float        # Bayesian regime posterior
    adjusted_score:  float        # aggregate_score × posterior
    allocation:      float        # final portfolio % (0.0 if below threshold)
    rank:            int          # 1 = top sector
    signal_trace:    dict = field(default_factory=dict)


@dataclass
class RegimeResult:
    """Output of one daily regime update."""
    date:        str
    leaderboard: list[SectorEntry]    # top 5, ranked by adjusted score
    allocation:  dict[str, float]     # {sector: pct} summing to 1.0
    leader:      str                  # rank 1 sector name
    qualifiers:  list[str]            # sectors above allocation threshold


# ── Main class ────────────────────────────────────────────────────────────────

class RegimeBayes:
    """
    Sector regime leaderboard and portfolio allocation engine.

    Call update() once per trading day.
    Output RegimeResult contains allocation percentages for all qualifying sectors.
    Posteriors are persisted to DB so conviction accumulates across restarts.
    """

    def __init__(
        self,
        sectors_cfg: dict,           # SECTORS config {sector: {tickers, etf, ...}}
        sector_etf_map: dict,        # {sector: etf_ticker}
        transition_priors: dict,     # {outgoing: {candidate: historical_probability}}
    ):
        self.sectors_cfg       = sectors_cfg
        self.sector_etf_map    = sector_etf_map
        self.transition_priors = transition_priors

        # Load persisted posteriors from DB — conviction never resets between restarts
        self._posteriors: dict[str, float] = self._load_posteriors()
        self._last_result: Optional[RegimeResult] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def update(
        self,
        today: date,
        raw_data: pd.DataFrame,
        sector_snapshots: dict[str, float],   # {sector: aggregate_score} from DB
        ipo_shares: dict[str, float],         # {sector: share_of_total_ipos} from ipo_sentiment
    ) -> RegimeResult:
        """
        Run one daily update cycle:
          1. Update Bayesian posteriors for all sectors
          2. Compute adjusted scores (aggregate × posterior)
          3. Rank all sectors → top 5 leaderboard
          4. Compute proportional allocation among qualifiers (adjusted > 0.5)
          5. Persist posteriors to DB
          6. Return RegimeResult with allocation vector and full signal trace

        ipo_shares: {sector: share_of_total_market_ipos} over last 30 days,
                    computed by ipo_sentiment.py. Pass uniform {sector: 1/n}
                    if no IPO data available (neutral signal).
        """
        today_str    = today.isoformat()
        all_sectors  = list(self.sectors_cfg.keys())

        # Current leader — rank 1 from last result, or highest snapshot on first run
        current_leader = self._current_leader(sector_snapshots)

        # Leader decline stats — used as RS divergence input for all candidates
        leader_rs = self._sector_decline_stats(current_leader, raw_data, today)

        entries: list[SectorEntry] = []

        for sector in all_sectors:
            aggregate_score = sector_snapshots.get(sector, 0.0)

            # Prior: historical transition probability from current leader to this sector
            prior = self._get_prior(sector, current_leader, len(all_sectors))

            # Signal 1: ticker recovery count
            recovering, total = self._ticker_recovery_count(sector, raw_data, today)
            lr_ticker = _lr_ticker_count(recovering, total)

            # Signal 2: ETF consecutive positive days
            etf_days = self._etf_consecutive_positive_days(sector, raw_data, today)
            lr_etf   = _lr_etf_consecutive_days(etf_days)

            # Signal 3: RS divergence
            # Leader's decline feeds all candidates equally — a weakening leader
            # strengthens the case for every candidate on the leaderboard
            lr_rs = _lr_rs_divergence(
                leader_rs["start_score"],
                leader_rs["end_score"],
                leader_rs["decline_days"],
            )

            # Signal 4: IPO sector share
            ipo_share = ipo_shares.get(sector, 0.0)
            lr_ipo    = _lr_ipo_cluster(ipo_share)

            # Bayesian update using persistent prior from yesterday (or DB)
            persistent_prior = self._posteriors.get(sector, prior)
            trace    = _apply_signals(persistent_prior, lr_ticker, lr_etf, lr_rs, lr_ipo)
            posterior = trace["posterior"]

            # Stage for DB persist below
            self._posteriors[sector] = posterior

            adjusted = round(aggregate_score * posterior, 4)

            entries.append(SectorEntry(
                sector=sector,
                aggregate_score=round(aggregate_score, 4),
                posterior=posterior,
                adjusted_score=adjusted,
                allocation=0.0,
                rank=0,
                signal_trace={
                    "date":                 today_str,
                    "recovering_tickers":   f"{recovering}/{total}",
                    "etf_consecutive_days": etf_days,
                    "leader_start_score":   leader_rs["start_score"],
                    "leader_end_score":     leader_rs["end_score"],
                    "leader_decline_days":  leader_rs["decline_days"],
                    "ipo_share":            round(ipo_share, 4),
                    **trace,
                },
            ))

        # Rank by adjusted score descending → top 5 leaderboard
        entries.sort(key=lambda e: e.adjusted_score, reverse=True)
        leaderboard = entries[:LEADERBOARD_SIZE]
        for i, entry in enumerate(leaderboard):
            entry.rank = i + 1

        # Proportional allocation among qualifiers
        qualifiers = [e for e in leaderboard if e.adjusted_score >= ALLOCATION_THRESHOLD]
        total_adj  = sum(e.adjusted_score for e in qualifiers)

        for entry in leaderboard:
            if entry in qualifiers and total_adj > 0:
                entry.allocation = round(entry.adjusted_score / total_adj, 4)
            else:
                entry.allocation = 0.0

        # Verify allocations sum to 1.0
        total_alloc = sum(e.allocation for e in leaderboard)
        if qualifiers and abs(total_alloc - 1.0) > 0.001:
            logger.warning(f"Regime [{today_str}]: allocation sum={total_alloc:.4f} — rounding drift")

        allocation      = {e.sector: e.allocation for e in leaderboard}
        leader          = leaderboard[0].sector if leaderboard else ""
        qualifier_names = [e.sector for e in qualifiers]

        result = RegimeResult(
            date=today_str,
            leaderboard=leaderboard,
            allocation=allocation,
            leader=leader,
            qualifiers=qualifier_names,
        )
        self._last_result = result

        # Persist posteriors — conviction survives restarts
        self._save_posteriors()

        logger.info(
            f"Regime [{today_str}] leader={leader} | "
            f"{len(qualifiers)} qualifiers | "
            + " | ".join(
                f"{e.sector}={e.allocation:.0%}"
                for e in leaderboard if e.allocation > 0
            )
        )

        return result

    def last_result(self) -> Optional[RegimeResult]:
        """Return most recent RegimeResult without recomputing."""
        return self._last_result

    # ── Ticker allocation within sector ───────────────────────────────────────

    @staticmethod
    def ticker_allocations(
        sector_allocation: float,
        ticker_scores: dict[str, float],
    ) -> dict[str, float]:
        """
        Compute proportional position sizes for tickers within a sector.

        sector_allocation: the sector's portfolio percentage from RegimeResult
        ticker_scores: {ticker: signal_score} — only tickers already passing
                       their sector-specific Lock 1 threshold should be passed in.
                       This function does no filtering.

        Leading ticker gets largest proportion, scaled by relative signal score.
        Returns: {ticker: portfolio_pct}
        """
        if not ticker_scores or sector_allocation <= 0:
            return {}
        total_score = sum(ticker_scores.values())
        if total_score <= 0:
            return {}
        return {
            ticker: round(sector_allocation * (score / total_score), 4)
            for ticker, score in ticker_scores.items()
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_posteriors(self) -> dict[str, float]:
        """Load persisted posteriors from DB. Falls back to uniform on first run."""
        uniform = 1.0 / max(len(self.sectors_cfg), 1)
        try:
            from backend.db import get_sector_posteriors
            stored = get_sector_posteriors()
            return {
                sector: stored.get(sector, uniform)
                for sector in self.sectors_cfg
            }
        except Exception as e:
            logger.warning(f"Regime: could not load posteriors from DB ({e}) — using uniform")
            return {sector: uniform for sector in self.sectors_cfg}

    def _save_posteriors(self) -> None:
        """Persist current posteriors to DB."""
        try:
            from backend.db import upsert_sector_posteriors
            upsert_sector_posteriors(self._posteriors)
        except Exception as e:
            logger.warning(f"Regime: failed to persist posteriors: {e}")

    def _current_leader(self, sector_snapshots: dict[str, float]) -> str:
        """Current leader from last result, or highest aggregate score on first run."""
        if self._last_result:
            return self._last_result.leader
        return max(sector_snapshots, key=sector_snapshots.get) if sector_snapshots else ""

    def _get_prior(self, sector: str, current_leader: str, total_sectors: int) -> float:
        """Historical transition probability, falling back to uniform distribution."""
        uniform = 1.0 / max(total_sectors, 1)
        prior   = self.transition_priors.get(current_leader, {}).get(sector, uniform)
        return max(0.05, min(0.95, prior))

    def _ticker_recovery_count(
        self,
        sector: str,
        raw_data: pd.DataFrame,
        today: date,
    ) -> tuple[int, int]:
        """Count tickers with TICKER_RECOVERY_DAYS consecutive positive days."""
        tickers = self.sectors_cfg.get(sector, {}).get("tickers", [])
        if not tickers:
            return 0, 0
        recovering = 0
        for ticker in tickers:
            try:
                if ticker not in raw_data.columns.get_level_values(0):
                    continue
                closes = raw_data[ticker]["Close"].dropna()
                mask   = closes.index.date <= today
                recent = closes[mask].tail(TICKER_RECOVERY_DAYS + 1)
                if len(recent) < TICKER_RECOVERY_DAYS + 1:
                    continue
                if (recent.pct_change().dropna() > 0).all():
                    recovering += 1
            except Exception:
                continue
        return recovering, len(tickers)

    def _etf_consecutive_positive_days(
        self,
        sector: str,
        raw_data: pd.DataFrame,
        today: date,
    ) -> int:
        """Count consecutive days the sector ETF has had a positive daily return."""
        etf = self.sector_etf_map.get(sector, "")
        if not etf:
            return 0
        try:
            if etf not in raw_data.columns.get_level_values(0):
                return 0
            closes = raw_data[etf]["Close"].dropna()
            mask   = closes.index.date <= today
            recent = closes[mask].tail(60)
            if len(recent) < 2:
                return 0
            count = 0
            for ret in reversed(recent.pct_change().dropna().values):
                if ret > 0:
                    count += 1
                else:
                    break
            return count
        except Exception:
            return 0

    def _sector_decline_stats(
        self,
        sector: str,
        raw_data: pd.DataFrame,
        today: date,
    ) -> dict:
        """
        Compute start score, end score and consecutive decline days
        for a sector's ETF over RS_WINDOW_DAYS.
        Used to measure leader weakening as RS divergence input.
        """
        etf = self.sector_etf_map.get(sector, "")
        if not etf:
            return {"start_score": 0.0, "end_score": 0.0, "decline_days": 0}
        try:
            if etf not in raw_data.columns.get_level_values(0):
                return {"start_score": 0.0, "end_score": 0.0, "decline_days": 0}
            closes = raw_data[etf]["Close"].dropna()
            mask   = closes.index.date <= today
            recent = closes[mask].tail(30)
            if len(recent) < RS_WINDOW_DAYS + 1:
                return {"start_score": 0.0, "end_score": 0.0, "decline_days": 0}
            start_score   = float(recent.iloc[-(RS_WINDOW_DAYS + 1)])
            end_score     = float(recent.iloc[-1])
            daily_returns = recent.pct_change().dropna()
            decline_days  = 0
            for ret in reversed(daily_returns.values):
                if ret < 0:
                    decline_days += 1
                else:
                    break
            return {
                "start_score":  round(start_score, 4),
                "end_score":    round(end_score, 4),
                "decline_days": decline_days,
            }
        except Exception:
            return {"start_score": 0.0, "end_score": 0.0, "decline_days": 0}


# ── Transition prior builder ──────────────────────────────────────────────────

def build_transition_priors(sector_history: list[dict]) -> dict[str, dict[str, float]]:
    """
    Build historical transition probability table from sector leadership history.

    sector_history: list of {"date": str, "leader": str} dicts, chronological.
    Returns: {outgoing_sector: {successor_sector: probability}}

    Example:
        priors = build_transition_priors(db.get_sector_leadership_history())
        rb = RegimeBayes(sectors_cfg, etf_map, priors)
    """
    from collections import defaultdict
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for i in range(1, len(sector_history)):
        prev = sector_history[i - 1]["leader"]
        curr = sector_history[i]["leader"]
        if prev != curr:
            counts[prev][curr] += 1
    priors: dict[str, dict[str, float]] = {}
    for outgoing, successors in counts.items():
        total = sum(successors.values())
        priors[outgoing] = {s: round(c / total, 4) for s, c in successors.items()}
    return priors


# ── Claude Lock 4 context payload ─────────────────────────────────────────────

def regime_context_for_claude(result: RegimeResult) -> str:
    """
    Structured regime summary for injection into Claude Lock 4 context.
    Shows allocation percentages, posterior confidence, and signal drivers
    so Claude understands the full portfolio distribution when approving trades.
    """
    lines = [
        f"Regime date:    {result.date}",
        f"Current leader: {result.leader}",
        f"Qualifiers:     {len(result.qualifiers)} sectors with active allocation",
        "",
        "Sector leaderboard (top 5):",
    ]
    for entry in result.leaderboard:
        alloc_str = f"{entry.allocation:.0%}" if entry.allocation > 0 else "no allocation"
        s = entry.signal_trace.get("leader_start_score", 0)
        e = entry.signal_trace.get("leader_end_score", 0)
        drop_str = f" leader_drop={((s-e)/s):.1%}" if s and s > 0 else ""
        lines.append(
            f"  #{entry.rank} {entry.sector:<20} "
            f"alloc={alloc_str:<8} "
            f"adj={entry.adjusted_score:.3f} "
            f"(agg={entry.aggregate_score:.3f} × post={entry.posterior:.2f}) "
            f"| tickers={entry.signal_trace.get('recovering_tickers','?')} "
            f"ETF_days={entry.signal_trace.get('etf_consecutive_days', 0)}"
            f"{drop_str} "
            f"IPO_share={entry.signal_trace.get('ipo_share', 0):.0%}"
        )
    lines += ["", "Portfolio allocation:"]
    for entry in result.leaderboard:
        if entry.allocation > 0:
            lines.append(f"  {entry.sector:<20} {entry.allocation:.1%}")
    return "\n".join(lines)
