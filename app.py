import os
import requests
import pandas as pd
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- ГЕОМЕТРИЯ И НАСТРОЙКИ ---
SYMBOL = 'SOLUSDC'
TIMEFRAME = '1m'
LEVERAGE = 75
MARGIN_USDC = 1.0  # Твоя ставка (1$)

EMA_FAST = 25
EMA_SLOW = 99
MIN_SLOPE = 0.0005  # Фильтр для SOL (наклон импульса)
EMERGENCY_SL = 0.03 # Аварийный стоп 3%
# -----------------------------

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
        # 1. Считаем EMA на минутках
        klines = client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=150)
        closes = [float(k[4]) for k in klines]
        ema_f = pd.Series(closes).ewm(span=EMA_FAST, adjust=False).mean()
        ema_s = pd.Series(closes).ewm(span=EMA_SLOW, adjust=False).mean()

        f_now, s_now = ema_f.iloc[-1], ema_s.iloc[-1]
        f_prev, s_prev = ema_f.iloc[-2], ema_s.iloc[-2]
        
        # Наклон за 3 минуты (физика ускорения)
        slope = abs(f_now - ema_f.iloc[-4]) / ema_f.iloc[-4]

        # Сигнал переворота
        new_signal = None
        if f_prev <= s_prev and f_now > s_now and slope >= MIN_SLOPE:
            new_signal = "LONG"
        elif f_prev >= s_prev and f_now < s_now and slope >= MIN_SLOPE:
            new_signal = "SHORT"

        # 2. Проверяем текущий статус
        pos = client.futures_position_information(symbol=SYMBOL)
        active = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active:
            p = active[0]
            amt = float(p['positionAmt'])
            current_side = "LONG" if amt > 0 else "SHORT"
            
            # РЕВЕРС: Если сигнал сменился - переворачиваем «тапки»
            if new_signal and new_signal != current_side:
                # Закрываем всё старое
                client.futures_create_order(symbol=SYMBOL, side='SELL' if amt > 0 else 'BUY', 
                                          type='MARKET', quantity=abs(amt), reduceOnly=True)
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                
                # Открываем новое
                execute_trade(client, new_signal, closes[-1])
                send_tg(f"🔄 *SOL REVERSE*: {current_side} ➡️ {new_signal}\n📐 Наклон: `{slope:.5f}`")
                return f"Reverse to {new_signal}"
            
            return f"Держу {current_side}. Наклон: {slope:.5f}"

        # 3. Если позиции нет - заходим
        if new_signal:
            execute_trade(client, new_signal, closes[-1])
            return f"Вход SOL: {new_signal}"

        return f"Поиск импульса SOL... Наклон: {slope:.5f}"

    except Exception as e:
        return f"Error: {e}", 400

def execute_trade(client, side, price):
    # Ставим плечо
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    
    # Расчет объема исходя из ставки 1$ и плеча 75
    qty = round((MARGIN_USDC * LEVERAGE) / price, 2)
    
    # Вход по рынку
    client.futures_create_order(symbol=SYMBOL, side='BUY' if side=="LONG" else 'SELL', 
                               type='MARKET', quantity=qty)

    # Аварийный стоп 3% (чтобы не ликвидировало при сбое связи)
    sl_price = round(price * (1 - EMERGENCY_SL) if side == "LONG" else price * (1 + EMERGENCY_SL), 2)
    client.futures_create_order(symbol=SYMBOL, side='SELL' if side=="LONG" else 'BUY', 
                               type='STOP_MARKET', stopPrice=str(sl_price), quantity=qty, reduceOnly=True)

    send_tg(f"🚀 *ВХОД SOL {side}*\n💰 Ставка: `{MARGIN_USDC}$` (75x)\n🛡 Стоп: `{sl_price}`")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
