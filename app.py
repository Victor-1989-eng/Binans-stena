import os
import time
import threading
import pandas as pd
import pandas_ta as ta
import ccxt
import requests
from flask import Flask

app = Flask(__name__)

# --- ГЛОБАЛЬНЫЕ НАСТРОЙКИ ---
SYMBOL = 'BNB/USDC'  # Убедись, что это совпадает с твоим балансом (USDC или USDT)
TRADE_AMOUNT_CURRENCY = 3.0  # Сумма входа (в USDC/USDT)
LEVERAGE = 20
STEP = 2.0
PROFIT_GOAL = 4.0

# Инициализация биржи
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
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
        except: pass

def get_market_sentiment():
    """Анализ RSI и тренда"""
    try:
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=50)
        df = pd.DataFrame(bars, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        rsi = ta.rsi(df['c'], length=14).iloc[-1]
        
        current_p = df['c'].iloc[-1]
        old_p = df['c'].iloc[-15]
        
        if rsi < 35: return "LONG", f"RSI Перепродан ({round(rsi,1)})"
        if rsi > 65: return "SHORT", f"RSI Перекуплен ({round(rsi,1)})"
        return ("LONG", f"Тренд ВВЕРХ") if current_p > old_p else ("SHORT", f"Тренд ВНИЗ")
    except:
        return "SHORT", "Ошибка анализа"

def bot_worker():
    send_tg("🚀 *РЕАЛЬНЫЙ БОТ ЗАПУЩЕН!* Режим Hedge + Авто-округление.")
    
    # 1. Настройка плеча
    try:
        exchange.set_leverage(LEVERAGE, SYMBOL)
    except: pass

    while True:
        try:
            # Загружаем правила биржи (округление)
            exchange.load_markets()
            
            # 2. Получаем позиции
            balance = exchange.fetch_balance()
            positions = balance['info']['positions']
            # Ищем позиции именно по нашему символу (Binance использует имя без слэша)
            clean_symbol = SYMBOL.replace('/', '')
            pos_data = {p['positionSide']: abs(float(p['positionAmt'])) for p in positions if p['symbol'] == clean_symbol}
            
            curr_p = exchange.fetch_ticker(SYMBOL)['last']
            long_amt = pos_data.get('LONG', 0)
            short_amt = pos_data.get('SHORT', 0)

            # 3. СТАРТ ЦИКЛА (Если позиций нет)
            if long_amt == 0 and short_amt == 0:
                side, reason = get_market_sentiment()
                # Рассчитываем объем с точностью биржи
                qty = float(exchange.amount_to_precision(SYMBOL, (TRADE_AMOUNT_CURRENCY * LEVERAGE) / curr_p))
                
                if side == "SHORT":
                    # Вход в Short
                    exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                    # Выставление Тейка (округление цены!)
                    tp_price = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_price, params={'positionSide': 'SHORT', 'reduceOnly': True})
                    send_tg(f"📉 *Вход SHORT* по `{curr_p}`\nТейк выставлен: `{tp_price}`\nПричина: {reason}")
                else:
                    # Вход в Long
                    exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                    # Выставление Тейка
                    tp_price = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_price, params={'positionSide': 'LONG', 'reduceOnly': True})
                    send_tg(f"📈 *Вход LONG* по `{curr_p}`\nТейк выставлен: `{tp_price}`\nПричина: {reason}")

            # 4. ЛОГИКА ЗАМКА (ХЕДЖ)
            # Если в Шорте, цена пошла против нас -> открываем Лонг
            if short_amt > 0 and long_amt == 0:
                pos_info = [p for p in positions if p['symbol'] == clean_symbol and p['positionSide'] == 'SHORT'][0]
                entry_s = float(pos_info['entryPrice'])
                if curr_p >= (entry_s + STEP):
                    qty = float(exchange.amount_to_precision(SYMBOL, short_amt))
                    exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                    tp_l = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_l, params={'positionSide': 'LONG', 'reduceOnly': True})
                    send_tg(f"🔒 *ЗАМОК (Лонг)* открыт по `{curr_p}`. Тейк: `{tp_l}`")

            # Если в Лонге, цена пошла против нас -> открываем Шорт
            if long_amt > 0 and short_amt == 0:
                pos_info = [p for p in positions if p['symbol'] == clean_symbol and p['positionSide'] == 'LONG'][0]
                entry_l = float(pos_info['entryPrice'])
                if curr_p <= (entry_l - STEP):
                    qty = float(exchange.amount_to_precision(SYMBOL, long_amt))
                    exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                    tp_s = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_s, params={'positionSide': 'SHORT', 'reduceOnly': True})
                    send_tg(f"🔒 *ЗАМОК (Шорт)* открыт по `{curr_p}`. Тейк: `{tp_s}`")

        except Exception as e:
            send_tg(f"⚠️ *Ошибка в работе:* `{str(e)}`")
            time.sleep(30)
        
        time.sleep(15)

# Фоновый поток
threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "Real Bot is Running", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
