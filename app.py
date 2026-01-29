import os
import time
import threading
import pandas as pd
import ccxt
import requests
from flask import Flask

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNB/USDC'
RISK_USD = 10.0   
REWARD_USD = 30.0 
LOOKBACK_MINUTES = 60 # Период поиска границ канала
COMMISSION_RATE = 0.0004 

stats = {
    "balance": 1000.0,
    "wins": 0, "losses": 0, "total_fees": 0.0,
    "in_position": False, "side": None, "sl": 0, "tp": 0, "qty": 0
}

exchange = ccxt.binance({'options': {'defaultType': 'future'}})

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def get_channel_extrema():
    try:
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=LOOKBACK_MINUTES)
        df = pd.DataFrame(bars, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        return df['h'].max(), df['l'].min()
    except: return None, None

def bot_worker():
    global stats
    send_tg("🎯 *v.9.0 СНАЙПЕРСКИЙ КОНВЕЙЕР ЗАПУЩЕН*\nЖду цену на границах канала (1:3).")

    while True:
        try:
            ticker = exchange.fetch_ticker(SYMBOL)
            curr_p = ticker['last']

            # 1. ПРОВЕРКА ВЫХОДА
            if stats["in_position"]:
                side = stats["side"]
                is_tp = (side == "BUY" and curr_p >= stats["tp"]) or (side == "SELL" and curr_p <= stats["tp"])
                is_sl = (side == "BUY" and curr_p <= stats["sl"]) or (side == "SELL" and curr_p >= stats["sl"])

                if is_tp or is_sl:
                    res = REWARD_USD if is_tp else -RISK_USD
                    fee = (stats["qty"] * curr_p * COMMISSION_RATE) * 2
                    stats["balance"] += (res - fee)
                    stats["total_fees"] += fee
                    if is_tp: stats["wins"] += 1 
                    else: stats["losses"] += 1
                    stats["in_position"] = False
                    
                    msg = "✅ ПРОФИТ" if is_tp else "❌ СТОП"
                    send_tg(f"{msg}\nБаланс: *{round(stats['balance'], 2)}$*\n{stats['wins']}W - {stats['losses']}L")
                    time.sleep(10)

            # 2. ПОИСК ВХОДА (Только если не в позиции)
            else:
                h, l = get_channel_extrema()
                if h and l:
                    side = None
                    if curr_p >= h: side = "SELL" # Отбиваемся от верха
                    elif curr_p <= l: side = "BUY" # Отбиваемся от низа

                    if side:
                        stop_dist = curr_p * 0.005 # Фикс. дистанция стопа
                        stats["qty"] = RISK_USD / stop_dist
                        stats["side"] = side
                        
                        if side == "BUY":
                            stats["sl"], stats["tp"] = curr_p - stop_dist, curr_p + (stop_dist * 3)
                        else:
                            stats["sl"], stats["tp"] = curr_p + stop_dist, curr_p - (stop_dist * 3)

                        stats["in_position"] = True
                        send_tg(f"🚀 *ВХОД ОТ ГРАНИЦЫ: {side}*\nЦена: `{curr_p}`\nЦель TP: `{round(stats['tp'], 2)}`")

        except Exception as e:
            time.sleep(10)
        time.sleep(10)

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return f"Balance: {stats['balance']}", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
