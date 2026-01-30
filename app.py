import os, requests, time, threading, numpy as np
import telebot
from flask import Flask
from binance.client import Client
from telebot import types

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'ZECUSDC'
LEVERAGE = 75
RISK_USD = 1.0
Z_THRESHOLD = 3.0  # Чувствительность (3.0 - консервативно, 2.0 - агрессивно)

# Инициализация
bot = telebot.TeleBot(os.environ.get("TELEGRAM_TOKEN"))
chat_id = os.environ.get("CHAT_ID")

def get_data():
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=60)
    closes = [float(k[4]) for k in klines]
    return np.array(closes)

# --- ИНТЕРФЕЙС КНОПОК ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📊 Статус', '⚙️ Агрессивный (Z=2)', '🛡 Консервативный (Z=3)')
    bot.reply_to(message, "Система ZEC-Math Sniper готова. Выбери режим:", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_buttons(message):
    global Z_THRESHOLD
    if message.text == '📊 Статус':
        bot.send_message(chat_id, f"Работаю по {SYMBOL}\nПлечо: x{LEVERAGE}\nТекущий Z-порог: {Z_THRESHOLD}")
    elif message.text == '⚙️ Агрессивный (Z=2)':
        Z_THRESHOLD = 2.0
        bot.send_message(chat_id, "Установлен агрессивный режим (больше сделок)")
    elif message.text == '🛡 Консервативный (Z=3)':
        Z_THRESHOLD = 3.0
        bot.send_message(chat_id, "Установлен консервативный режим (выше точность)")

def main_loop():
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    bot.send_message(chat_id, "🚀 Бот запущен на паре ZEC/USDC")
    
    while True:
        try:
            pos = client.futures_position_information(symbol=SYMBOL)
            current_pos = next((p for p in pos if p['symbol'] == SYMBOL), None)
            
            if not (current_pos and float(current_pos['positionAmt']) != 0):
                data = get_data()
                curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
                
                mean = np.mean(data)
                std = np.std(data)
                z = (curr_p - mean) / std
                
                side = None
                if z < -Z_THRESHOLD: side = "BUY"
                elif z > Z_THRESHOLD: side = "SELL"
                
                if side:
                    stop_dist = curr_p * 0.006 # Для ZEC берем стоп чуть шире - 0.6%
                    qty = round(RISK_USD / stop_dist, 1) # ZEC имеет меньше знаков после запятой
                    
                    sl = round(curr_p - stop_dist if side == "BUY" else curr_p + stop_dist, 3)
                    tp = round(curr_p + (stop_dist * 3) if side == "BUY" else curr_p - (stop_dist * 3), 3)
                    
                    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
                    client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty)
                    
                    opp = "SELL" if side == "BUY" else "BUY"
                    client.futures_create_order(symbol=SYMBOL, side=opp, type='STOP_MARKET', stopPrice=str(sl), closePosition=True)
                    client.futures_create_order(symbol=SYMBOL, side=opp, type='LIMIT', price=str(tp), quantity=qty, timeInForce='GTC', reduceOnly=True)
                    
                    bot.send_message(chat_id, f"🎯 *MATH ENTRY (ZEC)*\nZ-Score: `{z:.2f}`\nВход: `{curr_p}`\nЦель (1:3): `{tp}`")

            time.sleep(20)
        except Exception as e:
            time.sleep(30)

# Запуск потоков
threading.Thread(target=main_loop, daemon=True).start()
threading.Thread(target=bot.infinity_polling, daemon=True).start()

@app.route('/')
def health(): return "ZEC Bot Active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
