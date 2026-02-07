import os, time, threading, requests
from flask import Flask
from binance.client import Client
from binance.exceptions import BinanceAPIException

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = os.environ.get("SYMBOL", "SOLUSDC")
LEVERAGE = 100
MARGIN_USDC = 1.0       # Маржа $1
EMA_FAST = 7
EMA_SLOW = 25           # Теперь 25 - это медленная линия
THRESHOLD = 0.0003      # Фильтр шума
PROFIT_TARGET = 0.6    # Тейк 10 центов

client = Client(
    os.environ.get("BINANCE_API_KEY"), 
    os.environ.get("BINANCE_API_SECRET"),
    {"verify": True, "timeout": 20}
)

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage", 
                json={"chat_id": chat_id, "text": f"[{SYMBOL}] {text}", "parse_mode": "Markdown"}
            )
        except: pass

def get_ema(values, span):
    if len(values) < span: return 0
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]:
        ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def run_sniper():
    print(f"🤖 Скальпер 7/25 для {SYMBOL} запущен...")
    send_tg(f"⚔️ *Бот 7/25 Готов!* \nМонета: `{SYMBOL}`\nВыход: Тейк или Обратный крест")
    
    prev_f, prev_s = 0, 0
    last_signal_side = None # Память, чтобы не входить дважды

    while True:
        try:
            # 1. Берем всего 50 свечей (этого хватит для EMA 25)
            klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=50, recvWindow=60000)
            closes = [float(k[4]) for k in klines[:-1]]
            current_price = float(klines[-1][4])

            f_now = get_ema(closes, EMA_FAST)
            s_now = get_ema(closes, EMA_SLOW)

            # 2. Проверяем позицию
            pos = client.futures_position_information(symbol=SYMBOL, recvWindow=60000)
            active_pos = next((p for p in pos if p['symbol'] == SYMBOL and float(p['positionAmt']) != 0), None)

            if active_pos:
                # --- УМНЫЙ ВЫХОД (STOP REVERSAL) ---
                amt = float(active_pos['positionAmt'])
                diff = (f_now - s_now) / s_now
                
                # Если Лонг, но 7 упала под 25
                if amt > 0 and f_now < s_now and abs(diff) >= THRESHOLD:
                    close_position('SELL', "ОБРАТНЫЙ КРЕСТ (LONG -> EXIT)")
                    last_signal_side = None 
                
                # Если Шорт, но 7 выросла над 25
                elif amt < 0 and f_now > s_now and diff >= THRESHOLD:
                    close_position('BUY', "ОБРАТНЫЙ КРЕСТ (SHORT -> EXIT)")
                    last_signal_side = None

            else:
                # --- ВХОД В СДЕЛКУ ---
                if prev_f > 0:
                    diff = (f_now - s_now) / s_now
                    side = None

                    # ЛОНГ: 7 > 25 + зазор + память
                    if f_now > s_now and diff >= THRESHOLD and prev_f <= prev_s:
                        if last_signal_side != 'BUY':
                            side = 'BUY'
                    
                    # ШОРТ: 7 < 25 + зазор + память
                    elif f_now < s_now and abs(diff) >= THRESHOLD and prev_f >= prev_s:
                        if last_signal_side != 'SELL':
                            side = 'SELL'

                    if side:
                        execute_trade(side, current_price)
                        last_signal_side = side

            prev_f, prev_s = f_now, s_now

        except BinanceAPIException as e:
            print(f"⚠️ API: {e.message}")
        except Exception as e:
            print(f"❌ Error: {e}")
        
        time.sleep(15)

def execute_trade(side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE, recvWindow=60000)
        
        # Авто-расчет точности (precision)
        info = client.futures_exchange_info()
        s_info = next(i for i in info['symbols'] if i['symbol'] == SYMBOL)
        step = float(next(f for f in s_info['filters'] if f['filterType'] == 'LOT_SIZE')['stepSize'])
        precision = 0 if step >= 1 else len(str(step).split('.')[-1].rstrip('0'))
        
        qty = round((MARGIN_USDC * LEVERAGE) / price, precision)
        
        # Вход
        order = client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty, recvWindow=60000)
        entry = float(order.get('avgPrice', price))
        
        # Тейк
        tp_side = 'SELL' if side == 'BUY' else 'BUY'
        tp_price = round(entry + PROFIT_TARGET if side == 'BUY' else entry - PROFIT_TARGET, 2)
        
        client.futures_create_order(
            symbol=SYMBOL, side=tp_side, type='LIMIT', price=tp_price, quantity=qty,
            timeInForce='GTC', reduceOnly=True, recvWindow=60000
        )
        send_tg(f"🚀 *ВХОД {side}*\nЦена: `{entry}`\nТейк: `{tp_price}`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

def close_position(side, reason):
    try:
        client.futures_cancel_all_open_orders(symbol=SYMBOL, recvWindow=60000)
        pos = client.futures_position_information(symbol=SYMBOL, recvWindow=60000)
        qty = abs(float(next(p for p in pos if p['symbol'] == SYMBOL)['positionAmt']))
        client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty, reduceOnly=True, recvWindow=60000)
        send_tg(f"⚠️ *ВЫХОД:* {reason}")
    except Exception as e:
        print(f"Err Close: {e}")

if not os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    threading.Thread(target=run_sniper, daemon=True).start()

@app.route('/')
def health(): return f"{SYMBOL}_OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
