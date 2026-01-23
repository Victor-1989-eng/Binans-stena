import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ZEC "ОХОТА ЗА ИКСАМИ" ---
SYMBOL = 'ZECUSDC'
LEVERAGE = 20
QTY_ZEC = 1.0       # Объем в монетах ZEC
WALL_SIZE = 500     # Суммарный объем стен
AGGREGATION = 0.25  # Диапазон суммирования цен (центы)
MIN_5M_VOLUME = 250 # Фильтр активности (ZEC за 5 мин)

# ПАРАМЕТРЫ ПРОФИТА
BE_LEVEL = 0.010    # Безубыток на +1%
TP_LEVEL = 0.035    # Тейк-профит +3.5%
SL_LEVEL = 0.015    # Стоп-лосс -1.5%

STATS_FILE = "stats_zec.txt"

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

def get_5m_volume(client):
    try:
        klines = client.futures_klines(symbol=SYMBOL, interval='5m', limit=1)
        return float(klines[0][5]) # Объем (Volume) за последнюю свечу
    except: return 0

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        # Суммируем плотность в диапазоне AGGREGATION
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(client, side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        
        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # Для ZEC округление до 2 знаков для цены и 3 для количества
        price = round(price, 2)
        
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_ZEC)
        
        stop_p = round(price * (1 - SL_LEVEL) if side == "LONG" else price * (1 + SL_LEVEL), 2)
        take_p = round(price * (1 + TP_LEVEL) if side == "LONG" else price * (1 - TP_LEVEL), 2)
        
        # Выставляем стоп и тейк
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET', stopPrice=str(stop_p), closePosition=True)
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT', timeInForce='GTC', price=str(take_p), quantity=QTY_ZEC, reduceOnly=True)
        
        send_tg(f"🐺 *ZEC ВХОД {side}* по `{price}`\n🎯 Цель: `{take_p}` | 🛡 Стоп: `{stop_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка открытия ZEC: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "API Keys Missing", 500
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active_pos:
            p = active_pos[0]
            amt = float(p['positionAmt'])
            entry_p = float(p['entryPrice'])
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            
            pnl_pct = (curr_p - entry_p) / entry_p if amt > 0 else (entry_p - curr_p) / entry_p
            
            # Логика безубытка
            if pnl_pct >= BE_LEVEL:
                orders = client.futures_get_open_orders(symbol=SYMBOL)
                for o in orders:
                    if o['type'] == 'STOP_MARKET' and float(o['stopPrice']) != entry_p:
                        client.futures_cancel_order(symbol=SYMBOL, orderId=o['orderId'])
                        side = 'SELL' if amt > 0 else 'BUY'
                        client.futures_create_order(symbol=SYMBOL, side=side, type='STOP_MARKET', stopPrice=str(round(entry_p, 2)), closePosition=True)
                        send_tg("🛡 ZEC: Стоп перенесен в БЕЗУБЫТОК")
            
            return f"ZEC в сделке. Профит: {pnl_pct*100:.2f}%"

        # Если позиции нет — ищем вход
        vol_5m = get_5m_volume(client)
        if vol_5m < MIN_5M_VOLUME:
            return f"Рынок спит. Объем 5м: {vol_5m:.1f} (нужно {MIN_5M_VOLUME})"

        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        bid_p, bid_v = find_whale_walls(depth['bids'])
        ask_p, ask_v = find_whale_walls(depth['asks'])

        # Вход от нижней стены (Long)
        if bid_p and curr_p <= bid_p + 0.10:
            open_trade(client, "LONG", curr_p)
            return "Открываю LONG по ZEC"
            
        # Вход от верхней стены (Short)
        if ask_p and curr_p >= ask_p - 0.10:
            open_trade(client, "SHORT", curr_p)
            return "Открываю SHORT по ZEC"

        return f"ZEC Сканирую... Объем 5м: {vol_5m:.1f}. Стен > {WALL_SIZE} нет."
    except Exception as e:
        return f"Ошибка ZEC: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
