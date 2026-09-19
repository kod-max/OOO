import os
import threading
import time
from datetime import datetime
from flask import Flask
import pandas as pd
import pytz
import requests
import yfinance as yf

# --- НАСТРОЙКИ TELEGRAM ---
BOT_TOKEN = "8800134718:AAEx4Hs1Qq4F0ZBcS7TmyOV5S3YEihx0Vqo"
CHAT_ID = "-1003904438275"

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "XAUUSD": "GC=F"
}

FIBO_ENTRY_LEVEL = 0.5
RISK_REWARD_RATIO = 2.0
last_signals = {}

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Trading Bot is running 24/7!", 200

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"Ошибка отправки TG: {response.text}")
    except Exception as e:
        print(f"Исключение при отправке TG: {e}")

def get_market_data(ticker_symbol: str):
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="5d", interval="5m")
        if df.empty or len(df) < 50:
            return None
        df.reset_index(inplace=True)
        if 'Datetime' in df.columns:
            df['Datetime'] = pd.to_datetime(df['Datetime'], utc=True)
        return df
    except Exception as e:
        print(f"Ошибка получения котировок {ticker_symbol}: {e}")
        return None

def calculate_levels(df: pd.DataFrame):
    df['date'] = df['Datetime'].dt.date
    dates = df['date'].unique()
    if len(dates) < 2:
        return None
    
    prev_day = dates[-2]
    prev_day_data = df[df['date'] == prev_day]
    pdh = prev_day_data['High'].max()
    pdl = prev_day_data['Low'].min()

    current_day = dates[-1]
    today_data = df[df['date'] == current_day]
    asia_candles = today_data[(today_data['Datetime'].dt.hour >= 0) & (today_data['Datetime'].dt.hour < 6)]
    
    if asia_candles.empty:
        asia_high = today_data['High'].max()
        asia_low = today_data['Low'].min()
    else:
        asia_high = asia_candles['High'].max()
        asia_low = asia_candles['Low'].min()

    return {
        "PDH": pdh,
        "PDL": pdl,
        "Asia_High": asia_high,
        "Asia_Low": asia_low
    }

def analyze_strategy(symbol_name: str, yf_ticker: str):
    df = get_market_data(yf_ticker)
    if df is None:
        return

    levels = calculate_levels(df)
    if levels is None:
        return

    c1 = df.iloc[-4]
    c2 = df.iloc[-3]
    c3 = df.iloc[-2]
    current_candle_time = str(df.iloc[-1]['Datetime'])

    if last_signals.get(symbol_name) == current_candle_time:
        return

    swept_low = min(c1['Low'], c2['Low'], c3['Low']) < min(levels['PDL'], levels['Asia_Low'])
    bullish_fvg = c3['Low'] > c1['High']

    if swept_low and bullish_fvg:
        fvg_low = c1['High']
        fvg_high = c3['Low']
        entry_price = fvg_low + (fvg_high - fvg_low) * FIBO_ENTRY_LEVEL
        sl = min(c1['Low'], c2['Low'])
        risk = entry_price - sl
        if risk > 0:
            tp = entry_price + (risk * RISK_REWARD_RATIO)
            decimals = 5 if "EUR" in symbol_name else 2
            msg = (
                f"🟢 <b>СИГНАЛ: BUY LIMIT ({symbol_name})</b>\n\n"
                f"📍 <b>Вход (0.5 Fibo):</b> {round(entry_price, decimals)}\n"
                f"🛑 <b>Stop Loss:</b> {round(sl, decimals)}\n"
                f"🎯 <b>Take Profit (1:2):</b> {round(tp, decimals)}\n\n"
                f"Ликвидирован уровень: {round(min(levels['PDL'], levels['Asia_Low']), decimals)}"
            )
            send_telegram_message(msg)
            last_signals[symbol_name] = current_candle_time
            return

    swept_high = max(c1['High'], c2['High'], c3['High']) > max(levels['PDH'], levels['Asia_High'])
    bearish_fvg = c3['High'] < c1['Low']

    if swept_high and bearish_fvg:
        fvg_high = c1['Low']
        fvg_low = c3['High']
        entry_price = fvg_high - (fvg_high - fvg_low) * FIBO_ENTRY_LEVEL
        sl = max(c1['High'], c2['High'])
        risk = sl - entry_price
        if risk > 0:
            tp = entry_price - (risk * RISK_REWARD_RATIO)
            decimals = 5 if "EUR" in symbol_name else 2
            msg = (
                f"🔴 <b>СИГНАЛ: SELL LIMIT ({symbol_name})</b>\n\n"
                f"📍 <b>Вход (0.5 Fibo):</b> {round(entry_price, decimals)}\n"
                f"🛑 <b>Stop Loss:</b> {round(sl, decimals)}\n"
                f"🎯 <b>Take Profit (1:2):</b> {round(tp, decimals)}\n\n"
                f"Ликвидирован уровень: {round(max(levels['PDH'], levels['Asia_High']), decimals)}"
            )
            send_telegram_message(msg)
            last_signals[symbol_name] = current_candle_time
            return

def bot_worker():
    print("Торговый поток запущен.")
    send_telegram_message("🚀 <b>Сигнальный бот запущен на Render!</b>\nПары: EURUSD, XAUUSD\nТаймфрейм: M5")
    while True:
        for name, ticker in SYMBOLS.items():
            analyze_strategy(name, ticker)
        time.sleep(60)

if __name__ == "__main__":
    # Запуск бота в отдельном фоновом потоке
    t = threading.Thread(target=bot_worker, daemon=True)
    t.start()
    
    # Запуск веб-сервера на порту Render
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

