import os, time, threading, pandas as pd, ccxt, telebot, json
from telebot import types
from flask import Flask
from datetime import datetime

# --- [КОНФИГ] ---
SYMBOLS = ['BNB/USDC', 'ETH/USDC', 'SOL/USDC', 'BTC/USDC', 'DOGE/USDC']
RISK_USD = 5.0
RR = 3
STOP_PCT = 0.005
BE_THRESHOLD = 0.003
TIME_LIMIT = 20
EMA_PERIOD = 30
MIN_EDGE = 0.33
MIN_SAMPLES = 2

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BACKUP_CHAT_ID = os.environ.get("BACKUP_CHAT_ID") or CHAT_ID

bot = telebot.TeleBot(TOKEN)
exchange = ccxt.binance({'options': {'defaultType': 'future'}})
app = Flask(__name__)

stats = {"balance": 1000.0, "wins": 0, "losses": 0}
cond_stats = {}
active_trades = []
RUNNING = True
MODE = "paper"

# --- [СИСТЕМА ПАМЯТИ (PIN)] ---
def save_memory():
    try:
        data = {"stats": stats, "cond_stats": cond_stats}
        # 1. Отправляем
        msg = bot.send_message(BACKUP_CHAT_ID, f"#BACKUP\n{json.dumps(data)}")
        
        # 2. Пытаемся закрепить
        try:
            bot.pin_chat_message(BACKUP_CHAT_ID, msg.message_id)
        except Exception as e:
            # Если не вышло закрепить - сообщаем пользователю, но не падаем
            print(f"⚠️ Не могу закрепить: {e}")
            if "not enough rights" in str(e) or "administrator" in str(e):
                bot.send_message(CHAT_ID, "⚠️ **ВНИМАНИЕ:** Бот не может закрепить сообщение!\nСделайте бота **Администратором** канала бэкапов и включите право **'Pin Messages'**.")

    except Exception as e:
        print(f"Ошибка сохранения: {e}")

def load_memory():
    global stats, cond_stats
    try:
        print(f"🔄 Проверяю закреп в канале {BACKUP_CHAT_ID}...")
        
        try:
            msg = bot.get_chat_pinned_message(BACKUP_CHAT_ID)
        except Exception as e:
            print(f"⚠️ Ошибка получения закрепа: {e}")
            msg = None

        if msg and msg.text and "#BACKUP" in msg.text:
            start_index = msg.text.find('{')
            if start_index != -1:
                json_str = msg.text[start_index:]
                data = json.loads(json_str)
                stats = data.get("stats", stats)
                cond_stats = data.get("cond_stats", cond_stats)
                bot.send_message(CHAT_ID, f"🧠 Память загружена!\nБаланс: {round(stats['balance'], 2)}$\nПаттернов: {len(cond_stats)}")
                return True
        else:
            print("⚠️ Нет закрепленного сообщения с бэкапом.")
            bot.send_message(CHAT_ID, "ℹ️ Память чиста (нет закрепа). Начинаю с нуля.")
            
    except Exception as e:
        print(f"⚠️ Глобальная ошибка загрузки: {e}")
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

@bot.message_handler(commands=['start', 'menu'])
def send_menu(message):
    bot.send_message(message.chat.id, f"🎮 Sniper v10.75 | Режим: {MODE.upper()}", reply_markup=get_main_menu())

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    global MODE, RUNNING
    if call.data == "paper": MODE = "paper"
    elif call.data == "live": MODE = "live"
    elif call.data == "start": RUNNING = True
    elif call.data == "stop": RUNNING = False
    elif call.data == "balance":
        bot.send_message(CHAT_ID, f"📊 Баланс: `{round(stats['balance'], 2)}$` | Открыто: {len(active_trades)}")
    elif call.data == "stats":
        if not cond_stats: bot.send_message(CHAT_ID, "🧠 Мозг пуст."); return
        res = "🧠 **АНАЛИЗ ПАТТЕРНОВ:**\n"
        for k, v in list(cond_stats.items())[-15:]:
            total = v['W'] + v['L'] + v['T']
            wr = round(v['W'] / (v['W'] + v['L']) * 100, 1) if (v['W'] + v['L']) > 0 else 0
            res += f"● `{k}`: {wr}% WR | {total} сд.\n"
        bot.send_message(CHAT_ID, res)
    bot.answer_callback_query(call.id, "Ок")

