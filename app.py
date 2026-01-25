import os
import requests
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNBUSDC'
TRADE_AMOUNT_USDC = 5.0
STEP = 2.0         # Расстояние до активации Замка
PROFIT_GOAL = 4.0  # Твой тейк 4$
LEVERAGE = 20

def get_client():
    return Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

@app.route('/start')
def start_logic():
    client = get_client()
    try:
        pos_info = client.futures_position_information(symbol=SYMBOL)
        long_pos = [p for p in pos_info if p['positionSide'] == 'LONG'][0]
        short_pos = [p for p in pos_info if p['positionSide'] == 'SHORT'][0]
        
        long_amt = abs(float(long_pos['positionAmt']))
        short_amt = abs(float(short_pos['positionAmt']))

        # 1. СТАРТ: Если позиций нет вообще
        if long_amt == 0 and short_amt == 0:
            client.futures_cancel_all_open_orders(symbol=SYMBOL)
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            qty = round(TRADE_AMOUNT_USDC / curr_p, 2)
            
            # Вход в первый Шорт
            client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL, positionSide='SHORT', type=ORDER_TYPE_MARKET, quantity=qty)
            
            # Тейк для Шорта (880.02 -> 876.02)
            tp_p = round(curr_p - PROFIT_GOAL, 2)
            client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY, positionSide='SHORT', type=ORDER_TYPE_LIMIT, 
                                        price=str(tp_p), quantity=qty, timeInForce=TIME_IN_FORCE_GTC, postOnly=True)
            
            # Замок (Лонг на 882.02)
            lock_p = round(curr_p + STEP, 2)
            client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY, positionSide='LONG', type=ORDER_TYPE_STOP_LIMIT, 
                                        stopPrice=str(lock_p), price=str(lock_p), quantity=qty, timeInForce=TIME_IN_FORCE_GTC, postOnly=True)
            
            send_tg(f"🏁 *Цикл начат!*\nВход Short: `{curr_p}`\nТейк Short: `{tp_p}`\nЗамок Long: `{lock_p}`")
            return "New Cycle Started"

        # 2. ЕСЛИ МЫ В ЗАМКЕ (Обе позиции открыты)
        if long_amt > 0 and short_amt > 0:
            open_orders = client.futures_get_open_orders(symbol=SYMBOL)
            # Проверяем наличие тейка для Лонга (его может не быть сразу после активации замка)
            if not [o for o in open_orders if o['positionSide'] == 'LONG' and o['side'] == 'SELL']:
                curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
                tp_long = round(curr_p + PROFIT_GOAL, 2)
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL, positionSide='LONG', type=ORDER_TYPE_LIMIT, 
                                            price=str(tp_long), quantity=long_amt, timeInForce=TIME_IN_FORCE_GTC, postOnly=True)
                send_tg(f"🔒 *Замок активен!*\nВыставил тейк Лонга на `{tp_long}`")

        # 3. СИТУАЦИЯ А: Лонг закрылся по тейку, остался Шорт
        if short_amt > 0 and long_amt == 0:
            open_orders = client.futures_get_open_orders(symbol=SYMBOL)
            if not [o for o in open_orders if o['positionSide'] == 'SHORT' and o['side'] == 'BUY']:
                client.futures_cancel_all_open_orders(symbol=SYMBOL) # Чистим старые "дальние" тейки
                curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
                new_tp = round(curr_p - PROFIT_GOAL, 2)
                client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY, positionSide='SHORT', type=ORDER_TYPE_LIMIT, 
                                            price=str(new_tp), quantity=short_amt, timeInForce=TIME_IN_FORCE_GTC, postOnly=True)
                send_tg(f"📈 *Лонг закрыт!* Обновил тейк Шорта на `{new_tp}`")

        # 4. СИТУАЦИЯ Б: Шорт закрылся по тейку (после того как побывал в замке), остался Лонг
        # (Например, цена сначала сходила вверх, открыла замок, а потом упала и закрыла шорт)
        if long_amt > 0 and short_amt == 0:
            open_orders = client.futures_get_open_orders(symbol=SYMBOL)
            if not [o for o in open_orders if o['positionSide'] == 'LONG' and o['side'] == 'SELL']:
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
                new_tp = round(curr_p + PROFIT_GOAL, 2)
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL, positionSide='LONG', type=ORDER_TYPE_LIMIT, 
                                            price=str(new_tp), quantity=long_amt, timeInForce=TIME_IN_FORCE_GTC, postOnly=True)
                send_tg(f"📉 *Шорт закрыт!* Обновил тейк Лонга на `{new_tp}`")

        return "Monitoring Hedge..."

    except Exception as e:
        return str(e), 400

@app.route('/')
def health(): return "Ready", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
