# NinjaTrader 8 ↔ Python bridge

Send orders to NinjaTrader 8 from Python and get the fills back. No sockets, no DLLs, no add-on API: Python writes a small JSON file, a NinjaScript strategy inside NinjaTrader picks it up, places the order with NinjaTrader's own order management and writes a status file back.

I wrote this for my multi-account trading platform [ARGUS V9](https://github.com/SimplAiTech/argus-v9-mt5-trading-platform), where the same signals go to MetaTrader 5 accounts and to NinjaTrader 8 futures prop accounts. This repository is the bridge on its own, MIT-licensed.

```
Python ──► Documents\NinjaTrader 8\argus_orders\<id>.json ──► ARGUSListener (NinjaScript)
       ◄── Documents\NinjaTrader 8\argus_status\<id>.json ◄── fill / status
```

## What's inside

| Path | What it does |
|---|---|
| `NinjaScript/ARGUSListener.cs` | NinjaScript strategy. Watches the orders folder, enters market orders, attaches stop-loss and take-profit after the fill, writes `SUBMITTED` / `FILLED` / `CANCEL_SENT` status files. |
| `nt8_bridge/bridge.py` | `ATIBridge`: `place_order`, `cancel_order`, `check_status` (polls for the status file), `get_all_statuses`, `cleanup_old_files`. Standard library only. |
| `nt8_bridge/risk.py` | `NTRiskManager` for futures prop accounts: tick specs for MES/ES/MNQ/NQ/MYM/YM/MGC/GC/CL/6E/6J and more, contracts from risk in dollars, trailing drawdown, daily P&L, consistency rule (best day ≤ 40 % of profit). |
| `examples/place_and_cancel.py` | Buy 1 MES on Sim101, wait for the fill, close it. |

## Setup

1. **NinjaTrader 8:** Tools → Edit NinjaScript → Strategy → New, name it `ARGUSListener`, paste `NinjaScript/ARGUSListener.cs`, compile (F5).
2. **Apply it to a chart of the instrument you want to trade, on the account you want to trade.** Orders go to that chart's instrument and that strategy's account; the `instrument` and `account` fields in the JSON are kept for your logs only. For several instruments or accounts, run one instance per chart. Start on `Sim101`.
3. **Python 3.9+**, no dependencies:

```python
from nt8_bridge import ATIBridge
bridge = ATIBridge(r"C:/Users/<you>/Documents/NinjaTrader 8")
bridge.place_order("my-signal-0001", "BUY", "MES 12-26 CME", quantity=1,
                   account_name="Sim101", stop_loss_price=6010.25, take_profit_price=6040.25)
print(bridge.check_status("my-signal-0001", timeout=20))
```

Stop-loss and take-profit are absolute prices (0 = none). They are attached as an OCO-style bracket right after the entry fills.

## Notes and limits

- One position per signal id, market entries only. Limit/stop entries are not implemented.
- `cancel_order(id)` closes the position opened by that signal; it does not cancel a working limit order.
- File polling adds roughly 0.1–0.5 s of latency. Fine for intraday systems, not for scalping on ticks.
- Version 1.1 (this repo) fixes two issues in the cancel path of the original ARGUS listener: a cancel file was ignored because it shared the order's id, and a missing quantity could throw. The fixed listener has not yet been compiled in NinjaTrader on my side, so please open an issue if the compiler complains.
- Test on a simulation account first. Trading futures involves substantial risk of loss.

## More

The full ARGUS platform (MT5 multi-account execution, prop-firm rules, ML signal filter, backtesting) is described in the [ARGUS V9 showcase](https://github.com/SimplAiTech/argus-v9-mt5-trading-platform).

MIT licence, © 2026 SimplAiTech.
