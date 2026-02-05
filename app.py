import os
import math
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
SECRET = os.environ["SECRET"]

def send(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    })

@app.route("/")
def home():
    return "Bot is running"

@app.route("/tv", methods=["POST"])
def tv():
    data = request.json

    if data.get("passphrase") != SECRET:
        return jsonify({"error": "unauthorized"}), 401

    symbol = data["symbol"]
    price = float(data["price"])
    stop = float(data["stop"])
    account = float(data["account_try"])
    risk = float(data["risk_pct"])

    risk_amount = account * (risk/100)
    qty = math.floor(risk_amount / abs(price-stop))

    msg = f"""
<b>{symbol} Sinyal</b>
Giriş: {price}
Stop: {stop}
Önerilen Lot: {qty}
"""

    send(msg)

    return jsonify({"ok": True})
