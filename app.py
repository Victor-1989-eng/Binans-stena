import os, requests, time, threading
from flask import Flask

app = Flask(__name__)

# Секреты для Telegram
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# Расширенный список для заработка
ITEMS = (
    "T4_BAG,T5_BAG,T6_BAG,T4_CAPE,T5_CAPE,T6_CAPE,"
    "T4_MAIN_SWORD,T5_MAIN_SWORD,T6_MAIN_SWORD,"
    "T4_MAIN_AXE,T5_MAIN_AXE,T6_MAIN_AXE,"
    "T4_MAIN_SPEAR,T5_MAIN_SPEAR,T6_MAIN_SPEAR"
)

current_deals = []
status = "Ожидание данных..."

def send_tg(text):
    if TOKEN and CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                          json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
        except: pass

def scan_logic():
    global current_deals, status
    while True:
        try:
            # Запрос к Европе (Карлеон + ЧР)
            url = f"https://europe.albion-online-data.com/api/v2/stats/prices/{ITEMS}?locations=Caerleon,BlackMarket&qualities=1,2,3"
            r = requests.get(url, timeout=25)
            
            if r.status_code == 200:
                data = r.json()
                temp_data = {}
                for row in data:
                    if all(k in row for k in ('item_id', 'location', 'sell_price_min')):
                        k = (row['item_id'], row['quality'])
                        if k not in temp_data: temp_data[k] = {}
                        temp_data[k][row['location']] = row['sell_price_min']

                new_deals = []
                for (iid, qual), prices in temp_data.items():
                    p_city = prices.get('Caerleon', 0)
                    p_bm = prices.get('BlackMarket', 0)
                    
                    if p_city > 0 and p_bm > 0:
                        # Чистая прибыль (9% налог ЧР)
                        profit = p_bm - p_city - (p_bm * 0.09)
                        
                        if profit > 500:
                            res = {
                                "name": iid.replace("T4_", "4.").replace("T5_", "5.").replace("T6_", "6.").replace("_MAIN_", " "),
                                "q": qual, "buy": p_city, "sell": p_bm, "p": int(profit)
                            }
                            new_deals.append(res)
                            
                            # Уведомление в ТГ если профит > 15,000 (чтобы не спамить мелкими)
                            if profit > 15000:
                                send_tg(f"💰 *Earned {int(profit):,}* silver!\n`{iid}` (Q:{qual})\n🛒 Купи: {p_city:,}\n🏴‍☠️ ЧР: {p_bm:,}")
                
                current_deals = sorted(new_deals, key=lambda x: x['p'], reverse=True)
                status = f"Европа: OK. Обновлено в {time.strftime('%H:%M:%S')}. Сделок: {len(current_deals)}"
            else:
                status = f"Ошибка API: {r.status_code}"
        except Exception as e:
            status = f"Ошибка: {str(e)}"
        time.sleep(40)

threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def home():
    rows = "".join([f"<tr><td>{d['name']}</td><td>{d['q']}</td><td>{d['buy']:,}</td><td>{d['sell']:,}</td><td style='color:#00ff00; font-weight:bold;'>+{d['p']:,}</td></tr>" for d in current_deals])
    return f"""
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="background:#121212; color:white; font-family:sans-serif; padding:10px;">
        <h2 style="color:#5dade2;">🏴‍☠️ Albion Europe Beast Mode</h2>
        <div style="padding: 10px; border: 1px solid #444; margin-bottom: 15px; font-size: 0.9em; color: #f1c40f;">
            {status}
        </div>
        <table border="1" style="width:100%; border-collapse:collapse; font-size: 0.85em;">
            <tr style="background:#333;"><th>Item</th><th>Q</th><th>Buy</th><th>Sell</th><th>Profit</th></tr>
            {rows if rows else '<tr><td colspan="5" style="text-align:center; padding:20px;">Ищем сделки... Пролистай рынок и ЧР в игре!</td></tr>'}
        </table>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
