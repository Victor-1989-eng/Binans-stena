import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ПОД $5 ---
SYMBOLS = ['SOLUSDT', 'BNBUSDT']  # Для $5 лучше 1-2 монеты
LEVERAGE = 20
QTY_USD = 5            # Твоя маржа на сделку
TP_PCT = 0.02          # Тейк 2%
SL_PCT = 0.01          # Стоп 1%
BE_PCT = 0.008         # Безубыток при +0.8%
LOOKBACK_BARS = 24     # Поиск зон ликвидности за сутки

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

# --- МОЗГИ: ТРЕНД И ЛИКВИДНОСТЬ ---
def get_data(client, symbol):
    # Глобальный тренд (1 неделя)
    w_bars = client.futures_klines(symbol=symbol, interval='1w', limit=2)
    trend = "UP" if float(w_bars[-1][4]) > float(w_bars[-1][1]) else "DOWN"
    # Зоны ликвидности (1 час)
    h_bars = client.futures_klines(symbol=symbol, interval='1h', limit=LOOKBACK_BARS)
    lows = min([float(b[3]) for b in h_bars])
    highs = max([float(b[2]) for b in h_bars])
    return trend, lows, highs

# --- РУКИ: ОТКРЫТИЕ ПАЧКИ ОРДЕРОВ ---
def open_position(client, symbol, side, curr_p):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
        qty = round((QTY_USD * LEVERAGE) / curr_p, 2) # Расчет объема с плечом
        
        # 1. MARKET ВХОД
        client.futures_create_order(symbol=symbol, side=SIDE_BUY if side=="LONG" else SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=qty)
        
        # Параметры для защиты
        sl_price = round(curr_p * (1 - SL_PCT) if side=="LONG" else curr_p * (1 + SL_PCT), 2)
        tp_price = round(curr_p * (1 + TP_PCT) if side=="LONG" else curr_p * (1 - TP_PCT), 2)

        # 2. STOP_MARKET (Защита)
        client.futures_create_order(symbol=symbol, side=SIDE_SELL if side=="LONG" else SIDE_BUY, type='STOP_MARKET', stopPrice=str(sl_price), closePosition=True)
        
        # 3. LIMIT TAKE PROFIT (Цель)
        client.futures_create_order(symbol=symbol, side=SIDE_SELL if side=="LONG" else SIDE_BUY, type=ORDER_TYPE_LIMIT, price=str(tp_price), quantity=qty, timeInForce=TIME_IN_FORCE_GTC, reduceOnly=True)

        send_tg(f"🚀 *ВХОД {side}* {symbol}\n💰 Вход: `{curr_p}`\n🛑 Стоп: `{sl_price}`\n🎯 Тейк: `{tp_price}`")
    except Exception as e: send_tg(f"❌ Ошибка входа {symbol}: {e}")

# --- УПРАВЛЕНИЕ: ТРЕЙЛИНГ И БЕЗУБЫТОК ---
def manage_trailing(client, symbol, side, entry_p, curr_p):
    profit = (curr_p - entry_p) / entry_p if side == "LONG" else (entry_p - curr_p) / entry_p
    if profit >= BE_PCT:
        # Логика подтягивания стопа (Трейлинг)
        new_sl = round(curr_p * (1 - 0.005) if side == "LONG" else curr_p * (1 + 0.005), 2)
        update_stop_order(client, symbol, side, new_sl)

def update_stop_order(client, symbol, side, new_sl):
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        for o in orders:
            if o['type'] == 'STOP_MARKET':
                old_sl = float(o['stopPrice'])
                if (side == "LONG" and new_sl > old_sl) or (side == "SHORT" and new_sl < old_sl):
                    client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
                    client.futures_create_order(symbol=symbol, side=SIDE_SELL if side=="LONG" else SIDE_BUY, type='STOP_MARKET', stopPrice=str(new_sl), closePosition=True)
                    send_tg(f"📈 *SL Подтянут:* {new_sl}")
    except: pass

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "No API"
    for symbol in SYMBOLS:
        pos = client.futures_position_information(symbol=symbol)
        active = [p for p in pos if float(p['positionAmt']) != 0]
        curr_p = float(client.futures_symbol_ticker(symbol=symbol)['price'])

        if active:
            amt, entry = float(active[0]['positionAmt']), float(active[0]['entryPrice'])
            manage_trailing(client, symbol, "LONG" if amt > 0 else "SHORT", entry, curr_p)
        else:
            trend, liq_low, liq_high = get_data(client, symbol)
            if trend == "UP" and curr_p <= liq_low * 1.001: open_position(client, symbol, "LONG", curr_p)
            elif trend == "DOWN" and curr_p >= liq_high * 0.999: open_position(client, symbol, "SHORT", curr_p)
    return "OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
