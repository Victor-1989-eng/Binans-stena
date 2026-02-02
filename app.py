import os, time, random, threading, requests
import pandas as pd
import ccxt
from flask import Flask
from collections import defaultdict

app = Flask(__name__)

SYMBOL = 'BNB/USDC'
RISK_USD = 5.0
RR = 3
STOP_PCT = 0.005
EMA_PERIOD = 30
MIN_EDGE = 0.33
MIN_SAMPLES = 10

stats = {
    "balance": 1000.0,
    "wins": 0, "losses": 0,
    "in_position": False, "side": None,
    "sl": 0, "tp": 0, "qty": 0,
    "last_key": None
}

cond_stats = defaultdict(lambda: {"W":0,"L":0})

exchange = ccxt.binance({'options': {'defaultType': 'future'}})

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text})

def get_df():
    bars = exchange.fetch_ohlcv(SYMBOL, '1m', limit=50)
    return pd.DataFrame(bars, columns=['ts','o','h','l','c','v'])

def bot():
    send_tg("🤖 v9.4 САМОOБУЧАЮЩИЙСЯ СНАЙПЕР ЗАПУЩЕН")
    while True:
        try:
            df = get_df()
            curr = df['c'].iloc[-1]
            ema = df['c'].ewm(span=EMA_PERIOD).mean().iloc[-1]

            if stats["in_position"]:
                side = stats["side"]
                hit_tp = (side=="BUY" and curr>=stats["tp"]) or (side=="SELL" and curr<=stats["tp"])
                hit_sl = (side=="BUY" and curr<=stats["sl"]) or (side=="SELL" and curr>=stats["sl"])

                if hit_tp or hit_sl:
                    win = hit_tp
                    key = stats["last_key"]
                    if key:
                        if win: cond_stats[key]["W"] += 1
                        else: cond_stats[key]["L"] += 1

                    stats["balance"] += RISK_USD*RR if win else -RISK_USD
                    if win: stats["wins"]+=1
                    else: stats["losses"]+=1
                    stats["in_position"]=False

                    send_tg(f"{'✅ ПРОФИТ' if win else '❌ СТОП'}\nБаланс: {round(stats['balance'],2)}$\n{stats['wins']}W - {stats['losses']}L")

            else:
                if random.random() < 1/3:
                    dist = abs(curr-ema)/ema
                    far = dist >= 0.002

                    closes = df['c'].tail(4).values
                    impulse_up = closes[-1]>closes[-2]>closes[-3]
                    impulse_down = closes[-1]<closes[-2]<closes[-3]

                    rng = (df['h'].tail(10).max() - df['l'].tail(10).min()) / curr
                    range_big = rng >= 0.006

                    if curr > ema and impulse_up:
                        side="BUY"; trend="up"; impulse="yes"
                    elif curr < ema and impulse_down:
                        side="SELL"; trend="down"; impulse="yes"
                    else:
                        continue

                    key = f"{trend}_{impulse}_{far}_{range_big}"
                    rec = cond_stats[key]
                    total = rec["W"] + rec["L"]

                    if total >= MIN_SAMPLES:
                        p = rec["W"]/total
                        if p < MIN_EDGE:
                            continue

                    stop = curr * STOP_PCT
                    stats["side"]=side
                    stats["sl"]= curr-stop if side=="BUY" else curr+stop
                    stats["tp"]= curr+stop*RR if side=="BUY" else curr-stop*RR
                    stats["in_position"]=True
                    stats["last_key"]=key

                    send_tg(f"🎯 ВХОД {side}\nЦена: {curr}\nTP: {round(stats['tp'],2)} SL: {round(stats['sl'],2)}\nКлюч: {key}")
        except:
            time.sleep(5)
        time.sleep(10)

threading.Thread(target=bot, daemon=True).start()

@app.route('/')
def health(): return f"Balance: {stats['balance']}", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
