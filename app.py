import os, requests, time, threading
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ СНАЙПЕРА (v.12.0) ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 20        
RISK_USD = 1.0       # Рискуем 1$ (Стоп)
REWARD_USD = 3.0     # Цель 3$ (Тейк)
WALL_SIZE = 900      # Твоя настройка "Миллионер"
AGGREGATION = 0.5    

def get_binance_client():
    return Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        # Считаем объем в диапазоне агрегации
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val
    return None

def main_loop():
    client = get_binance_client()
    send_tg("🎯 *WHALE-SNIPER 1:3 ЗАПУЩЕН*\nЖду крупную стенку...")
    
    last_processed_id = None

    while True:
        try:
            # 1. ОТЧЕТ ПО ПРИБЫЛИ (Если сделка закрылась)
            trades = client.futures_account_trades(symbol=SYMBOL, limit=1)
            if trades and trades[0]['id'] != last_processed_id:
                pnl = float(trades[0]['realizedPnl'])
                if pnl != 0:
                    icon = "💰" if pnl > 0 else "📉"
                    send_tg(f"{icon} *СДЕЛКА ЗАКРЫТА*\nРезультат: `{round(pnl, 2)} USDT`")
                last_processed_id = trades[0]['id']

            # 2. ПРОВЕРКА ПОЗИЦИИ
            pos = client.futures_position_information(symbol=SYMBOL)
            in_pos = any(float(p['positionAmt']) != 0 for p in pos if p['symbol'] == SYMBOL)

            if not in_pos:
                depth = client.futures_order_book(symbol=SYMBOL, limit=100)
                bid_wall = find_whale_walls(depth['bids'])
                ask_wall = find_whale_walls(depth['asks'])
                curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

                side = None
                if bid_wall and curr_p <= bid_wall + 0.5: side = "BUY"
                elif ask_wall and curr_p >= ask_wall - 0.5: side = "SELL"

                if side:
                    # РАСЧЕТ МАТЕМАТИКИ 1:3
                    stop_dist = curr_p * 0.005 # Стоп 0.5% от цены
                    qty = round(RISK_USD / stop_dist, 2)
                    
                    sl = round(curr_p - stop_dist if side == "BUY" else curr_p + stop_dist, 2)
                    tp = round(curr_p + (stop_dist * 3) if side == "BUY" else curr_p - (stop_dist * 3), 2)

                    # Исполнение
                    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
                    client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty)
                    
                    # Ордера выхода
                    opp_side = "SELL" if side == "BUY" else "BUY"
                    client.futures_create_order(symbol=SYMBOL, side=opp_side, type='STOP_MARKET', stopPrice=str(sl), closePosition=True)
                    client.futures_create_order(symbol=SYMBOL, side=opp_side, type='LIMIT', price=str(tp), quantity=qty, timeInForce='GTC', reduceOnly=True)
                    
                    send_tg(f"🚀 *ВХОД ОТ КИТА ({side})*\nЦена: `{curr_p}`\n🎯 TP: `{tp}` | 🛡 SL: `{sl}`")

            time.sleep(15)
        except Exception as e:
            time.sleep(30)

threading.Thread(target=main_loop, daemon=True).start()

@app.route('/')
def health(): return "Whale Snipping Active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
