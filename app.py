
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
# НАСТРОЙКИ ТОРГОВОГО АЛГОРИТМА
# =====================================================================

SYMBOL = 'SOL/USDC'

INITIAL_BALANCE = 100.0
LEVERAGE = 20
MARGIN_PER_TRADE = 10.0
POSITION_SIZE_USD = MARGIN_PER_TRADE * LEVERAGE

# Агрегация стакана
ORDERBOOK_AGG_STEP = 0.01

# Определение стен
INITIAL_WALL_THRESHOLD_USD = 300_000
EATEN_WALL_THRESHOLD_USD = 100_000

# Проверка стакана за стеной
THIN_BOOK_CHECK_LEVELS = 10
MAX_BEHIND_WALL_VOL_USD = 20_000

# Торговые параметры (изменение цены SOL в долларах)
TAKE_PROFIT_USD = 0.15
STOP_LOSS_OFFSET = 0.15

# Безубыток
BREAKEVEN_TRIGGER_USD = 0.20
BREAKEVEN_OFFSET_USD = 0.08

# Кулдаун
LEVEL_COOLDOWN_SEC = 480

# Комиссии
MAKER_FEE = 0.0003
TAKER_FEE = 0.0003

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Хранилище
STATE_FILE = "paper_state.json"

# Дополнительные параметры контроля стен
WALL_NEAR_DISTANCE_PERCENT = 0.001
WALL_NEAR_MIN_STEPS = 3
WALL_STALE_SEC = 3
WALL_REAPPEAR_SEC = 2

# =====================================================================
# FLASK WEB SERVER
# =====================================================================

app = Flask(__name__)


@app.route("/")
@app.route("/health")
def health_check():
    return "Wall Eating Scalper Bot is Running!", 200


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# =====================================================================
# TELEGRAM NOTIFIER
# =====================================================================

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram Notice] {message}")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=5
        )

        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            print(
                f"Telegram Error: "
                f"{data.get('description')}"
            )

    except Exception as e:
        print(f"Ошибка отправки Telegram: {e}")


# =====================================================================
# STATE MANAGEMENT
# =====================================================================

