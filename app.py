import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# ==========================================
# --- НАСТРОЙКИ РЕЖИМА (МЕНЯТЬ ТУТ) ---
MODE = "PAPER"        # "PAPER" - понарошку, "REAL" - по-настоящему
DOLLAR_AMOUNT = 6.0   # Виртуальная или реальная сумма входа (в USDC)
# ==========================================

SYMBOL = 'ZECUSDC'
LEVERAGE = 20
TP_LEVEL = 0.105      # Цель 10.5%
SL_LEVEL = 0.020      # Стоп 2.0%
TRAIL_STEP = 0.010    # Шаг трейлинга 1%
STATS_FILE = "stats_demo.txt"

# Глобальная переменная для хранения виртуальной позиции (в памяти)
# Формат: {'side': 'LONG', 'entry': 370.5, 'stop': 365.0, 'take': 410.0, 'qty': 0.1}
virtual_pos = None 

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

def get_market_data(client):
    try:
        klines = client.futures_klines(symbol=SYMBOL, interval='5m', limit=12)
        avg_vol = sum(float(k[5]) for k in klines) / 12
        curr_vol = float(klines[-1][5])
        depth = client.futures_order_book(symbol=SYMBOL, limit=50)
        all_q = [float(q) for p, q in depth['bids']] + [float(q) for p, q in depth['asks']]
        dynamic_wall = (sum(all_q) / len(all_q)) * 3.5
        dynamic_wall = max(120, min(700, dynamic_wall))
        return curr_vol, dynamic_wall, avg_vol, depth
    except: return 0, 200, 0, None

def open_trade(client, side, price):
    global virtual_pos
    curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
    qty = round(DOLLAR_AMOUNT / curr_p, 1)
    if qty < 0.1: qty = 0.1
    
    stop_p = round(curr_p * (1 - SL_LEVEL) if side == "LONG" else curr_p * (1 + SL_LEVEL), 2)
    take_p = round(curr_p * (1 + TP_LEVEL) if side == "LONG" else curr_p * (1 - TP_LEVEL), 2)

    if MODE == "PAPER":
        virtual_pos = {
            'side': side, 'entry': curr_p, 'stop': stop_p, 
            'take': take_p, 'qty': qty, 'max_pnl': 0
        }
        send_tg(f"🧪 *ДЕМО-ВХОД {side}*\nЦена: `{curr_p}`\nСумма: `${DOLLAR_AMOUNT}`\nЦель: `{take_p}`")
    else:
        try:
            client.futures_change_position_mode(dualSidePosition=False)
            client.futures_change_margin_type(symbol=SYMBOL, marginType='ISOLATED')
            client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
            order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
            client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=qty)
            client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET', stopPrice=str(stop_p), closePosition=True)
            client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT', timeInForce='GTC', price=str(take_p), quantity=qty, reduceOnly=True)
            send_tg(f"🚀 *РЕАЛЬНЫЙ ВХОД {side}*\nЦена: `{curr_p}`\nЦель: `{take_p}`")
        except Exception as e:
            send_tg(f"❌ Ошибка входа: {e}")

@app.route('/')
def run_bot():
    global virtual_pos
    client = get_binance_client()
    if not client: return "No API", 500
    
    try:
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

        # --- МОНИТОРИНГ ПОЗИЦИИ ---
        if MODE == "PAPER" and virtual_pos:
            side, entry = virtual_pos['side'], virtual_pos['entry']
            pnl_pct = (curr_p - entry)/entry if side == "LONG" else (entry - curr_p)/entry
            
            # Проверка условий выхода (Стоп/Тейк)
            if (side == "LONG" and curr_p <= virtual_pos['stop']) or (side == "SHORT" and curr_p >= virtual_pos['stop']):
                send_tg(f"🍎 *ДЕМО: СТОП-ЛОСС* (PNL: `{pnl_pct*100:.2f}%`)")
                virtual_pos = None
            elif (side == "LONG" and curr_p >= virtual_pos['take']) or (side == "SHORT" and curr_p <= virtual_pos['take']):
                send_tg(f"💰 *ДЕМО: ТЕЙК-ПРОФИТ!* (PNL: `{pnl_pct*100:.2f}%`)")
                virtual_pos = None
            else:
                # Трейлинг-стоп (виртуальный)
                steps = int(pnl_pct / TRAIL_STEP)
                if steps >= 1:
                    new_stop = round(entry * (1 + (steps-1)*TRAIL_STEP) if side == "LONG" else entry * (1 - (steps-1)*TRAIL_STEP), 2)
                    if (side == "LONG" and new_stop > virtual_pos['stop']) or (side == "SHORT" and new_stop < virtual_pos['stop']):
                        virtual_pos['stop'] = new_stop
                        send_tg(f"🛡 *ДЕМО: Трейлинг* поднят до `{new_stop}`")
            return f"ДЕМО: {side} PNL: {pnl_pct*100:.2f}%"

        elif MODE == "REAL":
            pos = client.futures_position_information(symbol=SYMBOL)
            active = [p for p in pos if float(p['positionAmt']) != 0]
            if active:
                # Тут логика трейлинга для реальных ордеров (уже была в коде)
                return "В реальной сделке..."

        # --- ПОИСК ВХОДА (ОБЩИЙ ДЛЯ ОБОИХ РЕЖИМОВ) ---
        curr_vol, wall_limit, avg_h_vol, depth = get_market_data(client)
        if curr_vol < (avg_h_vol * 0.25):
            return f"Сон (Vol: {curr_vol:.1f})"

        def find_walls(data, limit):
            for p, q in data:
                vol = sum(float(rq) for rp, rq in data if abs(float(rp) - float(p)) <= 0.60)
                if vol >= limit: return float(p), vol
            return None, 0

        bid_p, bid_v = find_walls(depth['bids'], wall_limit)
        ask_p, ask_v = find_walls(depth['asks'], wall_limit)

        if bid_p and curr_p <= bid_p + 0.40:
            open_trade(client, "LONG", curr_p)
            return "Вход в LONG"
        if ask_p and curr_p >= ask_p - 0.40:
            open_trade(client, "SHORT", curr_p)
            return "Вход в SHORT"

        return f"Поиск. Планка: {wall_limit:.0f} ZEC. Vol: {curr_vol:.1f}"

    except Exception as e:
        return f"Error: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
