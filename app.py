import os, requests, time, threading
from flask import Flask

app = Flask(__name__)

# Секреты (убедись, что они прописаны в Render)
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# Максимальный список для теста "всего подряд"
TEST_ITEMS = "T4_BAG,T5_BAG,T6_BAG,T4_CAPE,T5_CAPE,T6_CAPE,T4_MAIN_SPEAR,T5_MAIN_SPEAR,T4_MAIN_BOW,T5_MAIN_BOW,T4_MAIN_SWORD,T5_MAIN_SWORD,T4_SHOES_LEATHER_SET1,T5_SHOES_LEATHER_SET1"

current_deals = []
last_error = "Ожидание первых данных из игры..."

def send_tg(text):
    if TOKEN and CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                          json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
        except: pass

def scan_logic():
    global current_deals, last_error
    while True:
        new_found = []
        try:
            # Запрос к API (Карлеон и ЧР)
            url = f"https://www.albion-online-data.com/api/v2/stats/prices/{TEST_ITEMS}?locations=Caerleon,BlackMarket&qualities=1,2,3"
            response = requests.get(url, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list):
                    market_data = {}
                    for row in data:
                        if all(k in row for k in ('item_id', 'quality', 'location', 'sell_price_min')):
                            k = (row['item_id'], row['quality'])
                            if k not in market_data: market_data[k] = {}
                            market_data[k][row['location']] = row['sell_price_min']

                    for (item_id, qual), locs in market_data.items():
                        p_city = locs.get('Caerleon', 0)
                        p_bm = locs.get('BlackMarket', 0)
                        
                        if p_city > 0 and p_bm > 0:
                            # Налог 9% (ЧР + комиссия ордера)
                            profit = p_bm - p_city - (p_bm * 0.09)
                            
                            # ПОРОГ СНИЖЕН ДО 500 СЕРЕБРА
                            if profit > 500:
                                new_found.append({
                                    "name": item_id.replace("T4_", "4.").replace("T5_", "5.").replace("T6_", "6.").replace("_MAIN_", " "),
                                    "q": qual, 
                                    "buy": p_city, 
                                    "sell": p_bm, 
                                    "profit": int(profit)
                                })
                                # В телеграм шлем только жир (> 30k), чтобы не спамить
                                if profit > 30000:
                                    send_tg(f"💰 *Жирный флип!*\n`{item_id}`\nПрофит: {int(profit):,} silver")
                    
                    current_deals = sorted(new_found, key=lambda x: x['profit'], reverse=True)
                    last_error = f"Обновлено в {time.strftime('%H:%M:%S')}. Найдено сделок: {len(current_deals)}"
            else:
                last_error = f"Ошибка API: {response.status_code}"

        except Exception as e:
            last_error = f"Ошибка: {str(e)}"
        
        time.sleep(45) # Обновляем чуть чаще

threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def index():
    status_style = "color: #00ff00;" if "Обновлено" in last_error else "color: yellow;"
    table_rows = ""
    for d in current_deals:
        table_rows += f"<tr><td>{d['name']}</td><td>{d['q']}</td><td>{d['buy']:,}</td><td>{d['sell']:,}</td><td style='color:#00ff00; font-weight:bold;'>+{d['profit']:,}</td></tr>"

    return f"""
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="background:#121212; color:white; font-family:sans-serif; padding:10px;">
        <h2 style="margin-bottom:5px;">🏴‍☠️ Albion Flip Picker</h2>
        <div style="padding: 8px; border: 1px solid #444; margin-bottom: 15px; font-size: 0.9em; {status_style}">
            {last_error}
        </div>
        <table border="1" style="width:100%; border-collapse:collapse; font-size: 0.85em;">
            <tr style="background:#333;"><th>Item</th><th>Q</th><th>Buy</th><th>Sell</th><th>Profit</th></tr>
            {table_rows if table_rows else '<tr><td colspan="5" style="text-align:center; padding:20px;">Пока пусто. Пролистай рынок в Карлеоне!</td></tr>'}
        </table>
    </body>
    </html>
    """

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
