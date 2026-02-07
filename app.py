import os, time, threading, requests
from flask import Flask
from binance.client import Client
from binance.exceptions import BinanceAPIException

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'SOLUSDC'
LEVERAGE = 100
MARGIN_USDC = 1.0
EMA_FAST = 7
EMA_SLOW = 25
PROFIT_TARGET = 0.10
THRESHOLD = 0.0004

client = Client(
    os.environ.get("BINANCE_API_KEY"), 
    os.environ.get("BINANCE_API_SECRET"),
    {"verify": True, "timeout": 20}
)

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def get_ema(values, span):
    if len(values) < span: return 0
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]:
        ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def run_sniper():
    print("🤖 Снайпер с памятью запущен...")
    send_tg("🧠 *Бот запущен в режиме СНАЙПЕР (одна сделка на один крест)*")
    
    prev_f, prev_s = 0, 0
    last_signal_side = None  # ТА САМАЯ ПАМЯТЬ

    while True:
        try:
            klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=50, recvWindow=6000)
            closes = [float(k[4]) for k in klines[:-1]]
            current_price = float(klines[-1][4])

            f_now = get_ema(closes, EMA_FAST)
            s_now = get_ema(closes, EMA_SLOW)

            pos = client.futures_position_information(symbol=SYMBOL, recvWindow=6000)
            has_pos = any(float(p['positionAmt']) != 0 for p in pos if p['symbol'] == SYMBOL)

            # Если позиции нет, проверяем условия входа
            if not has_pos and prev_f > 0:
                diff = (f_now - s_now) / s_now
                side = None

                # ЛОНГ: пересечение вверх + зазор + память (не лонг ли был последним?)
                if f_now > s_now and diff >= THRESHOLD and prev_f <= prev_s:
                    if last_signal_side != 'BUY':
                        side = 'BUY'
                
                # ШОРТ: пересечение вниз + зазор + память (не шорт ли был последним?)
                elif f_now < s_now and abs(diff) >= THRESHOLD and prev_f >= prev_s:
                    if last_signal_side != 'SELL':
                        side = 'SELL'

                if side:
                    execute_trade(side, current_price)
                    last_signal_side = side  # Запоминаем сторону сделки

            prev_f, prev_s = f_now, s_now

        except BinanceAPIException as e:
            print(f"⚠️ API Error: {e.message}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
        
        time.sleep(15)

def execute_trade(side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE, recvWindow=6000)
        qty = round((MARGIN_USDC * LEVERAGE) / price, 1)
        
        # Вход по рынку
        client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty, recvWindow=6000)
        
        # Выход по Тейку (Лимитка)
        tp_price = round(price + PROFIT_TARGET if side == 'BUY' else price - PROFIT_TARGET, 2)
        client.futures_create_order(
            symbol=SYMBOL, side='SELL' if side == 'BUY' else 'BUY',
            type='LIMIT', price=tp_price, quantity=qty,
            timeInForce='GTC', reduceOnly=True, recvWindow=6000
        )
        send_tg(f"🚀 *ВХОД {side}*\nЦена: `{price}`\nТейк: `{tp_price}`")
    except Exception as e:
        send_tg(f"❌ Ошибка сделки: {e}")

if not os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    threading.Thread(target=run_sniper, daemon=True).start()

@app.route('/')
def health(): return "ACTIVE"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
