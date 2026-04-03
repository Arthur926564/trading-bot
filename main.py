"""
TRADING strategy
"""
import asyncio
import logging
import os
import time
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

import alpaca_trade_api as tradeapi

from data.market_data import MarketDataFeed
from strategy.signal import SignalGenerator, Direction
from execution.order_manager import OrderManager
from monitoring.portfolio import Portfolio, Position

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")


# ── ATR helper (mirrors backtest) ─────────────────────────────────────────────
def compute_atr(prices: np.ndarray, period: int = 14) -> float:
    """Average True Range as % of price — close-only approximation."""
    if len(prices) < period + 1:
        return 0.01  # fallback: assume 1% vol
    tr = np.abs(np.diff(prices[-(period + 1):]))
    return float(np.mean(tr) / prices[-1])


# ── Per-symbol trailing stop state ────────────────────────────────────────────
class TrailState:
    """Tracks peak price and bars held for a single open position."""
    def __init__(self, entry_price: float):
        self.entry_price = entry_price
        self.peak_price  = entry_price
        self.bars_held   = 0

    def update(self, price: float):
        self.bars_held += 1
        self.peak_price = max(self.peak_price, price)

    def trail_pct(self, atr_pct: float) -> float:
        """Dynamic trail: wide early, tightens as profit builds."""
        entry_gain = (self.peak_price - self.entry_price) / self.entry_price
        if entry_gain < 0.02:
            multiplier = 4.0    # wide — don't shake out early
        elif entry_gain < 0.05:
            multiplier = 3.0    # normal
        else:
            multiplier = 2.0    # tight — protect large gains
        return max(min(atr_pct * multiplier, 0.12), 0.03)

    def should_trail_exit(self, price: float, atr_pct: float, min_bars: int) -> bool:
        if self.bars_held < min_bars:
            return False
        return price < self.peak_price * (1 - self.trail_pct(atr_pct))

    def should_profit_exit(self, price: float, atr_pct: float) -> bool:
        entry_gain = (price - self.entry_price) / self.entry_price
        return entry_gain > atr_pct * 6   # 6× ATR = take profit


