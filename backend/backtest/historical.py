"""
Values an archived analysis was run with, kept so the run reproduces after production moved on. No production reader.
"""

# The demo wallet's fixed daily loss cap: config.DEMO_L0_PORTFOLIO_LOSS_CAP_MAX until 2026-11-21, when the demo book
# moved to L0's share of equity (R067 a, R111 a). Read by the archived *_analysis.py / *_diagnostic.py runs and
# engine_sharpe.
HISTORICAL_DEMO_LOSS_CAP_USD = 100.0
