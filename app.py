import os
import math
import json
import asyncio
import threading
import requests
import time
import websockets
from flask import Flask

# --- НАСТРОЙКИ ТОРГОВЛИ НА ПРОЕДАНИЕ СТЕН ---
SYMBOL = 'SOL/USDC'
INITIAL_BALANCE = 100.0
LEVERAGE = 20
MARGIN_PER_TRADE = 10.0
POSITION_SIZE_USD = MARGIN_PER_TRADE * LEVERAGE  # $200

# Пороги стен
INITIAL_WALL_THRESHOLD_USD = 1_500_000  # Фиксируем стену, если объем >= $1.5M
EATEN_WALL_THRESHOLD_USD = 200_000      # Сигнал на вход, когда осталось < $200k

# Риск-менеджмент
TAKE_PROFIT_USD = 0.85                  # Тейк-профит на импульсе ($0.85)
STOP_LOSS_OFFSET = 0.25                 # Стоп-лосс за пробитый уровень ($0.25)

# Комиссии Binance Futures (Taker вход 0.05%, Maker TP 0.02%, Taker SL 0.05%)
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = "paper_state.json"

# --- FLASK WEB SERVER (для Render) ---
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return "Wall Eating Bot is Running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- TELEGRAM SENDER ---
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram Notice] {message}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(url, json=payload, timeout=5)
        data = res.json()
        if not data.get("ok"):
            print(f"❌ Telegram Error: {data.get('description')}")
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

# --- STATE MANAGEMENT ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "balance": INITIAL_BALANCE,
        "position": None,
        "cooldown_until": 0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "total_fees": 0.0
    }

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения состояния: {e}")

# --- АГРЕГАЦИЯ СТАКАНА С ШАГОМ 1.0 ---
def aggregate_orderbook(bids, asks, step=1.0):
    grouped_bids = {}
    grouped_asks = {}

    for p_str, q_str in bids:
        price = float(p_str)
        qty = float(q_str)
        level = math.floor(price / step) * step
        grouped_bids[level] = grouped_bids.get(level, 0.0) + (price * qty)

    for p_str, q_str in asks:
        price = float(p_str)
        qty = float(q_str)
        level = math.floor(price / step) * step
        grouped_asks[level] = grouped_asks.get(level, 0.0) + (price * qty)

    return grouped_bids, grouped_asks

