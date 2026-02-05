import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime

app = Flask(__name__)

TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

ACCOUNT_SIZE = 150000
RISK_PERCENT = 2

WATCHLIST = {
    "ASELS.IS": {"reference_price": None, "alerted": None},
    "TUPRS.IS": {"reference_price": None, "alerted": None},
    "FROTO.IS": {"reference_price": None, "alerted": None},
}

TICKERS = {symbol: yf.Ticker(symbol) for symbol in WATCHLIST.keys()}


def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    if 9 <= now.hour < 18:
        return True
    return False


def send(message):
    if not TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": message},
            timeout=5,
        )
    except Exception as e:
        print("Telegram error:", e)


def calculate_position(entry, stop):
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100)
    per_share_risk = abs(entry - stop)

    if per_share_risk == 0:
        return 0, 0

    lot = int(risk_amount / per_share_risk)
    total_risk = lot * per_share_risk
    return lot, total_risk


def get_current_prices():
    """Watchlist'teki hisselerin anlık fiyatlarını döndürür."""
    prices = {}
    for symbol in WATCHLIST:
        try:
            hist = TICKERS[symbol].history(period="1d", interval="1m", actions=False)
            if not hist.empty:
                prices[symbol] = round(float(hist["Close"].iloc[-1]), 2)
            else:
                prices[symbol] = None
        except Exception:
            prices[symbol] = None
    return prices


def price_monitor():
    print("Price monitor started")

    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            for symbol, data in WATCHLIST.items():
                hist = TICKERS[symbol].history(
                    period="1d",
                    interval="1m",
                    actions=False,
                )

                if hist.empty:
                    continue

                price = float(hist["Close"].iloc[-1])
                
                # İlk fiyat alındığında referans fiyatı ayarla
                if data["reference_price"] is None:
                    data["reference_price"] = price
                    continue

                reference = data["reference_price"]
                lower = reference - 1.0
                upper = reference + 1.0

                if price <= lower and data["alerted"] != "lower":
                    stop = upper
                    lot, total_risk = calculate_position(price, stop)

                    message = (
                        f"🔻 {symbol}\n"
                        f"Alt kırılım (-1 TL)\n"
                        f"Referans: {reference:.2f} TL\n"
                        f"Güncel: {price:.2f} TL\n"
                        f"Giriş: {price:.2f}\n"
                        f"Stop: {stop:.2f}\n"
                        f"Lot: {lot}\n"
                        f"Risk: {total_risk:.2f} TL"
                    )

                    send(message)
                    data["alerted"] = "lower"
                    data["reference_price"] = price  # Yeni referans fiyatı güncelle

                elif price >= upper and data["alerted"] != "upper":
                    stop = lower
                    lot, total_risk = calculate_position(price, stop)

                    message = (
                        f"🔺 {symbol}\n"
                        f"Üst kırılım (+1 TL)\n"
                        f"Referans: {reference:.2f} TL\n"
                        f"Güncel: {price:.2f} TL\n"
                        f"Giriş: {price:.2f}\n"
                        f"Stop: {stop:.2f}\n"
                        f"Lot: {lot}\n"
                        f"Risk: {total_risk:.2f} TL"
                    )

                    send(message)
                    data["alerted"] = "upper"
                    data["reference_price"] = price  # Yeni referans fiyatı güncelle

                elif lower < price < upper:
                    data["alerted"] = None

            time.sleep(30)

        except Exception as e:
            print("Monitor error:", e)
            time.sleep(10)


@app.route("/api/prices")
def api_prices():
    """Anlık fiyatları ve referans fiyatları JSON döndürür (panel otomatik güncelleme için)."""
    prices = get_current_prices()
    result = {}
    for symbol in WATCHLIST:
        price = prices.get(symbol)
        ref_price = WATCHLIST[symbol].get("reference_price")
        result[symbol] = {
            "price": price,
            "reference_price": ref_price,
            "lower": ref_price - 1.0 if ref_price is not None else None,
            "upper": ref_price + 1.0 if ref_price is not None else None,
        }
    return jsonify(result)


