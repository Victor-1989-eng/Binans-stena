import os, time, random, threading, json, requests
import pandas as pd
import ccxt
import numpy as np
from flask import Flask, request

app = Flask(__name__)

# --- [КОНФИГУРАЦИЯ] ---
SYMBOL = 'BNB/USDC'
RISK_USD = 5.0      
RR = 3              
STOP_PCT = 0.005    
EMA_PERIOD = 30
MIN_EDGE = 0.33     
MIN_SAMPLES = 10    
LEVERAGE = 50

# --- [ПАМЯТЬ И СТАТИСТИКА] ---
STATS_FILE = "cond_stats.json"
if os.path.exists(STATS_FILE):
    with open(STATS_FILE, "r") as f:
        cond_stats = json.load(f)
else:
    cond_stats = {}

stats = {
    "balance": 1000.0, "wins": 0, "losses": 0,
    "in_position": False, "side": None, 
    "sl": 0, "tp": 0, "qty": 0, "last_key": None
}

# --- [API И СОЕДИНЕНИЯ] ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
MODE = "paper"
RUNNING = True

exchange = ccxt.binance({
    'apiKey': os.environ.get("BINANCE_API_KEY"),
    'secret': os.environ.get("BINANCE_API_SECRET"),
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# --- [ИНТЕРФЕЙС TELEGRAM] ---
def send_tg(text, buttons=None):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload)
    except: pass

def get_buttons():
    return [
        [{"text": "🚀 Start", "callback_data": "start"}, {"text": "⏸ Stop", "callback_data": "stop"}],
        [{"text": "📝 Paper", "callback_data": "paper"}, {"text": "💰 Live", "callback_data": "live"}],
        [{"text": "🧠 Мозг (Ключи)", "callback_data": "stats"}, {"text": "📊 Баланс", "callback_data": "balance"}]
    ]

# --- [ЯДРО БОТА] ---
def bot_worker():
    global RUNNING, MODE
    # Ждем запуска сервера, прежде чем слать приветствие
    time.sleep(5)
    send_tg(f"🤖 **v10.2 QUANT SNIPER ОЖИЛ**\nНапиши /start для меню.", buttons=get_buttons())
    
    while True:
        if not RUNNING:
            time.sleep(5); continue

        try:
            bars = exchange.fetch_ohlcv(SYMBOL, '1m', limit=100)
            df = pd.DataFrame(bars, columns=['ts','o','h','l','c','v'])
            curr = df['c'].iloc[-1]
            ema = df['c'].ewm(span=EMA_PERIOD).mean().iloc[-1]

            # 1. ПРОВЕРКА ВЫХОДА (Для имитации в Paper)
            if stats["in_position"]:
                side = stats["side"]
                hit_tp = (side == "BUY" and curr >= stats["tp"]) or (side == "SELL" and curr <= stats["tp"])
                hit_sl = (side == "BUY" and curr <= stats["sl"]) or (side == "SELL" and curr >= stats["sl"])

                if hit_tp or hit_sl:
                    win = hit_tp
                    key = stats["last_key"]
                    if key:
                        if key not in cond_stats: cond_stats[key] = {"W": 0, "L": 0}
                        if win: cond_stats[key]["W"] += 1
                        else: cond_stats[key]["L"] += 1
                        with open(STATS_FILE, "w") as f: json.dump(cond_stats, f)

                    stats["balance"] += (RISK_USD * RR) if win else -RISK_USD
                    if win: stats["wins"] += 1
                    else: stats["losses"] += 1
                    stats["in_position"] = False
                    
                    res = "✅ PROFIT" if win else "❌ STOP"
                    send_tg(f"{res}\nКлюч: `{key}`\nБаланс: `{round(stats['balance'], 2)}$`", buttons=get_buttons())

            # 2. ПОИСК СИГНАЛА
            else:
                closes = df['c'].tail(4).values
                imp_up = closes[-1] > closes[-2] > closes[-3]
                imp_down = closes[-1] < closes[-2] < closes[-3]
                dist = abs(curr - ema) / ema
                far = dist >= 0.002
                rng = (df['h'].tail(10).max() - df['l'].tail(10).min()) / curr
                r_big = rng >= 0.006

                side = "BUY" if (curr > ema and imp_up) else "SELL" if (curr < ema and imp_down) else None
                
                if side:
                    key = f"{side.lower()}_f{far}_r{r_big}"
                    rec = cond_stats.get(key, {"W": 0, "L": 0})
                    total = rec["W"] + rec["L"]
                    
                    if total >= MIN_SAMPLES and (rec["W"] / total) < MIN_EDGE:
                        continue

                    stop_dist = curr * STOP_PCT
                    stats.update({
                        "side": side, "last_key": key, "in_position": True,
                        "sl": curr - stop_dist if side == "BUY" else curr + stop_dist,
                        "tp": curr + (stop_dist * RR) if side == "BUY" else curr - (stop_dist * RR)
                    })

                    if MODE == "live":
                        try:
                            exchange.set_leverage(LEVERAGE, SYMBOL)
                            qty = float(exchange.amount_to_precision(SYMBOL, RISK_USD / stop_dist))
                            exchange.create_market_order(SYMBOL, side.lower(), qty)
                            opp = 'sell' if side == "BUY" else 'buy'
                            exchange.create_order(SYMBOL, 'STOP_MARKET', opp, qty, params={'stopPrice': stats["sl"], 'reduceOnly': True})
                            exchange.create_order(SYMBOL, 'TAKE_PROFIT_MARKET', opp, qty, params={'stopPrice': stats["tp"], 'reduceOnly': True})
                        except Exception as e:
                            send_tg(f"🛑 LIVE ERROR: {e}")

                    send_tg(f"🎯 **ВХОД {side}**\nKey: `{key}`\nPrice: `{curr}`\nMode: `{MODE}`", buttons=get_buttons())

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)
        time.sleep(15)

