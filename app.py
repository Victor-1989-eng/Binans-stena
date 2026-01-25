import os, requests
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ПОД ТВОЙ ВЗГЛЯД ---
SYMBOL = 'BNBUSDC'
WALL_SIZE = 2000     # Ищем крупные блоки (как 3.2к и 2.3к)
AGGREGATION = 10.0   # Группировка как у тебя на экране
START_SL = 0.035     # Твой риск
FINAL_TP = 0.105     # Твоя цель

active_trades = {}
RETRY_COUNT = {} # Память для перезаходов

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

@app.route('/')
def run_logic():
    global active_trades, RETRY_COUNT
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    client = Client(api_key, api_secret)
    
    try:
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        # 1. ПРОВЕРКА ТЕКУЩИХ СДЕЛОК
        if SYMBOL in active_trades:
            trade = active_trades[SYMBOL]
            side = trade['side']
            pnl = (curr_p - trade['entry']) / trade['entry'] if side == 'LONG' else (trade['entry'] - curr_p) / trade['entry']
            
            # ТЕЙК
            if pnl >= FINAL_TP:
                send_tg(f"💰 ТЕЙК-ПРОФИТ! {SYMBOL} {side} закрыт в +10.5%")
                del active_trades[SYMBOL]
                return "Profit"

            # СТОП И ПЕРЕЗАХОД
            stop_hit = (side == 'LONG' and curr_p <= trade['stop']) or (side == 'SHORT' and curr_p >= trade['stop'])
            if stop_hit:
                # Проверяем, осталась ли стена для перезахода
                depth = client.futures_order_book(symbol=SYMBOL, limit=100)
                wall_p, wall_v = find_wall(depth['bids'] if side == 'LONG' else depth['asks'])
                
                if wall_v >= WALL_SIZE and RETRY_COUNT.get(SYMBOL, 0) < 1:
                    RETRY_COUNT[SYMBOL] = RETRY_COUNT.get(SYMBOL, 0) + 1
                    send_tg(f"🔄 Выбило стоп, но стена на месте! Перезахожу в {side} {SYMBOL}")
                    trade['entry'] = curr_p # Новый вход
                else:
                    send_tg(f"❌ Стоп-лосс по {SYMBOL}. Переворачиваюсь или жду новую стену.")
                    del active_trades[SYMBOL]
                    RETRY_COUNT[SYMBOL] = 0
                return "Stop or Retry"

        # 2. ПОИСК СТЕН (Группировка 10.0)
        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        bid_p, bid_v = find_wall(depth['bids']) # Пол
        ask_p, ask_v = find_wall(depth['asks']) # Потолок

        if bid_v >= WALL_SIZE and SYMBOL not in active_trades:
            if curr_p <= bid_p + 2.0: # Если подошли близко к нижней стене
                active_trades[SYMBOL] = {'side': 'LONG', 'entry': curr_p, 'stop': curr_p * (1 - START_SL), 'is_be': False}
                send_tg(f"🧱 Вижу стену снизу: {bid_v:.0f} BNB. Вхожу в LONG!")

        elif ask_v >= WALL_SIZE and SYMBOL not in active_trades:
            if curr_p >= ask_p - 2.0: # Если подошли близко к верхней стене
                active_trades[SYMBOL] = {'side': 'SHORT', 'entry': curr_p, 'stop': curr_p * (1 + START_SL), 'is_be': False}
                send_tg(f"🧱 Вижу стену сверху: {ask_v:.0f} BNB. Вхожу в SHORT!")

        return f"Цена: {curr_p}. Стены: Покупка {bid_v:.0f}, Продажа {ask_v:.0f}"

    except Exception as e:
        return str(e), 400

def find_wall(data):
    # Группировка данных по 10 баксов
    walls = {}
    for p, q in data:
        level = (float(p) // AGGREGATION) * AGGREGATION
        walls[level] = walls.get(level, 0) + float(q)
    
    if not walls: return 0, 0
    best_level = max(walls, key=walls.get)
    return best_level, walls[best_level]

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
