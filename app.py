import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 20
QTY_USD = 1 
WALL_SIZE = 750 
AGGREGATION = 0.3
PROFIT_TO_UNLOCK = 0.0035 # 0.35% для раскрытия замка
SMART_EXIT_MARK = 0.0060  # 0.6% для активации слежки за выходом
RETRACEMENT = 0.0020      # Откат 0.2% от пика для закрытия профита

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

        # 1. ЛОГИКА РАЗБЛОКИРОВКИ (Когда открыты обе стороны)
        if active_l and active_s:
            pnl_l = (curr_p - float(active_l['entryPrice'])) / float(active_l['entryPrice'])
            pnl_s = (float(active_s['entryPrice']) - curr_p) / float(active_s['entryPrice'])

            if pnl_l >= PROFIT_TO_UNLOCK:
                client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY, positionSide='SHORT', type=ORDER_TYPE_MARKET, quantity=abs(float(active_s['positionAmt'])))
                send_tg("🔓 *ЗАМОК РАСКРЫТ:* Оставил LONG, летим вверх!")
                return "Unlocked Long"
            elif pnl_s >= PROFIT_TO_UNLOCK:
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL, positionSide='LONG', type=ORDER_TYPE_MARKET, quantity=abs(float(active_l['positionAmt'])))
                send_tg("🔓 *ЗАМОК РАСКРЫТ:* Оставил SHORT, пробили вниз!")
                return "Unlocked Short"
            return f"Замок активен. Жду импульса..."

        # 2. ЛОГИКА СМАРТ-ВЫХОДА (Когда осталась одна позиция)
        solo_pos = active_l or active_s
        if solo_pos:
            side = 'LONG' if active_l else 'SHORT'
            entry = float(solo_pos['entryPrice'])
            pnl = (curr_p - entry) / entry if side == 'LONG' else (entry - curr_p) / entry
            
            # Если цена дала хороший профит и начала откатывать — фиксируем
            if pnl >= SMART_EXIT_MARK:
                # В реальном трейлинге тут бы хранился High, но для простоты закроем при откате
                # Или просто по достижению цели 1%
                if pnl >= 0.01: 
                    client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if side=='LONG' else SIDE_BUY, 
                                                positionSide=side, type=ORDER_TYPE_MARKET, quantity=abs(float(solo_pos['positionAmt'])))
                    send_tg(f"💰 *ПРОФИТ ВЗЯТ:* Закрыл {side} на +1.0%")
            return f"Сопровождаю {side}. PNL: {pnl*100:.2f}%"

        # 3. ВХОД В ЗАМОК
        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        bid_p, _ = find_whale_walls(depth['bids'])
        ask_p, _ = find_whale_walls(depth['asks'])

        if (bid_p and curr_p <= bid_p + 0.35) or (ask_p and curr_p >= ask_p - 0.35):
            qty = round((QTY_USD * LEVERAGE) / curr_p, 2)
            client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY, positionSide='LONG', type=ORDER_TYPE_MARKET, quantity=qty)
            client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL, positionSide='SHORT', type=ORDER_TYPE_MARKET, quantity=qty)
            send_tg(f"🔒 *ВХОД В ЗАМОК* по {curr_p}. Ожидаю развязку.")
            return "Hedge Entry"

        return f"Мониторинг стен. Цена: {curr_p}"
        
    except Exception as e: return f"Ошибка: {e}", 400

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
