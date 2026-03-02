import os, requests, time, threading
from flask import Flask

app = Flask(__name__)

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
# Курс: 1 млн серебра = 0.65 USDT (можно менять)
SILVER_TO_USDT = 0.65 / 1000000 

# Список самых прибыльных предметов, включая 4.3 и 4.4
ITEMS_LIST = [
    "T4_BAG", "T4_BAG@1", "T4_BAG@2", "T4_BAG@3", "T4_BAG@4",
    "T5_BAG", "T5_BAG@1", "T5_BAG@2", "T5_BAG@3",
    "T4_CAPE", "T4_CAPE@1", "T4_CAPE@3", "T4_CAPE@4",
    "T4_MAIN_SWORD@4", "T4_MAIN_AXE@4", "T4_MAIN_SPEAR@4",
    "T4_MAIN_DAGGER@4", "T4_MAIN_FIRESTAFF@4",
    "T4_ARMOR_PLATE_SET1@4", "T4_ARMOR_LEATHER_SET1@4", "T4_ARMOR_CLOTH_SET1@4",
    "T4_HEAD_PLATE_SET1@4", "T4_SHOES_PLATE_SET1@4",
    "T5_MAIN_SWORD@3", "T6_MAIN_AXE@2"
]

current_deals = []
status = "Запуск системы..."

def send_tg(text):
    if TOKEN and CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                          json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
        except:
            pass

def scan_logic():
    global current_deals, status
    while True:
        new_found = []
        try:
            # Дробим запросы по 5 предметов для стабильности API Европы
            for i in range(0, len(ITEMS_LIST), 5):
                chunk = ",".join(ITEMS_LIST[i:i+5])
                url = f"https://europe.albion-online-data.com/api/v2/stats/prices/{chunk}?locations=Caerleon,BlackMarket"
                
                r = requests.get(url, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    market_map = {}
                    
                    # Собираем данные в удобную карту
                    for row in data:
                        if isinstance(row, dict) and row.get('location') and row.get('item_id'):
                            k = (row['item_id'], row['quality'])
                            if k not in market_map: market_map[k] = {}
                            market_map[k][row['location']] = row.get('sell_price_min', 0)

                    # Считаем профит
                    for (iid, qual), locs in market_map.items():
                        p_city = locs.get('Caerleon', 0)
                        p_bm = locs.get('BlackMarket', 0)
                        
                        if p_city > 0 and p_bm > 0:
                            # Налог ЧР 9%
                            profit_silver = p_bm - p_city - (p_bm * 0.09)
                            
                            # Порог прибыли: 1000 серебра
                            if profit_silver > 1000:
                                profit_usdt = profit_silver * SILVER_TO_USDT
                                
                                # Красивое имя (T4_BAG@4 -> 4.4 Bag)
                                name = iid.replace("T4_", "4.").replace("T5_", "5.").replace("T6_", "6.").replace("@", ".").replace("_MAIN_", " ")
                                
                                res = {
                                    "name": name, "q": qual, "buy": p_city, "sell": p_bm, 
                                    "p_silver": int(profit_silver), "p_usdt": round(profit_usdt, 3)
                                }
                                new_found.append(res)
                                
                                # Уведомление в Телеграм если профит > 0.10$
                                if profit_usdt > 0.10:
                                    emoji = "💎" if ".4" in name else "💰"
                                    send_tg(f"{emoji} *Earned ${round(profit_usdt, 2)}*\n📦 `{name}` (Кач-во:{qual})\n🛒 Купи в городе: {p_city:,}\n🏴‍☠️ Вези на ЧР: {p_bm:,}")
            
            # Сортировка: самые выгодные сверху
            current_deals = sorted(new_found, key=lambda x: x['p_silver'], reverse=True)
            status = f"Европа: OK. Обновлено в {time.strftime('%H:%M:%S')}. Сделок: {len(current_deals)}"
            
        except Exception as e:
            status = f"Ошибка сканера: {str(e)}"
        
        time.sleep(40) # Пауза между циклами

# Запуск сканера в фоновом потоке
threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def home():
    rows = ""
    for d in current_deals:
        rows += f"""
        <tr>
            <td>{d['name']}</td>
            <td>{d['q']}</td>
            <td>{d['buy']:,}</td>
            <td>{d['sell']:,}</td>
            <td style='color:#00ff00; font-weight:bold;'>+{d['p_silver']:,} <br><small>(${d['p_usdt']})</small></td>
        </tr>
        """
    
    return f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Albion Europe Sniper</title>
    </head>
    <body style="background:#121212; color:white; font-family:sans-serif; padding:10px;">
        <h2 style="color:#5dade2; text-align:center;">💎 Albion Profit USDT</h2>
        <div style="background:#222; padding:10px; border-radius:5px; border:1px solid #444; margin-bottom:15px; font-size:0.9em; color:#f1c40f;">
            Статус: {status}
        </div>
        <table border="1" style="width:100%; border-collapse:collapse; font-size:0.85em; text-align:center;">
            <tr style="background:#333;">
                <th>Предмет</th><th>Q</th><th>Рынок</th><th>ЧР</th><th>Профит</th>
            </tr>
            {rows if rows else '<tr><td colspan="5" style="padding:30px; color:#888;">Данных пока нет.<br>Пролистай рынок и ЧР (Т4-Т6, зачарование 0-4)</td></tr>'}
        </table>
        <p style="font-size:0.7em; color:#666; margin-top:10px;">Курс: 1M silver = 0.65 USDT. Налог ЧР 9% учтен.</p>
    </body>
    </html>
    """

if __name__ == "__main__":
    # Порт для Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
