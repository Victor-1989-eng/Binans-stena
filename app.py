import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ "УМНОГО СНАЙПЕРА" ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 50
QTY_BNB = 0.24       # Безопасный объем для теста новой логики
WALL_SIZE = 1600     # Только огромные стены (фильтр фейков)
RANGE_MAX = 0.003    # Вход только если цена почти касается стены (0.3%)
CALLBACK_RATE = 0.3  # Трейлинг-стоп идет в 0.3% за ценой
LAST_CHECK_TIME = 0  # Защита от частых вызовов

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

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        # Суммируем плотность в радиусе 0.5 USDT
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= 0.5])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(client, side, entry_price, target_wall_price=None):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # 1. Вход по рынку (MARKET)
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_BNB)
        time.sleep(1.5) # Пауза для стабильности API

        # 2. Основной защитный СТОП-ЛОСС (0.5%)
        stop_p = round(entry_price * 0.995 if side == "LONG" else entry_price * 1.005, 2)
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET', 
                                    stopPrice=str(stop_p), closePosition=True)
        
        # 3. ТРЕЙЛИНГ-СТОП (Активируется у встречной стены или при +0.5%)
        activation_p = target_wall_price if target_wall_price else round(entry_price * 1.005 if side == "LONG" else entry_price * 0.995, 2)
        
        client.futures_create_order(
            symbol=SYMBOL,
            side=close_side,
            type='TRAILING_STOP_MARKET',
            quantity=QTY_BNB,
            callbackRate=CALLBACK_RATE,
            activationPrice=str(activation_p),
            reduceOnly=True
        )
        
        send_tg(f"🚀 *ВХОД {side}* (Стена: {WALL_SIZE})\n📈 Трейлинг после: `{activation_p}`\n🛡 Стоп: `{stop_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа/трейлинга: {e}")

@app.route('/')
def run_bot():
    global LAST_CHECK_TIME
    now = time.time()
    
    # Защита от вызовов чаще 50 секунд
    if now - LAST_CHECK_TIME < 50:
        return f"Пауза... Прошло {int(now - LAST_CHECK_TIME)} сек. Работаем раз в минуту."
    
    LAST_CHECK_TIME = now
    client = get_binance_client()
    if not client: return "API Keys Missing", 500

    try:
        # Проверка открытых позиций
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active_pos:
            return "В сделке. Трейлинг-стоп следит за ценой..."

        # Анализ стакана
        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        bid_p, bid_v = find_whale_walls(depth['bids'])
        ask_p, ask_v = find_whale_walls(depth['asks'])

        # Логика входа от стен
        if bid_p and (curr_p - bid_p) / bid_p <= RANGE_MAX:
            open_trade(client, "LONG", curr_p, target_wall_price=ask_p)
            return f"Открыт LONG от стены {bid_v}"
            
        elif ask_p and (ask_p - curr_p) / ask_p <= RANGE_MAX:
            open_trade(client, "SHORT", curr_p, target_wall_price=bid_p)
            return f"Открыт SHORT от стены {ask_v}"

        return f"Сканирую... Цена: {curr_p}. Крупных стен рядом нет."
        
    except Exception as e:
        return f"Ошибка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
