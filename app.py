import os
from flask import Flask
import requests
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ (ОБЯЗАТЕЛЬНО ЗАПОЛНИ) ---
TELEGRAM_TOKEN = "7988115767:AAFhpUf-DZDRpmI6ixFbw_-OB9AsPXdpOoQ"
TELEGRAM_CHAT_ID = "7215386084"
SYMBOL = 'BNBUSDT'
# Порог крупной заявки в BNB. 800 — это "кит", 1200 — это "очень крупный игрок"
WALL_SIZE_SIGNAL = 800 

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except Exception as e:
        print(f"Ошибка отправки в TG: {e}")

def analyze_order_book():
    client = Client()
    try:
        # Получаем стакан
        depth = client.get_order_book(symbol=SYMBOL, limit=100)
        bids = depth['bids']
        asks = depth['asks']
        
        current_price = float(bids[0][0])
        
        # Находим самую крупную стенку в покупках и продажах
        max_bid = max(bids, key=lambda x: float(x[1]))
        max_ask = max(asks, key=lambda x: float(x[1]))
        
        bid_p, bid_q = float(max_bid[0]), float(max_bid[1])
        ask_p, ask_q = float(max_ask[0]), float(max_ask[1])
        
        report = []

        # ЛОГИКА ДЛЯ ЛОНГА (ПОКУПКА)
        if bid_q >= WALL_SIZE_SIGNAL:
            report.append(f"💎 **ИДЕЯ ДЛЯ ЛОНГА** (от стены {bid_q:.1f} BNB)")
            report.append(f"✅ Вход: `{bid_p + 0.2}` (чуть выше стены)")
            report.append(f"🛡 Стоп: `{bid_p - 1.5}` (за стену)")
            report.append(f"🎯 Цель: `{bid_p + 6.0}`")
            report.append("---")

        # ЛОГИКА ДЛЯ ШОРТА (ПРОДАЖА)
        if ask_q >= WALL_SIZE_SIGNAL:
            report.append(f"🐻 **ИДЕЯ ДЛЯ ШОРТА** (от стены {ask_q:.1f} BNB)")
            report.append(f"✅ Вход: `{ask_p - 0.2}` (чуть ниже стены)")
            report.append(f"🛡 Стоп: `{ask_p + 1.5}` (за стену)")
            report.append(f"🎯 Цель: `{ask_p - 6.0}`")
            report.append("---")

        # ПРОВЕРКА ПЕРЕКОСА (ДАВЛЕНИЕ)
        sum_b = sum([float(q) for p, q in bids[:20]])
        sum_a = sum([float(q) for p, q in asks[:20]])
        bias = (sum_b / (sum_b + sum_a)) * 100
        
        if bias > 70:
            report.append(f"🔥 **ВНИМАНИЕ**: Покупатели давят ({bias:.1f}%)")
        elif bias < 30:
            report.append(f"❄️ **ВНИМАНИЕ**: Продавцы давят ({100-bias:.1f}%)")

        if report:
            final_msg = f"📊 **АНАЛИЗ {SYMBOL}** (Цена: {current_price})\n\n" + "\n".join(report)
            send_telegram(final_msg)
            return "Signal sent"
        
        return "No signals"
    except Exception as e:
        return f"Error: {e}"

@app.route('/')
def home():
    result = analyze_order_book()
    return f"Bot Active. Last scan: {result}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
