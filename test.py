import numpy as np
import alpaca_trade_api as tradeapi
import os
from dotenv import load_dotenv
load_dotenv()

api = tradeapi.REST(
    key_id=os.environ["ALPACA_KEY"],
    secret_key=os.environ["ALPACA_SECRET"],
    base_url="https://paper-api.alpaca.markets",
)

for symbol in ["AAPL", "MSFT", "SPY"]:
    bars = api.get_bars(symbol, tradeapi.TimeFrame.Day,
                        "2026-02-01", "2026-03-24", feed="iex").df
    closes = bars["close"].tolist()
    fast = np.mean(closes[-5:])
    slow = np.mean(closes[-20:])
    gap = (fast - slow) / slow
    trigger = slow * 1.005
    print(f"{symbol}: current=${closes[-1]:.2f}  slow_ma=${slow:.2f}  "
          f"gap={gap:.2%}  needs=${trigger:.2f}")
