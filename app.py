import os, time, threading, requests
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ (ПОДБИРАЙ ПРОЦЕНТ) ---
SYMBOL = os.environ.get("SYMBOL", "SOLUSDC")
THRESHOLD = 0.003       # 0.008 = 0.8%. Твой главный рычаг для проб.
LEVERAGE = 10            # Плечо 5х (безопасно для реверса)
MARGIN_USDC = 1.0       # Маржа на одну сделку

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": f"[{SYMBOL}] {text}", "parse_mode": "Markdown"})
        except: pass

def get_ema(values, span):
    if len(values) < span: return 0
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]: ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def run_infinite_rebound():
    print(f"🔄 Запущен ВЕЧНЫЙ РЕВЕРС на {SYMBOL}. Порог: {THRESHOLD*100}%")
    send_tg(f"🤖 *Бот-Резинка запущен!*\nСимвол: `{SYMBOL}`\nПорог: `{THRESHOLD*100}%` (Маркет-вход/выход)")
    
    while True:
        try:
            # Получаем свечи (интервал 1м)
            klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=50)
            closes = [float(k[4]) for k in klines[:-1]]
            curr_p = float(klines[-1][4])

            # Считаем EMA
            f_now = get_ema(closes, 7)
            s_now = get_ema(closes, 25)
            
            # Считаем зазор (отклонение быстрой от медленной)
            diff = (f_now - s_now) / s_now

            # Проверяем текущую позицию
            pos = client.futures_position_information(symbol=SYMBOL)
            active_pos = next((p for p in pos if p['symbol'] == SYMBOL and float(p['positionAmt']) != 0), None)
            amt = float(active_pos['positionAmt']) if active_pos else 0

            # --- ЛОГИКА ПЕРЕВОРОТА РЫНОЧНЫМИ ОРДЕРАМИ ---

            # Условие для ЛОНГА (цена внизу, резинка растянута вниз)
            if f_now < s_now and abs(diff) >= THRESHOLD:
                if amt <= 0: # Если мы в шорте или без позиции
                    if amt < 0:
                        execute_market_close('BUY', "ЗАКРЫТ ШОРТ (НИЖНИЙ ПИК)")
                    execute_market_entry('BUY', curr_p)

            # Условие для ШОРТА (цена вверху, резинка растянута вверх)
            elif f_now > s_now and diff >= THRESHOLD:
                if amt >= 0: # Если мы в лонге или без позиции
                    if amt > 0:
                        execute_market_close('SELL', "ЗАКРЫТ ЛОНГ (ВЕРХНИЙ ПИК)")
                    execute_market_entry('SELL', curr_p)

        except Exception as e:
            print(f"Ошибка в цикле: {e}")
        
        time.sleep(5) # Частота проверки рынка

def execute_market_entry(side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        qty = round((MARGIN_USDC * LEVERAGE) / price, 2)
        client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty)
        send_tg(f"🚀 *ВХОД {side}* по рынку. Цена: `{price}`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

def execute_market_close(side, reason):
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        qty = abs(float(next(p for p in pos if p['symbol'] == SYMBOL)['positionAmt']))
        client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty, reduceOnly=True)
        send_tg(f"💰 {reason}")
    except Exception as e:
        print(f"Ошибка закрытия: {e}")

# Flask для поддержки жизни на хостинге
if not os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    threading.Thread(target=run_infinite_rebound, daemon=True).start()

@app.route('/')
def health(): return "OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
