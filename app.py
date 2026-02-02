import os, time, threading, pandas as pd, ccxt, telebot, json
from telebot import types
from flask import Flask
from datetime import datetime

# --- [КОНФИГ СНАЙПЕРА] ---
SYMBOLS = ['BNB/USDC', 'ETH/USDC', 'SOL/USDC', 'BTC/USDC', 'DOGE/USDC']
RISK_USD = 5.0
RR = 3
STOP_PCT = 0.005
BE_THRESHOLD = 0.003
TIME_LIMIT = 20
EMA_PERIOD = 30
MIN_EDGE = 0.33
MIN_SAMPLES = 2

# --- [ID КАНАЛА ДЛЯ ПАМЯТИ] ---
# Замени на ID своего канала, чтобы память была в отдельном месте
BACKUP_CHAT_ID = os.environ.get("CHAT_ID") 

# --- [ДАННЫЕ] ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

bot = telebot.TeleBot(TOKEN)
exchange = ccxt.binance({'options': {'defaultType': 'future'}})
app = Flask(__name__)

stats = {"balance": 1000.0, "wins": 0, "losses": 0}
cond_stats = {}
active_trades = []
RUNNING = True
MODE = "paper"

# --- [ФУНКЦИИ ПАМЯТИ] ---

def save_memory():
    """Отправка слепка данных в Telegram"""
    try:
        data = {"stats": stats, "cond_stats": cond_stats}
        bot.send_message(BACKUP_CHAT_ID, f"#BACKUP\n{json.dumps(data)}")
    except Exception as e:
        print(f"Ошибка сохранения: {e}")

def load_memory():
    """Автоматическое восстановление при старте"""
    global stats, cond_stats
    try:
        print("🔄 Поиск бэкапа...")
        # Метод get_chat_history работает в личке или если бот админ в канале
        messages = bot.get_chat_history(BACKUP_CHAT_ID, limit=100)
        for msg in messages:
            if msg.text and msg.text.startswith("#BACKUP"):
                raw_data = msg.text.replace("#BACKUP\n", "")
                data = json.loads(raw_data)
                stats = data.get("stats", stats)
                cond_stats = data.get("cond_stats", cond_stats)
                bot.send_message(CHAT_ID, f"🧠 **Память восстановлена!**\nЗагружено паттернов: {len(cond_stats)}")
                return True
    except Exception as e:
        print(f"Ошибка загрузки: {e}")
    return False

# --- [ИНТЕРФЕЙС] ---

def get_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🚀 СТАРТ", callback_data="start"),
        types.InlineKeyboardButton("⏸ СТОП", callback_data="stop"),
        types.InlineKeyboardButton("📝 БУМАГА", callback_data="paper"),
        types.InlineKeyboardButton("💰 LIVE", callback_data="live"),
        types.InlineKeyboardButton("🧠 МОЗГ", callback_data="stats"),
        types.InlineKeyboardButton("📊 БАЛАНС", callback_data="balance")
    )
    return markup

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    global MODE, RUNNING
    if call.data == "paper": MODE = "paper"
    elif call.data == "live": MODE = "live"
    elif call.data == "start": RUNNING = True
    elif call.data == "stop": RUNNING = False
    elif call.data == "balance":
        bot.send_message(CHAT_ID, f"📊 Баланс: `{round(stats['balance'], 2)}$` | Сделок: {len(active_trades)}", reply_markup=get_main_menu())
    elif call.data == "stats":
        if not cond_stats:
            bot.send_message(CHAT_ID, "🧠 Мозг пуст...", reply_markup=get_main_menu())
            return
        res = "🧠 **АНАЛИЗ ПАТТЕРНОВ:**\n\n"
        for k, v in cond_stats.items():
            total = v['W'] + v['L'] + v['T']
            avg_t = round(v['total_time'] / total, 1) if total > 0 else 0
            wr = round(v['W'] / (v['W'] + v['L']) * 100, 1) if (v['W'] + v['L']) > 0 else 0
            res += f"● `{k}`\n   └ WR: {wr}% | ⏱ {avg_t} мин.\n"
        bot.send_message(CHAT_ID, res, reply_markup=get_main_menu())
    bot.answer_callback_query(call.id, f"Ок: {call.data}")

