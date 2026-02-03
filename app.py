import os, time, threading
import pandas as pd
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- ГЕОМЕТРИЯ ---
SYMBOL = 'SOLUSDC'
TIMEFRAME = '1m'
LEVERAGE = 75
MARGIN_USDC = 1.0
EMA_FAST = 25
EMA_SLOW = 99
EMA_PROTECT = 7
MIN_SLOPE = 0.0001
# -----------------

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def run_scanner():
    print("🚀 Снайпер во Франкфурте вышел на охоту...")
    while True:
        try:
            # Получаем данные
            klines = client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=150)
            closes = [float(k[4]) for k in klines]
            series = pd.Series(closes)
            
            # Расчет линий
            ema7 = series.ewm(span=EMA_PROTECT, adjust=False).mean().iloc[-1]
            f_series = series.ewm(span=EMA_FAST, adjust=False).mean()
            f_now, f_prev = f_series.iloc[-1], f_series.iloc[-2]
            
            s_series = series.ewm(span=EMA_SLOW, adjust=False).mean()
            s_now, s_prev = s_series.iloc[-1], s_series.iloc[-2]
            
            # Наклон (индикатор силы)
            slope = abs(f_now - f_series.iloc[-4]) / f_now

            # Статус позиций
            pos = client.futures_position_information(symbol=SYMBOL)
            active = [p for p in pos if float(p['positionAmt']) != 0]

            # 1. ЗАЩИТА: Безубыток по EMA 7
            if active:
                p = active[0]
                amt, side, entry = float(p['positionAmt']), ("LONG" if float(p['positionAmt']) > 0 else "SHORT"), float(p['entryPrice'])
                
                # Если цена ушла в профит (0.5%) и EMA 7 подтверждает силу
                is_safe = (side == "LONG" and ema7 > entry * 1.005) or (side == "SHORT" and ema7 < entry * 0.995)
                
                if is_safe:
                    orders = client.futures_get_open_orders(symbol=SYMBOL)
                    if not any(o.get('stopPrice') == str(round(entry, 2)) for o in orders):
                        client.futures_cancel_all_open_orders(symbol=SYMBOL)
                        client.futures_create_order(symbol=SYMBOL, side='SELL' if side=="LONG" else 'BUY',
                                                  type='STOP_MARKET', stopPrice=str(round(entry, 2)),
                                                  quantity=abs(amt), reduceOnly=True)
                        print(f"🛡 ПРЕДОХРАНИТЕЛЬ: Стоп в БУ на {entry}")

            # 2. СИГНАЛ ПЕРЕВОРОТА (25/99)
            signal = None
            if f_prev <= s_prev and f_now > s_now: signal = "LONG"
            elif f_prev >= s_prev and f_now < s_now: signal = "SHORT"

            if signal:
                if active:
                    current_side = "LONG" if float(active[0]['positionAmt']) > 0 else "SHORT"
                    if signal != current_side:
                        client.futures_cancel_all_open_orders(symbol=SYMBOL)
                        client.futures_create_order(symbol=SYMBOL, side='SELL' if current_side=="LONG" else 'BUY', 
                                                  type='MARKET', quantity=abs(float(active[0]['positionAmt'])), reduceOnly=True)
                        execute_trade(signal, closes[-1])
                else:
                    if slope >= MIN_SLOPE:
                        execute_trade(signal, closes[-1])

        except Exception as e:
            print(f"Ошибка сканера: {e}")
        
        time.sleep(2) # Проверка каждые 2 секунды

def execute_trade(side, price):
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    qty = round((MARGIN_USDC * LEVERAGE) / price, 2)
    client.futures_create_order(symbol=SYMBOL, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
    # Начальный аварийный стоп 3%
    sl = round(price * 0.97 if side == "LONG" else price * 1.03, 2)
    client.futures_create_order(symbol=SYMBOL, side='SELL' if side=="LONG" else 'BUY', 
                               type='STOP_MARKET', stopPrice=str(sl), quantity=qty, reduceOnly=True)
    print(f"🔥 ВХОД {side} по {price}")

# Запуск в фоне
threading.Thread(target=run_scanner, daemon=True).start()

@app.route('/')
def health(): return "Scanner is Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
