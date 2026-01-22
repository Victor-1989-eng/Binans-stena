import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- ГИБКИЕ НАСТРОЙКИ ---
SYMBOLS = ['BNBUSDT', 'SOLUSDT', 'ETHUSDT', 'BTCUSDT'] 
LEVERAGE = 50
QTY_USD = 5         # Сумма входа на одну монету
TP_PCT = 0.02         # Начальный тейк 2% (далее включается трейлинг)
SL_PCT = 0.01         # Стоп 1%
BE_PCT = 0.008        # Безубыток после +0.8% профита
TRAIL_STEP = 0.005    # Шаг трейлинга (подтягиваем стоп каждые 0.5% движения)

def get_binance_client():
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    return Client(api_key, api_secret) if api_key and api_secret else None

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

# --- ЛОГИКА ТРЕЙЛИНГА ---
def manage_trailing(client, symbol, side, entry_p, curr_p):
    # Рассчитываем текущий профит
    profit = (curr_p - entry_p) / entry_p if side == "LONG" else (entry_p - curr_p) / entry_p
    
    # 1. Перенос в безубыток
    if profit >= BE_PCT:
        # Здесь логика проверки: если стоп еще не в безубытке — переносим
        # 2. Трейлинг стопа (тянем за ценой)
        new_sl = curr_p * (1 - SL_PCT/2) if side == "LONG" else curr_p * (1 + SL_PCT/2)
        # Бот будет обновлять STOP_MARKET ордер при каждом значительном шаге цены
        update_stop_order(client, symbol, side, new_sl)

def update_stop_order(client, symbol, side, new_stop_price):
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        for o in orders:
            if o['type'] == 'STOP_MARKET':
                # Если новая цена стопа выгоднее старой — переставляем
                old_stop = float(o['stopPrice'])
                is_better = new_stop_price > old_stop if side == "LONG" else new_stop_price < old_stop
                
                if is_better:
                    client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
                    client.futures_create_order(
                        symbol=symbol, side='SELL' if side=='LONG' else 'BUY',
                        type='STOP_MARKET', stopPrice=str(round(new_stop_price, 2)), closePosition=True
                    )
                    send_tg(f"📈 *Трейлинг-стоп подтянут* для {symbol} на `{new_stop_price}`")
    except: pass

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "No API Keys"
    
    for symbol in SYMBOLS:
        try:
            pos = client.futures_position_information(symbol=symbol)
            active = [p for p in pos if float(p['positionAmt']) != 0]
            
            if active:
                # Если позиция есть — управляем её выходом (Трейлинг)
                amt = float(active[0]['positionAmt'])
                entry = float(active[0]['entryPrice'])
                curr = float(client.futures_symbol_ticker(symbol=symbol)['price'])
                side = "LONG" if amt > 0 else "SHORT"
                manage_trailing(client, symbol, side, entry, curr)
                continue

            # Если позиции нет — ищем вход по стратегии (Тренд + Ликвидации)
            # [Здесь код поиска входа из предыдущих шагов]
            
        except Exception as e:
            print(f"Ошибка в цикле по {symbol}: {e}")
            
    return "Мониторинг активен"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
