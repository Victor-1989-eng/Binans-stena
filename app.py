import os, time, threading, json
import pandas as pd
import ccxt
import telebot
from telebot import types
from flask import Flask

app = Flask(__name__)

# --- [КОНФИГУРАЦИЯ] ---
SYMBOL = 'BNB/USDC'
RISK_USD = 5.0
RR = 3
STOP_PCT = 0.005
EMA_PERIOD = 30
MIN_EDGE = 0.33
MIN_SAMPLES = 10

# --- [ПАМЯТЬ] ---
STATS_FILE = "cond_stats.json"
cond_stats = json.load(open(STATS_FILE)) if os.path.exists(STATS_FILE) else {}
stats = {"balance": 1000.0, "wins": 0, "losses": 0, "in_position": False, "side": None, "sl": 0, "tp": 0, "last_key": None}

# --- [API] ---
bot = telebot.TeleBot(os.environ.get("TELEGRAM_TOKEN"))
CHAT_ID = os.environ.get("CHAT_ID")
MODE = "paper"
RUNNING = True

exchange = ccxt.binance({'options': {'defaultType': 'future'}})

# --- [КНОПКИ] ---
def get_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🚀 Start", callback_data="start"),
               types.InlineKeyboardButton("⏸ Stop", callback_data="stop"),
               types.InlineKeyboardButton("📝 Paper", callback_data="paper"),
               types.InlineKeyboardButton("💰 Live", callback_data="live"),
               types.InlineKeyboardButton("🧠 Мозг", callback_data="stats"),
               types.InlineKeyboardButton("📊 Баланс", callback_data="balance"))
    return markup

# --- [ОБРАБОТКА ТЕЛЕГРАМ] ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.send_message(message.chat.id, "🎯 **Sniper v10.4 Онлайн**", reply_markup=get_main_menu())

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    global MODE, RUNNING
    action = call.data
    if action == "start": RUNNING = True
    elif action == "stop": RUNNING = False
    elif action == "paper": MODE = "paper"
    elif action == "live": MODE = "live"
    elif action == "balance":
        bot.send_message(CHAT_ID, f"📊 Баланс: {round(stats['balance'], 2)}$\nW/L: {stats['wins']}/{stats['losses']}")
    elif action == "stats":
        bot.send_message(CHAT_ID, f"🧠 Паттернов в базе: {len(cond_stats)}")
    
    bot.answer_callback_query(call.id, f"Выбрано: {action}")

# --- [ЛОГИКА ТОРГОВЛИ] ---
def bot_worker():
    while True:
        if RUNNING:
            try:
                bars = exchange.fetch_ohlcv(SYMBOL, '1m', limit=60)
                df = pd.DataFrame(bars, columns=['ts','o','h','l','c','v'])
                curr = df['c'].iloc[-1]
                ema = df['c'].ewm(span=EMA_PERIOD).mean().iloc[-1]

                if stats["in_position"]:
                    side = stats["side"]
                    hit_tp = (side == "BUY" and curr >= stats["tp"]) or (side == "SELL" and curr <= stats["tp"])
                    hit_sl = (side == "BUY" and curr <= stats["sl"]) or (side == "SELL" and curr >= stats["sl"])
                    
                    if hit_tp or hit_sl:
                        win = hit_tp
                        key = stats["last_key"]
                        if key:
                            if key not in cond_stats: cond_stats[key] = {"W":0,"L":0}
                            if win: cond_stats[key]["W"] += 1
                            else: cond_stats[key]["L"] += 1
                            with open(STATS_FILE, "w") as f: json.dump(cond_stats, f)
                        
                        stats["balance"] += (RISK_USD * RR) if win else -RISK_USD
                        if win: stats["wins"]+=1 
                        else: stats["losses"]+=1
                        stats["in_position"] = False
                        bot.send_message(CHAT_ID, f"{'✅ PROFIT' if win else '❌ STOP'}\nБаланс: {round(stats['balance'],2)}$", reply_markup=get_main_menu())
                
                else:
                    closes = df['c'].tail(3).values
                    imp_up = closes[-1] > closes[-2] > closes[-3]
                    imp_down = closes[-1] < closes[-2] < closes[-3]
                    
                    side = "BUY" if (curr > ema and imp_up) else "SELL" if (curr < ema and imp_down) else None
                    if side:
                        key = f"{side.lower()}_f{abs(curr-ema)/ema >= 0.002}"
                        rec = cond_stats.get(key, {"W":0,"L":0})
                        if (rec["W"]+rec["L"]) >= MIN_SAMPLES and (rec["W"]/(rec["W"]+rec["L"])) < MIN_EDGE: continue

                        stop = curr * STOP_PCT
                        stats.update({"side":side, "last_key":key, "in_position":True, "sl":curr-stop if side=="BUY" else curr+stop, "tp":curr+stop*RR if side=="BUY" else curr-stop*RR})
                        bot.send_message(CHAT_ID, f"🎯 Вход {side}\nКлюч: {key}", reply_markup=get_main_menu())
            except Exception as e: print(f"Trade Error: {e}")
        time.sleep(15)

# --- [ЗАПУСК] ---
@app.route('/')
def health(): return "OK", 200

if __name__ == "__main__":
    # Запуск торгового потока
    threading.Thread(target=bot_worker, daemon=True).start()
    # Запуск бота (Polling)
    threading.Thread(target=lambda: bot.infinity_polling(), daemon=True).start()
    # Flask для Render
    app.run(host="0.0.0.0", port=10000)
