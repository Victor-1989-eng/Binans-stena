import os
import math
import json
import asyncio
import threading
import requests
import time
import websockets
from flask import Flask

# =====================================================================
# --- НАСТРОЙКИ ТОРГОВОГО АЛГОРИТМА ---
# =====================================================================
SYMBOL = 'SOL/USDC'
INITIAL_BALANCE = 100.0
LEVERAGE = 20
MARGIN_PER_TRADE = 10.0
POSITION_SIZE_USD = MARGIN_PER_TRADE * LEVERAGE  # $200 в рынке

# 1. Настройки агрегации стакана
ORDERBOOK_AGG_STEP = 0.1                # Шаг группировки уровней (0.1, 0.5, 1.0, 2.0)

# 2. Пороги определения и проедания стен
INITIAL_WALL_THRESHOLD_USD = 1_500_000  # Детект стены от $1.5M
EATEN_WALL_THRESHOLD_USD = 200_000      # Сигнал на вход, когда осталось < $200k

# 3. Фильтр "Тонкого стакана за стеной"
THIN_BOOK_CHECK_LEVELS = 3              # Сколько уровней ЗА стеной проверяем
MAX_BEHIND_WALL_VOL_USD = 500_000       # Макс. объем на любом из уровней за стеной

# 4. Риск-менеджмент
TAKE_PROFIT_USD = 0.50                  # Тейк-профит (+$0.50 от входа)
STOP_LOSS_OFFSET = 0.25                 # Фиксированный стоп-лосс ($0.25)

# 5. Настройки безубытка (Break-Even)
BREAKEVEN_TRIGGER_USD = 0.30            # Переводим в БУ при движении на +$0.30
BREAKEVEN_OFFSET_USD = 0.08             # +$0.08 перекрывает Taker-комиссии ($0.20)

# 6. Кулдаун отработанного уровня (в секундах)
LEVEL_COOLDOWN_SEC = 900                # 15 минут заморозки уровня после входа

# 7. Комиссии Binance Futures (Taker 0.05%, Maker 0.02%)
MAKER_FEE = 0.0002
TAKER_FEE = 0.0003

# 8. Телеграм и хранилище
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = "paper_state.json"

# =====================================================================
# --- FLASK WEB SERVER ---
# =====================================================================
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return "Wall Eating Scalper Bot is Running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# =====================================================================
# --- TELEGRAM NOTIFIER ---
# =====================================================================
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
        print(f"Ошибка отправки сообщения в Telegram: {e}")

# =====================================================================
# --- STATE MANAGEMENT ---
# =====================================================================
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

# =====================================================================
# --- АГРЕГАЦИЯ И ПРОВЕРКА СТАКАНОМ ---
# =====================================================================
def aggregate_orderbook(bids, asks, step=1.0):
    grouped_bids = {}
    grouped_asks = {}

    for p_str, q_str in bids:
        price = float(p_str)
        qty = float(q_str)
        # Округляем до 4 знаков для избежания багов float
        level = round(math.floor(price / step) * step, 4)
        grouped_bids[level] = grouped_bids.get(level, 0.0) + (price * qty)

    for p_str, q_str in asks:
        price = float(p_str)
        qty = float(q_str)
        level = round(math.floor(price / step) * step, 4)
        grouped_asks[level] = grouped_asks.get(level, 0.0) + (price * qty)

    return grouped_bids, grouped_asks

def is_book_thin_behind(grouped_book, wall_lvl, direction, step, num_levels, max_vol):
    """
    Проверяет, тонкий ли стакан ЗА пробиваемой стеной.
    Возвращает (True, None, 0), если стакан пуст/тонок.
    Возвращает (False, blocked_lvl, vol), если встречен плотный уровень.
    """
    for i in range(1, num_levels + 1):
        if direction == 'LONG':
            check_lvl = round(wall_lvl + (i * step), 4)
        else:
            check_lvl = round(wall_lvl - (i * step), 4)

        vol = grouped_book.get(check_lvl, 0.0)
        if vol > max_vol:
            return False, check_lvl, vol

    return True, None, 0.0

