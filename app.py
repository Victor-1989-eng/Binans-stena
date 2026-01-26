import os
import time
import threading
import pandas as pd
import pandas_ta as ta
import ccxt
from flask import Flask

app = Flask(__name__)

# --- ГЛОБАЛЬНЫЕ НАСТРОЙКИ ---
SYMBOL = 'BNB/USDC'  # Или BNB/USDT
TRADE_AMOUNT_USDC = 3.5  # Сумма одного входа
LEVERAGE = 20
STEP = 2.0
PROFIT_GOAL = 4.0

# Инициализация биржи (через CCXT для надежности)
exchange = ccxt.binance({
    'apiKey': os.environ.get('BINANCE_API_KEY'),
    'secret': os.environ.get('BINANCE_API_SECRET'),
    'options': {'defaultType': 'future'},
    'enableRateLimit': True
})

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            import requests
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def get_market_sentiment():
    """Анализ RSI и тренда по свечам"""
    try:
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=50)
        df = pd.DataFrame(bars, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        rsi = ta.rsi(df['c'], length=14).iloc[-1]
        
        current_p = df['c'].iloc[-1]
        old_p = df['c'].iloc[-15]
        
        if rsi < 35: return "LONG", f"RSI Перепродан ({round(rsi,1)})"
        if rsi > 65: return "SHORT", f"RSI Перекуплен ({round(rsi,1)})"
        return ("LONG", f"Тренд ВВЕРХ (RSI {round(rsi,1)})") if current_p > old_p else ("SHORT", f"Тренд ВНИЗ (RSI {round(rsi,1)})")
    except:
        return "SHORT", "Ошибка анализа"

def bot_worker():
    send_tg("🚀 *РЕАЛЬНЫЙ БОТ ЗАПУЩЕН!* Режим: RSI + Trend + Hedge.")
    exchange.set_leverage(LEVERAGE, SYMBOL)
    
    while True:
        try:
            # 1. Получаем позиции и цену
            balance = exchange.fetch_balance()
            positions = balance['info']['positions']
            pos_data = {p['positionSide']: abs(float(p['positionAmt'])) for p in positions if p['symbol'] == SYMBOL.replace('/', '')}
            
            curr_p = exchange.fetch_ticker(SYMBOL)['last']
            long_amt = pos_data.get('LONG', 0)
            short_amt = pos_data.get('SHORT', 0)

            # 2. СТАРТ ЦИКЛА (Если позиций нет)
            if long_amt == 0 and short_amt == 0:
                side, reason = get_market_sentiment()
                qty = round((TRADE_AMOUNT_USDC * LEVERAGE) / curr_p, 2)
                
                if side == "SHORT":
                    exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                    tp = round(curr_p - PROFIT_GOAL, 2)
                    exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp, params={'positionSide': 'SHORT', 'reduceOnly': True})
                    send_tg(f"📉 *Вход SHORT* по `{curr_p}`\nПричина: {reason}\nТейк: `{tp}`")
                else:
                    exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                    tp = round(curr_p + PROFIT_GOAL, 2)
                    exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp, params={'positionSide': 'LONG', 'reduceOnly': True})
                    send_tg(f"📈 *Вход LONG* по `{curr_p}`\nПричина: {reason}\nТейк: `{tp}`")

            # 3. ЛОГИКА ЗАМКА (ХЕДЖИРОВАНИЕ)
            # Если мы в Шорте, а цена выросла -> открываем Лонг
            if short_amt > 0 and long_amt == 0:
                # Нужно найти цену входа шорта
                pos_info = [p for p in positions if p['symbol'] == SYMBOL.replace('/', '') and p['positionSide'] == 'SHORT'][0]
                entry_s = float(pos_info['entryPrice'])
                if curr_p >= (entry_s + STEP):
                    exchange.create_order(SYMBOL, 'market', 'buy', short_amt, params={'positionSide': 'LONG'})
                    tp_l = round(curr_p + PROFIT_GOAL, 2)
                    exchange.create_order(SYMBOL, 'limit', 'sell', short_amt, tp_l, params={'positionSide': 'LONG', 'reduceOnly': True})
                    send_tg(f"🔒 *ЗАМОК ОТКРЫТ!* Лонг по `{curr_p}` защищает шорт.")

            # Если мы в Лонге, а цена упала -> открываем Шорт
            if long_amt > 0 and short_amt == 0:
                pos_info = [p for p in positions if p['symbol'] == SYMBOL.replace('/', '') and p['positionSide'] == 'LONG'][0]
                entry_l = float(pos_info['entryPrice'])
                if curr_p <= (entry_l - STEP):
                    exchange.create_order(SYMBOL, 'market', 'sell', long_amt, params={'positionSide': 'SHORT'})
                    tp_s = round(curr_p - PROFIT_GOAL, 2)
                    exchange.create_order(SYMBOL, 'limit', 'buy', long_amt, tp_s, params={'positionSide': 'SHORT', 'reduceOnly': True})
                    send_tg(f"🔒 *ЗАМОК ОТКРЫТ!* Шорт по `{curr_p}` защищает лонг.")

            # 4. ПЕРЕСЧЕТ ТЕЙКОВ (ЗЕРКАЛЬНО)
            # Если Шорт закрылся по тейку, а Лонг еще висит - переставляем тейк Лонга от текущего дна
            # (Это происходит автоматически, так как бот увидит, что одна сторона пропала, 
            # но для полной зеркальности нужно удалять старые лимитки и ставить новые)
            # Для простоты: бот будет использовать выставленные лимитки самой биржи.

        except Exception as e:
            print(f"Ошибка: {e}")
        
        time.sleep(15)

# Запуск
threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "Real RSI Bot Active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
