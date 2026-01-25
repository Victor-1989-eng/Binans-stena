import os, requests
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ТВОЕЙ ЛОВУШКИ ---
SYMBOL = 'BNBUSDC'
STEP = 2.0        # Расстояние до переворота (минус)
PROFIT_GOAL = 4.0 # Сколько хотим забрать чистого движения

# Состояние бота в памяти
trade_data = {
    "is_active": False,
    "side": None,
    "entry_price": 0,
    "iteration": 0
}

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

@app.route('/')
def run_bot():
    global trade_data
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    client = Client(api_key, api_secret)
    
    try:
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

        # 1. ЕСЛИ МЫ НЕ В РЫНКЕ - ЗАХОДИМ ПРЯМО СЕЙЧАС
        if not trade_data["is_active"]:
            trade_data.update({
                "is_active": True,
                "side": "SHORT",
                "entry_price": curr_p,
                "iteration": 0
            })
            send_tg(f"🏁 *Старт капкана!*\nВход в SHORT по: `{curr_p}`\nТейк: `{curr_p - PROFIT_GOAL}`\nПереворот (Long): `{curr_p + STEP}`")
            return f"Запустили шорт по {curr_p}"

        # 2. МЫ В СДЕЛКЕ - ПРОВЕРЯЕМ УСЛОВИЯ
        entry = trade_data["entry_price"]
        side = trade_data["side"]

        # ПРОВЕРКА ТЕЙКА (ПОБЕДА)
        is_tp = (side == "SHORT" and curr_p <= (entry - PROFIT_GOAL)) or \
                (side == "LONG" and curr_p >= (entry + PROFIT_GOAL))
        
        if is_tp:
            # Считаем итог: Тейк (10) минус все прошлые перевороты (по 5)
            # В твоей схеме 50/50 это всегда даст плюс
            send_tg(f"💰 *ПРОФИТ!* Цена дошла до цели: `{curr_p}`. Цикл закрыт в ПЛЮС.")
            trade_data["is_active"] = False
            return "Take Profit hit!"

        # ПРОВЕРКА ПЕРЕВОРОТА (ЛОВУШКА)
        is_flip = (side == "SHORT" and curr_p >= (entry + STEP)) or \
                  (side == "LONG" and curr_p <= (entry - STEP))

        if is_flip:
            old_side = side
            new_side = "LONG" if side == "SHORT" else "SHORT"
            trade_data["side"] = new_side
            trade_data["entry_price"] = curr_p
            trade_data["iteration"] += 1
            
            send_tg(f"🔄 *ПЕРЕВОРОТ #{trade_data['iteration']}*\nЗакрыл {old_side} в -5. Открыл {new_side} по `{curr_p}`. Иду за профитом!")
            return "Flipped"

        return f"Слежу за {SYMBOL}. Цена: {curr_p}. Позиция: {side}. Цель: {entry + PROFIT_GOAL if side == 'LONG' else entry - PROFIT_GOAL}"

    except Exception as e:
        return str(e), 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
