"""
NinjaTrader 8 <-> Python bridge (from the ARGUS platform)
=========================================================
File-based bridge: Python writes JSON order files, the ARGUSListener NinjaScript
strategy running inside NinjaTrader 8 executes them and writes status files back.
No sockets, no DLLs, no NinjaTrader add-on API needed.

Order flow:
  Python → writes JSON to argus_orders/ → NinjaScript reads & executes → writes status to argus_status/
"""

import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger("nt8_bridge")


class ATIBridge:
    """
    File-based communication bridge with NinjaTrader 8.

    Python writes JSON order files → ARGUSListener.cs reads & executes
    → writes status files back → Python reads status.
    """

    def __init__(self, base_path: str):
        """
        Args:
            base_path: NT8 documents folder, e.g.:
                       C:/Users/<you>/Documents/NinjaTrader 8
        """
        self.base_path = Path(base_path)
        self.orders_dir = self.base_path / "argus_orders"
        self.status_dir = self.base_path / "argus_status"

        self.orders_dir.mkdir(parents=True, exist_ok=True)
        self.status_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"✅ ATI Bridge ready | Orders: {self.orders_dir}")

    # ------------------------------------------------------------------
    # Order Submission
    # ------------------------------------------------------------------

    def place_order(
        self,
        signal_id: str,
        action: str,
        instrument: str,
        quantity: int,
        account_name: str,
        stop_loss_price: float = 0.0,
        take_profit_price: float = 0.0,
    ) -> str:
        """
        Write an order JSON file for NT8 ARGUSListener to execute.

        Args:
            signal_id:        UUID from trade_queue
            action:           'BUY' or 'SELL'
            instrument:       NT8 instrument name, e.g. 'ES 03-26 CME'
            quantity:         Number of contracts
            account_name:     NT8 account identifier, e.g. 'FN-123456'
            stop_loss_price:  Absolute price for SL (0 = no SL)
            take_profit_price: Absolute price for TP (0 = no TP)

        Returns:
            Path to the written order file.
        """
        order = {
            "signal_id": signal_id,
            "action": action.upper(),
            "instrument": instrument,
            "quantity": quantity,
            "account": account_name,
            "stop_loss": stop_loss_price,
            "take_profit": take_profit_price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        order_file = self.orders_dir / f"{signal_id}.json"
        with open(order_file, "w") as f:
            json.dump(order, f, indent=2)

        logger.info(
            f"📤 ATI Order: {action} {quantity}x {instrument} "
            f"SL={stop_loss_price} TP={take_profit_price} [{signal_id[:8]}]"
        )
        return str(order_file)

    def cancel_order(self, signal_id: str):
        """Write a cancel command for a pending order."""
        cancel = {
            "signal_id": signal_id,
            "action": "CANCEL",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        cancel_file = self.orders_dir / f"{signal_id}_cancel.json"
        with open(cancel_file, "w") as f:
            json.dump(cancel, f, indent=2)
        logger.info(f"🚫 ATI Cancel: [{signal_id[:8]}]")

    # ------------------------------------------------------------------
    # Status Reading
    # ------------------------------------------------------------------

    def check_status(self, signal_id: str, timeout: int = 15) -> Optional[Dict]:
        """
        Poll for order status written by ARGUSListener.cs.

        Returns status dict or None on timeout.
        Status dict keys: signal_id, status, fill_price, quantity, timestamp, error
        """
        status_file = self.status_dir / f"{signal_id}.json"
        deadline = time.time() + timeout

        while time.time() < deadline:
            if status_file.exists():
                try:
                    with open(status_file, "r") as f:
                        status = json.load(f)
                    logger.info(
                        f"📥 ATI Status: {status.get('status')} "
                        f"@ {status.get('fill_price', '?')} [{signal_id[:8]}]"
                    )
                    return status
                except Exception:
                    pass
            time.sleep(0.5)

        logger.warning(f"⚠️ ATI Timeout: no status for [{signal_id[:8]}]")
        return None

    def get_all_statuses(self) -> Dict[str, Dict]:
        """Return all status files as {signal_id: status_dict}."""
        results = {}
        for f in self.status_dir.glob("*.json"):
            try:
                with open(f, "r") as fp:
                    data = json.load(fp)
                sid = f.stem
                results[sid] = data
            except Exception:
                pass
        return results

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup_old_files(self, max_age_hours: int = 48):
        """Remove processed order/status files older than max_age_hours."""
        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0
        for folder in [self.orders_dir, self.status_dir]:
            for f in folder.glob("*.json"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
        if removed:
            logger.info(f"🧹 ATI Cleanup: removed {removed} old files")

    def is_healthy(self) -> bool:
        """Check ATI directories are accessible."""
        return self.orders_dir.exists() and self.status_dir.exists()
