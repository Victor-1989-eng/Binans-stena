import asyncio
import json
import math
import os
import time
import csv
import threading
import requests
import websockets
from flask import Flask

# =====================================================================
# --- FLASK WEB SERVER (C ВЕБ-ЭНДПОИНТОМ ДЛЯ АНАЛИЗА) ---
# =====================================================================
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return "Wall Analytics Worker is Live & Running!", 200

@app.route('/analyze')
def trigger_analyze():
    """Эндпоинт для вызова перерасчета CSV прямо из браузера"""
    status = run_csv_reanalysis_and_send_telegram()
    return f"<h2>{status}</h2><p>Проверьте ваш Telegram-чат.</p>", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# =====================================================================
# --- НАСТРОЙКИ СБОРА СТАТИСТИКИ ---
# =====================================================================
SYMBOL = 'SOL/USDC'
ORDERBOOK_AGG_STEP = 0.1

TRACK_MIN_WALL_USD = 300_000            # Ловим стены от $300k
EATEN_TRIGGER_USD = 80_000              # Триггер проедания (< $80k)

THIN_BOOK_CHECK_LEVELS = 3              # Проверяем 3 уровня ($0.30) за стеной
TRACK_DURATION_SEC = 300                # Мониторинг 5 минут (300 сек)

TEST_DURATION_HOURS = 72                # Длительность сбора (3 дня)
CSV_FILE = "wall_stats.csv"

# Настройки Telegram
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
# --- МОДУЛЬ АНАЛИЗА CSV И ОТПРАВКИ В TELEGRAM ---
# =====================================================================
def run_csv_reanalysis_and_send_telegram():
    if not os.path.exists(CSV_FILE):
        send_telegram("❌ Файл wall_stats.csv пока не создан или пуст.")
        return "Файл wall_stats.csv не найден."

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
        send_telegram("⚠️ В wall_stats.csv нет записей для перерасчета.")
        return "В CSV файле 0 записей."

    # Наборы параметров для теста
    tp_candidates = [0.10, 0.15, 0.20, 0.25, 0.30]
    sl_candidates = [0.05, 0.10, 0.15, 0.20]
    wall_candidates = [300_000, 500_000, 800_000, 1_000_000]
    behind_candidates = [150_000, 300_000, 500_000, 1_000_000]

    best_results = []

    for tp in tp_candidates:
        for sl in sl_candidates:
            for w_min in wall_candidates:
                for b_max in behind_candidates:
                    trades, wins, losses = 0, 0, 0

                    for r in rows:
                        if r["initial_wall"] >= w_min and r["max_behind"] <= b_max:
                            trades += 1
                            if r["drawdown"] >= sl:
                                losses += 1
                            elif r["impulse"] >= tp:
                                wins += 1

                    if trades >= 5:
                        win_rate = (wins / trades) * 100
                        pnl = (wins * tp) - (losses * sl)

                        best_results.append({
                            "tp": tp, "sl": sl,
                            "w_min": w_min, "b_max": b_max,
                            "trades": trades, "wins": wins, "losses": losses,
                            "win_rate": win_rate, "pnl": pnl
                        })

    best_results.sort(key=lambda x: x["pnl"], reverse=True)

    if not best_results:
        send_telegram("⚠️ Не найдено прибыльных пресетов (мало сделок по фильтрам).")
        return "Прибыльных пресетов не найдено."

    msg = (
        f"📊 <b>ЭКСПРЕСС-АНАЛИЗ CSV ДАННЫХ</b>\n"
        f"Проанализировано пробоев: <b>{total_events}</b>\n"
        f"─────────────────────────────\n"
    )

    # Выводим ТОП-5 лучших вариантов
    for i, res in enumerate(best_results[:5], 1):
        msg += (
            f"<b>#{i} | TP: +${res['tp']:.2f} | SL: -${res['sl']:.2f}</b>\n"
            f"• Мин. стена: <b>${res['w_min']//1000}k</b> | За стеной: <b><${res['b_max']//1000}k</b>\n"
            f"• Сделок: <b>{res['trades']}</b> (W: {res['wins']} / L: {res['losses']})\n"
            f"• WinRate: <b>{res['win_rate']:.1f}%</b> | PnL: <b>+${res['pnl']:.2f}</b>\n\n"
        )

    send_telegram(msg)
    return f"Успешно! Отчет по {total_events} событиям отправлен в Telegram."

# =====================================================================
# --- ХРАНИЛИЩЕ И CSV ЛОГГЕР ---
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
# --- ОСНОВНОЙ ЦИКЛ WEBSOCKET МОНИТОРИНГА ---
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
        f"🚀 <b>Сбор статистики запущен!</b>\n"
        f"Пара: {SYMBOL} | Окно наблюдения: <b>{TRACK_DURATION_SEC}с</b>\n"
        f"Сбор выполняется тихо в CSV.\n\n"
        f"🔗 Ссылка для запроса отчета в любой момент:\n"
        f"<code>https://ВАШ-СЕРВИС.onrender.com/analyze</code>"
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

                    # Логирование
                    for m in list(active_monitors):
                        m["max_price"] = max(m["max_price"], current_price)
                        m["min_price"] = min(m["min_price"], current_price)

                        if now >= m["expire_time"]:
                            entry = m["entry_price"]
                            impulse = (m["max_price"] - entry) if m["direction"] == "LONG" else (entry - m["min_price"])
                            drawdown = (entry - m["min_price"]) if m["direction"] == "LONG" else (m["max_price"] - entry)

                            log_event_to_csv({
                                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m["start_time"])),
                                "direction": m["direction"], "wall_price": m["wall_price"],
                                "initial_wall_usd": m["initial_wall_usd"], "eaten_wall_usd": m["eaten_wall_usd"],
                                "max_behind_vol_usd": m["max_behind_vol_usd"], "entry_price": entry,
                                "max_impulse_usd": impulse, "max_drawdown_usd": drawdown,
                                "hit_tp_050": "YES" if impulse >= 0.50 else "NO",
                                "hit_sl_025": "YES" if drawdown >= 0.25 else "NO"
                            })
                            active_monitors.remove(m)

        except Exception as e:
            await asyncio.sleep(5)

    run_csv_reanalysis_and_send_telegram()

# =====================================================================
# --- ТОЧКА ВХОДА ---
# =====================================================================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    asyncio.run(start_analytics())
