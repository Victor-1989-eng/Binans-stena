import os, requests, time, threading
from flask import Flask

app = Flask(__name__)

# Секреты для Telegram
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# Список предметов (разбиваем на части для стабильности)
ITEMS_LIST = [
    "T4_BAG", "T5_BAG", "T6_BAG", "T4_CAPE", "T5_CAPE", "T6_CAPE",
    "T4_MAIN_SWORD", "T5_MAIN_SWORD", "T6_MAIN_SWORD",
    "T4_MAIN_AXE", "T5_MAIN_AXE", "T6_MAIN_AXE",
    "T4_BAG@1", "T5_BAG@1", "T4_CAPE@1"
]

current_deals = []
status = "Снайпер затаился..."

def send_tg(text):
    if TOKEN and CHAT_ID:
        try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
        except: pass

def scan_logic():
    global current_deals, status
    while True:
        new_found = []
        try:
            # Спрашиваем по 5 предметов за раз, чтобы API не "тупил"
            chunk_size = 5
            for i in range(0, len(ITEMS_LIST), chunk_size):
                chunk = ",".join(ITEMS_LIST[i:i+chunk_size])
                url = f"https://europe.albion-online-data.com/api/v2/stats/prices/{chunk}?locations=Caerleon,BlackMarket"
                
                r = requests.get(url, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    # Группируем цены
                    market_map = {}
                    for row in data:
                        k = (row['item_id'], row['quality'])
                        if k not in market_map: market_map[k] = {}
                        market_map[k][row['location']] = row['sell_price_min']

                    for (iid, qual), locs in market_map.items():
                        p_city = locs.get('Caerleon', 0)
                        p_bm = locs.get('BlackMarket', 0)
                        
                        if p_city > 0 and p_bm > 0:
                            profit = p_bm - p_city - (p_bm * 0.09)
                            # СНИЗИЛ ПОРОГ ДО 100 СЕРЕБРА, чтобы ты точно увидел результат
                            if profit > 100:
                                new_found.append({
                                    "name": iid.replace("T4_", "4.").replace("T5_", "5.").replace("T6_", "6.").replace("_MAIN_", " "),
                                    "q": qual, "buy": p_city, "sell": p_bm, "p": int(profit)
                                })
                                if profit > 20000:
                                    send_tg(f"💰 *Earned {int(profit):,}* silver!\n`{iid}` (Q:{qual})")
            
            current_deals = sorted(new_found, key=lambda x: x['p'], reverse=True)
            status = f"Европа: Вижу данные! Обновлено: {time.strftime('%H:%M:%S')}. Найдено: {len(current_deals)}"
            
        except Exception as e:
            status = f"Ошибка: {str(e)}"
        
        time.sleep(30)

threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def home():
    rows = "".join([f"<tr><td>{d['name']}</td><td>{d['q']}</td><td>{d['buy']:,}</td><td>{d['sell']:,}</td><td style='color:#00ff00;'>+{d['p']:,}</td></tr>" for d in current_deals])
    return f"""
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="background:#111; color:white; font-family:sans-serif; padding:10px;">
        <h2 style="color:#5dade2;">🎯 Sniper Mode: Europe</h2>
        <p style="color:yellow;">{status}</p>
        <table border="1" style="width:100%; border-collapse:collapse; font-size:0.8em;">
            <tr style="background:#333;"><th>Item</th><th>Q</th><th>Buy</th><th>Sell</th><th>Profit</th></tr>
            {rows if rows else '<tr><td colspan="5" style="text-align:center; padding:20px;">Твои данные летят, бот их ловит... Обнови через 20 сек!</td></tr>'}
        </table>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
