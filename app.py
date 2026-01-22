import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ V13.2 ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 50
QTY_USD = 1 
WALL_SIZE = 750 
AGGREGATION = 0.3
PROFIT_TO_UNLOCK = 0.0030 # 0.3% движения для раскрытия
TP_LEVEL = 0.01          # Тейк-профит 1%

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

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "API Keys Missing", 500
    try:
        pos_info = client.futures_position_information(symbol=SYMBOL)
        active_l = next((p for p in pos_info if p['positionSide'] == 'LONG' and float(p['positionAmt']) != 0), None)
        active_s = next((p for p in pos_info if p['positionSide'] == 'SHORT' and float(p['positionAmt']) != 0), None)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

        # 1. ЛОГИКА РАЗБЛОКИРОВКИ + АВТОСТОП
        if active_l and active_s:
            pnl_l = (curr_p - float(active_l['entryPrice'])) / float(active_l['entryPrice'])
            pnl_s = (float(active_s['entryPrice']) - curr_p) / float(active_s['entryPrice'])

            if pnl_l >= PROFIT_TO_UNLOCK or pnl_s >= PROFIT_TO_UNLOCK:
                side_to_close = 'SHORT' if pnl_l >= PROFIT_TO_UNLOCK else 'LONG'
                survivor_side = 'LONG' if side_to_close == 'SHORT' else 'SHORT'
                active_to_close = active_s if side_to_close == 'SHORT' else active_l
                survivor_pos = active_l if survivor_side == 'LONG' else active_s
                
                # Закрываем убыточную сторону по рынку
                client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY if side_to_close == 'SHORT' else SIDE_SELL, 
                                            positionSide=side_to_close, type=ORDER_TYPE_MARKET, quantity=abs(float(active_to_close['positionAmt'])))
                
                # СТАВИМ РЕАЛЬНЫЙ СТОП В БЕЗУБЫТОК НА БИРЖУ
                entry_p = float(survivor_pos['entryPrice'])
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if survivor_side == 'LONG' else SIDE_BUY, 
                                            positionSide=survivor_side, type=ORDER_TYPE_STOP_MARKET, stopPrice=str(round(entry_p, 2)), closePosition=True)
                
                # СТАВИМ ТЕЙК-ПРОФИТ 1% НА БИРЖУ
                tp_price = entry_p * (1 + TP_LEVEL) if survivor_side == 'LONG' else entry_p * (1 - TP_LEVEL)
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if survivor_side == 'LONG' else SIDE_BUY, 
                                            positionSide=survivor_side, type=ORDER_TYPE_LIMIT, price=str(round(tp_price, 2)), quantity=abs(float(survivor_pos['positionAmt'])), timeInForce=TIME_IN_FORCE_GTC, reduceOnly=True)

                send_tg(f"🔓 *РАЗБЛОКИРОВКА:* Оставил {survivor_side}. Стоп и Тейк выставлены на биржу!")
                return "Unlocked and Protected"
            return "Замок активен. Жду импульс."

        # 2. ЕСЛИ ОСТАЛАСЬ ОДНА ПОЗИЦИЯ (Инфо-статус)
        if active_l or active_s:
            side = 'LONG' if active_l else 'SHORT'
            return f"Сопровождение {side} (Стопы уже на бирже)."

        # 3. ВХОД В ЗАМОК
        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        bid_p, _ = find_whale_walls(depth['bids'])
        ask_p, _ = find_whale_walls(depth['asks'])

        if (bid_p and curr_p <= bid_p + 0.35) or (ask_p and curr_p >= ask_p - 0.35):
            qty = round((QTY_USD * LEVERAGE) / curr_p, 2)
            # Открываем обе стороны по рынку
            client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY, positionSide='LONG', type=ORDER_TYPE_MARKET, quantity=qty)
            client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL, positionSide='SHORT', type=ORDER_TYPE_MARKET, quantity=qty)
            send_tg(f"🔒 *ВХОД В ЗАМОК* по {curr_p}. Ожидаю развязку.")
            return "Hedge Entry"

        return f"Мониторинг. Цена: {curr_p}"
        
    except Exception as e: return f"Ошибка: {e}", 400

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
