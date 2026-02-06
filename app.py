import os, time, threading, requests
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ СНАЙПЕРА ---
SYMBOL = 'SOLUSDC'
LEVERAGE = 100
MARGIN_USDC = 1.0  # Твой $1
EMA_FAST = 7
EMA_SLOW = 25
PROFIT_TARGET = 0.10  # Забираем 10 центов

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text})
        except: pass

def get_ema(values, span):
    if len(values) < span: return 0
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]:
        ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def run_sniper():
    send_tg(f"🎯 *SOL Снайпер запущен!*\nМаржа: ${MARGIN_USDC}, Плечо: {LEVERAGE}x\nСтратегия: EMA {EMA_FAST}/{EMA_SLOW}, Тейк: {PROFIT_TARGET}$")
    
    prev_f, prev_s = 0, 0
    
    while True:
        try:
            # Получаем последние свечи
            klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=50)
            closes = [float(k[4]) for k in klines[:-1]] # Берем закрытые свечи
            current_price = float(klines[-1][4])

            f_now = get_ema(closes, EMA_FAST)
            s_now = get_ema(closes, EMA_SLOW)

            # Проверяем, есть ли уже открытая позиция
            pos = client.futures_position_information(symbol=SYMBOL)
            has_pos = any(float(p['positionAmt']) != 0 for p in pos if p['symbol'] == SYMBOL)

            if not has_pos and prev_f > 0:
                side = None
                # Сигнал пересечения
                if prev_f <= prev_s and f_now > s_now:
                    side = 'BUY'
                elif prev_f >= prev_s and f_now < s_now:
                    side = 'SELL'

                if side:
                    execute_trade(side, current_price)

            prev_f, prev_s = f_now, s_now

        except Exception as e:
            print(f"Ошибка цикла: {e}")
        
        time.sleep(10) # Проверка каждые 10 секунд

def execute_trade(side, price):
    try:
        # Устанавливаем плечо
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        
        # Шаг 1: Считаем количество (для SOL 1 знак после запятой)
        qty = round((MARGIN_USDC * LEVERAGE) / price, 1)
        
        # Шаг 2: Вход по рынку
        order = client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty)
        entry_price = float(order.get('avgPrice', price))
        
        # Шаг 3: Выставляем Тейк-Профит (Лимитный ордер на выход)
        tp_price = round(entry_price + PROFIT_TARGET if side == 'BUY' else entry_price - PROFIT_TARGET, 2)
        
        client.futures_create_order(
            symbol=SYMBOL,
            side='SELL' if side == 'BUY' else 'BUY',
            type='LIMIT',
            price=tp_price,
            quantity=qty,
            timeInForce='GTC',
            reduceOnly=True
        )
        
        send_tg(f"🚀 *ВХОД {side}*\nЦена: `{entry_price}`\nТейк: `{tp_price}`")
        
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

@app.route('/')
def health(): return "SOL_SNIPER_RUNNING"

if __name__ == "__main__":
    threading.Thread(target=run_sniper, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
