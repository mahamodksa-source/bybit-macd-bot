import requests
import pandas as pd
import time
import numpy as np

# إعدادات التليجرام
TELEGRAM_TOKEN = "8146710117:AAE0qNfD08ZBCiYk2iY2350qnEpGekfUfcg"
CHAT_ID = "618962376"

# إعدادات الفلترة
INTERVAL = 15   # فريم 15 دقيقة
MIN_VOLUME = 500000  # حد أدنى للفوليوم بالدولار

# حفظ آخر تنبيه مرسل لكل عملة
last_alerts = {}

# دالة إرسال رسالة لتليجرام
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print("خطأ إرسال:", e)

# دالة لجلب جميع عملات السبوت من بايبت
def get_spot_symbols():
    url = "https://api.bybit.com/v5/market/instruments-info?category=spot"
    data = requests.get(url).json()
    symbols = [s["symbol"] for s in data["result"]["list"] if s["quoteCoin"] == "USDT"]
    return symbols

# دالة لجلب بيانات الكلاينز
def get_klines(symbol, interval=15, limit=200):
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url).json()
    if "result" not in data or "list" not in data["result"]:
        return None
    df = pd.DataFrame(data["result"]["list"], columns=[
        "time", "open", "high", "low", "close", "volume", "turnover"
    ])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df

# دالة حساب MACD
def compute_macd(df, fast=12, slow=26, signal=9):
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = df["ema_fast"] - df["ema_slow"]
    df["signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    return df

# المراقبة
def scanner():
    symbols = get_spot_symbols()
    print(f"✅ عدد الأزواج: {len(symbols)}")

    for sym in symbols:
        df = get_klines(sym, interval=INTERVAL)
        if df is None or len(df) < 35:
            continue
        
        df = compute_macd(df)
        close = df["close"].iloc[-1]
        volume = df["volume"].iloc[-1] * close  # بالدولار
        ts = df["time"].iloc[-1]  # توقيت آخر شمعة

        macd_prev, signal_prev = df["macd"].iloc[-2], df["signal"].iloc[-2]
        macd_now, signal_now = df["macd"].iloc[-1], df["signal"].iloc[-1]

        # شرط: تقاطع صعودي MACD فوق Signal + فوليوم كافي
        if macd_prev < signal_prev and macd_now > signal_now and volume >= MIN_VOLUME:
            # تحقق إننا ما أرسلنا إشعار لنفس العملة على نفس الشمعة
            last_ts = last_alerts.get(sym)
            if last_ts != ts:
                msg = f"🚀 إشارة شراء (MACD)\nرمز: {sym}\nالسعر: {close}\nالفوليوم: {round(volume)} USDT\nالوقت: {ts}"
                print(msg)
                send_telegram(msg)
                last_alerts[sym] = ts  # تحديث آخر تنبيه

if __name__ == "__main__":
    while True:
        scanner()
        time.sleep(60)  # فحص كل دقيقة

