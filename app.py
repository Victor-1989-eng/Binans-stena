import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ДЛЯ ZEC ---
SYMBOL = 'ZECUSDT'
LEVERAGE = 20
QTY_USD = 2 
WALL_SIZE = 1800  # Стенка в 1800 ZEC
AGGREGATION = 0.05 # Для ZEC шаг цены меньше, сужаем поиск
PROFIT_TO_UNLOCK = 0.0035 # 0.35% для разблокировки
ACTIVATION_PNL = 0.0080   # Трейлинг включаем чуть позже (+0.8%)
CALLBACK_RATE = 0.0030    # Откат 0.3% для фиксации прибыли

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

        # 1. РАЗБЛОКИРОВКА И УСТАНОВКА ЗАЩИТЫ
        if active_l and active_s:
            pnl_l = (curr_p - float(active_l['entryPrice'])) / float(active_l['entryPrice'])
            pnl_s = (float(active_s['entryPrice']) - curr_p) / float(active_s['entryPrice'])

            if pnl_l >= PROFIT_TO_UNLOCK or pnl_s >= PROFIT_TO_UNLOCK:
                side_to_close = 'SHORT' if pnl_l >= PROFIT_TO_UNLOCK else 'LONG'
                survivor = 'LONG' if side_to_close == 'SHORT' else 'SHORT'
                act_close = active_s if side_to_close == 'SHORT' else active_l
                
                client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY if side_to_close == 'SHORT' else SIDE_SELL, 
                                            positionSide=side_to_close, type=ORDER_TYPE_MARKET, quantity=abs(float(act_close['positionAmt'])))
                send_tg(f"🔓 *ZEC*: Замок раскрыт. Оставил {survivor}. Ставлю защиту.")
                return "Unlocked"

        # 2. АВТО-ЗАЩИТА И ТРЕЙЛИНГ (Если позиция уже одна)
        if (active_l or active_s) and not (active_l and active_s):
            side = 'LONG' if active_l else 'SHORT'
            pos = active_l if active_l else active_s
            entry_p = float(pos['entryPrice'])
            pnl = (curr_p - entry_p) / entry_p if side == 'LONG' else (entry_p - curr_p) / entry_p
            
            # Проверка ордеров защиты
            orders = client.futures_get_open_orders(symbol=SYMBOL)
            if not orders:
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if side == 'LONG' else SIDE_BUY, 
                                            positionSide=side, type=ORDER_TYPE_STOP_MARKET, stopPrice=str(round(entry_p, 2)), closePosition=True)
                send_tg(f"🛡 *ZEC*: Добавил стоп в безубыток.")

            # Умный трейлинг выход
            if pnl >= ACTIVATION_PNL:
                new_sl = curr_p * (1 - CALLBACK_RATE) if side == 'LONG' else curr_p * (1 + CALLBACK_RATE)
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if side == 'LONG' else SIDE_BUY, 
                                            positionSide=side, type=ORDER_TYPE_STOP_MARKET, stopPrice=str(round(new_sl, 3)), closePosition=True)
                return f"ZEC Trailing: {pnl*100:.2f}%"

        # 3. ПОИСК СТЕН КИТОВ
        if not active_l and not active_s:
            depth = client.futures_order_book(symbol=SYMBOL, limit=50)
            bid = next((float(p) for p, q in depth['bids'] if float(q) >= WALL_SIZE), None)
            ask = next((float(p) for p, q in depth['asks'] if float(q) >= WALL_SIZE), None)

            if (bid and curr_p <= bid + 0.1) or (ask and curr_p >= ask - 0.1):
                qty = round((QTY_USD * LEVERAGE) / curr_p, 3)
                client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY, positionSide='LONG', type=ORDER_TYPE_MARKET, quantity=qty)
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL, positionSide='SHORT', type=ORDER_TYPE_MARKET, quantity=qty)
                send_tg(f"🔒 *ZEC*: Вход в замок от стены {WALL_SIZE} ZEC")
                return "ZEC Hedge Entry"

        return f"ZEC Scan. Price: {curr_p}"
        
    except Exception as e:
        send_tg(f"⚠️ ZEC Error: {str(e)}")
        return f"Error: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
