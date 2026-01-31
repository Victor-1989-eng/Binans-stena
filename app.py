import os, time, threading, numpy as np
import telebot
from flask import Flask
from binance.client import Client
from telebot import types

app = Flask(__name__)

# --- НАСТРОЙКИ v.18.3 ---
SYMBOLS = ['BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'ZECUSDC', 'LTCUSDC', 'LINKUSDC', 'ADAUSDC', 
           'XRPUSDC', 'DOTUSDC', 'AVAXUSDC', 'BNBUSDC', 'MATICUSDC', 'UNIUSDC', 
           'BCHUSDC', 'NEARUSDC', 'TIAUSDC', 'ARBUSDC', 'OPUSDC', 'INJUSDC', 'DOGEUSDC']
LEVERAGE = 75
RISK_USD = 2.0  # Увеличили до $2 для надежности ордеров
Z_THRESHOLD = 2.0 

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
    bot.reply_to(message, "Sniper v.18.3: Конфликты устранены. 20 пар.", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_buttons(message):
    global Z_THRESHOLD
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    if message.text == '📊 Статус':
        bot.send_message(chat_id, f"📡 Система активна\nZ-порог: {Z_THRESHOLD}")
    elif message.text == '🔥 Аномалии':
        bot.send_message(chat_id, "🔍 Сканирую...")
        all_z = []
        for s in SYMBOLS:
            z, _ = get_symbol_stats(client, s)
            all_z.append({'s': s, 'z': z})
        all_z.sort(key=lambda x: abs(x['z']), reverse=True)
        msg = "🚀 **РАДАР:**\n\n"
        for i in all_z[:5]:
            emo = "📈" if i['z'] > 0 else "📉"
            msg += f"{emo} `{i['s']}`: `{i['z']:.2f}`\n"
        bot.send_message(chat_id, msg, parse_mode="Markdown")
    elif 'Агрессивный' in message.text: Z_THRESHOLD = 2.0
    elif 'Консервативный' in message.text: Z_THRESHOLD = 3.0

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    while True:
        try:
            pos = client.futures_position_information()
            if not any(float(p['positionAmt']) != 0 for p in pos):
                for symbol in SYMBOLS:
                    z, curr_p = get_symbol_stats(client, symbol)
                    if abs(z) >= Z_THRESHOLD:
                        side = "BUY" if z < 0 else "SELL"
                        # Улучшенный расчет минимального лота (Notional > 5.1 USDC)
                        dist = curr_p * 0.007
                        qty = round(max(RISK_USD / dist, 5.1 / curr_p * LEVERAGE / LEVERAGE), 2) # Грубый хак для минимума
                        
                        # Для монет типа DOGE/ADA нужны целые числа, для BTC - дробные. 
                        # В v.18.3 просто берем 1 знак для простоты.
                        qty = round(qty, 1) if curr_p > 1 else round(qty, 0)

                        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
                        client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
                        
                        sl = round(curr_p - dist if side == "BUY" else curr_p + dist, 4)
                        tp = round(curr_p + (dist * 3) if side == "BUY" else curr_p - (dist * 3), 4)
                        opp = "SELL" if side == "BUY" else "BUY"
                        client.futures_create_order(symbol=symbol, side=opp, type='STOP_MARKET', stopPrice=sl, closePosition=True)
                        client.futures_create_order(symbol=symbol, side=opp, type='TAKE_PROFIT_MARKET', stopPrice=tp, closePosition=True)
                        bot.send_message(chat_id, f"🎯 *ВХОД: {symbol}*\nZ: `{z:.2f}`\nТейк: `{tp}`")
                        break
                    time.sleep(0.2)
            time.sleep(15)
        except Exception as e:
            print(f"Ошибка цикла: {e}")
            time.sleep(10)

@app.route('/')
def health(): return "OK", 200

if __name__ == "__main__":
    # Очистка старых соединений Telegram перед стартом
    bot.remove_webhook()
    time.sleep(1)
    threading.Thread(target=main_loop, daemon=True).start()
    # Запуск бота с игнорированием старых ошибок конфликта
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
    app.run(host='0.0.0.0', port=10000)
