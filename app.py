import os
import requests
import time
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ТЕСТА ---
SYMBOL = 'BNBUSDC'
TRADE_AMOUNT = 100.0  # Виртуальные 100 долларов
STEP = 2.0
PROFIT_GOAL = 4.0

# Виртуальное состояние (в реальном коде это хранится на бирже)
paper_trade = {
    "short_pos": 0,    # 0 - нет позиции, 1 - открыта
    "long_pos": 0,
    "entry_short": 0,
    "entry_long": 0,
    "tp_short": 0,
    "tp_long": 0,
    "balance": 1000.0  # Стартовый демо-баланс
}

client = Client() # Ключи не нужны для получения цены

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

@app.route('/start')
def paper_logic():
    global paper_trade
    try:
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        # 1. СТАРТ ЦИКЛА
        if paper_trade["short_pos"] == 0 and paper_trade["long_pos"] == 0:
            paper_trade["short_pos"] = 1
            paper_trade["entry_short"] = curr_p
            paper_trade["tp_short"] = round(curr_p - PROFIT_GOAL, 2)
            send_tg(f"📝 *PAPER:* Открыт Шорт по `{curr_p}`. Тейк: `{paper_trade['tp_short']}`")
            return f"Paper Short Opened at {curr_p}"

        # 2. ПРОВЕРКА ТЕЙКА ШОРТА
        if paper_trade["short_pos"] == 1 and curr_p <= paper_trade["tp_short"]:
            paper_trade["short_pos"] = 0
            paper_trade["balance"] += (TRADE_AMOUNT * 0.04) # Имитация профита
            send_tg(f"💰 *PAPER:* Тейк Шорта на `{curr_p}`! Баланс: `{paper_trade['balance']}`")
            # Если лонг еще висел, пересчитываем его тейк (зеркально)
            if paper_trade["long_pos"] == 1:
                paper_trade["tp_long"] = round(curr_p + PROFIT_GOAL, 2)
                send_tg(f"🔄 *PAPER:* Переставил тейк Лонга на `{paper_trade['tp_long']}`")

        # 3. ПРОВЕРКА АКТИВАЦИИ ЗАМКА (ЛОНГ)
        if paper_trade["short_pos"] == 1 and paper_trade["long_pos"] == 0:
            if curr_p >= (paper_trade["entry_short"] + STEP):
                paper_trade["long_pos"] = 1
                paper_trade["entry_long"] = curr_p
                paper_trade["tp_long"] = round(curr_p + PROFIT_GOAL, 2)
                send_tg(f"🔒 *PAPER:* Замок (Лонг) активирован по `{curr_p}`. Тейк: `{paper_trade['tp_long']}`")

        # 4. ПРОВЕРКА ТЕЙКА ЛОНГА
        if paper_trade["long_pos"] == 1 and curr_p >= paper_trade["tp_long"]:
            paper_trade["long_pos"] = 0
            paper_trade["balance"] += (TRADE_AMOUNT * 0.04)
            send_tg(f"💰 *PAPER:* Тейк Лонга на `{curr_p}`! Баланс: `{paper_trade['balance']}`")
            # Если шорт висел, пересчитываем его тейк от пика лонга
            if paper_trade["short_pos"] == 1:
                paper_trade["tp_short"] = round(curr_p - PROFIT_GOAL, 2)
                send_tg(f"🔄 *PAPER:* Переставил тейк Шорта на `{paper_trade['tp_short']}`")

        return f"Paper Bot: BNB={curr_p}, S:{paper_trade['short_pos']}, L:{paper_trade['long_pos']}"

    except Exception as e:
        return str(e), 400

@app.route('/')
def health(): return "Paper bot is ready", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
