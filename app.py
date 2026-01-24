import os, requests, time
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ---
BASKET = ['BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'BNBUSDC', 'PAXGUSDT', 'XRPUSDC']
START_SL = 0.035  # 3.5%
FINAL_TP = 0.105  # 10.5%
TRAIL_STEP = 0.03 # 3%

active_trades = {}

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def get_market_analysis(client):
    analysis = []
    for symbol in BASKET:
        try:
            ticker = client.futures_24hr_ticker(symbol=symbol)
            change = float(ticker['priceChangePercent'])
            analysis.append({'symbol': symbol, 'change': change})
        except: continue
    # Сортируем: сверху самые сильные
    analysis.sort(key=lambda x: x['change'], reverse=True)
    return analysis

@app.route('/')
def run_conveyor():
    global active_trades
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    
    if not active_trades:
        send_tg("⚙️ *АНАЛИЗ РЫНКА ДЛЯ НОВОГО ЦИКЛА...*")
        market_data = get_market_analysis(client)
        
        if len(market_data) < 6: return "Ошибка данных API", 500
        
        # Делим 3 на 3
        longs = market_data[:3]
        shorts = market_data[3:]
        
        for item in longs:
            open_position(client, item['symbol'], 'LONG')
        for item in shorts:
            open_position(client, item['symbol'], 'SHORT')
            
        send_tg(f"🚀 *ЦИКЛ ЗАПУЩЕН (3х3)*\n📈 LONG: {', '.join([x['symbol'] for x in longs])}\n📉 SHORT: {', '.join([x['symbol'] for x in shorts])}")
    else:
        # Логика мониторинга и трейлинга (такая же, как в V17.5)
        check_active_trades(client)
        
    return f"В работе: {len(active_trades)} позиций."

def open_position(client, symbol, side):
    try:
        price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        stop = round(price * (1 - START_SL) if side == 'LONG' else price * (1 + START_SL), 4)
        take = round(price * (1 + FINAL_TP) if side == 'LONG' else price * (1 - FINAL_TP), 4)
        active_trades[symbol] = {
            'side': side, 'entry': price, 'stop': stop, 'take': take, 'pnl_max': 0
        }
    except Exception as e: print(f"Error opening {symbol}: {e}")

def check_active_trades(client):
    # (Здесь остается логика из предыдущего шага: слежение за стопами и тейками)
    pass # Для краткости, в реальном коде здесь будет полный цикл проверки

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
