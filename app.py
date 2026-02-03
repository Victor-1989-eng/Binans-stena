import os, time, threading
import pandas as pd
from binance.client import Client
from binance.enums import *

# --- НАСТРОЙКИ ---
SYMBOL = 'SOLUSDC'
TIMEFRAME = '1m'
LEVERAGE = 75
MARGIN_USDC = 1.0
EMA_FAST = 25
EMA_SLOW = 99
MIN_SLOPE = 0.0001 # Твой настроенный фильтр
# -----------------

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def run_scanner():
    print("🚀 Скальпер запущен в режиме Real-time")
    while True:
        try:
            # Получаем свечи (минутки)
            klines = client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=150)
            closes = [float(k[4]) for k in klines]
            
            series = pd.Series(closes)
            ema_f = series.ewm(span=EMA_FAST, adjust=False).mean()
            ema_s = series.ewm(span=EMA_SLOW, adjust=False).mean()

            f_now, s_now = ema_f.iloc[-1], ema_s.iloc[-1]
            f_prev, s_prev = ema_f.iloc[-2], ema_s.iloc[-2]
            slope = abs(f_now - ema_f.iloc[-4]) / ema_f.iloc[-4]

            # Проверяем позицию
            pos = client.futures_position_information(symbol=SYMBOL)
            active = [p for p in pos if float(p['positionAmt']) != 0]
            
            # ЛОГИКА СНАЙПЕРА (Только момент пересечения)
            signal = None
            if f_prev <= s_prev and f_now > s_now: signal = "LONG"
            elif f_prev >= s_prev and f_now < s_now: signal = "SHORT"

            if signal:
                if active:
                    # Реверс
                    amt = float(active[0]['positionAmt'])
                    side = "LONG" if amt > 0 else "SHORT"
                    if signal != side:
                        client.futures_create_order(symbol=SYMBOL, side='SELL' if amt > 0 else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        execute_trade(signal, closes[-1])
                else:
                    # Первый вход с фильтром
                    if slope >= MIN_SLOPE:
                        execute_trade(signal, closes[-1])

        except Exception as e:
            print(f"Ошибка: {e}")
        
        time.sleep(2) # Пауза 2 секунды между проверками

def execute_trade(side, price):
    qty = round((MARGIN_USDC * LEVERAGE) / price, 2)
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    client.futures_create_order(symbol=SYMBOL, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
    print(f"🔥 ВХОД {side} по {price}")

# Запуск сканера в отдельном потоке, чтобы Render не ругался на таймаут
threading.Thread(target=run_scanner, daemon=True).start()

# Flask нужен только чтобы Render считал приложение живым
from flask import Flask
app = Flask(__name__)
@app.route('/')
def health(): return "Scalper is Running"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
