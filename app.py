import os, time, threading, numpy as np
import telebot
from flask import Flask
from binance.client import Client
from telebot import types

app = Flask(__name__)

# --- НАСТРОЙКИ v.18.0 ---
# Список из 20 ликвидных пар к USDC
SYMBOLS = [
    'BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'ZECUSDC', 'LTCUSDC', 'LINKUSDC', 'ADAUSDC', 
    'XRPUSDC', 'DOTUSDC', 'AVAXUSDC', 'BNBUSDC', 'MATICUSDC', 'UNIUSDC', 'BCHUSDC',
    'NEARUSDC', 'TIAUSDC', 'ARBUSDC', 'OPUSDC', 'INJUSDC', 'DOGEUSDC'
]
LEVERAGE = 75
RISK_USD = 1.0
Z_THRESHOLD = 3.0 

bot = telebot.TeleBot(os.environ.get("TELEGRAM_TOKEN"))
chat_id = os.environ.get("CHAT_ID")

def get_symbol_stats(client, symbol):
    try:
        klines = client.futures_klines(symbol=symbol, interval='1m', limit=60)
        closes = np.array([float(k[4]) for k in klines])
        curr_p = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        z = (curr_p - np.mean(closes)) / np.std(closes) if np.std(closes) != 0 else 0
        return z, curr_p
    except: return 0, 0

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📊 Статус', '⚙️ Агрессивный (Z=2)', '🛡 Консервативный (Z=3)')
    bot.reply_to(message, "Система Sniper v.18.0 (20 пар). Режим: Одна сделка в моменте.", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_buttons(message):
    global Z_THRESHOLD
    if message.text == '📊 Статус':
        bot.send_message(chat_id, f"📡 Сканирую 20 пар\nZ-порог: {Z_THRESHOLD}\nРежим: Ожидание аномалии")
    elif message.text == '⚙️ Агрессивный (Z=2)':
        Z_THRESHOLD = 2.0
        bot.send_message(chat_id, "🚀 Установлен Z=2 (Агрессивный)")
    elif message.text == '🛡 Консервативный (Z=3)':
        Z_THRESHOLD = 3.0
        bot.send_message(chat_id, "🛡 Установлен Z=3 (Консервативный)")

def main_loop():
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    
    while True:
        try:
            # ПРОВЕРКА: Есть ли хоть одна открытая позиция на фьючерсном аккаунте?
            all_pos = client.futures_position_information()
            active_positions = [p for p in all_pos if float(p['positionAmt']) != 0]

            if len(active_positions) > 0:
                # Если позиция есть — ничего не делаем, ждем 30 сек
                time.sleep(30)
                continue

            # Если позиций нет — сканируем список пар
            for symbol in SYMBOLS:
                z, curr_p = get_symbol_stats(client, symbol)
                
                if abs(z) > Z_THRESHOLD:
                    side = "BUY" if z < -Z_THRESHOLD else "SELL"
                    
                    # Логика расчета лота
                    stop_dist = curr_p * 0.006
                    # Динамическая точность лота
                    info = client.futures_exchange_info()
                    sym_info = next(i for i in info['symbols'] if i['symbol'] == symbol)
                    step_size = float(sym_info['filters'][1]['stepSize'])
                    precision = int(round(-np.log10(step_size), 0))
                    
                    qty = round(RISK_USD / stop_dist, precision)
                    if qty == 0: continue # Пропускаем, если риск слишком мал для лота

                    # Вход
                    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
                    client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
                    
                    # Стоп и Тейк
                    sl = round(curr_p - stop_dist if side == "BUY" else curr_p + stop_dist, 4)
                    tp = round(curr_p + (stop_dist * 3) if side == "BUY" else curr_p - (stop_dist * 3), 4)
                    
                    opp = "SELL" if side == "BUY" else "BUY"
                    client.futures_create_order(symbol=symbol, side=opp, type='STOP_MARKET', stopPrice=sl, closePosition=True)
                    client.futures_create_order(symbol=symbol, side=opp, type='TAKE_PROFIT_MARKET', stopPrice=tp, closePosition=True)
                    
                    bot.send_message(chat_id, f"🎯 *ВХОД: {symbol}*\nZ-Score: `{z:.2f}`\n\nБот заблокирован до закрытия этой сделки.")
                    break # Выходим из цикла сканирования, так как сделка открыта

                time.sleep(1) # Защита от бана API

        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(10)
        time.sleep(10)

threading.Thread(target=main_loop, daemon=True).start()
threading.Thread(target=bot.infinity_polling, daemon=True).start()

@app.route('/')
def health(): return "20-Pair Bot Active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
