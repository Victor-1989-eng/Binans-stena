import os, time, threading, numpy as np
import telebot
from flask import Flask
from binance.client import Client
from telebot import types

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'ZECUSDC'
LEVERAGE = 50 
RISK_USD = 1.0  # Твой риск 1/3
Z_THRESHOLD = 2.0 
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
    markup.add('📊 Статус', '🔥 Аномалия ZEC')
    markup.add('⚙️ Z=2.0', '🛡 Z=3.0')
    bot.reply_to(message, "Sniper v.19.2 готов. Работаю по ZEC.", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_buttons(message):
    global Z_THRESHOLD
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    
    if message.text == '📊 Статус':
        bot.send_message(message.chat.id, f"📡 Мониторинг: {SYMBOL}\nТекущий Z: {Z_THRESHOLD}")
    
    elif message.text == '🔥 Аномалия ZEC':
        z, p = get_symbol_stats(client, SYMBOL)
        bot.send_message(message.chat.id, f"💎 ZEC Z-Score: `{z:.2f}`\nЦена: `{p}`")

    elif 'Z=2.0' in message.text:
        Z_THRESHOLD = 2.0
        bot.send_message(message.chat.id, "🚀 Режим: 2.0")
    elif 'Z=3.0' in message.text:
        Z_THRESHOLD = 3.0
        bot.send_message(message.chat.id, "🛡 Режим: 3.0")

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    if os.path.exists(LOCK_FILE): os.remove(LOCK_FILE)
    with open(LOCK_FILE, "w") as f: f.write("lock")

    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    
    while True:
        try:
            pos = client.futures_position_information(symbol=SYMBOL)
            if float(pos[0]['positionAmt']) == 0:
                z, curr_p = get_symbol_stats(client, SYMBOL)
                
                if abs(z) >= Z_THRESHOLD:
                    side = "BUY" if z < 0 else "SELL"
                    
                    # Точность лота (для ZEC обычно 3 знака)
                    qty = round(max(RISK_USD / (curr_p * 0.007), 5.1 / curr_p), 3)

                    # ВХОД
                    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
                    client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty)
                    
                    # СТОП И ТЕЙК
                    dist = curr_p * 0.007
                    sl = round(curr_p - dist if side == "BUY" else curr_p + dist, 2)
                    tp = round(curr_p + (dist * 3) if side == "BUY" else curr_p - (dist * 3), 2)
                    
                    opp = "SELL" if side == "BUY" else "BUY"
                    client.futures_create_order(symbol=SYMBOL, side=opp, type='STOP_MARKET', stopPrice=sl, closePosition=True)
                    client.futures_create_order(symbol=SYMBOL, side=opp, type='TAKE_PROFIT_MARKET', stopPrice=tp, closePosition=True)
                    
                    bot.send_message(chat_id, f"🎯 **ВХОД ZEC!**\nZ: `{z:.2f}`\nТейк: `{tp}`")
            time.sleep(15)
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(10)

@app.route('/')
def health(): return "OK", 200

if __name__ == "__main__":
    # ПРИНУДИТЕЛЬНАЯ ОЧИСТКА ВЕБХУКА (чтобы старт работал)
    bot.remove_webhook()
    time.sleep(1)
    
    threading.Thread(target=main_loop, daemon=True).start()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling(timeout=20)