# --- [ЯДРО] ---
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def bot_worker():
    global stats, active_trades
    while True:
        if RUNNING:
            # 1. УПРАВЛЕНИЕ
            for trade in active_trades[:]:
                try:
                    ticker = exchange.fetch_ticker(trade["sym"])
                    curr = ticker['last']
                    elapsed = (datetime.now() - trade["start_time"]).total_seconds() / 60
                    
                    if not trade["be_active"]:
                        dist = (curr - trade["entry"]) / trade["entry"] if trade["side"] == "BUY" else (trade["entry"] - curr) / trade["entry"]
                        if dist >= BE_THRESHOLD:
                            trade["sl"] = trade["entry"]; trade["be_active"] = True
                            bot.send_message(CHAT_ID, f"🛡 **БЕЗУБЫТОК** {trade['sym']}")

                    hit_tp = (trade["side"] == "BUY" and curr >= trade["tp"]) or (trade["side"] == "SELL" and curr <= trade["tp"])
                    hit_sl = (trade["side"] == "BUY" and curr <= trade["sl"]) or (trade["side"] == "SELL" and curr >= trade["sl"])
                    
                    is_in_profit = (trade["side"] == "BUY" and curr > trade["entry"]) or (trade["side"] == "SELL" and curr < trade["entry"])
                    timeout = (elapsed >= TIME_LIMIT) and not is_in_profit

                    if hit_tp or hit_sl or timeout:
                        res_usd = 0; res_type = ""
                        if hit_tp: res_usd = RISK_USD * RR; res_type = "win"; txt = f"✅ ПРОФИТ {trade['sym']}"
                        elif hit_sl: res_usd = 0 if trade["be_active"] else -RISK_USD; res_type = "loss"; txt = f"❌ СТОП {trade['sym']}"
                        else:
                            pnl = (curr - trade["entry"]) / trade["entry"] if trade["side"] == "BUY" else (trade["entry"] - curr) / trade["entry"]
                            res_usd = (pnl / STOP_PCT) * RISK_USD; res_type = "timeout"; txt = f"⏰ ТАЙМ-АУТ {trade['sym']}"

                        k = trade["key"]; cond_stats.setdefault(k, {"W":0, "L":0, "T":0, "total_time": 0})
                        if res_type == "win": cond_stats[k]["W"] += 1
                        elif res_type == "loss": cond_stats[k]["L"] += 1
                        else: cond_stats[k]["T"] += 1
                        cond_stats[k]["total_time"] += elapsed
                        
                        stats["balance"] += res_usd
                        active_trades.remove(trade)
                        bot.send_message(CHAT_ID, f"{txt}\n💰 {round(res_usd, 2)}$ | Бал: {round(stats['balance'], 2)}$")
                        save_memory()
                except: pass

            # 2. ПОИСК
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
                        ema = df['ema'].iloc[-1]
                        
                        direction = "ВВЕРХ" if curr > ema else "ВНИЗ"
                        f_imp = "Имп" if abs(curr-ema)/ema >= 0.002 else "Вяло"
                        key = f"{sym.split('/')[0]}_{direction}_{f_imp}_{datetime.utcnow().hour}"
                        
                        rec = cond_stats.get(key, {"W":0, "L":0})
                        if (rec["W"] + rec["L"]) >= MIN_SAMPLES:
                            if (rec["W"] / (rec["W"] + rec["L"])) < MIN_EDGE: continue

                        stop = curr * STOP_PCT
                        active_trades.append({
                            "sym": sym, "side": "BUY" if direction=="ВВЕРХ" else "SELL", "entry": curr,
                            "sl": round(curr - stop if direction=="ВВЕРХ" else curr + stop, 4),
                            "tp": round(curr + stop*RR if direction=="ВВЕРХ" else curr - stop*RR, 4),
                            "key": key, "start_time": datetime.now(), "be_active": False
                        })
                        bot.send_message(CHAT_ID, f"🎯 **ВХОД {sym}**\nЦена: `{curr}`\n🔑: `{key}`")
                    except: continue
        time.sleep(15)

@app.route('/')
def home(): return "v10.75 Robust OK", 200

if __name__ == "__main__":
    load_memory()
    threading.Thread(target=bot_worker, daemon=True).start()
    threading.Thread(target=lambda: bot.infinity_polling(), daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
