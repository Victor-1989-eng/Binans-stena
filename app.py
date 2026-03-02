import os, requests, time, threading
from flask import Flask

app = Flask(__name__)

# Предметы для теста (добавил мечи и топоры, их на ЧР много)
ITEMS = "T4_BAG,T5_BAG,T4_CAPE,T5_CAPE,T4_MAIN_SWORD,T5_MAIN_SWORD,T4_MAIN_AXE,T5_MAIN_AXE"

current_deals = []
status = "Проверка связи с Европой..."

def scan_logic():
    global current_deals, status
    while True:
        try:
            # Запрос к API Европы
            url = f"https://europe.albion-online-data.com/api/v2/stats/prices/{ITEMS}?locations=Caerleon,BlackMarket&qualities=1,2,3"
            r = requests.get(url, timeout=25)
            
            if r.status_code == 200:
                data = r.json()
                temp_data = {}
                for row in data:
                    if all(k in row for k in ('item_id', 'location', 'sell_price_min')):
                        key = (row['item_id'], row['quality'])
                        if key not in temp_data: temp_data[key] = {}
                        temp_data[key][row['location']] = row['sell_price_min']

                new_deals = []
                for (iid, qual), prices in temp_data.items():
                    p_city = prices.get('Caerleon', 0)
                    p_bm = prices.get('BlackMarket', 0)
                    
                    # УСЛОВИЕ: Показываем всё, где есть хоть какая-то цена в Карлеоне
                    if p_city > 0:
                        # Считаем профит просто для инфы (может быть минус)
                        profit = p_bm - p_city - (p_bm * 0.09) if p_bm > 0 else -p_city
                        
                        new_deals.append({
                            "name": iid.replace("T4_", "4.").replace("T5_", "5.").replace("MAIN_", ""),
                            "q": qual,
                            "buy": p_city,
                            "sell": p_bm if p_bm > 0 else "Нет данных",
                            "p": int(profit)
                        })
                
                # Сортируем: сначала самые выгодные
                current_deals = sorted(new_deals, key=lambda x: x['p'], reverse=True)
                status = f"Данные получены! Обновлено: {time.strftime('%H:%M:%S')}. Всего строк: {len(current_deals)}"
            else:
                status = f"Ошибка API: {r.status_code}"
        except Exception as e:
            status = f"Ошибка: {str(e)}"
        time.sleep(30)

threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def home():
    rows = ""
    for d in current_deals:
        p_color = "#00ff00" if d['p'] > 0 else "#ff4444"
        rows += f"""
        <tr>
            <td>{d['name']}</td>
            <td>{d['q']}</td>
            <td>{d['buy']:,}</td>
            <td>{d['sell'] if isinstance(d['sell'], str) else f"{d['sell']:,}"}</td>
            <td style='color:{p_color};'>{d['p']:,}</td>
        </tr>"""

    return f"""
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="background:#111; color:white; font-family:sans-serif; padding:10px;">
        <h2 style="color:#5dade2;">🇪🇺 Europe Debug Mode</h2>
        <p style="color:yellow; font-size:0.9em;">{status}</p>
        <table border="1" style="width:100%; border-collapse:collapse; font-size:0.8em;">
            <tr style="background:#333;"><th>Item</th><th>Q</th><th>Market</th><th>BM</th><th>Profit</th></tr>
            {rows if rows else '<tr><td colspan="5" style="text-align:center; padding:20px;">База API пуста. Листай рынок в игре!</td></tr>'}
        </table>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
