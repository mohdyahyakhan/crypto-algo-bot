import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime
import pytz
import json
import os
from flask import Flask
from threading import Thread
import sys

# ===== RENDER KE LIYE DUMMY SERVER =====
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running"

def run_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_server, daemon=True).start()
time.sleep(2) # Flask ko start hone ka time de
# =======================================

P_L_FILE = "pnl.json"

# P&L load karo agar file hai
if os.path.exists(P_L_FILE):
    with open(P_L_FILE, "r") as f:
        total_pnl = json.load(f).get("total_pnl", 0.0)
else:
    total_pnl = 0.0

# ========== CONFIG ==========
COINS_CONFIG = {
    "TAOUSDT": {"main_tf": "4h", "confirm_tf": "1h"},
    "BTCUSDT": {"main_tf": "4h", "confirm_tf": "1h"},
    "HYPEUSDT": {"main_tf": "1h", "confirm_tf": "4h"}, # 1H main
    "SOLUSDT": {"main_tf": "1h", "confirm_tf": "4h"} # 1H main
}
SYMBOLS = list(COINS_CONFIG.keys())
CHECK_INTERVAL_SEC = 600 # 10 min - rate limit se bachne ke liye
EMA_PERIOD = 50 # Test ke liye 50, real me 300 kar dena
TP_PCT = 0.02 # 2% Take Profit
SL_PCT = 0.01 # 1% Stop Loss

# Paper Trading State
positions = {} # Symbol: {entry_price, tp, sl, entry_time}

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
        print(f"Error {symbol} {timeframe}: {e}", flush=True)
        return None

# ========== PAPER TRADE LOGIC ==========
def save_pnl():
    with open(P_L_FILE, "w") as f:
        json.dump({"total_pnl": total_pnl}, f)

def check_exit(symbol, current_price):
    global total_pnl
    if symbol not in positions:
        return False

    pos = positions[symbol]
    if current_price >= pos['tp']:
        pnl_pct = ((current_price/pos['entry_price']-1)*100)
        total_pnl += pnl_pct
        save_pnl()
        print(f"🎯 {symbol} TP HIT! PnL:{pnl_pct:.2f}% | Total P&L:{total_pnl:.2f}%", flush=True)
        log_trade(symbol, pos, current_price, "TP", pnl_pct)
        del positions[symbol]
        return True
    elif current_price <= pos['sl']:
        pnl_pct = ((current_price/pos['entry_price']-1)*100)
        total_pnl += pnl_pct
        save_pnl()
        print(f"🛑 {symbol} SL HIT! PnL:{pnl_pct:.2f}% | Total P&L:{total_pnl:.2f}%", flush=True)
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
    print(f"🔥 SIGNAL: {symbol} | Entry:{entry_price:.2f} | TP:{tp:.2f} | SL:{sl:.2f}", flush=True)

def log_trade(symbol, pos, exit_price, exit_type, pnl_pct):
    with open("paper_trades.txt", "a") as f:
        f.write(f"{datetime.now()} | {symbol} | {exit_type} | Entry:{pos['entry_price']:.2f} Exit:{exit_price:.2f} PnL:{pnl_pct:.2f}% | Total:{total_pnl:.2f}%\n")

# ========== SIGNAL CHECK ==========
def check_signal(symbol):
    config = COINS_CONFIG[symbol]
    main_tf = config["main_tf"]
    confirm_tf = config["confirm_tf"]

    # Exit check - hamesha main_tf ke current price se
    df_main = get_price_data(symbol, main_tf)
    if df_main is None:
        return
    current_price = df_main['close'].iloc[-1]
    if check_exit(symbol, current_price):
        return

    # Entry check - agar position nahi hai
    if symbol in positions:
        return

    df_confirm = get_price_data(symbol, confirm_tf)
    if df_confirm is None or len(df_main) < 2:
        return

    df_main = calculate_st_ema(df_main)
    df_confirm = calculate_st_ema(df_confirm)

    last_close_main = df_main['close'].iloc[-1]
    last_ema_main = df_main['ema'].iloc[-1]
    last_st_main = df_main['st_dir'].iloc[-1]
    prev_st_main = df_main['st_dir'].iloc[-2]
    last_st_confirm = df_confirm['st_dir'].iloc[-1]

    print(f"{symbol}[{main_tf}]: Price:{last_close_main:.0f} EMA:{last_ema_main:.0f} ST:{last_st_main}", end=" | ", flush=True)

    if last_close_main > last_ema_main and prev_st_main == -1 and last_st_main == 1 and last_st_confirm == 1:
        open_position(symbol, last_close_main)
    else:
        print("No signal", flush=True)

# ========== MAIN LOOP ==========
if __name__ == "__main__":
    print("Bot started. 24/7 Paper Trading Running...", flush=True)
    print(f"TP: {TP_PCT*100}% | SL: {SL_PCT*100}% | EMA: {EMA_PERIOD}", flush=True)
    print(f"Timeframes: TAO/BTC=4H, HYPE/SOL=1H\n", flush=True)

    while True:
        ist_time = datetime.now(pytz.timezone("Asia/Kolkata")).strftime('%H:%M:%S')
        print(f"\n[{ist_time}] Checking... | Total P&L: {total_pnl:.2f}%", flush=True)
        for sym in SYMBOLS:
            check_signal(sym)
            time.sleep(1) # Rate limit se bachne ke liye 1 sec gap
        sys.stdout.flush()
        time.sleep(CHECK_INTERVAL_SEC)