def default_state():
    return {
        "balance": INITIAL_BALANCE,
        "position": None,
        "cooldown_until": 0,
        "level_cooldowns": {},
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "total_fees": 0.0
    }


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(
                STATE_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                state = json.load(f)

            defaults = default_state()

            for key, value in defaults.items():
                if key not in state:
                    state[key] = value

            return state

        except Exception as e:
            print(f"Ошибка загрузки состояния: {e}")

    return default_state()


def save_state(state):
    try:
        temp_file = STATE_FILE + ".tmp"

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(temp_file, STATE_FILE)

    except Exception as e:
        print(f"Ошибка сохранения состояния: {e}")


# =====================================================================
# АГРЕГАЦИЯ СТАКАНА
# =====================================================================

def aggregate_orderbook(bids, asks, step=1.0):

    grouped_bids = {}
    grouped_asks = {}

    for p_str, q_str in bids:

        price = float(p_str)
        qty = float(q_str)

        level = round(
            math.floor(price / step) * step,
            4
        )

        grouped_bids[level] = (
            grouped_bids.get(level, 0.0)
            + price * qty
        )

    for p_str, q_str in asks:

        price = float(p_str)
        qty = float(q_str)

        level = round(
            math.floor(price / step) * step,
            4
        )

        grouped_asks[level] = (
            grouped_asks.get(level, 0.0)
            + price * qty
        )

    return grouped_bids, grouped_asks


# =====================================================================
# ПРОВЕРКА ТОНКОГО СТАКАНА ЗА СТЕНОЙ
# =====================================================================

def is_book_thin_behind(
    grouped_book,
    wall_lvl,
    direction,
    step,
    num_levels,
    max_vol
):

    for i in range(1, num_levels + 1):

        if direction == "LONG":

            check_lvl = round(
                wall_lvl + (i * step),
                4
            )

        else:

            check_lvl = round(
                wall_lvl - (i * step),
                4
            )

        vol = grouped_book.get(check_lvl, 0.0)

        if vol > max_vol:

            return False, check_lvl, vol

    return True, None, 0.0


# =====================================================================
# ФОРМАТИРОВАНИЕ PNL
# =====================================================================

def format_pnl_str(pnl: float) -> str:

    if pnl >= 0:
        return f"+${pnl:.2f}"

    return f"-${abs(pnl):.2f}"


# =====================================================================
# КЛЮЧ КУЛДАУНА
# =====================================================================

def get_cooldown_key(direction, level):

    return f"{direction}_{level:.4f}"


def is_level_in_cooldown(state, direction, level):

    key = get_cooldown_key(direction, level)

    cooldowns = state.get("level_cooldowns", {})

    return time.time() < cooldowns.get(key, 0)


def activate_level_cooldown(state, direction, level):

    key = get_cooldown_key(direction, level)

    state.setdefault("level_cooldowns", {})

    state["level_cooldowns"][key] = (
        time.time() + LEVEL_COOLDOWN_SEC
    )


def clean_expired_cooldowns(state):

    now = time.time()

    cooldowns = state.get("level_cooldowns", {})

    state["level_cooldowns"] = {
        key: expiry
        for key, expiry in cooldowns.items()
        if expiry > now
    }


# =====================================================================
# ОТКРЫТИЕ PAPER-ПОЗИЦИИ
# =====================================================================

def open_paper_position(
    state,
    direction,
    entry_price,
    wall_level
):

    if state["position"] is not None:
        return False

    if state["balance"] < MARGIN_PER_TRADE:
        print("Недостаточно баланса для новой позиции.")
        return False

    quantity = POSITION_SIZE_USD / entry_price

    if direction == "LONG":

        tp = entry_price + TAKE_PROFIT_USD
        sl = entry_price - STOP_LOSS_OFFSET

    else:

        tp = entry_price - TAKE_PROFIT_USD
        sl = entry_price + STOP_LOSS_OFFSET

    state["position"] = {
        "type": direction,
        "entry": entry_price,
        "quantity": quantity,
        "notional": POSITION_SIZE_USD,
        "wall_level": wall_level,
        "tp": tp,
        "sl": sl,
        "is_breakeven": False,
        "opened_at": time.time()
    }

    state["total_trades"] += 1

    activate_level_cooldown(
        state,
        direction,
        wall_level
    )

    save_state(state)

    send_telegram(
        f"🚀 <b>ОТКРЫТА PAPER ПОЗИЦИЯ {direction}!</b>\n"
        f"Пара: {SYMBOL}\n"
        f"Вход: <b>${entry_price:.4f}</b>\n"
        f"Стена: ${wall_level:.4f}\n"
        f"Объем позиции: ${POSITION_SIZE_USD:.2f}\n"
        f"Плечо: {LEVERAGE}x\n"
        f"Тейк-профит: ${tp:.4f}\n"
        f"Стоп-лосс: ${sl:.4f}"
    )

    print(
        f"Открыта {direction}: "
        f"entry={entry_price:.4f}, "
        f"TP={tp:.4f}, SL={sl:.4f}"
    )

    return True


# =====================================================================
# ЗАКРЫТИЕ PAPER-ПОЗИЦИИ
# =====================================================================

def close_paper_position(
    state,
    exit_price,
    reason
):

    pos = state["position"]

    if pos is None:
        return

    entry = pos["entry"]
    quantity = pos["quantity"]
    direction = pos["type"]

    entry_notional = entry * quantity
    exit_notional = exit_price * quantity

    if direction == "LONG":

        gross_pnl = quantity * (exit_price - entry)

    else:

        gross_pnl = quantity * (entry - exit_price)

    # Комиссия за открытие и закрытие
    fee = (
        entry_notional * TAKER_FEE
        + exit_notional * TAKER_FEE
    )

    net_pnl = gross_pnl - fee

    state["balance"] += net_pnl
    state["total_fees"] += fee

    if net_pnl >= 0:
        state["wins"] += 1
    else:
        state["losses"] += 1

    state["position"] = None

    save_state(state)

    send_telegram(
        f"🏁 <b>ПОЗИЦИЯ {direction} ЗАКРЫТА!</b>\n"
        f"Причина: {reason}\n"
        f"Вход: ${entry:.4f}\n"
        f"Выход: ${exit_price:.4f}\n"
        f"Валовая прибыль: {format_pnl_str(gross_pnl)}\n"
        f"Комиссия: ${fee:.4f}\n"
        f"Чистый результат: "
        f"<b>{format_pnl_str(net_pnl)}</b>\n"
        f"Баланс: <b>${state['balance']:.2f}</b>\n"
        f"Всего сделок: {state['total_trades']}\n"
        f"Побед: {state['wins']} | "
        f"Убытков: {state['losses']}"
    )

    print(
        f"Закрыта {direction}: "
        f"PNL={net_pnl:.4f}, "
        f"balance={state['balance']:.2f}"
    )


# =====================================================================
# СОПРОВОЖДЕНИЕ ПОЗИЦИИ
# =====================================================================

def manage_position(state, current_price):

    pos = state["position"]

    if pos is None:
        return

    entry = pos["entry"]
    sl = pos["sl"]
    tp = pos["tp"]
    direction = pos["type"]

    # -------------------------------------------------
    # LONG
    # -------------------------------------------------

    if direction == "LONG":

        if (
            not pos.get("is_breakeven", False)
            and current_price >= (
                entry + BREAKEVEN_TRIGGER_USD
            )
        ):

            new_sl = entry + BREAKEVEN_OFFSET_USD

            pos["sl"] = new_sl
            pos["is_breakeven"] = True

            save_state(state)

            send_telegram(
                "🛡️ <b>LONG ПЕРЕВЕДЕН В БЕЗУБЫТОК!</b>\n"
                f"Текущая цена: ${current_price:.4f}\n"
                f"Новый стоп: ${new_sl:.4f}"
            )

        if current_price >= tp:

            close_paper_position(
                state,
                tp,
                "TAKE PROFIT"
            )

        elif current_price <= pos["sl"]:

            close_paper_position(
                state,
                pos["sl"],
                "STOP LOSS / BREAKEVEN"
            )

    # -------------------------------------------------
    # SHORT
    # -------------------------------------------------

    elif direction == "SHORT":

        if (
            not pos.get("is_breakeven", False)
            and current_price <= (
                entry - BREAKEVEN_TRIGGER_USD
            )
        ):

            new_sl = entry - BREAKEVEN_OFFSET_USD

            pos["sl"] = new_sl
            pos["is_breakeven"] = True

            save_state(state)

            send_telegram(
                "🛡️ <b>SHORT ПЕРЕВЕДЕН В БЕЗУБЫТОК!</b>\n"
                f"Текущая цена: ${current_price:.4f}\n"
                f"Новый стоп: ${new_sl:.4f}"
            )

        if current_price <= tp:

            close_paper_position(
                state,
                tp,
                "TAKE PROFIT"
            )

        elif current_price >= pos["sl"]:

            close_paper_position(
                state,
                pos["sl"],
                "STOP LOSS / BREAKEVEN"
            )


# =====================================================================
# ОБНОВЛЕНИЕ РЕЕСТРА СТЕН
# =====================================================================

def update_wall_registry(
    tracked_walls,
    grouped_book,
    current_price,
    direction,
    now
):

    for level, volume in grouped_book.items():

        if volume >= INITIAL_WALL_THRESHOLD_USD:

            if level not in tracked_walls:

                tracked_walls[level] = {
                    "initial_volume": volume,
                    "current_volume": volume,
                    "last_seen": now
                }

            else:

                tracked_walls[level]["current_volume"] = volume
                tracked_walls[level]["last_seen"] = now

    for level in list(tracked_walls.keys()):

        wall = tracked_walls[level]

        if level in grouped_book:

            wall["current_volume"] = grouped_book[level]
            wall["last_seen"] = now

        else:

            if direction == "LONG":

                crossed = current_price >= level

            else:

                crossed = current_price <= level

            if crossed:

                wall["current_volume"] = 0.0

            elif now - wall["last_seen"] > WALL_STALE_SEC:

                del tracked_walls[level]


# =====================================================================
# ПОИСК СИГНАЛА ПРОЕДАНИЯ СТЕНЫ
# =====================================================================

def find_wall_signal(
    state,
    tracked_walls,
    grouped_book,
    behind_book,
    current_price,
    direction,
    now
):

    levels = sorted(
        tracked_walls.keys(),
        reverse=(direction == "SHORT")
    )

    for wall_level in levels:

        wall = tracked_walls[wall_level]

        if is_level_in_cooldown(
            state,
            direction,
            wall_level
        ):
            continue

        volume = wall["current_volume"]

        if volume > EATEN_WALL_THRESHOLD_USD:
            continue

        if direction == "LONG":

            crossed = current_price >= wall_level

        else:

            crossed = current_price <= wall_level

        distance = abs(current_price - wall_level)

        max_distance = max(
            current_price * WALL_NEAR_DISTANCE_PERCENT,
            ORDERBOOK_AGG_STEP * WALL_NEAR_MIN_STEPS
        )

        if not crossed and distance > max_distance:
            continue

        is_thin, blocked_level, blocked_volume = (
            is_book_thin_behind(
                behind_book,
                wall_level,
                direction,
                ORDERBOOK_AGG_STEP,
                THIN_BOOK_CHECK_LEVELS,
                MAX_BEHIND_WALL_VOL_USD
            )
        )

        if not is_thin:

            print(
                f"⛔ {direction}: стена ${wall_level:.4f} "
                f"заблокирована уровнем "
                f"${blocked_level:.4f}, "
                f"объем ${blocked_volume:,.0f}"
            )

            continue

        return wall_level

    return None


# =====================================================================
# ОСНОВНОЙ WEBSOCKET БОТА
# =====================================================================

async def start_orderbook_ws():

    state = load_state()

    send_telegram(
        "⚡ <b>Wall Eating Scalper Bot v2.5 запущен!</b>\n"
        f"Пара: {SYMBOL}\n"
        f"Шаг агрегации: ${ORDERBOOK_AGG_STEP}\n"
        f"Детект стены: ≥ "
        f"${INITIAL_WALL_THRESHOLD_USD:,.0f}\n"
        f"Триггер проедания: менее "
        f"${EATEN_WALL_THRESHOLD_USD:,.0f}\n"
        f"Фильтр глубины: {THIN_BOOK_CHECK_LEVELS} уровней\n"
        f"Максимальный объем: "
        f"${MAX_BEHIND_WALL_VOL_USD:,.0f}\n"
        f"Тейк: ${TAKE_PROFIT_USD:.2f}\n"
        f"Стоп: ${STOP_LOSS_OFFSET:.2f}\n"
        f"Безубыток: "
        f"${BREAKEVEN_TRIGGER_USD:.2f} "
        f"(смещение ${BREAKEVEN_OFFSET_USD:.2f})\n"
        f"Баланс: ${state['balance']:.2f}\n"
        f"Режим: PAPER TRADING"
    )

    ws_symbol = SYMBOL.replace("/", "").lower()

    ws_url = (
        f"wss://fstream.binance.com/ws/"
        f"{ws_symbol}@depth20@100ms"
    )

    tracked_ask_walls = {}
    tracked_bid_walls = {}

    while True:

        try:

            print(
                f"🔌 Подключение к Binance Futures: {ws_url}"
            )

            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=20
            ) as ws:

                print(
                    "✅ Стакан подключен! "
                    "Мониторинг стен активен."
                )

                # После переподключения реестр стен очищается.
                # Уже открытая позиция сохраняется в state.

                tracked_ask_walls.clear()
                tracked_bid_walls.clear()

                async for message in ws:

                    try:

                        data = json.loads(message)

                        bids = data.get("b", [])
                        asks = data.get("a", [])

                        if not bids or not asks:
                            continue

                        best_bid = float(bids[0][0])
                        best_ask = float(asks[0][0])

                        current_price = (
                            best_bid + best_ask
                        ) / 2.0

                        now = time.time()

                        # Агрегация стакана
                        g_bids, g_asks = aggregate_orderbook(
                            bids,
                            asks,
                            step=ORDERBOOK_AGG_STEP
                        )

                        # Обновление реестра стен
                        update_wall_registry(
                            tracked_ask_walls,
                            g_asks,
                            current_price,
                            "LONG",
                            now
                        )

                        update_wall_registry(
                            tracked_bid_walls,
                            g_bids,
                            current_price,
                            "SHORT",
                            now
                        )

                        # Очистка кулдаунов
                        clean_expired_cooldowns(state)

                        # Сохранение кулдаунов периодически
                        if int(now) % 10 == 0:
                            save_state(state)

                        # -----------------------------------------
                        # 1. СОПРОВОЖДЕНИЕ ПОЗИЦИИ
                        # -----------------------------------------

                        if state["position"] is not None:

                            manage_position(
                                state,
                                current_price
                            )

                            # Пока позиция открыта,
                            # новые сделки не открываем.
                            if state["position"] is not None:
                                continue

                        # -----------------------------------------
                        # 2. ПОИСК LONG
                        # -----------------------------------------

                        wall_level = find_wall_signal(
                            state,
                            tracked_ask_walls,
                            g_asks,
                            g_asks,
                            current_price,
                            "LONG",
                            now
                        )

                        if wall_level is not None:

                            opened = open_paper_position(
                                state,
                                "LONG",
                                current_price,
                                wall_level
                            )

                            if opened:

                                tracked_ask_walls.pop(
                                    wall_level,
                                    None
                                )

                                continue

                        # -----------------------------------------
                        # 3. ПОИСК SHORT
                        # -----------------------------------------

                        wall_level = find_wall_signal(
                            state,
                            tracked_bid_walls,
                            g_bids,
                            current_price,
                            "SHORT",
                            now
                        )

                        if wall_level is not None:

                            opened = open_paper_position(
                                state,
                                "SHORT",
                                current_price,
                                wall_level
                            )

                            if opened:

                                tracked_bid_walls.pop(
                                    wall_level,
                                    None
                                )

                                continue

                    except Exception as e:

                        print(
                            f"⚠️ Ошибка обработки стакана: {e}"
                        )

        except Exception as e:

            print(
                f"⚠️ Ошибка WebSocket: {e}. "
                f"Переподключение через 5 секунд..."
            )

            await asyncio.sleep(5)


# =====================================================================
# ТОЧКА ВХОДА
# =====================================================================

if __name__ == "__main__":

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    asyncio.run(start_orderbook_ws())
