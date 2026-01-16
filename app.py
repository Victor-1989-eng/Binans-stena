import os
from flask import Flask
import requests
from binance.client import Client

app = Flask(__name__)

# --- ТВОИ НАСТРОЙКИ ---
TELEGRAM_TOKEN = "7988115767:AAFhpUf-DZDRpmI6ixFbw_-OB9AsPXdpOoQ"
TELEGRAM_CHAT_ID = "7215386084"
SYMBOL = 'BNBUSDT'
WALL_SIZE = 850  # Размер "плиты" для входа

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})

def analyze_order_book():
    client = Client()
    try:
        # Берем глубокий стакан (100 уровней)
        depth = client.get_order_book(symbol=SYMBOL, limit=100)
        bids = depth['bids']
        asks = depth['asks']
        current_price = float(bids[0][0])
        
        # Находим самую мощную плиту
        best_bid = max(bids, key=lambda x: float(x[1]))
        best_ask = max(asks, key=lambda x: float(x[1]))
        
        bid_p, bid_q = float(best_bid[0]), float(best_bid[1])
        ask_p, ask_q = float(best_ask[0]), float(best_ask[1])

        msg = ""

        # ЛОГИКА ДЛЯ ЛОНГА
        if bid_q >= WALL_SIZE:
            entry = bid_p + 0.15 # Входим чуть выше кита
            stop = bid_p - 1.2    # Стоп за кита
            take = entry + 4.5    # Цель (в 3 раза больше риска)
            
            msg = (f"🚀 **ВХОДИМ В ЛОНГ**\n\n"
                   f"💰 Вход: `{entry}`\n"
                   f"🛡 Стоп: `{stop}`\n"
                   f"🎯 Тейк: `{take}`\n\n"
                   f"ℹ️ Опора: стена {bid_q:.0f} BNB")

        # ЛОГИКА ДЛЯ ШОРТА
        elif ask_q >= WALL_SIZE:
            entry = ask_p - 0.15 # Входим чуть ниже кита
            stop = ask_p + 1.2    # Стоп за кита
            take = entry - 4.5    # Цель
            
            msg = (f"📉 **ВХОДИМ В ШОРТ**\n\n"
                   f"💰 Вход: `{entry}`\n"
                   f"🛡 Стоп: `{stop}`\n"
                   f"🎯 Тейк: `{take}`\n\n"
                   f"ℹ️ Сопротивление: стена {ask_q:.0f} BNB")

        if msg:
            send_telegram(msg)
            return "Trade Signal Sent"
        return "No Big Walls"
    except Exception as e:
        return f"Error: {e}"

@app.route('/')
def home():
    res = analyze_order_book()
    return f"Bot status: {res}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
