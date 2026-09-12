import os
import threading
import time
from datetime import datetime
import pytz
from flask import Flask, jsonify
from mt5linux import MetaTrader5
import pandas as pd
import pandas_ta as ta

# ==================== ВЕБ-СЕРВЕР ДЛЯ RENDER ====================
app = Flask(__name__)
bot_status = {"status": "starting", "last_scan": None, "active_trades": []}

@app.route('/')
@app.route('/healthz')
def health():
    return jsonify(bot_status), 200

# ==================== НАСТРОЙКИ СТРАТЕГИИ ====================
SYMBOLS_CONFIG = {
    "EURUSD": {
        "lot": 0.01,
        "magic": 1001,
        "atr_len": 14,
        "atr_mult": 0.5,
        "sl_buffer_pts": 15,
        "rr": 2.5
    },
    "XAUUSD": {
        "lot": 0.01,
        "magic": 1002,
        "atr_len": 14,
        "atr_mult": 0.6,
        "sl_buffer_pts": 50,
        "rr": 2.5
    }
}

KILLZONES_UTC = [(7, 10), (12, 15)]

# Инициализация моста к MT5
mt5 = MetaTrader5(host='localhost', port=18812)

def is_killzone():
    now_utc = datetime.now(pytz.utc)
    for start, end in KILLZONES_UTC:
        if start <= now_utc.hour < end:
            return True
    return False

def get_ohlcv(symbol, timeframe, n_bars=100):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n_bars)
    if rates is None or len(rates) < 30:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def find_swings(df, window=3):
    df['swing_high'] = False
    df['swing_low'] = False
    for i in range(window, len(df) - window - 1):
        if all(df['high'].iloc[i] > df['high'].iloc[i - j] for j in range(1, window + 1)) and \
           all(df['high'].iloc[i] > df['high'].iloc[i + j] for j in range(1, window + 1)):
            df.at[i, 'swing_high'] = True

        if all(df['low'].iloc[i] < df['low'].iloc[i - j] for j in range(1, window + 1)) and \
           all(df['low'].iloc[i] < df['low'].iloc[i + j] for j in range(1, window + 1)):
            df.at[i, 'swing_low'] = True
    return df

def get_market_structure(df):
    df = find_swings(df, window=2)
    highs = df[df['swing_high']]
    lows = df[df['swing_low']]
    if highs.empty or lows.empty:
        return 'RANGE'
    last_high = highs.iloc[-1]['high']
    last_low = lows.iloc[-1]['low']
    last_close = df['close'].iloc[-2]
    if last_close > last_high:
        return 'BULLISH'
    elif last_close < last_low:
        return 'BEARISH'
    return 'RANGE'

def detect_fvgs_and_ifvgs(df, atr_mult=0.5, atr_len=14):
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=atr_len)
    fvgs = []
    for i in range(2, len(df) - 1):
        atr_val = df['atr'].iloc[i]
        min_gap = atr_val * atr_mult if pd.notna(atr_val) else 0

        if (df['low'].iloc[i] - df['high'].iloc[i - 2]) >= min_gap:
            fvgs.append({'type': 'bullish', 'top': df['low'].iloc[i], 'bottom': df['high'].iloc[i - 2], 'idx': i})
        elif (df['low'].iloc[i - 2] - df['high'].iloc[i]) >= min_gap:
            fvgs.append({'type': 'bearish', 'top': df['low'].iloc[i - 2], 'bottom': df['high'].iloc[i], 'idx': i})

    for f in fvgs:
        for j in range(f['idx'] + 1, len(df) - 1):
            close = df['close'].iloc[j]
            if f['type'] == 'bullish' and close < f['bottom']:
                f['type'] = 'ifvg_bearish'
            elif f['type'] == 'bearish' and close > f['top']:
                f['type'] = 'ifvg_bullish'
    return fvgs

def execute_order(symbol, side, entry_price, raw_sl, cfg):
    info = mt5.symbol_info(symbol)
    if not info:
        return False

    point = info.point
    min_dist = (info.trade_stops_level + 5) * point
    buf = cfg["sl_buffer_pts"] * point
    rr = cfg["rr"]

    if side == 'buy':
        order_type = mt5.ORDER_TYPE_BUY
        sl = min(raw_sl - buf, entry_price - min_dist)
        tp = entry_price + (abs(entry_price - sl) * rr)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        sl = max(raw_sl + buf, entry_price + min_dist)
        tp = entry_price - (abs(sl - entry_price) * rr)

    filling = mt5.ORDER_FILLING_IOC if (info.filling_mode & mt5.SYMBOL_FILLING_IOC) else mt5.ORDER_FILLING_RETURN

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": cfg["lot"],
        "type": order_type,
        "price": round(entry_price, info.digits),
        "sl": round(sl, info.digits),
        "tp": round(tp, info.digits),
        "deviation": 25,
        "magic": cfg["magic"],
        "comment": f"SMC_{symbol}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }
    res = mt5.order_send(request)
    return res.retcode == mt5.TRADE_RETCODE_DONE

def trading_loop():
    print("Инициализация MT5 и вход в аккаунт...")
    
    # Подключение к торговому счёту через переменные окружения
    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")

    if login and password and server:
        mt5.login(login=int(login), password=password, server=server)

    for sym in SYMBOLS_CONFIG.keys():
        mt5.symbol_select(sym, True)

    bot_status["status"] = "running"

    while True:
        try:
            bot_status["last_scan"] = datetime.now(pytz.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            
            if is_killzone():
                for sym, cfg in SYMBOLS_CONFIG.items():
                    positions = mt5.positions_get(symbol=sym)
                    if positions and any(p.magic == cfg["magic"] for p in positions):
                        continue

                    htf_df = get_ohlcv(sym, mt5.TIMEFRAME_H1, 80)
                    ltf_df = get_ohlcv(sym, mt5.TIMEFRAME_M5, 100)
                    if htf_df is None or ltf_df is None:
                        continue

                    htf_bias = get_market_structure(htf_df)
                    ltf_bias = get_market_structure(ltf_df)
                    zones = detect_fvgs_and_ifvgs(ltf_df, cfg["atr_mult"], cfg["atr_len"])

                    tick = mt5.symbol_info_tick(sym)
                    if not tick:
                        continue

                    if htf_bias == 'BULLISH' and ltf_bias == 'BULLISH':
                        for z in reversed(zones[-8:]):
                            if z['type'] in ['bullish', 'ifvg_bullish'] and z['bottom'] <= tick.ask <= z['top']:
                                execute_order(sym, 'buy', tick.ask, z['bottom'], cfg)
                                break

                    elif htf_bias == 'BEARISH' and ltf_bias == 'BEARISH':
                        for z in reversed(zones[-8:]):
                            if z['type'] in ['bearish', 'ifvg_bearish'] and z['bottom'] <= tick.bid <= z['top']:
                                execute_order(sym, 'sell', tick.bid, z['top'], cfg)
                                break

        except Exception as e:
            print(f"Ошибка в цикле: {e}")

        time.sleep(15)

# Запуск торговой логики в отдельном потоке
worker_thread = threading.Thread(target=trading_loop, daemon=True)
worker_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
