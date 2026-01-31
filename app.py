import os, time, threading, numpy as np
import telebot
from flask import Flask
from binance.client import Client
from telebot import types

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOLS = [
    'BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'ZECUSDC', 'LTCUSDC', 'LINKUSDC', 'ADAUSDC', 
    'XRPUSDC', 'DOTUSDC', 'AVAXUSDC', 'BNBUSDC', 'MATICUSDC', 'UNIUSDC', 'BCHUSDC',
    'NEARUSDC', 'TIAUSDC', 'ARBUSDC', 'OPUSDC', 'INJUSDC', 'DOGEUSDC'
]
LEVERAGE = 75
RISK_USD = 1.0
Z_THRESHOLD = 2.0  # У тебя сейчас стоит Агрессивный
LOCK_FILE = "/tmp/bot.lock"

bot = telebot.TeleBot(os.environ.get("TELEGRAM_TOKEN"))
chat_id = os.environ.get("CHAT_ID")

def get_symbol_stats(client, symbol):
    try:
        klines = client.futures_klines(symbol=symbol, interval='1m', limit=60)
        closes = np.array([float(k[4]) for k in klines])
        curr_p = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        std = np.std(closes)
        z = (curr_p - np.mean(closes)) / std if std != 0 else 0
        return z, curr_p
    except: return 0, 0

# --- ИНТЕРФЕЙС ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📊 Статус', '🔥 Аномалии')
    markup.add('⚙️ Агрессивный (Z=2)', '🛡 Консервативный (Z=3)')
    bot.reply_to(message, "Sniper v.18.2 запущен.\n20 пар под прицелом.", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_buttons(message):
    global Z_THRESHOLD
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    
    if message.text == '📊 Статус':
        bot.send_message(chat_id, f"📡 Мониторинг: 20 пар\nПлечо: x{LEVERAGE}\nТекущий Z: {Z_THRESHOLD}")
    
    elif message.text == '🔥 Аномалии':
        bot.send_message(chat_id, "🔍 Проверяю радары...")
        all_z = []
        for s in SYMBOLS:
            z, _ = get_symbol_stats(client, s)
            all_z.append({'s': s, 'z': z})
        all_z.sort(key=lambda x: abs(x['z']), reverse=True)
        
        msg = "🚀 **ТОП ОТКЛОНЕНИЙ:**\n\n"
        for i in all_z[:5]:
            emo = "📈" if i['z'] > 0 else "📉"
            status = "🔥 ВХОД!" if abs(i['z']) >= Z_THRESHOLD else "⏳ Ждем"
            msg += f"{emo} `{i['s']}`: `{i['z']:.2f}` ({status})\n"
        bot.send_message(chat_id, msg, parse_mode="Markdown")

    elif message.text == '⚙️ Агрессивный (Z=2)':
        Z_THRESHOLD = 2.0
        bot.send_message(chat_id, "🚀 Режим: Агрессивный")
    elif message.text == '🛡 Консервативный (Z=3)':
        Z_THRESHOLD = 3.0
        bot.send_message(chat_id, "🛡 Режим: Консервативный")

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    if os.path.exists(LOCK_FILE): return
    with open(LOCK_FILE, "w") as f: f.write("lock")

    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    
    try:
        while True:
            # Проверяем, нет ли открытых сделок
            pos = client.futures_position_information()
            if not any(float(p['positionAmt']) != 0 for p in pos):
                for symbol in SYMBOLS:
                    z, curr_p = get_symbol_stats(client, symbol)
                    
                    if abs(z) >= Z_THRESHOLD:
                        side = "BUY" if z < 0 else "SELL"
                        
                        # Авто-определение точности лота
                        ex_info = client.futures_exchange_info()
                        s_info = next(i for i in ex_info['symbols'] if i['symbol'] == symbol)
                        step = float(s_info['filters'][1]['stepSize'])
                        prec = int(round(-np.log10(step), 0))
                        
                        # Расчет параметров
                        dist = curr_p * 0.006
                        qty = round(RISK_USD / dist, prec)
                        if qty <= 0: continue

                        # ВХОД
                        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
                        client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
                        
                        # СТОП И ТЕЙК
                        sl = round(curr_p - dist if side == "BUY" else curr_p + dist, 4)
                        tp = round(curr_p + (dist * 3) if side == "BUY" else curr_p - (dist * 3), 4)
                        
                        opp = "SELL" if side == "BUY" else "BUY"
                        client.futures_create_order(symbol=symbol, side=opp, type='STOP_MARKET', stopPrice=sl, closePosition=True)
                        client.futures_create_order(symbol=symbol, side=opp, type='TAKE_PROFIT_MARKET', stopPrice=tp, closePosition=True)
                        
                        bot.send_message(chat_id, f"🎯 *ВХОД: {symbol}*\nZ-Score: `{z:.2f}`\nЦель: `{tp}` (+$3)")
                        break 
                    time.sleep(0.3)
            time.sleep(15)
    finally:
        if os.path.exists(LOCK_FILE): os.remove(LOCK_FILE)

threading.Thread(target=main_loop, daemon=True).start()
threading.Thread(target=bot.infinity_polling, daemon=True).start()

@app.route('/')
def health(): return "OK", 200

if __name__ == "__main__":
    if os.path.exists(LOCK_FILE): os.remove(LOCK_FILE)
    app.run(host='0.0.0.0', port=10000)
