import os
import time
import threading
import ccxt
import requests
import random
from flask import Flask

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNB/USDC'
RISK_USD = 5.0        # маленький риск на сделку
COMMISSION_RATE = 0.0004
STOP_PERCENT = 0.005   # 0.5%
RR = 3                 # тейк = стоп * 3

stats = {
    "balance": 1000.0,
    "wins": 0,
    "losses": 0,
    "total_fees": 0.0,
    "in_position": False,
    "side": None,
    "sl": 0,
    "tp": 0,
    "qty": 0
}

exchange = ccxt.binance({'options': {'defaultType': 'future'}, 'enableRateLimit': True})

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
            )
        except:
            pass

def bot_worker():
    global stats
    send_tg("🎲 *МИНИ-РУЛЕТКА 1:3 ЗАПУЩЕНА*\nРиск на сделку 5$ | Стоп 0.5% | TP ×3 | Вход случайный 1/3")

    while True:
        try:
            curr_p = exchange.fetch_ticker(SYMBOL)['last']

            # --- Закрытие позиции ---
            if stats["in_position"]:
                side = stats["side"]
                is_tp = (side == "BUY" and curr_p >= stats["tp"]) or (side == "SELL" and curr_p <= stats["tp"])
                is_sl = (side == "BUY" and curr_p <= stats["sl"]) or (side == "SELL" and curr_p >= stats["sl"])

                if is_tp or is_sl:
                    res = (RISK_USD * RR) if is_tp else -RISK_USD
                    fee = (stats["qty"] * curr_p * COMMISSION_RATE) * 2
                    stats["balance"] += (res - fee)
                    stats["total_fees"] += fee
                    if is_tp: stats["wins"] += 1
                    else: stats["losses"] += 1
                    stats["in_position"] = False

                    msg = "✅ ПРОФИТ" if is_tp else "❌ СТОП"
                    send_tg(f"{msg}\nБаланс: *{round(stats['balance'],2)}$*\n{stats['wins']}W - {stats['losses']}L")
                    time.sleep(5)

            # --- Случайный вход 1/3 ---
            else:
                if random.random() < 1/3:
                    side = random.choice(["BUY","SELL"])
                    stop_dist = curr_p * STOP_PERCENT
                    stats["qty"] = RISK_USD / stop_dist
                    stats["side"] = side

                    if side == "BUY":
                        stats["sl"], stats["tp"] = curr_p - stop_dist, curr_p + stop_dist*RR
                    else:
                        stats["sl"], stats["tp"] = curr_p + stop_dist, curr_p - stop_dist*RR

                    stats["in_position"] = True
                    send_tg(f"🎲 Мини-вход: {side}\nЦена: {curr_p}\nTP: {round(stats['tp'],2)} | SL: {round(stats['sl'],2)}")

        except Exception:
            time.sleep(5)

        time.sleep(10)

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health():
    return f"Balance: {stats['balance']}", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
