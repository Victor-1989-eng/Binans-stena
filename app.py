import os
import time
import threading
import pandas as pd
import ccxt
import requests
from flask import Flask

app = Flask(__name__)

# --- НАСТРОЙКИ СИМУЛЯТОРА ---
SYMBOL = 'BNB/USDC'
RISK_USD = 10.0   
REWARD_USD = 30.0 
COMMISSION_RATE = 0.0004 

# Расширенная статистика
stats = {
    "balance": 1000.0,
    "wins": 0,
    "losses": 0,
    "total_fees": 0.0,
    "in_position": False,
    "side": None,
    "entry_price": 0,
    "sl": 0,
    "tp": 0,
    "qty": 0
}

exchange = ccxt.binance({'options': {'defaultType': 'future'}})

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def get_virtual_side():
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        return "BUY" if ticker['last'] > ticker['open'] else "SELL"
    except: return "BUY"

def paper_worker():
    global stats
    send_tg("📝 *СИМУЛЯТОР 8.2.1 ЗАПУЩЕН*\nМатематика 1:3 в действии.")

    while True:
        try:
            ticker = exchange.fetch_ticker(SYMBOL)
            curr_p = ticker['last']

            if stats["in_position"]:
                side = stats["side"]
                is_tp = (side == "BUY" and curr_p >= stats["tp"]) or (side == "SELL" and curr_p <= stats["tp"])
                is_sl = (side == "BUY" and curr_p <= stats["sl"]) or (side == "SELL" and curr_p >= stats["sl"])

                if is_tp or is_sl:
                    # Считаем результат
                    raw_result = REWARD_USD if is_tp else -RISK_USD
                    fee = (stats["qty"] * curr_p * COMMISSION_RATE) * 2
                    net_result = raw_result - fee
                    
                    # Обновляем статы
                    stats["balance"] += net_result
                    stats["total_fees"] += fee
                    if is_tp: stats["wins"] += 1 
                    else: stats["losses"] += 1
                    
                    stats["in_position"] = False
                    
                    # ИТОГОВЫЙ ОТЧЕТ
                    status_icon = "💰 ПРОФИТ" if is_tp else "📉 СТОП-ЛОСС"
                    total_trades = stats["wins"] + stats["losses"]
                    win_rate = (stats["wins"] / total_trades) * 100
                    
                    report = (
                        f"{status_icon}\n"
                        f"Результат сделки: `{round(net_result, 2)}$` (с комиссией)\n"
                        f"--- --- --- ---\n"
                        f"📊 *ОТЧЕТ ПО ЦИКЛУ:*\n"
                        f"Всего сделок: `{total_trades}`\n"
                        f"Побед: `{stats['wins']}` | Поражений: `{stats['losses']}`\n"
                        f"Win Rate: `{round(win_rate, 1)}%`\n"
                        f"Уплачено комиссий: `{round(stats['total_fees'], 2)}$`\n"
                        f"Текущий баланс: *{round(stats['balance'], 2)}$*"
                    )
                    send_tg(report)
                    time.sleep(15) 

            else:
                # Вход в новую сделку
                side = get_virtual_side()
                stats["entry_price"] = curr_p
                stop_dist = curr_p * 0.005 
                stats["qty"] = RISK_USD / stop_dist
                
                if side == "BUY":
                    stats["sl"], stats["tp"] = curr_p - stop_dist, curr_p + (stop_dist * 3)
                else:
                    stats["sl"], stats["tp"] = curr_p + stop_dist, curr_p - (stop_dist * 3)

                stats["in_position"], stats["side"] = True, side
                send_tg(f"🚀 *НОВЫЙ ВХОД: {side}*\nЦена: `{curr_p}`\nTP: `{round(stats['tp'], 2)}` | SL: `{round(stats['sl'], 2)}`")

        except Exception as e:
            time.sleep(10)
        
        time.sleep(5)

threading.Thread(target=paper_worker, daemon=True).start()

@app.route('/')
def health(): return f"Stats: {stats['wins']}W / {stats['losses']}L", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
