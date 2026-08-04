import asyncio
import json
import math
import os
import time
import csv
import requests
import websockets

# =====================================================================
# --- НАСТРОЙКИ ТЕСТА ---
# =====================================================================
SYMBOL = 'SOL/USDC'
ORDERBOOK_AGG_STEP = 0.1

TRACK_MIN_WALL_USD = 300_000            # Фиксируем стены от $300k
EATEN_TRIGGER_USD = 80_000              # Триггер проедания (< $80k)

THIN_BOOK_CHECK_LEVELS = 3              # Проверка уровней за стеной
TRACK_DURATION_SEC = 60                 # Следим 60 секунд за импульсом

TEST_DURATION_HOURS = 24                # Длительность теста (3 дня)
CSV_FILE = "wall_stats.csv"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

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
        print(f"Ошибка отправки в Telegram: {e}")

# =====================================================================
# --- ХРАНИЛИЩЕ И CSV ---
# =====================================================================
def init_csv():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "direction", "wall_price", "initial_wall_usd", 
                "eaten_wall_usd", "max_behind_vol_usd", "entry_price",
                "max_impulse_usd", "max_drawdown_usd", 
                "hit_tp_050", "hit_sl_025"
            ])

def log_event_to_csv(data):
    with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            data["timestamp"], data["direction"], data["wall_price"],
            round(data["initial_wall_usd"]), round(data["eaten_wall_usd"]),
            round(data["max_behind_vol_usd"]), data["entry_price"],
            round(data["max_impulse_usd"], 2), round(data["max_drawdown_usd"], 2),
            data["hit_tp_050"], data["hit_sl_025"]
        ])

def aggregate_orderbook(bids, asks, step=0.1):
    grouped_bids, grouped_asks = {}, {}
    for p_str, q_str in bids:
        price, qty = float(p_str), float(q_str)
        level = round(math.floor(price / step) * step, 4)
        grouped_bids[level] = grouped_bids.get(level, 0.0) + (price * qty)

    for p_str, q_str in asks:
        price, qty = float(p_str), float(q_str)
        level = round(math.floor(price / step) * step, 4)
        grouped_asks[level] = grouped_asks.get(level, 0.0) + (price * qty)

    return grouped_bids, grouped_asks

def get_max_behind_vol(grouped_book, wall_lvl, direction, step, num_levels):
    max_vol = 0.0
    for i in range(1, num_levels + 1):
        check_lvl = round(wall_lvl + (i * step), 4) if direction == 'LONG' else round(wall_lvl - (i * step), 4)
        vol = grouped_book.get(check_lvl, 0.0)
        if vol > max_vol:
            max_vol = vol
    return max_vol

# =====================================================================
# --- МОДУЛЬ АНАЛИЗА И ПОДБОРА ЛУЧШИХ ПАРАМЕТРОВ ---
# =====================================================================
def generate_optimal_config_report():
    if not os.path.exists(CSV_FILE):
        send_telegram("⚠️ Нет данных в CSV для формирования итогового отчета.")
        return

    rows = []
    with open(CSV_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "initial_wall": float(r["initial_wall_usd"]),
                "max_behind": float(r["max_behind_vol_usd"]),
                "impulse": float(r["max_impulse_usd"]),
                "drawdown": float(r["max_drawdown_usd"]),
            })

    total_events = len(rows)
    if total_events == 0:
        send_telegram("⚠️ За 3 дня не зафиксировано ни одного пробоя стен.")
        return

    # Сетка перебора параметров
    wall_candidates = [300_000, 400_000, 500_000, 600_000, 700_000, 800_000, 1_000_000]
    behind_candidates = [150_000, 250_000, 350_000, 500_000, 1_000_000]

    best_score = -99999
    best_config = None
    best_stats = {}

    for w_min in wall_candidates:
        for b_max in behind_candidates:
            trades = 0
            wins = 0
            losses = 0

            for r in rows:
                # Применяем фильтр
                if r["initial_wall"] >= w_min and r["max_behind"] <= b_max:
                    trades += 1
                    # Если просадка раньше или одновременно перекрыла стоп
                    if r["drawdown"] >= 0.25:
                        losses += 1
                    elif r["impulse"] >= 0.50:
                        wins += 1

            if trades >= 5:  # Релевантный выбор (минимум 5 сделок)
                win_rate = (wins / trades) * 100
                # Финансовый профит: +$0.50 за вин, -$0.25 за лосс
                pnl = (wins * 0.50) - (losses * 0.25)

                if pnl > best_score:
                    best_score = pnl
                    best_config = (w_min, b_max)
                    best_stats = {
                        "trades": trades,
                        "wins": wins,
                        "losses": losses,
                        "win_rate": win_rate,
                        "pnl": pnl
                    }

    if not best_config:
        send_telegram("⚠️ Не удалось подобрать прибыльную комбинацию (мало данных).")
        return

    opt_wall, opt_behind = best_config

    report_msg = (
        f"🏆 <b>ИТОГОВЫЙ ОТЧЕТ ЗА 3 ДНЯ ТЕСТА</b>\n"
        f"Проанализировано пробоев: <b>{total_events}</b>\n"
        f"─────────────────────────────\n"
        f"🥇 <b>Лучшие найденные параметры:</b>\n"
        f"• Всего сделок по фильтру: <b>{best_stats['trades']}</b>\n"
        f"• Побед (TP +$0.50): <b>{best_stats['wins']}</b>\n"
        f"• Поражений (SL -$0.25): <b>{best_stats['losses']}</b>\n"
        f"• Win Rate: <b>{best_stats['win_rate']:.1f}%</b>\n"
        f"• Чистый PnL с 1 лота: <b>+${best_stats['pnl']:.2f}</b>\n\n"
        f"📋 <b>ГОТОВЫЙ КОД ДЛЯ app.py:</b>\n"
        f"<pre>"
        f"ORDERBOOK_AGG_STEP = 0.1\n"
        f"INITIAL_WALL_THRESHOLD_USD = {opt_wall}\n"
        f"EATEN_WALL_THRESHOLD_USD = {EATEN_TRIGGER_USD}\n"
        f"THIN_BOOK_CHECK_LEVELS = 3\n"
        f"MAX_BEHIND_WALL_VOL_USD = {opt_behind}"
        f"</pre>\n\n"
        f"Вставьте эти значения в ваш основной бот paper-торговли!"
    )
    send_telegram(report_msg)

