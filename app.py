import os
import requests
import pandas as pd
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- ГЕОМЕТРИЯ И НАСТРОЙКИ СТРАТЕГИИ ---
SYMBOL = 'SOLUSDC'
TIMEFRAME = '1m'
LEVERAGE = 75
MARGIN_USDC = 1.0   # Твоя ставка (1$)

EMA_FAST = 25
EMA_SLOW = 99
MIN_SLOPE = 0.0001  # Фильтр для ПЕРВОГО входа
EMERGENCY_SL = 0.03 # Аварийный стоп (3%)
# ---------------------------------------

def get_binance_client():
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    return Client(api_key, api_secret)

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except:
            pass

@app.route('/')
def run_bot():
    client = get_binance_client()
    try:
        # 1. Загружаем свечи
        klines = client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=150)
        closes = [float(k[4]) for k in klines]
        
        # 2. Математика EMA
        series = pd.Series(closes)
        ema_f = series.ewm(span=EMA_FAST, adjust=False).mean()
        ema_s = series.ewm(span=EMA_SLOW, adjust=False).mean()

        f_now, s_now = ema_f.iloc[-1], ema_s.iloc[-1]
        f_prev, s_prev = ema_f.iloc[-2], ema_s.iloc[-2]
        
        # Наклон за последние 3 минуты (для фильтра входа)
        slope = abs(f_now - ema_f.iloc[-4]) / ema_f.iloc[-4]

        # 3. Определяем потенциальный сигнал
        potential_signal = None
        if f_prev <= s_prev and f_now > s_now:
            potential_signal = "LONG"
        elif f_prev >= s_prev and f_now < s_now:
            potential_signal = "SHORT"

        # 4. Проверяем текущие позиции
        pos = client.futures_position_information(symbol=SYMBOL)
        active = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active:
            p = active[0]
            amt = float(p['positionAmt'])
            current_side = "LONG" if amt > 0 else "SHORT"
            
            # РЕВЕРС: Если есть пересечение в другую сторону — ПЕРЕВОРАЧИВАЕМ БЕЗ ФИЛЬТРА НАКЛОНА
            if potential_signal and potential_signal != current_side:
                # Закрываем старую по рынку
                client.futures_create_order(symbol=SYMBOL, side='SELL' if amt > 0 else 'BUY', 
                                          type='MARKET', quantity=abs(amt), reduceOnly=True)
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                
                # Открываем новую
                execute_trade(client, potential_signal, closes[-1])
                send_tg(f"🔄 *REVERSE*: {current_side} ➡️ {potential_signal}\n📐 Наклон при выходе: `{slope:.5f}`")
                return f"Reversed to {potential_signal}"
            
            return f"Держу {current_side}. Наклон: {slope:.5f}. Жду пересечения."

        # 5. Первый вход (если позиции нет) — ТУТ ФИЛЬТР НАКЛОНА НУЖЕН
        if potential_signal:
            if slope >= MIN_SLOPE:
                execute_trade(client, potential_signal, closes[-1])
                return f"First Entry: {potential_signal}"
            else:
                return f"Сигнал {potential_signal} пропущен (слабый наклон: {slope:.5f})"

        return f"Сканирую рынок... Текущий наклон: {slope:.5f}"

    except Exception as e:
        return f"Ошибка: {e}", 400

def execute_trade(client, side, price):
    # Настройка плеча
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    
    # Расчет объема (ставка 1$ * плечо 75)
    qty = round((MARGIN_USDC * LEVERAGE) / price, 2)
    
    # Вход по МАРКЕТУ (скорость Франкфурта)
    order = client.futures_create_order(
        symbol=SYMBOL, 
        side='BUY' if side == "LONG" else 'SELL', 
        type='MARKET', 
        quantity=qty
    )

    # Аварийный Стоп (3%)
    sl_price = round(price * (1 - EMERGENCY_SL) if side == "LONG" else price * (1 + EMERGENCY_SL), 2)
    client.futures_create_order(
        symbol=SYMBOL, 
        side='SELL' if side == "LONG" else 'BUY', 
        type='STOP_MARKET', 
        stopPrice=str(sl_price), 
        quantity=qty, 
        reduceOnly=True
    )

    send_tg(f"🚀 *ВХОД {side}*\n💰 Ставка: `{MARGIN_USDC}$` (75x)\n🛡 Стоп: `{sl_price}`")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