# --- [ОБРАБОТКА WEBHOOK (СООБЩЕНИЯ + КНОПКИ)] ---
@app.route('/webhook', methods=['POST'])
def webhook():
    global MODE, RUNNING
    data = request.json
    
    # Реакция на текстовые сообщения (команда /start)
    if "message" in data:
        msg = data["message"]
        if msg.get("text") == "/start":
            send_tg("🚀 **Квантовый Снайпер v10.2**\nВыбери режим работы:", buttons=get_buttons())
            return "ok", 200

    # Реакция на нажатия кнопок
    if "callback_query" in data:
        cb = data["callback_query"]
        action = cb["data"]
        
        if action == "start": RUNNING = True
        elif action == "stop": RUNNING = False
        elif action == "paper": MODE = "paper"
        elif action == "live": MODE = "live"
        elif action == "balance":
            send_tg(f"📈 **ТЕКУЩИЙ СЧЕТ:**\nБаланс: `{round(stats['balance'], 2)}$`\nW/L: {stats['wins']}/{stats['losses']}")
        elif action == "stats":
            if not cond_stats:
                send_tg("🧠 Мозг пуст. Нужно больше сделок.")
            else:
                report = "🧠 **АНАЛИЗ ПАТТЕРНОВ:**\n"
                sorted_keys = sorted(cond_stats.items(), key=lambda x: x[1]['W']/(x[1]['W']+x[1]['L']) if (x[1]['W']+x[1]['L'])>0 else 0, reverse=True)
                for k, v in sorted_keys[:10]:
                    total = v['W'] + v['L']
                    wr = round(v['W'] / total * 100, 1) if total > 0 else 0
                    report += f"`{k}`: *{wr}%* (n={total})\n"
                send_tg(report)
        
        if action in ["start", "stop", "paper", "live"]:
            send_tg(f"⚙️ Статус обновлен: `{action.upper()}`", buttons=get_buttons())
    return "ok", 200

@app.route('/')
def health(): return f"Quant Sniper Active. Mode: {MODE}", 200

if __name__ == "__main__":
    threading.Thread(target=bot_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