# --- WEBSOCKET ДЛЯ ФЬЮЧЕРСНОГО СТАКАНА BINANCE ---
async def start_orderbook_ws():
    state = load_state()

    send_telegram(
        f"⚡ <b>Wall Breakout Bot (Проедание Стен) Запущен!</b>\n"
        f"Пара: {SYMBOL} | Группировка: $1.0\n"
        f"Детектор стены: <b>≥ ${INITIAL_WALL_THRESHOLD_USD:,.0f}</b>\n"
        f"Триггер входа: <b>< ${EATEN_WALL_THRESHOLD_USD:,.0f}</b>\n"
        f"Баланс: ${state['balance']:.2f}"
    )

    ws_symbol = SYMBOL.replace('/', '').lower()
    ws_url = f"wss://fstream.binance.com/ws/{ws_symbol}@depth20@100ms"

    # Память для отслеживания стен (кэш пиковых объемов)
    tracked_ask_walls = {}  # {level: max_volume}
    tracked_bid_walls = {}  # {level: max_volume}

    while True:
        try:
            print(f"🔌 Подключение к WebSocket стакана Futures: {ws_url}")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                print("✅ Стакан подключен! Мониторим проедание стен...")

                async for message in ws:
                    data = json.loads(message)
                    bids = data.get('b', [])
                    asks = data.get('a', [])

                    if not bids or not asks:
                        continue

                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])
                    current_price = (best_bid + best_ask) / 2.0

                    g_bids, g_asks = aggregate_orderbook(bids, asks, step=1.0)

                    # --- ОБНОВЛЕНИЕ КЭША ИЗВЕСТНЫХ СТЕН ---
                    for lvl, vol in g_asks.items():
                        if vol >= INITIAL_WALL_THRESHOLD_USD:
                            tracked_ask_walls[lvl] = max(tracked_ask_walls.get(lvl, 0), vol)

                    for lvl, vol in g_bids.items():
                        if vol >= INITIAL_WALL_THRESHOLD_USD:
                            tracked_bid_walls[lvl] = max(tracked_bid_walls.get(lvl, 0), vol)

                    # --- 1. ЕСЛИ ПОЗИЦИЯ УЖЕ ОТКРЫТА ---
                    if state['position'] is not None:
                        pos = state['position']
                        entry = pos['entry']
                        sl = pos['sl']
                        tp = pos['tp']
                        pos_type = pos['type']

                        if pos_type == 'LONG':
                            # Тейк-Профит (Maker)
                            if current_price >= tp:
                                exit_p = tp
                                gross_pnl = (POSITION_SIZE_USD / entry) * (exit_p - entry)
                                fee = (POSITION_SIZE_USD * TAKER_FEE) + (POSITION_SIZE_USD * MAKER_FEE)
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['wins'] += 1
                                state['total_trades'] += 1
                                state['position'] = None
                                save_state(state)

                                send_telegram(
                                    f"🎯 <b>LONG ЗАКРЫТ ПО ТЕЙК-ПРОФИТУ!</b>\n"
                                    f"Вход: ${entry:.2f} ➔ Выход: ${exit_p:.2f}\n"
                                    f"Профит: <b>+${net_pnl:.2f}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                            # Стоп-Лосс (Taker)
                            elif current_price <= sl:
                                exit_p = sl
                                gross_pnl = (POSITION_SIZE_USD / entry) * (exit_p - entry)
                                fee = (POSITION_SIZE_USD * TAKER_FEE) * 2
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['losses'] += 1
                                state['total_trades'] += 1
                                state['position'] = None
                                state['cooldown_until'] = time.time() + 300  # Пауза 5 минут
                                save_state(state)

                                send_telegram(
                                    f"🛑 <b>LONG ЗАКРЫТ ПО СТОП-ЛОССУ (Ложный пробой)</b>\n"
                                    f"Вход: ${entry:.2f} ➔ Выход: ${exit_p:.2f}\n"
                                    f"Убыток: <b>-${abs(net_pnl):.2f}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                        elif pos_type == 'SHORT':
                            # Тейк-Профит (Maker)
                            if current_price <= tp:
                                exit_p = tp
                                gross_pnl = (POSITION_SIZE_USD / entry) * (entry - exit_p)
                                fee = (POSITION_SIZE_USD * TAKER_FEE) + (POSITION_SIZE_USD * MAKER_FEE)
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['wins'] += 1
                                state['total_trades'] += 1
                                state['position'] = None
                                save_state(state)

                                send_telegram(
                                    f"🎯 <b>SHORT ЗАКРЫТ ПО ТЕЙК-ПРОФИТУ!</b>\n"
                                    f"Вход: ${entry:.2f} ➔ Выход: ${exit_p:.2f}\n"
                                    f"Профит: <b>+${net_pnl:.2f}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                            # Стоп-Лосс (Taker)
                            elif current_price >= sl:
                                exit_p = sl
                                gross_pnl = (POSITION_SIZE_USD / entry) * (entry - exit_p)
                                fee = (POSITION_SIZE_USD * TAKER_FEE) * 2
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['losses'] += 1
                                state['total_trades'] += 1
                                state['position'] = None
                                state['cooldown_until'] = time.time() + 300  # Пауза 5 минут
                                save_state(state)

                                send_telegram(
                                    f"🛑 <b>SHORT ЗАКРЫТ ПО СТОП-ЛОССУ (Ложный пробой)</b>\n"
                                    f"Вход: ${entry:.2f} ➔ Выход: ${exit_p:.2f}\n"
                                    f"Убыток: <b>-${abs(net_pnl):.2f}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                    # --- 2. ПОИСК ПРОЕДАНИЯ СТЕН (ВХОД В СДЕЛКУ) ---
                    else:
                        if time.time() < state.get('cooldown_until', 0):
                            continue

                        # А) ПРОВЕРКА ПРОЕДАНИЯ СТЕНЫ ПРОДАВЦОВ (ASKS) ➔ ВХОД В LONG
                        for wall_lvl, peak_vol in list(tracked_ask_walls.items()):
                            # Если цена находится близко к этой стене
                            if (wall_lvl - 0.50) <= current_price <= (wall_lvl + 0.30):
                                current_vol = g_asks.get(wall_lvl, 0.0)

                                # УСЛОВИЕ: Раньше была стена $1.5M+, а сейчас осталось меньше $200k
                                if current_vol < EATEN_WALL_THRESHOLD_USD:
                                    entry_p = current_price
                                    sl_p = wall_lvl - STOP_LOSS_OFFSET
                                    tp_p = entry_p + TAKE_PROFIT_USD

                                    state['position'] = {
                                        'type': 'LONG',
                                        'entry': entry_p,
                                        'sl': sl_p,
                                        'tp': tp_p,
                                        'wall_price': wall_lvl
                                    }
                                    save_state(state)
                                    del tracked_ask_walls[wall_lvl]  # Удаляем пробитую стену

                                    send_telegram(
                                        f"🚀 <b>ПРОЕДАНИЕ СТЕНЫ! ВХОД В LONG</b>\n"
                                        f"🔥 Пробита стена: <b>${wall_lvl:.0f}</b> (Пик: ${peak_vol/1e6:.2f}M ➔ Ост: ${current_vol/1e3:.0f}k)\n"
                                        f"Вход по маркету: <b>${entry_p:.2f}</b>\n"
                                        f"Тейк-профит: ${tp_p:.2f} | Стоп-лосс: ${sl_p:.2f}"
                                    )
                                    break

                        if state['position'] is not None:
                            continue

                        # Б) ПРОВЕРКА ПРОЕДАНИЯ СТЕНЫ ПОКУПАТЕЛЕЙ (BIDS) ➔ ВХОД В SHORT
                        for wall_lvl, peak_vol in list(tracked_bid_walls.items()):
                            if (wall_lvl - 0.30) <= current_price <= (wall_lvl + 1.50):
                                current_vol = g_bids.get(wall_lvl, 0.0)

                                if current_vol < EATEN_WALL_THRESHOLD_USD:
                                    entry_p = current_price
                                    sl_p = wall_lvl + 1.0 + STOP_LOSS_OFFSET
                                    tp_p = entry_p - TAKE_PROFIT_USD

                                    state['position'] = {
                                        'type': 'SHORT',
                                        'entry': entry_p,
                                        'sl': sl_p,
                                        'tp': tp_p,
                                        'wall_price': wall_lvl
                                    }
                                    save_state(state)
                                    del tracked_bid_walls[wall_lvl]  # Удаляем пробитую стену

                                    send_telegram(
                                        f"📉 <b>ПРОЕДАНИЕ СТЕНЫ! ВХОД В SHORT</b>\n"
                                        f"🔥 Пробита стена: <b>${wall_lvl:.0f}</b> (Пик: ${peak_vol/1e6:.2f}M ➔ Ост: ${current_vol/1e3:.0f}k)\n"
                                        f"Вход по маркету: <b>${entry_p:.2f}</b>\n"
                                        f"Тейк-профит: ${tp_p:.2f} | Стоп-лосс: ${sl_p:.2f}"
                                    )
                                    break

        except Exception as e:
            print(f"⚠️ Ошибка соединения стакана: {e}. Переподключение через 5 сек...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    asyncio.run(start_orderbook_ws())
