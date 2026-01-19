import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ "УМНОГО ЗВЕРЯ" ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 50       # Снизили до 50 для стабильности при объеме 0.55
QTY_BNB = 0.25      # Твоя новая ставка (~10$ маржи)
WALL_SIZE = 900     
RANGE_MAX = 0.015
AGGREGATION = 0.5
STATS_FILE = "stats_v2.txt"
MAX_TIME = 3600     

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

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(client, side, entry_price, target_wall_price=None):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # 1. Вход
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='LIMIT',
            timeInForce='GTC', quantity=QTY_BNB, price=str(round(entry_price, 2)))
        
        time.sleep(1) # Пауза для стабильности API

        # 2. Расчет Тейка (Умная логика)
        stop_p = round(entry_price * 0.996 if side == "LONG" else entry_price * 1.004, 2)
        
        if target_wall_price:
            # Ставим тейк чуть-чуть не доходя до встречной стены (на 0.1 USDT)
            take_p = round(target_wall_price - 0.1 if side == "LONG" else target_wall_price + 0.1, 2)
        else:
            # Если стены нет - стандартные 0.55%
            take_p = round(entry_price * 1.0055 if side == "LONG" else entry_price * 0.9945, 2)
        
        # 3. Защита
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET',
            stopPrice=str(stop_p), closePosition=True)
        
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT',
            timeInForce='GTC', price=str(take_p), quantity=QTY_BNB, reduceOnly=True)
        
        send_tg(f"⚡️ *ВХОД {side}* по `{entry_price}`\n🎯 Цель (стена): `{take_p}`\n🛡 Стоп: `{stop_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "API Keys Missing", 500
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active_pos:
            p = active_pos[0]
            trade_time = int(p['updateTime']) / 1000
            if (time.time() - trade_time) > MAX_TIME:
                client.futures_create_order(symbol=SYMBOL, side='SELL' if float(p['positionAmt']) > 0 else 'BUY', 
                                            type='MARKET', quantity=abs(float(p['positionAmt'])), reduceOnly=True)
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                send_tg("⏰ Выход по времени (60 мин)")
            return "В сделке... Ожидаю профит или таймер."

        # ПОИСК СТЕН
        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        bid_p, bid_v = find_whale_walls(depth['bids'])
        ask_p, ask_v = find_whale_walls(depth['asks'])

        # Инфо для браузера
        status = f"<h3>Статус: Сканирую стакан (Стена: {WALL_SIZE})</h3>"
        status += f"Текущая цена: <b>{curr_p}</b><br><hr>"
        
        if bid_p: status += f"✅ Вижу BUY стену: {bid_v:.1f} на {bid_p}<br>"
        if ask_p: status += f"✅ Вижу SELL стену: {ask_v:.1f} на {ask_p}<br>"

        # Логика входа
        if bid_p and curr_p <= bid_p + 1.0: # Если рядом с нижней стеной
            open_trade(client, "LONG", bid_p + 0.1, target_wall_price=ask_p)
        elif ask_p and curr_p >= ask_p - 1.0: # Если рядом с верхней стеной
            open_trade(client, "SHORT", ask_p - 0.1, target_wall_price=bid_p)
        else:
            status += "<br>Стен в радиусе входа пока нет."

        return status
    except Exception as e:
        return f"Ошибка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
