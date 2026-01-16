import os
from flask import Flask
import requests
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = "7988115767:AAFhpUf-DZDRpmI6ixFbw_-OB9AsPXdpOoQ"
TELEGRAM_CHAT_ID = "7215386084"
SYMBOL = 'BNBUSDT'
WALL_SIZE = 950 # Еще строже отбор китов

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})

def get_btc_status(client):
    # Проверяем, куда идет "папа" рынка (BTC) за последние 5 минут
    klines = client.get_klines(symbol='BTCUSDT', interval=Client.KLINE_INTERVAL_1MINUTE, limit=5)
    start_price = float(klines[0][1])
    end_price = float(klines[-1][4])
    return "UP" if end_price > start_price else "DOWN"

def analyze_order_book():
    client = Client()
    try:
        btc_trend = get_btc_status(client)
        depth = client.get_order_book(symbol=SYMBOL, limit=100)
        
        max_bid = max(depth['bids'], key=lambda x: float(x[1]))
        max_ask = max(depth['asks'], key=lambda x: float(x[1]))
        
        bid_p, bid_q = float(max_bid[0]), float(max_bid[1])
        ask_p, ask_q = float(max_ask[0]), float(max_ask[1])
        
        msg = ""

        # УСЛОВИЕ ДЛЯ ИДЕАЛЬНОГО ЛОНГА
        # (Стена BNB + Биткоин не падает)
        if bid_q >= WALL_SIZE:
            if btc_trend == "UP":
                msg = (f"🌟 **ИДЕАЛЬНЫЙ ЛОНГ (Confirmed)**\n"
                       f"✅ Стена: {bid_q:.0f} BNB\n"
                       f"🌍 Поводырь (BTC): Растет 📈\n\n"
                       f"💰 Вход: `{bid_p + 0.2}`\n🛡 Стоп: `{bid_p - 1.2}`\n🎯 Тейк: `{bid_p + 4.5}`")
            else:
                msg = f"⚠️ Вижу стену на покупку ({bid_q:.0f} BNB), но **BTC падает**. Вход опасен!"

        # УСЛОВИЕ ДЛЯ ИДЕАЛЬНОГО ШОРТА
        elif ask_q >= WALL_SIZE:
            if btc_trend == "DOWN":
                msg = (f"💀 **ИДЕАЛЬНЫЙ ШОРТ (Confirmed)**\n"
                       f"✅ Стена: {ask_q:.0f} BNB\n"
                       f"🌍 Поводырь (BTC): Падает 📉\n\n"
                       f"💰 Вход: `{ask_p - 0.2}`\n🛡 Стоп: `{ask_p + 1.2}`\n🎯 Тейк: `{ask_p - 4.5}`")
            else:
                msg = f"⚠️ Вижу стену на продажу ({ask_q:.0f} BNB), но **BTC растет**. Не шорти!"

        if msg:
            send_telegram(msg)
            return "Signal processed"
        return "Market Scan: Neutral"
    except Exception as e:
        return f"Error: {e}"

@app.route('/')
def home():
    res = analyze_order_book()
    return f"Status: {res}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
