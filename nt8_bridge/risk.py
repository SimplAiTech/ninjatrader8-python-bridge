"""
ARGUS NinjaTrader Risk Manager
================================
Handles FundedNext Futures account rules and position sizing.

FundedNext Futures rules:
  - No hard daily loss limit (unlike FTMO)
  - Trailing max drawdown (e.g. $4,500 on $50k account)
  - Consistency rule: best single day ≤ 40% of total profit
  - Max contracts: 5 Minis or 50 Micros (configurable per account)
  - Profit target to pass challenge (e.g. 8% of initial balance)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger("nt8_bridge.risk")


# ---------------------------------------------------------------------------
# Tick specs for common futures contracts
# ---------------------------------------------------------------------------
TICK_SPECS: Dict[str, Tuple[float, float]] = {
    # Instrument prefix → (tick_size, tick_value_usd)
    "MES": (0.25, 1.25),    # Micro E-mini S&P 500
    "ES":  (0.25, 12.50),   # E-mini S&P 500
    "MNQ": (0.25, 0.50),    # Micro E-mini NASDAQ-100
    "NQ":  (0.25, 5.00),    # E-mini NASDAQ-100
    "MYM": (1.00, 0.50),    # Micro E-mini Dow Jones
    "YM":  (1.00, 5.00),    # E-mini Dow Jones
    "M2K": (0.10, 0.50),    # Micro E-mini Russell 2000
    "RTY": (0.10, 5.00),    # E-mini Russell 2000
    "MGC": (0.10, 1.00),    # Micro Gold
    "GC":  (0.10, 10.00),   # Gold
    "MCL": (0.01, 1.00),    # Micro Crude Oil
    "CL":  (0.01, 10.00),   # Crude Oil
    "BTC": (5.00, 25.00),   # Bitcoin CME
    "MBT": (5.00, 2.50),    # Micro Bitcoin CME
    "6E":  (0.00005, 6.25), # Euro FX
    "6J":  (0.0000005, 6.25), # Japanese Yen
}

# Default instrument map: ARGUS symbol → NT8 instrument name
# User can override these per account. Update the month/year as needed.
DEFAULT_INSTRUMENT_MAP: Dict[str, str] = {
    "US500":    "ES 03-26 CME",
    "USTEC100": "NQ 03-26 CME",
    "US30":     "YM 03-26 CBOT",
    "XAUUSD":   "GC 04-26 COMEX",
    "XAUUSD.PRO": "GC 04-26 COMEX",
    "BTCUSD":   "BTC 03-26 CME",
    "EURUSD":   "6E 03-26 CME",
}


def get_tick_spec(instrument: str) -> Tuple[float, float]:
    """Return (tick_size, tick_value_usd) for a given NT8 instrument name."""
    prefix = instrument.split(" ")[0].upper()
    # Try exact match first, then prefix match
    if prefix in TICK_SPECS:
        return TICK_SPECS[prefix]
    for key in TICK_SPECS:
        if prefix.startswith(key):
            return TICK_SPECS[key]
    logger.warning(f"Unknown tick spec for {instrument}, using ES default")
    return (0.25, 12.50)


# ---------------------------------------------------------------------------
# Account data class
# ---------------------------------------------------------------------------
@dataclass
class NTAccount:
    """NinjaTrader account with prop firm rule configuration."""

    # Identity
    id: str                               # Supabase UUID
    account_name: str                     # Display name
    nt_account_name: str                  # NT8 account ID (e.g. "FN-123456" or "Sim101")

    # Prop firm
    prop_firm: str = "FundedNext Futures"
    phase: str = "FUNDED"                 # CHALLENGE / FUNDED

    # Balances (updated from Supabase or manually)
    initial_balance: float = 50_000.0
    current_balance: float = 50_000.0
    current_equity: float = 50_000.0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0               # Cumulative profit since start

    # Risk limits
    max_drawdown_usd: float = 2_500.0    # Trailing drawdown limit
    profit_target_usd: float = 4_000.0   # Target to pass challenge
    risk_per_trade_pct: float = 1.0      # % of balance per trade
    max_contracts_mini: int = 5
    max_contracts_micro: int = 50
    consistency_rule_pct: float = 40.0   # Best day ≤ X% of total profit

    # Trading config
    is_micro: bool = False                # True = trade micros (MES/MNQ), False = minis
    account_status: str = "ACTIVE"        # ACTIVE / PAUSED / PASSED / BREACHED

    # Instrument map override (symbol → NT8 name)
    instrument_map: Dict[str, str] = field(default_factory=dict)

    def get_instrument(self, symbol: str) -> Optional[str]:
        """Resolve ARGUS symbol to NT8 instrument, with fallback to defaults."""
        return self.instrument_map.get(symbol) or DEFAULT_INSTRUMENT_MAP.get(symbol)

    @property
    def drawdown_used(self) -> float:
        return max(0.0, self.initial_balance - self.current_equity)

    @property
    def drawdown_remaining(self) -> float:
        return max(0.0, self.max_drawdown_usd - self.drawdown_used)

    @property
    def profit_progress_pct(self) -> float:
        if self.profit_target_usd <= 0:
            return 0.0
        return min(100.0, (self.total_pnl / self.profit_target_usd) * 100)


# ---------------------------------------------------------------------------
# Risk Manager
# ---------------------------------------------------------------------------
class NTRiskManager:
    """
    Validates trades against NinjaTrader/FundedNext prop firm rules
    and calculates position size in contracts.
    """

    def __init__(self, account: NTAccount):
        self.account = account
        self._best_day_pnl: float = 0.0   # Track for consistency rule

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def check_trade_allowed(
        self, action: str, instrument: str, quantity: int
    ) -> Tuple[bool, str]:
        """
        Validate a trade before execution.
        Returns (allowed: bool, reason: str).
        """
        acc = self.account

        # 1. Account status
        if acc.account_status != "ACTIVE":
            return False, f"Account {acc.account_status}"

        # 2. Trailing drawdown
        if acc.drawdown_used >= acc.max_drawdown_usd:
            return False, (
                f"HARD_STOP_DRAWDOWN "
                f"(${acc.drawdown_used:.0f} >= ${acc.max_drawdown_usd:.0f})"
            )

        # 3. Contract size limits
        max_c = acc.max_contracts_micro if acc.is_micro else acc.max_contracts_mini
        if quantity > max_c:
            return False, (
                f"Exceeds max contracts "
                f"({quantity} > {max_c} {'micros' if acc.is_micro else 'minis'})"
            )

        # 4. Consistency rule (only if there's profit to compare)
        if acc.total_pnl > 0 and self._best_day_pnl > 0:
            pct = (self._best_day_pnl / acc.total_pnl) * 100
            if pct > acc.consistency_rule_pct:
                return False, (
                    f"Consistency rule: best day {pct:.1f}% "
                    f"> limit {acc.consistency_rule_pct}%"
                )

        return True, "OK"

    # ------------------------------------------------------------------
    # Position Sizing
    # ------------------------------------------------------------------

    def calculate_contracts(
        self,
        instrument: str,
        stop_ticks: int,
    ) -> int:
        """
        Calculate number of contracts based on account risk settings.

        Args:
            instrument:  NT8 instrument name (to get tick value)
            stop_ticks:  SL distance in ticks

        Returns: Number of contracts (minimum 1)
        """
        acc = self.account
        if stop_ticks <= 0:
            return 1

        tick_size, tick_value = get_tick_spec(instrument)
        risk_usd = acc.current_balance * (acc.risk_per_trade_pct / 100.0)
        raw = risk_usd / (stop_ticks * tick_value)
        contracts = max(1, int(raw))

        max_c = acc.max_contracts_micro if acc.is_micro else acc.max_contracts_mini
        contracts = min(contracts, max_c)

        logger.info(
            f"🧮 NT Size: Bal=${acc.current_balance:.0f} "
            f"Risk={acc.risk_per_trade_pct}% (${risk_usd:.0f}) "
            f"SL={stop_ticks}ticks × ${tick_value} → {contracts} contracts"
        )
        return contracts

    def sl_points_to_ticks(self, instrument: str, sl_points: float) -> int:
        """Convert SL distance in price points to ticks."""
        tick_size, _ = get_tick_spec(instrument)
        if tick_size <= 0:
            return 4
        return max(1, round(sl_points / tick_size))

    def sl_to_price(self, entry_price: float, action: str, instrument: str, stop_ticks: int) -> float:
        """Calculate absolute SL price from ticks."""
        tick_size, _ = get_tick_spec(instrument)
        dist = stop_ticks * tick_size
        return entry_price - dist if action == "BUY" else entry_price + dist

    def tp_to_price(self, entry_price: float, action: str, instrument: str, stop_ticks: int, rr: float = 1.5) -> float:
        """Calculate absolute TP price from ticks and R:R ratio."""
        tick_size, _ = get_tick_spec(instrument)
        dist = stop_ticks * tick_size * rr
        return entry_price + dist if action == "BUY" else entry_price - dist

    # ------------------------------------------------------------------
    # Daily tracking (call from execution loop)
    # ------------------------------------------------------------------

    def update_day_pnl(self, day_pnl: float):
        """Update best-day PnL tracker (call with today's closed PnL)."""
        if day_pnl > self._best_day_pnl:
            self._best_day_pnl = day_pnl
