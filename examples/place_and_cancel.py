"""Send one market order to NinjaTrader 8 from Python, wait for the fill, then close it.

Before running: ARGUSListener is compiled in NinjaTrader 8 and applied to a chart of the
instrument below, on the account below (use Sim101 to try it safely).
"""
import time
import uuid
from pathlib import Path

from nt8_bridge import ATIBridge

NT8_DOCS = Path.home() / "Documents" / "NinjaTrader 8"   # where ARGUSListener watches by default

bridge = ATIBridge(str(NT8_DOCS))
signal_id = str(uuid.uuid4())

bridge.place_order(signal_id, "BUY", "MES 12-26 CME", quantity=1, account_name="Sim101")
status = bridge.check_status(signal_id, timeout=20)
print("entry:", status)

time.sleep(10)
bridge.cancel_order(signal_id)          # closes the position opened by this signal
print("after cancel:", bridge.check_status(signal_id, timeout=20))