@app.route("/", methods=["GET", "POST"])
def home():
    error = None

    prices = get_current_prices()
    price_rows = []
    for s in WATCHLIST:
        price = prices.get(s)
        ref_price = WATCHLIST[s].get("reference_price")
        if ref_price is not None and price is not None:
            lower = ref_price - 1.0
            upper = ref_price + 1.0
        else:
            lower = None
            upper = None
        price_rows.append({
            "symbol": s,
            "price": price,
            "lower": lower,
            "upper": upper,
            "reference_price": ref_price,
        })

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>BIST Alarm Paneli</title>
        <style>
            body { font-family: system-ui, sans-serif; max-width: 520px; margin: 24px auto; padding: 0 16px; }
            h2 { margin-top: 0; }
            .prices { background: #f5f5f5; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; }
            .prices table { width: 100%; border-collapse: collapse; }
            .prices th, .prices td { text-align: left; padding: 8px; }
            .prices th { color: #555; font-weight: 600; }
            .price-cell { font-weight: 600; }
            .in-range { color: #0a0; }
            .above { color: #c00; }
            .below { color: #00a; }
            .no-price { color: #999; }
            .refresh { font-size: 12px; color: #666; margin-top: 8px; }
            form label { display: inline-block; width: 80px; }
            form input[type="text"], form select { padding: 6px; margin: 4px 0; }
            form button { padding: 8px 16px; margin-top: 8px; cursor: pointer; }
            .error { color: #c00; margin-bottom: 12px; }
        </style>
    </head>
    <body>
    <h2>BIST Alarm Paneli</h2>

    <div class="prices">
        <h3 style="margin: 0 0 10px 0;">Anlık Fiyatlar</h3>
        <table>
            <thead>
                <tr><th>Hisse</th><th>Anlık Fiyat</th><th>Alt / Üst</th><th>Durum</th></tr>
            </thead>
            <tbody id="price-body">
                {% for row in price_rows %}
                <tr>
                    <td>{{ row.symbol }}</td>
                    <td class="price-cell" data-symbol="{{ row.symbol }}" data-lower="{{ row.lower if row.lower is not none else '' }}" data-upper="{{ row.upper if row.upper is not none else '' }}">{{ row.price if row.price is not none else "—" }}</td>
                    <td>{% if row.lower is not none %}{{ "%.2f"|format(row.lower) }} / {{ "%.2f"|format(row.upper) }}{% else %}—{% endif %}</td>
                    <td class="status-cell" data-symbol="{{ row.symbol }}">—</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        <div class="refresh">
            Son güncelleme: <span id="last-update">yüklendi</span>
            <button type="button" id="btn-refresh" style="margin-left: 12px; padding: 4px 10px; cursor: pointer;">Yenile</button>
        </div>
    </div>

    {% if error %}
    <p class="error">{{ error }}</p>
    {% endif %}

    <p style="font-size: 14px; color: #666; margin-top: 20px;">
        <strong>Not:</strong> Sistem otomatik olarak her hisse için güncel fiyat ±1 TL aralığında alarm verir. 
        Referans fiyat ilk fiyat alındığında belirlenir ve alarm verildikten sonra güncellenir.
    </p>

    <script>
    function setStatus(cell, price, lower, upper) {
        cell.classList.remove("in-range", "above", "below");
        if (price == null || lower == null || upper == null) { cell.textContent = "—"; return; }
        if (price <= lower) { cell.textContent = "Alt kırılım (-1 TL)"; cell.classList.add("below"); }
        else if (price >= upper) { cell.textContent = "Üst kırılım (+1 TL)"; cell.classList.add("above"); }
        else { cell.textContent = "Bant içi (±1 TL)"; cell.classList.add("in-range"); }
    }
    function updatePrices() {
        fetch("/api/prices")
            .then(function(r) { return r.json(); })
            .then(function(data) {
                document.querySelectorAll(".price-cell").forEach(function(cell) {
                    var sym = cell.dataset.symbol;
                    var symbolData = data[sym];
                    if (!symbolData) return;
                    
                    var price = symbolData.price;
                    var lower = symbolData.lower;
                    var upper = symbolData.upper;
                    
                    cell.textContent = price != null ? price : "—";
                    if (price != null) cell.classList.remove("no-price"); else cell.classList.add("no-price");
                    
                    // Alt/Üst limitleri güncelle
                    var limitCell = cell.parentElement.querySelector("td:nth-child(3)");
                    if (limitCell) {
                        if (lower != null && upper != null) {
                            limitCell.textContent = lower.toFixed(2) + " / " + upper.toFixed(2);
                        } else {
                            limitCell.textContent = "—";
                        }
                    }
                    
                    var statusCell = cell.parentElement.querySelector(".status-cell");
                    if (statusCell) setStatus(statusCell, price, lower, upper);
                });
                var el = document.getElementById("last-update");
                if (el) el.textContent = new Date().toLocaleTimeString("tr-TR");
            })
            .catch(function() {});
    }
    setInterval(updatePrices, 30000);
    setTimeout(updatePrices, 2000);
    document.getElementById("btn-refresh").onclick = updatePrices;
    </script>
    </body>
    </html>
    """

    return render_template_string(
        html,
        price_rows=price_rows,
        error=error,
    )


monitor_thread = threading.Thread(target=price_monitor)
monitor_thread.daemon = True
monitor_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
