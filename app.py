import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАЛАШТУВАННЯ СТРАТЕГІЇ ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 20        # Зменшив плече, бо стратегія трендова
QTY_BNB = 0.20
TP_PCT = 0.015       # Тейк 1.5%
SL_PCT = 0.008       # Стоп 0.8%
LOOKBACK_BARS = 24   # Скільки свічок 1H аналізувати для пошуку "зон ліквідації"

def get_binance_client():
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    return Client(api_key, api_secret) if api_key and api_secret else None

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

# --- КРОК 1: ВИЗНАЧЕННЯ ГЛОБАЛЬНОГО ТРЕНДУ (1W) ---
def get_global_trend(client):
    try:
        bars = client.futures_klines(symbol=SYMBOL, interval='1w', limit=2)
        # Якщо поточна ціна вища за відкриття тижня — ТРЕНД ВГОРУ
        close_curr = float(bars[-1][4])
        open_curr = float(bars[-1][1])
        return "UP" if close_curr > open_curr else "DOWN"
    except: return "NEUTRAL"

# --- КРОК 3: ПОШУК ЗОН ЛІКВІДАЦІЇ (Low/High за період) ---
def get_liquidation_levels(client):
    try:
        # Беремо 1-годинні свічки для пошуку рівнів, де накопичились стопи
        bars = client.futures_klines(symbol=SYMBOL, interval='1h', limit=LOOKBACK_BARS)
        lows = [float(b[3]) for b in bars]
        highs = [float(b[2]) for b in bars]
        return min(lows), max(highs)
    except: return None, None

def open_trade(client, side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        entry_p = round(price, 2)
        
        if side == "LONG":
            order_side, close_side = 'BUY', 'SELL'
            tp_p = round(entry_p * (1 + TP_PCT), 2)
            sl_p = round(entry_p * (1 - SL_PCT), 2)
        else:
            order_side, close_side = 'SELL', 'BUY'
            tp_p = round(entry_p * (1 - TP_PCT), 2)
            sl_p = round(entry_p * (1 + SL_PCT), 2)

        # Вхід по маркету
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_BNB)
        # Тейк
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT', 
                                    price=str(tp_p), quantity=QTY_BNB, timeInForce='GTC', reduceOnly=True)
        # Стоп
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET', 
                                    stopPrice=str(sl_p), closePosition=True)

        send_tg(f"🚀 *ВХІД ЗА ТРЕНДОМ {side}*\n💰 Вхід: `{entry_p}`\n🎯 Тейк: `{tp_p}`\n🛑 Стоп: `{sl_p}`")
    except Exception as e:
        send_tg(f"❌ Помилка входу: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "No API Keys"
    
    try:
        # 1. Перевіряємо, чи є вже відкрита позиція
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        if active_pos:
            return f"Бот у позиції. PNL: {active_pos[0]['unRealizedProfit']}$"

        # 2. Отримуємо дані
        trend = get_global_trend(client)          # Глобальний тренд (1W)
        liq_low, liq_high = get_liquidation_levels(client) # Зони ліквідації
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

        if not liq_low: return "Помилка отримання рівнів"

        # 3. ЛОГІКА ВХОДУ (Шаг 4 стратегії)
        # ЛОНГ: Тренд вгору + Ціна "вколола" зону ліквідації знизу (откат)
        if trend == "UP" and curr_p <= liq_low * 1.001:
            open_trade(client, "LONG", curr_p)
            return f"Зайшов у LONG. Тренд UP, зняли ліквідність на {liq_low}"

        # ШОРТ: Тренд вниз + Ціна "вколола" зону ліквідації зверху (откат)
        if trend == "DOWN" and curr_p >= liq_high * 0.999:
            open_trade(client, "SHORT", curr_p)
            return f"Зайшов у SHORT. Тренд DOWN, зняли ліквідність на {liq_high}"

        return f"Моніторинг... Тренд: {trend}. Чекаємо відкат до {liq_low if trend=='UP' else liq_high}"

    except Exception as e:
        return f"Помилка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