# --- [ЛОГИКА ТОРГОВЛИ] ---

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def bot_worker():
    global stats, active_trades
    while True:
        if RUNNING:
            # 1. ПРОВЕРКА ВЫХОДА
            for trade in active_trades[:]:
                try:
                    ticker = exchange.fetch_ticker(trade["sym"])
                    curr = ticker['last']
                    elapsed = (datetime.now() - trade["start_time"]).total_seconds() / 60
                    
                    if not trade["be_active"]:
                        dist = (curr - trade["entry"]) / trade["entry"] if trade["side"] == "BUY" else (trade["entry"] - curr) / trade["entry"]
                        if dist >= BE_THRESHOLD:
                            trade["sl"] = trade["entry"]
                            trade["be_active"] = True
                            bot.send_message(CHAT_ID, f"🛡 **БЕЗУБЫТОК** {trade['sym']}")

                    hit_tp = (trade["side"] == "BUY" and curr >= trade["tp"]) or (trade["side"] == "SELL" and curr <= trade["tp"])
                    hit_sl = (trade["side"] == "BUY" and curr <= trade["sl"]) or (trade["side"] == "SELL" and curr >= trade["sl"])
                    timeout = elapsed >= TIME_LIMIT

                    if hit_tp or hit_sl or timeout:
                        res_usd = 0
                        res_type = ""
                        if hit_tp: res_usd = RISK_USD * RR; res_type = "win"; txt = f"✅ ПРОФИТ {trade['sym']}"
                        elif hit_sl: res_usd = 0 if trade["be_active"] else -RISK_USD; res_type = "loss"; txt = f"❌ СТОП {trade['sym']}"
                        else:
                            pnl = (curr - trade["entry"]) / trade["entry"] if trade["side"] == "BUY" else (trade["entry"] - curr) / trade["entry"]
                            res_usd = (pnl / STOP_PCT) * RISK_USD
                            res_type = "timeout"; txt = f"⏰ ТАЙМ-АУТ {trade['sym']}"

                        k = trade["key"]
                        if k not in cond_stats: cond_stats[k] = {"W":0, "L":0, "T":0, "total_time": 0}
                        if res_type == "win": cond_stats[k]["W"] += 1
                        elif res_type == "loss": cond_stats[k]["L"] += 1
                        else: cond_stats[k]["T"] += 1
                        cond_stats[k]["total_time"] += elapsed

                        stats["balance"] += res_usd
                        active_trades.remove(trade)
                        bot.send_message(CHAT_ID, f"{txt}\n💰 Итог: {round(res_usd, 2)}$\n📊 Баланс: {round(stats['balance'], 2)}$", reply_markup=get_main_menu())
                        save_memory() # Сохраняем после каждой закрытой сделки
                except: pass

            # 2. ПОИСК ВХОДА
            trade_limit = 5 if MODE == "paper" else 1
            if len(active_trades) < trade_limit:
                for sym in SYMBOLS:
                    if any(t["sym"] == sym for t in active_trades): continue
                    if len(active_trades) >= trade_limit: break
                    try:
                        bars = exchange.fetch_ohlcv(sym, '1m', limit=50)
                        df = pd.DataFrame(bars, columns=['ts','o','h','l','c','v'])
                        curr = df['c'].iloc[-1]
                        df['ema'] = df['c'].ewm(span=EMA_PERIOD).mean()
                        df['rsi'] = calculate_rsi(df['c'])
                        df['vol_ema'] = df['v'].rolling(20).mean()
                        df['range'] = df['h'] - df['l']
                        df['range_ema'] = df['range'].rolling(20).mean()

                        ema, rsi = df['ema'].iloc[-1], df['rsi'].iloc[-1]
                        direction = "ВВЕРХ" if curr > ema else "ВНИЗ" if curr < ema else None
                        
                        if direction:
                            f_imp = "Имп" if abs(curr-ema)/ema >= 0.002 else "Вяло"
                            f_vol = "Вол" if (df['range'].iloc[-1] > df['range_ema'].iloc[-1]) else "Штиль"
                            f_mon = "Объем" if (df['v'].iloc[-1] > df['vol_ema'].iloc[-1]) else "Пусто"
                            f_rsi = "Перегрев" if (direction=="ВВЕРХ" and rsi > 70) or (direction=="ВНИЗ" and rsi < 30) else "Сила"
                            key = f"{sym.split('/')[0]}_{direction}_{f_imp}_{f_vol}_{datetime.utcnow().hour}_{f_mon}_{f_rsi}"
                            
                            rec = cond_stats.get(key, {"W":0, "L":0})
                            if (rec["W"]+rec["L"]) >= MIN_SAMPLES and (rec["W"]/(rec["W"]+rec["L"])) < MIN_EDGE: continue

                            stop = curr * STOP_PCT
                            active_trades.append({
                                "sym": sym, "side": "BUY" if direction=="ВВЕРХ" else "SELL",
                                "entry": curr, "sl": round(curr - stop if direction=="ВВЕРХ" else curr + stop, 4),
                                "tp": round(curr + stop*RR if direction=="ВВЕРХ" else curr - stop*RR, 4),
                                "key": key, "start_time": datetime.now(), "be_active": False
                            })
                            bot.send_message(CHAT_ID, f"🎯 **ВХОД {sym}**\nЦена: `{curr}`\n🔑: `{key}`", reply_markup=get_main_menu())
                    except: continue
        time.sleep(15)

@app.route('/')
def home(): return "Sniper v10.40 LifeCycle OK", 200

if __name__ == "__main__":
    # Загружаем память при старте
    load_memory()
    
    # Запуск бота
    threading.Thread(target=bot_worker, daemon=True).start()
    threading.Thread(target=lambda: bot.infinity_polling(), daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
