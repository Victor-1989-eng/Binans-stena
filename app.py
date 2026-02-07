import os, time, threading, requests
from flask import Flask
from binance.client import Client
from binance.exceptions import BinanceAPIException

app = Flask(__name__)

# --- НАСТРОЙКИ СНАЙПЕРА ---
SYMBOL = 'SOLUSDC'
LEVERAGE = 100
MARGIN_USDC = 1.0       # Твоя маржа $1
EMA_FAST = 7
EMA_SLOW = 25
PROFIT_TARGET = 0.10    # Тейк-профит 10 центов
THRESHOLD = 0.0005      # Твой зазор (0.06% разницы между линиями)

# Инициализация клиента (API ключи берутся из Environment Variables на Render)
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
            res = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
            if res.status_code != 200: print(f"TG Error: {res.text}")
        except: pass

def get_ema(values, span):
    if len(values) < span: return 0
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]:
        ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def run_sniper():
    print("🤖 Поток сканера запущен...")
    send_tg(f"🎯 *SOL Снайпер активирован!*\nЗазор: `{THRESHOLD}`\nТейк: `{PROFIT_TARGET}$`")
    
    prev_f, prev_s = 0, 0
    
    while True:
        try:
            # 1. Получаем свечи (минутки)
            klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=50, recvWindow=6000)
            closes = [float(k[4]) for k in klines[:-1]] # Только закрытые
            current_price = float(klines[-1][4])

            f_now = get_ema(closes, EMA_FAST)
            s_now = get_ema(closes, EMA_SLOW)

            # 2. Проверяем наличие позиции
            pos = client.futures_position_information(symbol=SYMBOL, recvWindow=6000)
            has_pos = any(float(p['positionAmt']) != 0 for p in pos if p['symbol'] == SYMBOL)

            # 3. Логика пересечения с зазором
            if not has_pos and prev_f > 0:
                diff = (f_now - s_now) / s_now
                side = None

                # ЛОНГ: 7 пересекла 25 вверх + разрыв больше порога
                if f_now > s_now and diff >= THRESHOLD and prev_f <= prev_s:
                    side = 'BUY'
                # ШОРТ: 7 пересекла 25 вниз + разрыв больше порога
                elif f_now < s_now and abs(diff) >= THRESHOLD and prev_f >= prev_s:
                    side = 'SELL'

                if side:
                    execute_trade(side, current_price)

            prev_f, prev_s = f_now, s_now

        except BinanceAPIException as e:
            print(f"⚠️ Binance Error: {e.message}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
        
        time.sleep(15) # Пауза между проверками

def execute_trade(side, price):
    try:
        # Установка плеча
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE, recvWindow=6000)
        
        # Расчет объема (для SOL 1 знак после запятой)
        qty = round((MARGIN_USDC * LEVERAGE) / price, 1)
        
        # Вход по рынку
        order = client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty, recvWindow=6000)
        entry_price = float(order.get('avgPrice', price))
        
        # Расчет и выставление Тейк-Профита (Лимитка)
        tp_side = 'SELL' if side == 'BUY' else 'BUY'
        tp_price = round(entry_price + PROFIT_TARGET if side == 'BUY' else entry_price - PROFIT_TARGET, 2)
        
        client.futures_create_order(
            symbol=SYMBOL,
            side=tp_side,
            type='LIMIT',
            price=tp_price,
            quantity=qty,
            timeInForce='GTC',
            reduceOnly=True,
            recvWindow=6000
        )
        
        send_tg(f"🚀 *ВХОД {side}*\nЦена: `{entry_price}`\nТейк: `{tp_price}`")
        
    except Exception as e:
        send_tg(f"❌ Ошибка сделки: {e}")

# Защита от двойного запуска потока в Flask
if not os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    threading.Thread(target=run_sniper, daemon=True).start()

@app.route('/')
def health(): return "SOL_SNIPER_ACTIVE"

if __name__ == "__main__":
    # На Render порт берется из переменной окружения
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
