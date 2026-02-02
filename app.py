import os, time, threading, json, requests
import pandas as pd
import ccxt
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

# --- [ПАМЯТЬ] ---
STATS_FILE = "cond_stats.json"
cond_stats = json.load(open(STATS_FILE)) if os.path.exists(STATS_FILE) else {}

stats = {
    "balance": 1000.0, "wins": 0, "losses": 0,
    "in_position": False, "side": None, "sl": 0, "tp": 0, "last_key": None
}

# --- [API] ---
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

# --- [ИСПРАВЛЕННАЯ ФУНКЦИЯ ОТПРАВКИ] ---
def send_tg(text, buttons=None):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("!!! ОШИБКА: TELEGRAM_TOKEN или CHAT_ID не установлены в Environment Variables")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    
    try:
        r = requests.post(url, data=payload, timeout=10)
        if not r.json().get("ok"):
            print(f"!!! Telegram API Error: {r.text}")
    except Exception as e:
        print(f"!!! Request Error: {e}")

def get_buttons():
    return [
        [{"text": "🚀 Start", "callback_data": "start"}, {"text": "⏸ Stop", "callback_data": "stop"}],
        [{"text": "📝 Paper", "callback_data": "paper"}, {"text": "💰 Live", "callback_data": "live"}],
        [{"text": "🧠 Мозг", "callback_data": "stats"}, {"text": "📊 Баланс", "callback_data": "balance"}]
    ]

# --- [ОСНОВНОЙ ЦИКЛ] ---
def bot_worker():
    print("--- Поток бота запущен ---")
    time.sleep(10) # Даем серверу прогрузиться
    send_tg("✅ **Система Sniper v10.3 онлайн!**\nНапиши /start для меню.", buttons=get_buttons())
    
    while True:
        if not RUNNING:
            time.sleep(5); continue
        try:
            bars = exchange.fetch_ohlcv(SYMBOL, '1m', limit=100)
            df = pd.DataFrame(bars, columns=['ts','o','h','l','c','v'])
            curr = df['c'].iloc[-1]
            ema = df['c'].ewm(span=EMA_PERIOD).mean().iloc[-1]

            if stats["in_position"]:
                # Логика проверки выхода (TP/SL)
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
                    send_tg(f"{'✅ PROFIT' if win else '❌ STOP'}\nБаланс: {round(stats['balance'], 2)}$", buttons=get_buttons())

            else:
                # Поиск сигнала (простая стратегия импульса)
                closes = df['c'].tail(4).values
                imp_up = closes[-1] > closes[-2] > closes[-3]
                imp_down = closes[-1] < closes[-2] < closes[-3]
                
                side = "BUY" if (curr > ema and imp_up) else "SELL" if (curr < ema and imp_down) else None
                
                if side:
                    key = f"{side.lower()}_f{abs(curr-ema)/ema >= 0.002}"
                    # Проверка MIN_EDGE
                    rec = cond_stats.get(key, {"W": 0, "L": 0})
                    if (rec["W"]+rec["L"]) >= MIN_SAMPLES and (rec["W"]/(rec["W"]+rec["L"])) < MIN_EDGE: continue

                    stop_dist = curr * STOP_PCT
                    stats.update({
                        "side": side, "last_key": key, "in_position": True,
                        "sl": curr - stop_dist if side == "BUY" else curr + stop_dist,
                        "tp": curr + (stop_dist * RR) if side == "BUY" else curr - (stop_dist * RR)
                    })
                    send_tg(f"🎯 **ВХОД {side}**\nКлюч: `{key}`\nЦена: {curr}", buttons=get_buttons())

        except Exception as e:
            print(f"Ошибка в цикле: {e}")
        time.sleep(20)

# --- [ВЕБХУК] ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data: return "no data", 400
    
    # 1. Если это текстовое сообщение
    if "message" in data:
        if data["message"].get("text") == "/start":
            send_tg("🚀 **Меню управления квантовым ботом:**", buttons=get_buttons())

    # 2. Если это нажатие кнопки
    elif "callback_query" in data:
        cb = data["callback_query"]
        action = cb["data"]
        global MODE, RUNNING
        
        if action == "start": RUNNING = True
        elif action == "stop": RUNNING = False
        elif action == "paper": MODE = "paper"
        elif action == "live": MODE = "live"
        elif action == "balance":
            send_tg(f"📊 **Баланс:** {round(stats['balance'], 2)}$\nW/L: {stats['wins']}/{stats['losses']}")
        elif action == "stats":
            send_tg(f"🧠 **База знаний:** {len(cond_stats)} паттернов.")
        
        send_tg(f"✅ Команда `{action}` принята.", buttons=get_buttons())
        
    return "ok", 200

@app.route('/')
def health(): return "Bot is Alive", 200

if __name__ == "__main__":
    threading.Thread(target=bot_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
