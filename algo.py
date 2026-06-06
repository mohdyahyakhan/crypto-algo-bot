import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime

# ========== CONFIG ==========
SYMBOLS = ["TAOUSDT", "BTCUSDT", "HYPEUSDT", "SOLUSDT"]
TIMEFRAME_4H = "4h"
TIMEFRAME_1H = "1h"
CHECK_INTERVAL_SEC = 300 # 5 min
EMA_PERIOD = 50 # Test ke liye 50, real me 300 kar dena
TP_PCT = 0.02 # 2% Take Profit
SL_PCT = 0.01 # 1% Stop Loss

# Paper Trading State
positions = {} # Symbol: {entry_price, tp, sl, entry_time}
total_pnl = 0.0 # Total Paper Trading P&L

# ========== EXCHANGE ==========
exchange = ccxt.bybit({
    "enableRateLimit": True,
    "options": {"defaultType": "linear"},
})

# ========== INDICATORS ==========
def calculate_st_ema(df, atr_period=10, multiplier=3, ema_period=EMA_PERIOD):
    high, low, close = df["high"], df["low"], df["close"]
    tr = np.maximum(high - low, np.maximum(abs(high - close.shift()), abs(low - close.shift())))
    atr = tr.rolling(window=atr_period).mean()
    hl2 = (high + low) / 2
    df["ema"] = close.ewm(span=ema_period).mean()
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)
    st = pd.Series(index=df.index, dtype=float)
    st_dir = pd.Series(index=df.index, dtype=int)
    for i in range(1, len(df)):
        prev_st = st.iloc[i-1]
        if np.isnan(prev_st):
            st.iloc[i] = lower_band.iloc[i]
            st_dir.iloc[i] = 1
        else:
            if close.iloc[i] > prev_st:
                st.iloc[i] = max(lower_band.iloc[i], prev_st)
                st_dir.iloc[i] = 1
            else:
                st.iloc[i] = min(upper_band.iloc[i], prev_st)
                st_dir.iloc[i] = -1
    df["st"] = st
    df["st_dir"] = st_dir
    return df

# ========== DATA ==========
def get_price_data(symbol, timeframe):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=300)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        print(f"Error {symbol}: {e}")
        return None

# ========== PAPER TRADE LOGIC ==========
def check_exit(symbol, current_price):
    global total_pnl
    if symbol not in positions:
        return False

    pos = positions[symbol]
    if current_price >= pos['tp']:
        pnl_pct = ((current_price/pos['entry_price']-1)*100)
        total_pnl += pnl_pct
        print(f"🎯 {symbol} TP HIT! PnL:{pnl_pct:.2f}% | Total P&L:{total_pnl:.2f}%")
        log_trade(symbol, pos, current_price, "TP", pnl_pct)
        del positions[symbol]
        return True
    elif current_price <= pos['sl']:
        pnl_pct = ((current_price/pos['entry_price']-1)*100)
        total_pnl += pnl_pct
        print(f"🛑 {symbol} SL HIT! PnL:{pnl_pct:.2f}% | Total P&L:{total_pnl:.2f}%")
        log_trade(symbol, pos, current_price, "SL", pnl_pct)
        del positions[symbol]
        return True
    return False

def open_position(symbol, entry_price):
    tp = entry_price * (1 + TP_PCT)
    sl = entry_price * (1 - SL_PCT)
    positions[symbol] = {
        'entry_price': entry_price,
        'tp': tp,
        'sl': sl,
        'entry_time': datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    print(f"🔥 SIGNAL: {symbol} | Entry:{entry_price:.2f} | TP:{tp:.2f} | SL:{sl:.2f}")

def log_trade(symbol, pos, exit_price, exit_type, pnl_pct):
    with open("paper_trades.txt", "a") as f:
        f.write(f"{datetime.now()} | {symbol} | {exit_type} | Entry:{pos['entry_price']:.2f} Exit:{exit_price:.2f} PnL:{pnl_pct:.2f}% | Total:{total_pnl:.2f}%\n")

# ========== SIGNAL CHECK ==========
def check_signal(symbol):
    # Exit check pehle
    df_1h = get_price_data(symbol, TIMEFRAME_1H)
    if df_1h is None:
        return
    current_price = df_1h['close'].iloc[-1]
    if check_exit(symbol, current_price):
        return

    # Entry check - agar position nahi hai
    if symbol in positions:
        return

    df_4h = get_price_data(symbol, TIMEFRAME_4H)
    if df_4h is None or len(df_4h) < 2:
        return

    df_4h = calculate_st_ema(df_4h)
    df_1h = calculate_st_ema(df_1h)

    last_close_4h = df_4h['close'].iloc[-1]
    last_ema_4h = df_4h['ema'].iloc[-1]
    last_st_4h = df_4h['st_dir'].iloc[-1]
    prev_st_4h = df_4h['st_dir'].iloc[-2]
    last_st_1h = df_1h['st_dir'].iloc[-1]

    print(f"{symbol}: Price:{last_close_4h:.0f} EMA:{last_ema_4h:.0f} ST:{last_st_4h}", end=" | ")

    if last_close_4h > last_ema_4h and prev_st_4h == -1 and last_st_4h == 1 and last_st_1h == 1:
        open_position(symbol, last_close_4h)
    else:
        print("No signal")

# ========== MAIN LOOP ==========
if __name__ == "__main__":
    print("Bot started. 24/7 Paper Trading Running...")
    print(f"TP: {TP_PCT*100}% | SL: {SL_PCT*100}% | EMA: {EMA_PERIOD}\n")

    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Checking... | Total P&L: {total_pnl:.2f}%")
        for sym in SYMBOLS:
            check_signal(sym)
        time.sleep(CHECK_INTERVAL_SEC)