def format_pnl_str(pnl: float) -> str:
    if pnl >= 0:
        return f"+${pnl:.2f}"
    else:
        return f"-${abs(pnl):.2f}"

# =====================================================================
# --- ОСНОВНОЙ ЛУП WEBSOCKET БОТА ---
# =====================================================================
async def start_orderbook_ws():
    state = load_state()

    send_telegram(
        f"⚡ <b>Wall Breakout Bot v2.4 (с проверкой глубины) Запущен!</b>\n"
        f"Пара: {SYMBOL} | Шаг агрегации: <b>${ORDERBOOK_AGG_STEP}</b>\n"
        f"Детект стены: <b>≥ ${INITIAL_WALL_THRESHOLD_USD:,.0f}</b>\n"
        f"Триггер проедания: <b>< ${EATEN_WALL_THRESHOLD_USD:,.0f}</b>\n"
        f"Фильтр глубины: <b>{THIN_BOOK_CHECK_LEVELS} уровней < ${MAX_BEHIND_WALL_VOL_USD:,.0f}</b>\n"
        f"Тейк: <b>+$0.50</b> | Стоп: <b>-$0.25</b> | БУ: <b>+$0.30 (+0.08)</b>\n"
        f"Баланс: ${state['balance']:.2f}"
    )

    ws_symbol = SYMBOL.replace('/', '').lower()
    ws_url = f"wss://fstream.binance.com/ws/{ws_symbol}@depth20@100ms"

    tracked_ask_walls = {}
    tracked_bid_walls = {}
    level_cooldowns = {}

    while True:
        try:
            print(f"🔌 Подключение к WebSocket стакана Futures: {ws_url}")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                print("✅ Стакан подключен! Мониторим проедание стен и глубину...")

                async for message in ws:
                    data = json.loads(message)
                    bids = data.get('b', [])
                    asks = data.get('a', [])

                    if not bids or not asks:
                        continue

                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])
                    current_price = (best_bid + best_ask) / 2.0

                    # Агрегация с шагом и безопасным округлением
                    g_bids, g_asks = aggregate_orderbook(bids, asks, step=ORDERBOOK_AGG_STEP)

                    # Обновление реестра крупных стен
                    for lvl, vol in g_asks.items():
                        if vol >= INITIAL_WALL_THRESHOLD_USD:
                            tracked_ask_walls[lvl] = max(tracked_ask_walls.get(lvl, 0), vol)

                    for lvl, vol in g_bids.items():
                        if vol >= INITIAL_WALL_THRESHOLD_USD:
                            tracked_bid_walls[lvl] = max(tracked_bid_walls.get(lvl, 0), vol)

                    # 1. СОПРОВОЖДЕНИЕ ПОЗИЦИИ
                    if state['position'] is not None:
                        pos = state['position']
                        entry = pos['entry']
                        sl = pos['sl']
                        tp = pos['tp']
                        pos_type = pos['type']

                        if pos_type == 'LONG':
                            if not pos.get('is_breakeven', False) and current_price >= (entry + BREAKEVEN_TRIGGER_USD):
                                new_sl = entry + BREAKEVEN_OFFSET_USD
                                pos['sl'] = new_sl
                                pos['is_breakeven'] = True
                                save_state(state)
                                send_telegram(
                                    f"🛡️ <b>LONG ПЕРЕВЕДЕН В БЕЗУБЫТОК!</b>\n"
                                    f"Текущая цена: ${current_price:.2f}\n"
                                    f"Новый стоп-лосс: <b>${new_sl:.2f}</b> (Вход: ${entry:.2f})"
                                )

                            elif current_price >= tp:
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
                                    f"Профит: <b>{format_pnl_str(net_pnl)}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                            elif current_price <= sl:
                                exit_p = sl
                                gross_pnl = (POSITION_SIZE_USD / entry) * (exit_p - entry)
                                fee = (POSITION_SIZE_USD * TAKER_FEE) * 2
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['total_trades'] += 1

                                if pos.get('is_breakeven', False):
                                    msg_title = "🛡️ <b>LONG ЗАКРЫТ В БЕЗУБЫТОК</b>"
                                else:
                                    state['losses'] += 1
                                    state['cooldown_until'] = time.time() + 300
                                    msg_title = "🛑 <b>LONG ЗАКРЫТ ПО СТОП-ЛОССУ (Ложный пробой)</b>"

                                state['position'] = None
                                save_state(state)

                                send_telegram(
                                    f"{msg_title}\n"
                                    f"Вход: ${entry:.2f} ➔ Выход: ${exit_p:.2f}\n"
                                    f"Итог: <b>{format_pnl_str(net_pnl)}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                        elif pos_type == 'SHORT':
                            if not pos.get('is_breakeven', False) and current_price <= (entry - BREAKEVEN_TRIGGER_USD):
                                new_sl = entry - BREAKEVEN_OFFSET_USD
                                pos['sl'] = new_sl
                                pos['is_breakeven'] = True
                                save_state(state)
                                send_telegram(
                                    f"🛡️ <b>SHORT ПЕРЕВЕДЕН В БЕЗУБЫТОК!</b>\n"
                                    f"Текущая цена: ${current_price:.2f}\n"
                                    f"Новый стоп-лосс: <b>${new_sl:.2f}</b> (Вход: ${entry:.2f})"
                                )

                            elif current_price <= tp:
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
                                    f"Профит: <b>{format_pnl_str(net_pnl)}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                            elif current_price >= sl:
                                exit_p = sl
                                gross_pnl = (POSITION_SIZE_USD / entry) * (entry - exit_p)
                                fee = (POSITION_SIZE_USD * TAKER_FEE) * 2
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['total_trades'] += 1

                                if pos.get('is_breakeven', False):
                                    msg_title = "🛡️ <b>SHORT ЗАКРЫТ В БЕЗУБЫТОК</b>"
                                else:
                                    state['losses'] += 1
                                    state['cooldown_until'] = time.time() + 300
                                    msg_title = "🛑 <b>SHORT ЗАКРЫТ ПО СТОП-ЛОССУ (Ложный пробой)</b>"

                                state['position'] = None
                                save_state(state)

                                send_telegram(
                                    f"{msg_title}\n"
                                    f"Вход: ${entry:.2f} ➔ Выход: ${exit_p:.2f}\n"
                                    f"Итог: <b>{format_pnl_str(net_pnl)}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                    # 2. ПОИСК ТОЧЕК ВХОДА С ПРОВЕРКОЙ СТАКАНА
                    else:
                        if time.time() < state.get('cooldown_until', 0):
                            continue

                        # А) LONG
                        for wall_lvl, peak_vol in list(tracked_ask_walls.items()):
                            if time.time() < level_cooldowns.get(wall_lvl, 0):
                                continue

                            if (wall_lvl - 0.15) <= current_price <= (wall_lvl + 0.10):
                                current_vol = g_asks.get(wall_lvl, 0.0)

                                if current_vol < EATEN_WALL_THRESHOLD_USD:
                                    # ПРОВЕРКА: Тонкий ли стакан ЗА стеной?
                                    is_thin, block_lvl, block_vol = is_book_thin_behind(
                                        g_asks, wall_lvl, 'LONG', ORDERBOOK_AGG_STEP,
                                        THIN_BOOK_CHECK_LEVELS, MAX_BEHIND_WALL_VOL_USD
                                    )

                                    if not is_thin:
                                        print(f"⚠️ Пропуск LONG у ${wall_lvl}: уровень ${block_lvl} перекрыт объемом ${block_vol/1e3:.0f}k")
                                        continue

                                    entry_p = current_price
                                    sl_p = entry_p - STOP_LOSS_OFFSET
                                    tp_p = entry_p + TAKE_PROFIT_USD

                                    state['position'] = {
                                        'type': 'LONG',
                                        'entry': entry_p,
                                        'sl': sl_p,
                                        'tp': tp_p,
                                        'wall_price': wall_lvl,
                                        'is_breakeven': False
                                    }
                                    level_cooldowns[wall_lvl] = time.time() + LEVEL_COOLDOWN_SEC
                                    save_state(state)
                                    del tracked_ask_walls[wall_lvl]

                                    send_telegram(
                                        f"🚀 <b>ПРОЕДАНИЕ СТЕНЫ! ВХОД В LONG</b>\n"
                                        f"🔥 Пробита стена: <b>${wall_lvl:.2f}</b> (Пик: ${peak_vol/1e6:.2f}M ➔ Ост: ${current_vol/1e3:.0f}k)\n"
                                        f"📊 Проверка стакана: <b>Уровни за стеной чисты (<${MAX_BEHIND_WALL_VOL_USD/1e3:.0f}k)</b>\n"
                                        f"Вход по маркету: <b>${entry_p:.2f}</b>\n"
                                        f"Тейк-профит: ${tp_p:.2f} | Стоп-лосс: ${sl_p:.2f}"
                                    )
                                    break

                        if state['position'] is not None:
                            continue

                        # Б) SHORT
                        for wall_lvl, peak_vol in list(tracked_bid_walls.items()):
                            if time.time() < level_cooldowns.get(wall_lvl, 0):
                                continue

                            if (wall_lvl - 0.10) <= current_price <= (wall_lvl + 0.15):
                                current_vol = g_bids.get(wall_lvl, 0.0)

                                if current_vol < EATEN_WALL_THRESHOLD_USD:
                                    # ПРОВЕРКА: Тонкий ли стакан ЗА стеной?
                                    is_thin, block_lvl, block_vol = is_book_thin_behind(
                                        g_bids, wall_lvl, 'SHORT', ORDERBOOK_AGG_STEP,
                                        THIN_BOOK_CHECK_LEVELS, MAX_BEHIND_WALL_VOL_USD
                                    )

                                    if not is_thin:
                                        print(f"⚠️ Пропуск SHORT у ${wall_lvl}: уровень ${block_lvl} перекрыт объемом ${block_vol/1e3:.0f}k")
                                        continue

                                    entry_p = current_price
                                    sl_p = entry_p + STOP_LOSS_OFFSET
                                    tp_p = entry_p - TAKE_PROFIT_USD

                                    state['position'] = {
                                        'type': 'SHORT',
                                        'entry': entry_p,
                                        'sl': sl_p,
                                        'tp': tp_p,
                                        'wall_price': wall_lvl,
                                        'is_breakeven': False
                                    }
                                    level_cooldowns[wall_lvl] = time.time() + LEVEL_COOLDOWN_SEC
                                    save_state(state)
                                    del tracked_bid_walls[wall_lvl]

                                    send_telegram(
                                        f"📉 <b>ПРОЕДАНИЕ СТЕНЫ! ВХОД В SHORT</b>\n"
                                        f"🔥 Пробита стена: <b>${wall_lvl:.2f}</b> (Пик: ${peak_vol/1e6:.2f}M ➔ Ост: ${current_vol/1e3:.0f}k)\n"
                                        f"📊 Проверка стакана: <b>Уровни за стеной чисты (<${MAX_BEHIND_WALL_VOL_USD/1e3:.0f}k)</b>\n"
                                        f"Вход по маркету: <b>${entry_p:.2f}</b>\n"
                                        f"Тейк-профит: ${tp_p:.2f} | Стоп-лосс: ${sl_p:.2f}"
                                    )
                                    break

        except Exception as e:
            print(f"⚠️ Ошибка соединения стакана: {e}. Переподключение через 5 сек...")
            await asyncio.sleep(5)

# =====================================================================
# --- ТОЧКА ВХОДА ---
# =====================================================================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    asyncio.run(start_orderbook_ws())