# ── Main bot ──────────────────────────────────────────────────────────────────
async def run():
    symbols = ["AAPL", "MSFT", "SPY", "GOOGL", "NVDA", "QQQ"]

    MIN_HOLD_BARS       = 5    # bars before trail stop activates
    MAX_POSITION_PCT    = 0.20 # max 20% of equity per position
    BASE_VOL            = 0.01 # 1% baseline for vol-adjusted sizing
    FRACTION            = 0.50 # base fraction of equity to deploy

    # ── Alpaca connection ──────────────────────────────────────────────────
    api = tradeapi.REST(
        key_id=os.environ["ALPACA_KEY"],
        secret_key=os.environ["ALPACA_SECRET"],
        base_url="https://paper-api.alpaca.markets",
    )
    account    = api.get_account()
    real_cash  = float(account.cash)
    log.info("Alpaca paper balance: $%.2f", real_cash)

    # ── Components ────────────────────────────────────────────────────────
    feed = MarketDataFeed(symbols=symbols)

    strategy = SignalGenerator(
        trend_window         = 50,
        long_trend_window    = 200,
        dip_lookback         = 5,
        min_dip_pct          = 0.02,
        max_dip_pct          = 0.08,
        rsi_oversold         = 45.0,
        min_volatility       = 0.005,
    )

    portfolio = Portfolio(initial_cash=real_cash)
    orders    = OrderManager(portfolio=portfolio, max_position_pct=MAX_POSITION_PCT)

    # ── Restore existing Alpaca positions ─────────────────────────────────
    alpaca_positions = api.list_positions()
    trail_states: dict[str, TrailState] = {}

    for p in alpaca_positions:
        sym        = p.symbol
        qty        = int(p.qty)
        avg_price  = float(p.avg_entry_price)
        cur_price  = float(p.current_price)

        portfolio._positions[sym] = Position(
            symbol           = sym,
            qty              = qty,
            avg_entry_price  = avg_price,
            current_price    = cur_price,
        )
        # Reconstruct trail state from existing position
        trail_states[sym] = TrailState(entry_price=avg_price)
        trail_states[sym].peak_price = cur_price  # conservative: assume no peak data

        log.info("Restored position: %s qty=%d entry=%.2f current=%.2f",
                 sym, qty, avg_price, cur_price)

    portfolio.load_from_alpaca(alpaca_positions)

    # ── Load history (needs 200+ days for long MA filter) ─────────────────
    log.info("Loading 1500 days of history...")
    feed.load_history(days=1500)
    log.info("History loaded — starting live stream")

    last_bar_time      = time.time()
    last_logged_minute = None

    # ── Watchdog ──────────────────────────────────────────────────────────
    async def watchdog():
        while True:
            await asyncio.sleep(120)
            elapsed = time.time() - last_bar_time
            if elapsed > 120:
                log.warning("No bar in %.0fs — raising watchdog timeout", elapsed)
                raise Exception("Watchdog timeout")

    asyncio.create_task(watchdog())

    # ── Main bar loop ─────────────────────────────────────────────────────
    async for bar in feed.stream_bars(interval="1min"):
        last_bar_time = time.time()

        symbol = bar.symbol
        portfolio.update_prices(bar.prices)

        history = feed.get_price_series(symbol)
        if len(history) < 201:
            # Not enough history yet for 200-day MA — skip
            continue

        prices       = np.array(history, dtype=float)
        price        = prices[-1]
        atr_pct      = compute_atr(prices)
        has_position = portfolio.has_position(symbol)

        # ── Trailing stop check (before signal generation) ────────────────
        if has_position and symbol in trail_states:
            ts = trail_states[symbol]
            ts.update(price)

            profit_exit = ts.should_profit_exit(price, atr_pct)
            trail_exit  = ts.should_trail_exit(price, atr_pct, MIN_HOLD_BARS)

            if profit_exit or trail_exit:
                reason = "PROFIT-TARGET" if profit_exit else "TRAIL-STOP"
                log.info("[%s] %s @ %.2f | peak=%.2f bars=%d",
                         reason, symbol, price, ts.peak_price, ts.bars_held)

                # Build a synthetic FLAT signal so OrderManager handles the sell
                from strategy.signal import Signal
                flat_signal = Signal(
                    symbol    = symbol,
                    direction = Direction.FLAT,
                    strength  = 1.0,
                    reason    = reason,
                )
                await orders.handle_signal(flat_signal)
                del trail_states[symbol]
                continue   # skip signal generation this bar

        # ── Signal generation ─────────────────────────────────────────────
        signals = strategy.generate(symbol, history, has_position)

        for signal in signals:
            log.info("[SIGNAL] %s %s strength=%.2f | %s",
                     symbol, signal.direction.value, signal.strength, signal.reason)

            if signal.direction == Direction.LONG and not has_position:
                # Vol-adjusted position sizing (mirrors BacktestResult.enter)
                vol          = getattr(signal, "volatility", atr_pct)
                vol_scalar   = min(BASE_VOL / max(vol, 1e-6), 1.0)
                adj_fraction = FRACTION * vol_scalar

                equity       = float(api.get_account().equity)
                target_value = equity * adj_fraction
                qty          = int(target_value / price)

                if qty > 0:
                    log.info("[ENTRY] %s qty=%d @ ~%.2f | vol_scalar=%.2f fraction=%.2f",
                             symbol, qty, price, vol_scalar, adj_fraction)
                    await orders.handle_signal(signal)
                    trail_states[symbol] = TrailState(entry_price=price)
                else:
                    log.warning("[SKIP] %s qty=0 after sizing (equity=%.2f price=%.2f)",
                                symbol, equity, price)

            elif signal.direction == Direction.FLAT and has_position:
                log.info("[EXIT] %s @ %.2f | %s", symbol, price, signal.reason)
                await orders.handle_signal(signal)
                trail_states.pop(symbol, None)

        # ── Periodic status log (every 5 minutes, no spam) ────────────────
        now = datetime.now()
        if now.minute % 5 == 0 and now.minute != last_logged_minute:
            portfolio.log_status()
            last_logged_minute = now.minute


if __name__ == "__main__":
    asyncio.run(run())
