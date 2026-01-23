import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ МУЛЬТИ-БОТА ---
SYMBOLS_CONFIG = {
    'BNBUSDT': {'wall': 750, 'prec': 2},
    'ETHUSDT': {'wall': 400, 'prec': 2},
    'SOLUSDT': {'wall': 2500, 'prec': 2}
}
LEVERAGE = 50
QTY_USD = 1 
PROFIT_TO_UNLOCK = 0.0030 
ACTIVATION_PNL = 0.0075   # Чуть поднял для мульти-актива
CALLBACK_RATE = 0.0025    # Откат для фиксации

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
        for symbol, config in SYMBOLS_CONFIG.items():
            pos_info = client.futures_position_information(symbol=symbol)
            active_l = next((p for p in pos_info if p['positionSide'] == 'LONG' and float(p['positionAmt']) != 0), None)
            active_s = next((p for p in pos_info if p['positionSide'] == 'SHORT' and float(p['positionAmt']) != 0), None)
            curr_p = float(client.futures_symbol_ticker(symbol=symbol)['price'])

            # 1. РАЗБЛОКИРОВКА ЗАМКА
            if active_l and active_s:
                p_l = (curr_p - float(active_l['entryPrice'])) / float(active_l['entryPrice'])
                p_s = (float(active_s['entryPrice']) - curr_p) / float(active_s['entryPrice'])

                if p_l >= PROFIT_TO_UNLOCK or p_s >= PROFIT_TO_UNLOCK:
                    side_to_close = 'SHORT' if p_l >= PROFIT_TO_UNLOCK else 'LONG'
                    survivor = 'LONG' if side_to_close == 'SHORT' else 'SHORT'
                    act_close = active_s if side_to_close == 'SHORT' else active_l
                    
                    client.futures_create_order(symbol=symbol, side=SIDE_BUY if side_to_close == 'SHORT' else SIDE_SELL, 
                                                positionSide=side_to_close, type=ORDER_TYPE_MARKET, quantity=abs(float(act_close['positionAmt'])))
                    send_tg(f"🔓 *{symbol}*: Замок раскрыт! Оставил {survivor}.")
                continue

            # 2. ТРЕЙЛИНГ И ЗАЩИТА
            if (active_l or active_s) and not (active_l and active_s):
                side = 'LONG' if active_l else 'SHORT'
                pos = active_l if active_l else active_s
                entry_p = float(pos['entryPrice'])
                pnl = (curr_p - entry_p) / entry_p if side == 'LONG' else (entry_p - curr_p) / entry_p
                
                orders = client.futures_get_open_orders(symbol=symbol)
                if not orders:
                    client.futures_create_order(symbol=symbol, side=SIDE_SELL if side == 'LONG' else SIDE_BUY, 
                                                positionSide=side, type=ORDER_TYPE_STOP_MARKET, stopPrice=str(round(entry_p, 2)), closePosition=True)
                
                if pnl >= ACTIVATION_PNL:
                    new_sl = curr_p * (1 - CALLBACK_RATE) if side == 'LONG' else curr_p * (1 + CALLBACK_RATE)
                    client.futures_cancel_all_open_orders(symbol=symbol)
                    client.futures_create_order(symbol=symbol, side=SIDE_SELL if side == 'LONG' else SIDE_BUY, 
                                                positionSide=side, type=ORDER_TYPE_STOP_MARKET, stopPrice=str(round(new_sl, 2)), closePosition=True)
                continue

            # 3. ПОИСК НОВЫХ ВХОДОВ
            depth = client.futures_order_book(symbol=symbol, limit=20)
            # Ищем стенку в стакане
            bid = next((float(p) for p, q in depth['bids'] if float(q) >= config['wall']), None)
            ask = next((float(p) for p, q in depth['asks'] if float(q) >= config['wall']), None)

            if (bid and curr_p <= bid * 1.0005) or (ask and curr_p >= ask * 0.9995):
                qty = round((QTY_USD * LEVERAGE) / curr_p, config['prec'])
                client.futures_create_order(symbol=symbol, side=SIDE_BUY, positionSide='LONG', type=ORDER_TYPE_MARKET, quantity=qty)
                client.futures_create_order(symbol=symbol, side=SIDE_SELL, positionSide='SHORT', type=ORDER_TYPE_MARKET, quantity=qty)
                send_tg(f"🔒 *{symbol}*: Вход в замок от стены кита!")
        
        return "Multi-Scan OK"
    except Exception as e:
        send_tg(f"⚠️ Ошибка мульти-бота: {str(e)}")
        return f"Error: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
