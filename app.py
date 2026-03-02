import os, requests, time, threading
from flask import Flask

app = Flask(__name__)

# Предметы, которые ты только что листал (добавь свои, если листал другие)
ITEMS = "T4_BAG,T5_BAG,T6_BAG,T4_CAPE,T5_CAPE,T6_CAPE,T4_MAIN_SWORD,T5_MAIN_SWORD,T4_MAIN_AXE,T5_MAIN_AXE,T4_MAIN_SPEAR,T5_MAIN_SPEAR"

current_deals = []
status = "Ожидание данных из Европы..."

def scan_logic():
    global current_deals, status
    while True:
        try:
            # ПРЯМОЙ URL ДЛЯ ЕВРОПЫ
            url = f"https://europe.albion-online-data.com/api/v2/stats/prices/{ITEMS}?locations=Caerleon,BlackMarket&qualities=1,2,3"
            r = requests.get(url, timeout=25)
            
            if r.status_code == 200:
                data = r.json()
                temp_data = {}
                # Группируем полученные данные
                for row in data:
                    item_id = row.get('item_id')
                    loc = row.get('location')
                    price = row.get('sell_price_min', 0)
                    qual = row.get('quality', 1)
                    
                    if item_id and loc and price > 0:
                        key = (item_id, qual)
                        if key not in temp_data: temp_data[key] = {}
                        temp_data[key][loc] = price

                new_deals = []
                for (iid, qual), locs in temp_data.items():
                    p_city = locs.get('Caerleon', 0)
                    p_bm = locs.get('BlackMarket', 0)
                    
                    if p_city > 0 and p_bm > 0:
                        # Налог ЧР (примерно 9% вместе с выставлением)
                        profit = p_bm - p_city - (p_bm * 0.09)
                        
                        # Показываем всё, где профит больше 500 серебра
                        if profit > 500:
                            new_deals.append({
                                "name": iid.replace("T4_", "4.").replace("T5_", "5."),
                                "q": qual,
                                "buy": p_city,
                                "sell": p_bm,
                                "p": int(profit)
                            })
                
                current_deals = sorted(new_deals, key=lambda x: x['p'], reverse=True)
                status = f"Европа активна! Обновлено: {time.strftime('%H:%M:%S')}. Найдено сделок: {len(current_deals)}"
            else:
                status = f"Ошибка API: {r.status_code}"
        except Exception as e:
            status = f"Ошибка сканера: {str(e)}"
        
        time.sleep(30)

threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def home():
    rows = ""
    for d in current_deals:
        rows += f"<tr><td>{d['name']}</td><td>{d['q']}</td><td>{d['buy']:,}</td><td>{d['sell']:,}</td><td style='color:#00ff00;font-weight:bold;'>+{d['p']:,}</td></tr>"

    return f"""
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="background:#121212; color:white; font-family:sans-serif; padding:10px;">
        <h2 style="color:#5dade2; margin-bottom:5px;">🇪🇺 Albion Europe Profit</h2>
        <p style="color:yellow; font-size:0.9em; margin-top:0;">{status}</p>
        <table border="1" style="width:100%; border-collapse:collapse; font-size:0.85em;">
            <tr style="background:#333;"><th>Item</th><th>Q</th><th>Buy (City)</th><th>Sell (BM)</th><th>Profit</th></tr>
            {rows if rows else '<tr><td colspan="5" style="text-align:center; padding:20px;">Сделок пока нет. Пролистай рынок И Черный рынок в игре!</td></tr>'}
        </table>
        <p style="font-size:0.7em; color:#666; margin-top:20px;">* Учитывается налог 9% на Черном Рынке.</p>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
