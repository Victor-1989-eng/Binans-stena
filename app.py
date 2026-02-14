import os, json, time, threading, requests
from flask import Flask
from binance.client import Client
import websocket

app = Flask(__name__)

# ================= НАСТРОЙКИ (ПОД ТВОЮ СХЕМУ) =================
IS_PAPER_MODE = True       # True - тесты, False - реальные деньги!
SYMBOL_UPPER = "SOLUSDT"
SYMBOL_LOWER = "solusdt"   # Для WebSocket

# Параметры индикаторов
EMA_FAST = 25
EMA_SLOW = 99
TREND_CONFIRM = 0.0005     # Зазор 0.05% для входа/перезахода
REVERSE_GAP = 0.003        # "Резинка" 0.9% для переворота

# Параметры депозита
LEVERAGE = 30
MARGIN_STEP = 10.0         # Маржа на один шаг (из твоих $1000)
VIRTUAL_BALANCE = 100.0   # Тестовый баланс
# ==============================================================

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
closes = []
paper_vars = {"pos_amt": 0, "entry_price": 0, "side": None, "balance": VIRTUAL_BALANCE}
max_stats = {"max_long_gap": 0, "max_short_gap": 0}

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except Exception as e:
            print(f"Ошибка TG: {e}")

def get_ema(values, span):
    if len(values) < span: return values[-1]
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]:
        ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def execute_order(side, price, gap):
    global paper_vars
    prefix = "📝 [PAPER]" if IS_PAPER_MODE else "🚀 [REAL]"
    
    # В Paper Mode при открытии новой сделки "закрываем" старую в уме
    if IS_PAPER_MODE and paper_vars["side"] is not None:
        p_factor = 1 if paper_vars["side"] == "BUY" else -1
        profit = (price - paper_vars["entry_price"]) * paper_vars["pos_amt"] * p_factor
        paper_vars["balance"] += profit
        send_tg(f"💰 Промежуточный фикс: `${profit:.2f}`. Баланс: `${paper_vars['balance']:.2f}`")

    if IS_PAPER_MODE:
        paper_vars["side"] = side
        paper_vars["entry_price"] = price
        paper_vars["pos_amt"] = (MARGIN_STEP * LEVERAGE) / price
        send_tg(f"{prefix} Вход {side} по `{price}`. Gap: `{gap:.5f}`")
    else:
        # Логика для реальных торгов на Binance
        try:
            client.futures_change_leverage(symbol=SYMBOL_UPPER, leverage=LEVERAGE)
            qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
            if qty < 0.1: qty = 0.1
            client.futures_create_order(symbol=SYMBOL_UPPER, side=side, type='MARKET', quantity=qty)
            send_tg(f"{prefix} Реальный ордер {side} на `{qty}` исполнен.")
        except Exception as e:
            send_tg(f"❌ Ошибка Binance: `{e}`")

def close_and_clear_paper(price):
    global paper_vars
    if paper_vars["side"] is not None:
        p_factor = 1 if paper_vars["side"] == "BUY" else -1
        profit = (price - paper_vars["entry_price"]) * paper_vars["pos_amt"] * p_factor
        paper_vars["balance"] += profit
        send_tg(f"🏁 СТОП ТРЕНД. Закрыто: `${profit:.2f}`. Баланс: `${paper_vars['balance']:.2f}`. Ждем новый крест.")
        paper_vars["side"] = None
        paper_vars["pos_amt"] = 0

def process_candle(close_price):
    global closes, max_stats, paper_vars
    closes.append(close_price)
    if len(closes) > 300: closes.pop(0)
    if len(closes) < EMA_SLOW: return

    f_ema = get_ema(closes, EMA_FAST)
    s_ema = get_ema(closes, EMA_SLOW)
    gap = (close_price - f_ema) / f_ema

    # Статистика разрывов
    if gap > max_stats["max_short_gap"]: max_stats["max_short_gap"] = gap
    if gap < max_stats["max_long_gap"]: max_stats["max_long_gap"] = gap

    cross_up = f_ema > s_ema
    cross_down = f_ema < s_ema
    curr_side = paper_vars["side"]

    # 1. ВХОД ПО ТРЕНДУ (Если вне позиции)
    if curr_side is None:
        if cross_up and gap >= TREND_CONFIRM:
            execute_order("BUY", close_price, gap)
        elif cross_down and gap <= -TREND_CONFIRM:
            execute_order("SELL", close_price, gap)

    # 2. ЛОГИКА В ПОЗИЦИИ
    else:
        # А) ПЕРЕВОРOT ПО "РЕЗИНКЕ" (0.009)
        if curr_side == "BUY" and gap >= REVERSE_GAP:
            send_tg(f"⚡️ ПЕРЕВОРOT! Резинка +{gap:.4f}. Входим в ШОРТ.")
            execute_order("SELL", close_price, gap)
        
        elif curr_side == "SELL" and gap <= -REVERSE_GAP:
            send_tg(f"⚡️ ПЕРЕВОРOT! Резинка {gap:.4f}. Входим в ЛОНГ.")
            execute_order("BUY", close_price, gap)

        # Б) ЗАКРЫТИЕ И ПЕРЕЗАХОД ПРИ ВОЗВРАТЕ К СРЕДНЕЙ (Твой сценарий)
        # Если были в Шорте после переворота и цена ушла НИЖЕ средней на зазор
        elif curr_side == "SELL" and cross_up and gap <= -TREND_CONFIRM:
            send_tg("🎯 Возврат к средней пройден! Фикс Шорта -> Новый ЛОНГ по тренду")
            execute_order("BUY", close_price, gap)
            
        elif curr_side == "BUY" and cross_down and gap >= TREND_CONFIRM:
            send_tg("🎯 Возврат к средней пройден! Фикс Лонга -> Новый ШОРТ по тренду")
            execute_order("SELL", close_price, gap)

        # В) ОКОНЧАТЕЛЬНЫЙ ВЫХОД ПРИ ПЕРЕСЕЧЕНИИ EMA
        if (curr_side == "BUY" and cross_down) or (curr_side == "SELL" and cross_up):
            close_and_clear_paper(close_price)

def start_socket():
    url = f"wss://fstream.binance.com/ws/{SYMBOL_LOWER}@kline_1m"
    def on_message(ws, msg):
        js = json.loads(msg)
        if js['k']['x']: process_candle(float(js['k']['c']))
    def on_error(ws, err): print(f"Socket Error: {err}")
    def on_close(ws, a, b): 
        time.sleep(5)
        start_socket()
    ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close)
    ws.run_forever()

threading.Thread(target=start_socket, daemon=True).start()

@app.route('/')
def index():
    status = "PAPER" if IS_PAPER_MODE else "REAL"
    return {
        "mode": status,
        "balance": f"{paper_vars['balance']:.2f}$",
        "current_side": paper_vars["side"],
        "max_up": f"{max_stats['max_short_gap']:.5f}",
        "max_down": f"{max_stats['max_long_gap']:.5f}"
    }

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
