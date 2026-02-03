import os
import requests
import pandas as pd
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNBUSDC'
LEVERAGE = 75
EMA_FAST = 25
EMA_SLOW = 99
SL_PCT = 0.003    # Стоп 0.3% (Риск 1$)
TP_PCT = 0.009    # Тейк 0.9% (Профит 3$)
BE_TRIGGER = 0.0045 # БУ при +0.45%

# ФИЛЬТР НАКЛОНА (Slope)
# Минимальное изменение EMA за 3 свечи в процентах
# 0.0005 = 0.05% наклона. Если меньше - считаем движение слабым.
MIN_SLOPE = 0.0003 
# -----------------

def get_binance_client():
    return Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    url = f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_TOKEN')}/sendMessage"
    try: requests.post(url, json={"chat_id": os.environ.get("CHAT_ID"), "text": text, "parse_mode": "Markdown"})
    except: pass

@app.route('/')
def run_bot():
    client = get_binance_client()
    try:
        # 1. Проверка позиции и БУ
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active_pos:
            p = active_pos[0]
            amt, entry = float(p['positionAmt']), float(p['entryPrice'])
            curr = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            side_long = amt > 0
            pnl = (curr - entry) / entry if side_long else (entry - curr) / entry
            
            if pnl >= BE_TRIGGER:
                orders = client.futures_get_open_orders(symbol=SYMBOL)
                for o in orders:
                    if o['type'] in ['STOP_MARKET', 'STOP'] and abs(float(o['stopPrice']) - entry) > 0.05:
                        client.futures_cancel_order(symbol=SYMBOL, orderId=o['orderId'])
                        client.futures_create_order(symbol=SYMBOL, side='SELL' if side_long else 'BUY', 
                                                  type='STOP_MARKET', stopPrice=str(round(entry, 2)), reduceOnly=True)
                        send_tg(f"🛡 *БЕЗУБЫТОК*: Защита активирована")
            return f"В сделке. PNL: {pnl*100:.2f}%"

        # 2. Расчет EMA и Наклона
        klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=150)
        closes = [float(k[4]) for k in klines]
        
        ema_f = pd.Series(closes).ewm(span=EMA_FAST, adjust=False).mean()
        ema_s = pd.Series(closes).ewm(span=EMA_SLOW, adjust=False).mean()

        # Текущие и прошлые значения для проверки пересечения
        f_now, s_now = ema_f.iloc[-1], ema_s.iloc[-1]
        f_prev, s_prev = ema_f.iloc[-2], ema_s.iloc[-2]

        # Расчет наклона быстрой EMA (за последние 3 свечи)
        # Это показывает "мощность" импульса
        slope = abs(ema_f.iloc[-1] - ema_f.iloc[-4]) / ema_f.iloc[-4]

        # 3. Логика входа с фильтром наклона
        side = None
        if f_prev <= s_prev and f_now > s_now:
            if slope >= MIN_SLOPE:
                side = "LONG"
            else:
                return f"Сигнал LONG пропущен: слабый наклон ({slope:.5f})"
        
        elif f_prev >= s_prev and f_now < s_now:
            if slope >= MIN_SLOPE:
                side = "SHORT"
            else:
                return f"Сигнал SHORT пропущен: слабый наклон ({slope:.5f})"

        if side:
            execute_trade(client, side, closes[-1])
            return f"Вход {side} подтвержден фильтром наклона!"

        return f"Мониторинг. Наклон: {slope:.5f} (Нужно > {MIN_SLOPE})"

    except Exception as e:
        return f"Error: {e}", 400

def execute_trade(client, side, price):
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    qty = round(75 / price, 3)
    
    # Рыночный вход для скорости
    client.futures_create_order(symbol=SYMBOL, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)

    sl = round(price * (1 - SL_PCT) if side == "LONG" else price * (1 + SL_PCT), 2)
    tp = round(price * (1 + TP_PCT) if side == "LONG" else price * (1 - TP_PCT), 2)

    # Защитные ордера
    side_close = 'SELL' if side=="LONG" else 'BUY'
    client.futures_create_order(symbol=SYMBOL, side=side_close, type='LIMIT', timeInForce='GTC', price=str(tp), quantity=qty, reduceOnly=True)
    client.futures_create_order(symbol=SYMBOL, side=side_close, type='STOP_MARKET', stopPrice=str(sl), quantity=qty, reduceOnly=True)

    send_tg(f"⚡️ *СУПЕР СКАЛЬПЕР: ВХОД {side}*\n📐 Наклон подтвержден\n🎯 TP: `{tp}`\n🛡 SL: `{sl}`")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
