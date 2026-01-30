import os, time, threading, numpy as np
import telebot
from flask import Flask
from binance.client import Client
from telebot import types

app = Flask(__name__)

# --- НАСТРОЙКИ v.17.0 ---
SYMBOLS = ['ZECUSDC', 'LTCUSDC', 'LINKUSDC', 'SOLUSDC'] # Наш "Золотой Квартет"
LEVERAGE = 75
RISK_USD = 1.0
Z_THRESHOLD = 3.0 

bot = telebot.TeleBot(os.environ.get("TELEGRAM_TOKEN"))
chat_id = os.environ.get("CHAT_ID")

# Функция расчета Z-Score для конкретной монеты
def get_symbol_stats(client, symbol):
    klines = client.futures_klines(symbol=symbol, interval='1m', limit=60)
    closes = np.array([float(k[4]) for k in klines])
    curr_p = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    
    mean = np.mean(closes)
    std = np.std(closes)
    z = (curr_p - mean) / std if std != 0 else 0
    return z, curr_p

# --- ИНТЕРФЕЙС ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📊 Статус', '⚙️ Агрессивный (Z=2)', '🛡 Консервативный (Z=3)')
    bot.reply_to(message, "Система Multi-Math Sniper готова.\nМониторю: ZEC, LTC, LINK, SOL", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_buttons(message):
    global Z_THRESHOLD
    if message.text == '📊 Статус':
        bot.send_message(chat_id, f"📡 Сканирую: {', '.join(SYMBOLS)}\nПлечо: x{LEVERAGE}\nZ-порог: {Z_THRESHOLD}")
    elif message.text == '⚙️ Агрессивный (Z=2)':
        Z_THRESHOLD = 2.0
        bot.send_message(chat_id, "🚀 Режим: Агрессивный (Z=2)")
    elif message.text == '🛡 Консервативный (Z=3)':
        Z_THRESHOLD = 3.0
        bot.send_message(chat_id, "🛡 Режим: Консервативный (Z=3)")

def main_loop():
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    bot.send_message(chat_id, "🚀 Бот-Мультиснайпер запущен и ищет аномалии...")
    
    while True:
        for symbol in SYMBOLS:
            try:
                # Проверяем, нет ли позиции по ЭТОЙ монете
                pos = client.futures_position_information(symbol=symbol)
                has_pos = any(float(p['positionAmt']) != 0 for p in pos if p['symbol'] == symbol)
                
                if not has_pos:
                    z, curr_p = get_symbol_stats(client, symbol)
                    
                    side = None
                    if z < -Z_THRESHOLD: side = "BUY"
                    elif z > Z_THRESHOLD: side = "SELL"
                    
                    if side:
                        # Настройка точности лота (у каждой монеты своя)
                        precision = 1 if symbol != 'LINKUSDC' else 0 # LINK требует целые числа
                        
                        stop_dist = curr_p * 0.006
                        qty = round(RISK_USD / stop_dist, precision)
                        if qty == 0: qty = 1.0 # Защита от слишком малого объема
                        
                        sl = round(curr_p - stop_dist if side == "BUY" else curr_p + stop_dist, 3)
                        tp = round(curr_p + (stop_dist * 3) if side == "BUY" else curr_p - (stop_dist * 3), 3)
                        
                        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
                        client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
                        
                        opp = "SELL" if side == "BUY" else "BUY"
                        # Стоп и Тейк по рынку (надежнее для волатильности)
                        client.futures_create_order(symbol=symbol, side=opp, type='STOP_MARKET', stopPrice=str(sl), closePosition=True)
                        client.futures_create_order(symbol=symbol, side=opp, type='TAKE_PROFIT_MARKET', stopPrice=str(tp), closePosition=True)
                        
                        bot.send_message(chat_id, f"🎯 *ВХОД {symbol}*\nZ-Score: `{z:.2f}`\nВход: `{curr_p}`\nЦель +$3: `{tp}`")
                
                time.sleep(2) # Пауза между монетами, чтобы не спамить API
            except Exception as e:
                print(f"Ошибка по {symbol}: {e}")
                time.sleep(5)
        
        time.sleep(15) # Отдых перед следующим кругом сканирования

# Запуск
threading.Thread(target=main_loop, daemon=True).start()
threading.Thread(target=bot.infinity_polling, daemon=True).start()

@app.route('/')
def health(): return "Multi-Bot Active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
