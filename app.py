import os, time, threading, numpy as np
import telebot
from flask import Flask
from binance.client import Client
from telebot import types

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOLS = ['BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'ZECUSDC', 'LTCUSDC', 'LINKUSDC', 'ADAUSDC', 'NEARUSDC', 'DOGEUSDC']
DEFAULT_LEVERAGE = 75
RISK_USD = 2.0 
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

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📊 Статус', '🔥 Аномалии')
    bot.send_message(message.chat.id, "Sniper v.18.6: Плечи и режимы исправлены.", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_buttons(message):
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    if message.text == '📊 Статус':
        bot.send_message(chat_id, "✅ Бот в сети. Жду аномалий.")
    elif message.text == '🔥 Аномалии':
        bot.send_message(chat_id, "🔍 Сканирую...")
        all_z = []
        for s in SYMBOLS:
            z, _ = get_symbol_stats(client, s)
            all_z.append({'s': s, 'z': z})
        all_z.sort(key=lambda x: abs(x['z']), reverse=True)
        msg = "🚀 **РАДАР:**\n\n"
        for i in all_z[:5]:
            msg += f"`{i['s']}`: `{i['z']:.2f}`\n"
        bot.send_message(chat_id, msg, parse_mode="Markdown")

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
                        
                        # 1. ПОЛУЧАЕМ МАКСИМАЛЬНОЕ ПЛЕЧО ДЛЯ МОНЕТЫ
                        brackets = client.futures_leverage_bracket(symbol=symbol)
                        max_lev = int(brackets[0]['brackets'][0]['initialLeverage'])
                        final_leverage = min(DEFAULT_LEVERAGE, max_lev)
                        
                        client.futures_change_leverage(symbol=symbol, leverage=final_leverage)
                        
                        # 2. ОПРЕДЕЛЯЕМ ТОЧНОСТЬ (PRECISION)
                        info = client.futures_exchange_info()
                        s_info = next(i for i in info['symbols'] if i['symbol'] == symbol)
                        step = float(s_info['filters'][1]['stepSize'])
                        prec = int(round(-np.log10(step), 0))
                        
                        # 3. РАСЧЕТ ЛОТА
                        dist = curr_p * 0.007
                        qty = round(max(RISK_USD / dist, 5.1 / curr_p), prec)

                        # 4. ВХОД (с параметром BOTH для совместимости режимов)
                        client.futures_create_order(
                            symbol=symbol, side=side, type='MARKET', 
                            quantity=qty, positionSide='BOTH'
                        )
                        
                        sl = round(curr_p - dist if side == "BUY" else curr_p + dist, 4)
                        tp = round(curr_p + (dist * 3) if side == "BUY" else curr_p - (dist * 3), 4)
                        
                        opp = "SELL" if side == "BUY" else "BUY"
                        client.futures_create_order(symbol=symbol, side=opp, type='STOP_MARKET', stopPrice=sl, closePosition=True, positionSide='BOTH')
                        client.futures_create_order(symbol=symbol, side=opp, type='TAKE_PROFIT_MARKET', stopPrice=tp, closePosition=True, positionSide='BOTH')
                        
                        bot.send_message(chat_id, f"🎯 *ВХОД: {symbol}*\nZ: `{z:.2f}`\nПлечо: `x{final_leverage}`")
                        break
                    time.sleep(0.5)
            time.sleep(15)
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(10)

if __name__ == "__main__":
    bot.remove_webhook()
    threading.Thread(target=main_loop, daemon=True).start()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
