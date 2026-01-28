import os
import requests
import time
import threading
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ТЕСТА ---
SYMBOL = 'BNBUSDC'
TRADE_AMOUNT = 100.0
STEP = 2.0
PROFIT_GOAL = 4.0

# Виртуальное состояние
paper_trade = {
    "short_pos": 0,
    "long_pos": 0,
    "entry_short": 0,
    "entry_long": 0,
    "tp_short": 0,
    "tp_long": 0,
    "balance": 1000.0
}

client = Client()

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

# --- ФОНОВАЯ ЛОГИКА ---
def bot_worker():
    global paper_trade
    send_tg("🚀 *Бумажный бот ожил!* Начинаю слежку за рынком.")
    
    while True:
        try:
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            
            # 1. СТАРТ ЦИКЛА
            if paper_trade["short_pos"] == 0 and paper_trade["long_pos"] == 0:
                paper_trade["short_pos"] = 1
                paper_trade["entry_short"] = curr_p
                paper_trade["tp_short"] = round(curr_p - PROFIT_GOAL, 2)
                send_tg(f"📝 *PAPER:* Открыт Шорт по `{curr_p}`. Тейк: `{paper_trade['tp_short']}`")

            # 2. ТЕЙК ШОРТА
            if paper_trade["short_pos"] == 1 and curr_p <= paper_trade["tp_short"]:
                paper_trade["short_pos"] = 0
                paper_trade["balance"] += (TRADE_AMOUNT * 0.04)
                send_tg(f"💰 *PAPER:* Тейк Шорта на `{curr_p}`! Баланс: `{round(paper_trade['balance'], 2)}`")
                if paper_trade["long_pos"] == 1:
                    paper_trade["tp_long"] = round(curr_p + PROFIT_GOAL, 2)
                    send_tg(f"🔄 *PAPER:* Переставил тейк Лонга на `{paper_trade['tp_long']}`")

            # 3. ЗАМОК (ЛОНГ)
            if paper_trade["short_pos"] == 1 and paper_trade["long_pos"] == 0:
                if curr_p >= (paper_trade["entry_short"] + STEP):
                    paper_trade["long_pos"] = 1
                    paper_trade["entry_long"] = curr_p
                    paper_trade["tp_long"] = round(curr_p + PROFIT_GOAL, 2)
                    send_tg(f"🔒 *PAPER:* Замок (Лонг) по `{curr_p}`. Тейк: `{paper_trade['tp_long']}`")

            # 4. ТЕЙК ЛОНГА
            if paper_trade["long_pos"] == 1 and curr_p >= paper_trade["tp_long"]:
                paper_trade["long_pos"] = 0
                paper_trade["balance"] += (TRADE_AMOUNT * 0.04)
                send_tg(f"💰 *PAPER:* Тейк Лонга на `{curr_p}`! Баланс: `{round(paper_trade['balance'], 2)}`")
                if paper_trade["short_pos"] == 1:
                    paper_trade["tp_short"] = round(curr_p - PROFIT_GOAL, 2)
                    send_tg(f"🔄 *PAPER:* Переставил тейк Шорта на `{paper_trade['tp_short']}`")

        except Exception as e:
            print(f"Ошибка: {e}")
        
        time.sleep(20) # Проверка каждые 20 секунд

# Запуск потока сразу при старте
threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health():
    return "Bot is active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
Эта логика мне принесла 20 баксов за сутки
