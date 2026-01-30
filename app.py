import os, requests, time, threading
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ v.13.2 (РЕЖИМ 75x) ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 75        
RISK_USD = 1.0       # Риск всегда $1
WALL_SIZE = 500      
AGGREGATION = 0.5    

def get_binance_client():
    return Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if token and chat_id:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                          json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def find_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val
    return None

def main_loop():
    client = get_binance_client()
    send_tg("🚀 *WHALE-SNIPER v.13.2 ЗАПУЩЕН*\nМатематика: Адаптивный 1:3 | Плечо x75")
    last_id = None

    while True:
        try:
            # 1. ОТЧЕТНОСТЬ
            trades = client.futures_account_trades(symbol=SYMBOL, limit=1)
            if trades and trades[0]['id'] != last_id:
                pnl = float(trades[0]['realizedPnl'])
                if pnl != 0:
                    icon = "🎯" if pnl > 0 else "🛡"
                    send_tg(f"{icon} *ИТОГ СДЕЛКИ*\nРезультат: `{pnl:.2f} USDT`")
                last_id = trades[0]['id']

            # 2. ПРОВЕРКА ПОЗИЦИИ
            pos = client.futures_position_information(symbol=SYMBOL)
            current_pos = next((p for p in pos if p['symbol'] == SYMBOL), None)
            
            if not (current_pos and float(current_pos['positionAmt']) != 0):
                # 3. ПОИСК ВХОДА
                depth = client.futures_order_book(symbol=SYMBOL, limit=100)
                bid_wall = find_walls(depth['bids'])
                ask_wall = find_walls(depth['asks'])
                curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

                side, wall_p = None, 0
                if bid_wall and curr_p <= bid_wall + 0.6: 
                    side, wall_p = "BUY", bid_wall
                elif ask_wall and curr_p >= ask_wall - 0.6: 
                    side, wall_p = "SELL", ask_wall

                if side:
                    # АДАПТИВНАЯ МАТЕМАТИКА
                    stop_dist = abs(curr_p - wall_p) + (curr_p * 0.0015)
                    stop_dist = max(stop_dist, curr_p * 0.002) 
                    stop_dist = min(stop_dist, curr_p * 0.01)

                    qty = round(RISK_USD / stop_dist, 2)
                    sl = round(curr_p - stop_dist if side == "BUY" else curr_p + stop_dist, 2)
                    tp = round(curr_p + (stop_dist * 3) if side == "BUY" else curr_p - (stop_dist * 3), 2)

                    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
                    client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty)
                    
                    opp = "SELL" if side == "BUY" else "BUY"
                    client.futures_create_order(symbol=SYMBOL, side=opp, type='STOP_MARKET', stopPrice=str(sl), closePosition=True)
                    client.futures_create_order(symbol=SYMBOL, side=opp, type='LIMIT', price=str(tp), quantity=qty, timeInForce='GTC', reduceOnly=True)
                    
                    send_tg(f"🐳 *ВХОД ({side})*\nСтена: `{wall_p}`\nСтоп: `{sl}` | Тейк: `{tp}`")

            time.sleep(15)
        except Exception as e:
            time.sleep(30)

threading.Thread(target=main_loop, daemon=True).start()

@app.route('/')
def health(): return "OK", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
