# Quant Trading strategy

A clean Python scaffold for a real algo trading system.
Paper trade first. Always.

This is a project I build using claude (I wouldn't say vibe coded but close), which run on a rasberry pi 24/7. The goal of this project was more to test reliability and how error handling works when having an "automated" strategy running continuously.

## Project structure

```
tfa_trading/
├── main.py                  ← entry point, wires everything together
├── data/
│   └── market_data.py       ← historical + live streaming via Alpaca
├── strategy/
│   └── signal.py            ← YOUR STRATEGY GOES HERE
├── execution/
│   └── order_manager.py     ← signal → order, with risk checks
├── monitoring/
│   └── portfolio.py         ← P&L, positions, drawdown tracking
├── backtest/
│   └── runner.py            ← run strategy on historical data
└── requirements.txt
```

## Setup

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Environment variables

```bash
export ALPACA_KEY="your_key_here"
export ALPACA_SECRET="your_secret_here"
```

Get a free paper trading account at https://alpaca.markets

## Running

```bash
# Backtest first (no real money)
python -m backtest.runner

# Paper trade (real market data, fake money)
python main.py
```

## Plugging in your strategy

Open `strategy/signal.py` and replace `_compute_signal()`:

```python
def _compute_signal(self, symbol, price, bar) -> Signal | None:
    prices = feed.get_price_series(symbol)

    # Your model returns a value between -1 and +1
    raw_signal = your_model.predict(prices)

    if raw_signal > 0.5:
        return Signal(symbol, Direction.LONG,  strength=raw_signal)
    if raw_signal < -0.5:
        return Signal(symbol, Direction.SHORT, strength=abs(raw_signal))
    return None  # flat / no trade
```

`strength` (0.0–1.0) controls position size via Kelly-inspired sizing in `OrderManager`.

## Key design decisions

- **Async throughout** — `asyncio` lets signal computation run while waiting for API I/O
- **Audit log** — every order is recorded in `OrderManager._order_log`
- **Risk checks before every order** — max position %, max total exposure
- **Paper mode default** — `ALPACA_BASE` points to paper API; change to live deliberately
- **Backtest uses same signal logic** — no separate backtest strategy, avoids overfitting
