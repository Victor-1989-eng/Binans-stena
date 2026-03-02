import os, requests, time, threading
from flask import Flask, render_template_string

app = Flask(__name__)

# Настройки из твоих секретов (в Render добавь их в Environment Variables)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# СПИСОК "BEAST MODE" (Сгруппирован для обхода лимитов API)
ITEM_GROUPS = [
    "T4_BAG,T5_BAG,T6_BAG,T7_BAG,T8_BAG,T4_CAPE,T5_CAPE,T6_CAPE,T7_CAPE,T8_CAPE",
    "T4_MAIN_SPEAR,T5_MAIN_SPEAR,T6_MAIN_SPEAR,T4_MAIN_BOW,T5_MAIN_BOW,T6_MAIN_BOW",
    "T4_MAIN_SWORD,T5_MAIN_SWORD,T6_MAIN_SWORD,T4_MAIN_AXE,T5_MAIN_AXE,T6_MAIN_AXE",
    "T4_ARMOR_PLATE_SET1,T5_ARMOR_PLATE_SET1,T6_ARMOR_PLATE_SET1,T4_ARMOR_LEATHER_SET1,T5_ARMOR_LEATHER_SET1",
    "T4_SHOES_CLOTH_SET1,T5_SHOES_CLOTH_SET1,T6_SHOES_CLOTH_SET1,T4_HEAD_PLATE_SET1,T5_HEAD_PLATE_SET1"
]

# Глобальное хранилище данных
current_deals = []

def send_tg(text):
    if TOKEN and CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
        except: pass

def scan_logic():
    global current_deals
    while True:
        all_found = []
        for group in ITEM_GROUPS:
            try:
                # Запрашиваем Карлеон и Черный Рынок, качества: Обычное, Хорошее, Выдающееся
                url = f"https://www.albion-online-data.com/api/v2/stats/prices/{group}?locations=Caerleon,BlackMarket&qualities=1,2,3"
                data = requests.get(url, timeout=10).json()
                
                pairs = {}
                for entry in data:
                    key = (entry['item_id'], entry['quality'])
                    if key not in pairs: pairs[key] = {}
                    pairs[key][entry['location']] = entry['sell_price_min']

                for (item_id, quality), locs in pairs.items():
                    p_city = locs.get('Caerleon', 0)
                    p_bm = locs.get('BlackMarket', 0)

                    if p_city > 0 and p_bm > 0:
                        # Налог ЧР (6%) + комиссия за выставление (2.5%) = 8.5%
                        profit = p_bm - p_city - (p_bm * 0.085)

                        if profit > 10000: # Порог прибыли для таблицы
                            all_found.append({
                                "name": item_id,
                                "q": quality,
                                "buy": p_city,
                                "sell": p_bm,
                                "profit": round(profit, 2)
                            })
                            
                            # Уведомление в ТГ только для жирных сделок (> 40k silver)
                            if profit > 40000:
                                send_tg(f"💰 *ФЛИП В КАРЛЕОНЕ*\n`{item_id}` (Q:{quality})\n🛒 Купи: {p_city:,}\n🏴‍☠️ ЧР: {p_bm:,}\n*Earned {round(profit, 2):,} silver*")
            except: continue
            time.sleep(2) # Пауза между группами
        
        current_deals = sorted(all_found, key=lambda x: x['profit'], reverse=True)
        time.sleep(180) # Обновляем раз в 3 минуты

# Фоновый поток
threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def dashboard():
    html = """
    <html>
    <head>
        <title>Albion Beast Mode</title>
        <meta http-equiv="refresh" content="60">
        <style>
            body { background: #121212; color: #e0e0e0; font-family: sans-serif; padding: 20px; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { padding: 12px; border: 1px solid #333; text-align: left; }
            th { background: #1f1f1f; }
            tr:nth-child(even) { background: #1a1a1a; }
            .profit { color: #4caf50; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>🏴‍☠️ Albion Caerleon Beast Mode</h1>
        <p>Найдено активных сделок: <b>{{ deals|length }}</b></p>
        <table>
            <tr><th>Предмет</th><th>Качество</th><th>Рынок (Купить)</th><th>ЧР (Продать)</th><th>ЧИСТЫЙ ПРОФИТ</th></tr>
            {% for d in deals %}
            <tr>
                <td>{{ d.name }}</td><td>{{ d.q }}</td><td>{{ d.buy:, }}</td><td>{{ d.sell:, }}</td>
                <td class="profit">+ {{ d.profit:, }} silver</td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    return render_template_string(html, deals=current_deals)

if __name__ == '__main__':
    # Render передает порт через переменную окружения
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
