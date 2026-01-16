import os
from flask import Flask
import requests
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = "7988115767:AAFhpUf-DZDRpmI6ixFbw_-OB9AsPXdpOoQ"
TELEGRAM_CHAT_ID = "7215386084"
SYMBOL = 'BNBUSDT'
# Порог крупной заявки в BNB (вчера мы видели 800-1400, поставим 700 как сигнал)
BIG_WALL_THRESHOLD = 700 

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})

def analyze_order_book():
    client = Client() # Работает без API ключей для публичных данных
    
    # Получаем стакан (глубина 100 уровней)
    depth = client.get_order_book(symbol=SYMBOL, limit=100)
    
    bids = depth['bids'] # Покупки
    asks = depth['asks'] # Продажи
    
    msg = []
    
    # 1. Ищем крупные плиты в покупках (ПОЛ)
    for price, qty in bids:
        if float(qty) >= BIG_WALL_THRESHOLD:
            msg.append(f"🟢 **БЕТОН СНИЗУ**: {float(qty):.1f} BNB на цене **{price}**")
            
    # 2. Ищем крупные плиты в продажах (ПОТОЛОК)
    for price, qty in asks:
        if float(qty) >= BIG_WALL_THRESHOLD:
            msg.append(f"🔴 **СТЕНА СВЕРХУ**: {float(qty):.1f} BNB на цене **{price}**")

    # 3. Считаем индекс давления (кто сильнее в стакане)
    sum_bids = sum([float(q) for p, q in bids[:20]])
    sum_asks = sum([float(q) for p, q in asks[:20]])
    bias = (sum_bids / (sum_bids + sum_asks)) * 100
    
    if bias > 65:
        msg.append(f"📊 Сильный перекос в ПОКУПКУ: {bias:.1f}%")
    elif bias < 35:
        msg.append(f"📊 Сильный перекос в ПРОДАЖУ: {100-bias:.1f}%")

    if msg:
        full_message = f"🔍 **Анализ {SYMBOL}**\n" + "\n".join(msg)
        send_telegram(full_message)
        return "Signal sent"
    return "No big walls"

@app.route('/')
def home():
    # Каждый раз, когда Render или внешний пингер заходит на страницу, бот проверяет стакан
    result = analyze_order_book()
    return f"Bot is running. Result: {result}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