# =====================================================================
# --- ЦИКЛ МОНИТОРИНГА ---
# =====================================================================
async def start_analytics():
    init_csv()
    ws_symbol = SYMBOL.replace('/', '').lower()
    ws_url = f"wss://fstream.binance.com/ws/{ws_symbol}@depth20@100ms"

    tracked_ask_walls = {}
    tracked_bid_walls = {}
    active_monitors = []

    start_time = time.time()
    end_time = start_time + (TEST_DURATION_HOURS * 3600)

    send_telegram(
        f"🚀 <b>Запущен 3-дневный сбор статистики!</b>\n"
        f"Пара: {SYMBOL} | Шаг: <b>${ORDERBOOK_AGG_STEP}</b>\n"
        f"Завершение и итоговый отчет: <b>{time.strftime('%Y-%m-%d %H:%M', time.localtime(end_time))}</b>"
    )

    while time.time() < end_time:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                async for message in ws:
                    if time.time() >= end_time:
                        break

                    data = json.loads(message)
                    bids, asks = data.get('b', []), data.get('a', [])
                    if not bids or not asks:
                        continue

                    best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
                    current_price = (best_bid + best_ask) / 2.0
                    now = time.time()

                    g_bids, g_asks = aggregate_orderbook(bids, asks, step=ORDERBOOK_AGG_STEP)

                    for lvl, vol in g_asks.items():
                        if vol >= TRACK_MIN_WALL_USD:
                            tracked_ask_walls[lvl] = max(tracked_ask_walls.get(lvl, 0), vol)

                    for lvl, vol in g_bids.items():
                        if vol >= TRACK_MIN_WALL_USD:
                            tracked_bid_walls[lvl] = max(tracked_bid_walls.get(lvl, 0), vol)

                    # LONG
                    for wall_lvl, peak_vol in list(tracked_ask_walls.items()):
                        if (wall_lvl - 0.15) <= current_price <= (wall_lvl + 0.10):
                            curr_vol = g_asks.get(wall_lvl, 0.0)
                            if curr_vol < EATEN_TRIGGER_USD:
                                max_behind = get_max_behind_vol(g_asks, wall_lvl, 'LONG', ORDERBOOK_AGG_STEP, THIN_BOOK_CHECK_LEVELS)
                                active_monitors.append({
                                    "direction": "LONG", "wall_price": wall_lvl,
                                    "initial_wall_usd": peak_vol, "eaten_wall_usd": curr_vol,
                                    "max_behind_vol_usd": max_behind, "entry_price": current_price,
                                    "start_time": now, "expire_time": now + TRACK_DURATION_SEC,
                                    "max_price": current_price, "min_price": current_price
                                })
                                del tracked_ask_walls[wall_lvl]

                    # SHORT
                    for wall_lvl, peak_vol in list(tracked_bid_walls.items()):
                        if (wall_lvl - 0.10) <= current_price <= (wall_lvl + 0.15):
                            curr_vol = g_bids.get(wall_lvl, 0.0)
                            if curr_vol < EATEN_TRIGGER_USD:
                                max_behind = get_max_behind_vol(g_bids, wall_lvl, 'SHORT', ORDERBOOK_AGG_STEP, THIN_BOOK_CHECK_LEVELS)
                                active_monitors.append({
                                    "direction": "SHORT", "wall_price": wall_lvl,
                                    "initial_wall_usd": peak_vol, "eaten_wall_usd": curr_vol,
                                    "max_behind_vol_usd": max_behind, "entry_price": current_price,
                                    "start_time": now, "expire_time": now + TRACK_DURATION_SEC,
                                    "max_price": current_price, "min_price": current_price
                                })
                                del tracked_bid_walls[wall_lvl]

                    # Фиксация и сохранение
                    for m in list(active_monitors):
                        m["max_price"] = max(m["max_price"], current_price)
                        m["min_price"] = min(m["min_price"], current_price)

                        if now >= m["expire_time"]:
                            entry = m["entry_price"]
                            impulse = (m["max_price"] - entry) if m["direction"] == "LONG" else (entry - m["min_price"])
                            drawdown = (entry - m["min_price"]) if m["direction"] == "LONG" else (m["max_price"] - entry)

                            log_data = {
                                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m["start_time"])),
                                "direction": m["direction"], "wall_price": m["wall_price"],
                                "initial_wall_usd": m["initial_wall_usd"], "eaten_wall_usd": m["eaten_wall_usd"],
                                "max_behind_vol_usd": m["max_behind_vol_usd"], "entry_price": entry,
                                "max_impulse_usd": impulse, "max_drawdown_usd": drawdown,
                                "hit_tp_050": "YES" if impulse >= 0.50 else "NO",
                                "hit_sl_025": "YES" if drawdown >= 0.25 else "NO"
                            }
                            log_event_to_csv(log_data)
                            active_monitors.remove(m)

        except Exception as e:
            await asyncio.sleep(5)

    # ПО ИСТЕЧЕНИИ 3 ДНЕЙ
    generate_optimal_config_report()

if __name__ == "__main__":
    asyncio.run(start_analytics())
