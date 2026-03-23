import os
from dotenv import load_dotenv
load_dotenv()
import alpaca_trade_api as tradeapi

api = tradeapi.REST(
    key_id=os.environ["ALPACA_KEY"],
    secret_key=os.environ["ALPACA_SECRET"],
    base_url="https://paper-api.alpaca.markets",
)

account = api.get_account()
print(f"\n--- Account ---")
print(f"Cash:            ${float(account.cash):,.2f}")
print(f"Portfolio value: ${float(account.portfolio_value):,.2f}")
print(f"Buying power:    ${float(account.buying_power):,.2f}")
print(f"P&L today:       ${float(account.equity) - float(account.last_equity):,.2f}")

print(f"\n--- Positions ---")
positions = api.list_positions()
if not positions:
    print("No open positions")
for p in positions:
    pnl = float(p.unrealized_pl)
    print(f"{p.symbol:6} qty={p.qty:>6}  side={p.side:5}  "
          f"entry=${float(p.avg_entry_price):,.2f}  "
          f"now=${float(p.current_price):,.2f}  "
          f"uPnL=${pnl:+,.2f}")
