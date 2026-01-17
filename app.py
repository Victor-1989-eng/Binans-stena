import os
import requests
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 75
QTY_BNB = 0.24      # Объем позиции (~3$ x 75)
WALL_SIZE = 700     # Минимальная плита кита
RANGE_MAX = 0.012   # Максимальная ширина канала (1.2%)
STATS_FILE = "stats.txt"

def get_binance_client():
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        return None
    return Client(api_key, api_secret)

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def update_stats(profit):
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f: f.write("0,0.0")
    with open(STATS_FILE, "r") as f:
        data = f.read().split(",")
        count = int(data[0]) + 1
        total_profit = float(data[1]) + profit
    with open(STATS_FILE, "w") as f:
        f.write(f"{count},{total_profit}")
    if count % 10 == 0:
        status = "🟢 ПРОФИТ" if total_profit > 0 else "🔴 УБЫТОК"
        send_tg(f"📊 *ИТОГ СЕРИИ: 10 СДЕЛОК*\n\nРезультат: `{total_profit:.2f} USDT`\nСтатус: {status}")

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= 0.3])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(client, side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        client.futures_change_margin_type(symbol=SYMBOL, marginType='ISOLATED')
        
        order_side = SIDE_BUY if side == "LONG" else SIDE_SELL
        client.futures_create_order(
            symbol=SYMBOL, side=order_side, type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC, quantity=QTY_BNB, price=str(round(price, 2))
        )
        
        # Стоп 0.7%, Тейк 1.1%
        stop_p = round(price * 0.993 if side == "LONG" else price * 1.007, 2)
        take_p = round(price * 1.011 if side == "LONG" else price * 0.989, 2)
        
        client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if side == "LONG" else SIDE_BUY, 
                                     type=ORDER_TYPE_STOP_MARKET, stopPrice=str(stop_p), closePosition=True)
        client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if side == "LONG" else SIDE_BUY, 
                                     type=ORDER_TYPE_LIMIT, timeInForce=TIME_IN_FORCE_GTC, 
                                     price=str(take_p), quantity=QTY_BNB, reduceOnly=True)
        
        send_tg(f"🚀 *ВХОД {side}*\n💰 Цена: `{price}`\n🛡 Stop: `{stop_p}`\n🎯 Take: `{take_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка открытия: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "Ключи не найдены", 500

    try:
        # 1. Проверка текущего IP (для твоего контроля)
        my_ip = requests.get('https://api.ipify.org').text
        print(f"🌐 IP: {my_ip}")

        # 2. Проверка закрытых сделок для статистики
        pos = client.futures_position_information(symbol=SYMBOL)
        has_position = any(float(p['positionAmt']) != 0 for p in pos)
        
        if not has_position:
            trades = client.futures_account_trades(symbol=SYMBOL, limit=1)
            if trades:
                pnl = float(trades[0]['realizedPnl'])
                if pnl != 0:
                    # Чтобы не дублировать статистику, можно добавить проверку времени сделки
                    update_stats(pnl)

        # 3. Логика поиска входа
        if has_position:
            return f"В позиции. IP: {my_ip}"

        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        bid_p, bid_v = find_whale_walls(depth['bids'])
        ask_p, ask_v = find_whale_walls(depth['asks'])

        if bid_p and ask_p:
            gap = (ask_p - bid_p) / bid_p
            curr_p = float(depth['bids'][0][0])

            if gap <= RANGE_MAX:
                if curr_p <= bid_p + (ask_p - bid_p) * 0.2:
                    open_trade(client, "LONG", bid_p + 0.15)
                    return "Opening Long..."
                elif curr_p >= ask_p - (ask_p - bid_p) * 0.2:
                    open_trade(client, "SHORT", ask_p - 0.15)
                    return "Opening Short..."

        return f"Сканирую... IP: {my_ip}"

    except Exception as e:
        print(f"Ошибка: {e}")
        return f"Ошибка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
