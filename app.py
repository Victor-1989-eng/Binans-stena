import os, time, threading, requests
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = os.environ.get("SYMBOL", "SOLUSDC")
THRESHOLD = 0.003       # 0.5% - Порог входа И переворота
STEP_DIFF = 0.002       # 0.2% - Шаг усреднения (если тянет дальше)
MAX_STEPS = 9           # Макс. кол-во усреднений (чтобы маржи хватило)
LEVERAGE = 30            # Плечо
MARGIN_STEP = 1.0       # Маржа на один ордер

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

# Память
current_steps = 0
last_entry_diff = 0

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

def run_swing_grid():
    global current_steps, last_entry_diff
    print(f"🔄 КАЧЕЛИ С УСРЕДНЕНИЕМ запущены. Порог: {THRESHOLD*100}%")
    send_tg(f"🤖 *Бот-Качели (Flip+Grid)*\nПорог переворота: `{THRESHOLD*100}%`\nШаг усреднения: `{STEP_DIFF*100}%`")
    
    while True:
        try:
            klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=50)
            closes = [float(k[4]) for k in klines[:-1]]
            curr_p = float(klines[-1][4])

            f_now = get_ema(closes, 7)
            s_now = get_ema(closes, 25)
            # diff положительный = резинка вверх (нужен шорт)
            # diff отрицательный = резинка вниз (нужен лонг)
            diff = (f_now - s_now) / s_now 

            pos = client.futures_position_information(symbol=SYMBOL)
            active_pos = next((p for p in pos if p['symbol'] == SYMBOL and float(p['positionAmt']) != 0), None)
            amt = float(active_pos['positionAmt']) if active_pos else 0

            # --- ЛОГИКА ---

            # 1. ЕСЛИ МЫ БЕЗ ПОЗИЦИИ (Первый запуск)
            if amt == 0:
                current_steps = 0
                if diff <= -THRESHOLD: # Резинка внизу (-0.005) -> ЛОНГ
                    execute_entry('BUY', curr_p)
                    last_entry_diff = diff # Запоминаем уровень (-0.005)
                    current_steps = 1
                elif diff >= THRESHOLD: # Резинка вверху (+0.005) -> ШОРТ
                    execute_entry('SELL', curr_p)
                    last_entry_diff = diff # Запоминаем уровень (+0.005)
                    current_steps = 1

            # 2. ЕСЛИ МЫ В ЛОНГЕ (amt > 0)
            elif amt > 0:
                # А) Усреднение (Цена падает ниже, diff становится более отрицательным)
                # Пример: зашли на -0.005, стало -0.007 (-0.005 - 0.002)
                if diff <= (last_entry_diff - STEP_DIFF) and current_steps < MAX_STEPS:
                    execute_entry('BUY', curr_p)
                    last_entry_diff = diff
                    current_steps += 1
                    send_tg(f"📉 Усреднение ЛОНГА №{current_steps}. Зазор: {diff*100:.2f}%")

                # Б) ПЕРЕВОРОТ В ШОРТ (Цена улетела вверх, diff стал +0.005)
                elif diff >= THRESHOLD:
                    flip_position('SELL', curr_p, "Верхний пик")
                    last_entry_diff = diff
                    current_steps = 1

            # 3. ЕСЛИ МЫ В ШОРТЕ (amt < 0)
            elif amt < 0:
                # А) Усреднение (Цена растет выше, diff становится более положительным)
                # Пример: зашли на 0.005, стало 0.007 (0.005 + 0.002)
                if diff >= (last_entry_diff + STEP_DIFF) and current_steps < MAX_STEPS:
                    execute_entry('SELL', curr_p)
                    last_entry_diff = diff
                    current_steps += 1
                    send_tg(f"📈 Усреднение ШОРТА №{current_steps}. Зазор: {diff*100:.2f}%")

                # Б) ПЕРЕВОРОТ В ЛОНГ (Цена упала вниз, diff стал -0.005)
                elif diff <= -THRESHOLD:
                    flip_position('BUY', curr_p, "Нижний пик")
                    last_entry_diff = diff
                    current_steps = 1

        except Exception as e:
            print(f"Err: {e}")
        
        time.sleep(30)

def execute_entry(side, price):
    try:
        # Авто-кросс
        try: client.futures_change_margin_type(symbol=SYMBOL, marginType='CROSSED')
        except: pass
        
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty)
        send_tg(f"✅ *ВХОД {side}* (Добор). Цена: `{price}`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

def flip_position(new_side, price, reason):
    try:
        # 1. Закрываем старую позицию полностью
        pos = client.futures_position_information(symbol=SYMBOL)
        old_qty = abs(float(next(p for p in pos if p['symbol'] == SYMBOL)['positionAmt']))
        
        # Определяем сторону закрытия (если новый SELL, значит закрываем BUY)
        close_side = 'SELL' if new_side == 'SELL' else 'BUY' 
        
        # Сначала закрываем старое
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='MARKET', quantity=old_qty, reduceOnly=True)
        send_tg(f"💰 *ЗАКРЫТИЕ ПОЗИЦИИ* ({reason})")
        time.sleep(1) # Секунда передышки, чтобы биржа обработала закрытие

        # 2. Открываем новую позицию с нуля (первый шаг)
        new_qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        client.futures_create_order(symbol=SYMBOL, side=new_side, type='MARKET', quantity=new_qty)
        send_tg(f"🚀 *ПЕРЕВОРОТ В {new_side}*. Цена: `{price}`")
        
    except Exception as e:
        send_tg(f"❌ Ошибка переворота: {e}")

if not os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    threading.Thread(target=run_swing_grid, daemon=True).start()

@app.route('/')
def health(): return "OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
