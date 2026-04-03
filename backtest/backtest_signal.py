import numpy as np
import pandas as pd
from datetime import datetime

from strategy.signal import SignalGenerator, Direction
from data.market_data import MarketDataFeed

from dotenv import load_dotenv
load_dotenv()


class BacktestResult:
    def __init__(self, fraction: float = 0.50):
        self.trades = []
        self.equity_curve = []
        self.cash = 10_000.0
        self.position = 0.0
        self.entry_price = 0.0
        self.bars_held = 0
        self.fraction = fraction
        self.base_vol = 0.01

    def enter(self, price: float, volatility: float, current_equity: float):
        if self.position == 0:
            # Scale position down when volatility is high
            vol_scalar = min(self.base_vol / volatility, 1.0)  # base_vol = 0.01 (1%)
            adjusted_fraction = self.fraction * vol_scalar
            
            allocation = current_equity * adjusted_fraction
            allocation = min(allocation, self.cash)
            
            self.position = allocation / price
            self.entry_price = price
            self.cash -= allocation
            self.bars_held = 0



    def exit(self, price: float):
        if self.position > 0:
            proceeds = self.position * price          # what the shares are worth now
            pnl = proceeds - (self.position * self.entry_price)
            self.cash += proceeds                     # return proceeds to cash
            self.trades.append(pnl)
            self.position = 0
            self.entry_price = 0.0

    def value(self, price: float) -> float:
        return self.cash + self.position * price      # total equity = cash + open has_position


def compute_atr(prices: np.ndarray, period: int = 14) -> float:
    """Average True Range as % of price — works on close-only data."""
    tr = np.abs(np.diff(prices[-period-1:]))
    return np.mean(tr) / prices[-1]

def backtest(df: pd.DataFrame, symbol: str):
    strategy = SignalGenerator(
        trend_window=50,
        long_trend_window=200,
        dip_lookback=5,
        min_dip_pct=0.02,
        max_dip_pct=0.08,
        rsi_oversold=45.0,
        min_volatility=0.005,
    )
    TRAIL_PCT = 0.05
    MIN_HOLD_BARS = 3

    result = BacktestResult()
    history = []
    peak_price = 0
    TRAIL_PCT = 0.07   # widened from 0.04 — gives room for normal daily swings
    MIN_HOLD_BARS = 5  # prevents exiting on same-bar noise

    for i in range(len(df)):
        price = float(df["close"].iloc[i])
        date  = df["timestamp"].iloc[i].date()
        history.append(price)

        has_position = result.position > 0

        # ── Trailing stop (evaluated before signals, bypasses generator) ──
        if has_position:
            result.bars_held += 1
            peak_price = max(peak_price, price)
            
            entry_gain = (price - result.entry_price) / result.entry_price
            atr_pct    = compute_atr(np.array(history))
            trail_pct  = max(min(atr_pct * 3.0, 0.12), 0.04)
            
            # Take profit at 2× daily ATR gain (lock in the move)
            take_profit_hit = entry_gain > atr_pct * 6   # 6× ATR = meaningful move captured
            trail_hit       = price < peak_price * (1 - trail_pct) and result.bars_held >= MIN_HOLD_BARS

            if take_profit_hit or trail_hit:
                result.exit(price)
                reason = "PROFIT" if take_profit_hit else "TRAIL "
                pnl = result.trades[-1]

                print(f"  TRAIL @ {price:.2f} | {date} | PnL: {pnl:+.2f}")
                peak_price    = 0
                has_position  = False
                result.equity_curve.append(result.value(price))
                continue   # ← skip signal generation this bar — fixes same-bar re-entry

        # ── Signal generation ──
        signals = strategy.generate(symbol, history, has_position)

        for signal in signals:
            if signal.direction == Direction.LONG and not has_position:
                equity = result.value(price)
                result.enter(price, volatility=signal.volatility,  current_equity=equity)
                peak_price   = price
                has_position = True   # prevent double-entry within same bar's signal list
                print(f"  ENTER @ {price:.2f} | {date}")

            elif signal.direction == Direction.FLAT and has_position:
                result.exit(price)
                pnl          = result.trades[-1]
                has_position = False
                peak_price   = 0
                print(f"  EXIT  @ {price:.2f} | {date} | PnL: {pnl:+.2f}")

        result.equity_curve.append(result.value(price))

    # ── Force-close any open position at end of data ──
    if result.position > 0:
        price = float(df["close"].iloc[-1])
        result.exit(price)
        pnl = result.trades[-1]
        print(f"  CLOSE @ {price:.2f} | end of data | PnL: {pnl:+.2f}")

    return result



def compute_metrics(result: BacktestResult):
    equity = np.array(result.equity_curve)

    returns = np.diff(equity) / equity[:-1]
    total_return = (equity[-1] / equity[0]) - 1

    win_trades = [t for t in result.trades if t > 0]
    loss_trades = [t for t in result.trades if t <= 0]

    win_rate = len(win_trades) / len(result.trades) if result.trades else 0

    max_drawdown = 0
    peak = equity[0]
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        max_drawdown = max(max_drawdown, dd)

    return {
        "total_return": total_return,
        "num_trades": len(result.trades),
        "win_rate": win_rate,
        "max_drawdown": max_drawdown,
    }


if __name__ == "__main__":
    # Load your data (example CSV)
    feed = MarketDataFeed(symbols=["AAPL"])
    data = feed.load_history(days=1500)

    df = data["AAPL"].reset_index()

    
    result = backtest(df, "AAPL")
    print(backtest)
    metrics = compute_metrics(result)
    print(metrics)


    print("\n=== BACKTEST RESULTS ===")
    print(f"Total Return: {metrics['total_return'] * 100:.2f}%")
    print(f"Trades: {metrics['num_trades']}")
    print(f"Win Rate: {metrics['win_rate'] * 100:.2f}%")
    print(f"Max Drawdown: {metrics['max_drawdown'] * 100:.2f}%")
