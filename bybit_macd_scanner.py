import os
import time
import requests
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional

# ====== الإعدادات من Environment ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")          # ضعها في Render > Settings > Environment
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")               # مثلاً 618962376
INTERVAL = int(os.getenv("INTERVAL_MIN", "15"))       # دقائق
MIN_VOLUME = float(os.getenv("MIN_VOLUME_USD", "500000"))  # بالدولار

# ====== جلسة HTTP مع مهلة وإعادة محاولات ======
session = requests.Session()
session.headers.update({"User-Agent": "BybitMACDScanner/1.0"})
TIMEOUT = 20

# كاش للرموز وآخر التنبيهات
spot_symbols_cache: List[str] = []
last_alerts: Dict[str, pd.Timestamp] = {}

def send_telegram(text: str) -> None:
    token = TELEGRAM_TOKEN
    chat_id = CHAT_ID
    if not token or not chat_id:
        print("⚠️ TELEGRAM_TOKEN/CHAT_ID غير موجودين في Environment.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = session.post(url, json=payload, timeout=TIMEOUT)
        if r.status_code != 200:
            print("⚠️ Telegram error:", r.status_code, r.text[:200])
    except Exception as e:
        print("⚠️ Telegram send error:", e)

def get_spot_symbols(force_refresh: bool = False) -> List[str]:
    global spot_symbols_cache
    if spot_symbols_cache and not force_refresh:
        return spot_symbols_cache
    url = "https://api.bybit.com/v5/market/instruments-info?category=spot"
    try:
        data = session.get(url, timeout=TIMEOUT).json()
        lst = data.get("result", {}).get("list", [])
        spot_symbols_cache = [s["symbol"] for s in lst if s.get("quoteCoin") == "USDT"]
    except Exception as e:
        print("⚠️ get_spot_symbols error:", e)
        spot_symbols_cache = []
    return spot_symbols_cache

def get_klines(symbol: str, interval_min: int = 15, limit: int = 200) -> Optional[pd.DataFrame]:
    url = (
        "https://api.bybit.com/v5/market/kline"
        f"?category=spot&symbol={symbol}&interval={interval_min}&limit={limit}"
    )
    try:
        data = session.get(url, timeout=TIMEOUT).json()
        rows = data.get("result", {}).get("list")
        if not rows:
            return None
        df = pd.DataFrame(
            rows,
            columns=["time", "open", "high", "low", "close", "volume", "turnover"],
        )
        # تحويل الأنواع
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close", "volume"])
        return df
    except Exception as e:
        print(f"⚠️ get_klines error for {symbol}:", e)
        return None

def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = df["ema_fast"] - df["ema_slow"]
    df["signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    return df

def scanner():
    symbols = get_spot_symbols()
    print(f"✅ [{datetime.utcnow().isoformat()}Z] عدد أزواج USDT: {len(symbols)}")

    for sym in symbols:
        df = get_klines(sym, interval_min=INTERVAL)
        if df is None or len(df) < 35:
            continue

        df = compute_macd(df)
        close = float(df["close"].iloc[-1])
        # فوليوم الشمعة بالدولار
        volume_usd = float(df["volume"].iloc[-1]) * close
        ts = df["time"].iloc[-1]

        macd_prev, signal_prev = float(df["macd"].iloc[-2]), float(df["signal"].iloc[-2])
        macd_now, signal_now   = float(df["macd"].iloc[-1]), float(df["signal"].iloc[-1])

        # تقاطع صعودي + فوليوم كافي
        if macd_prev < signal_prev and macd_now > signal_now and volume_usd >= MIN_VOLUME:
            last_ts = last_alerts.get(sym)
            if last_ts != ts:
                msg = (
                    "🚀 إشارة شراء (MACD)\n"
                    f"رمز: {sym}\n"
                    f"السعر: {close}\n"
                    f"الفوليوم: {round(volume_usd):,} USDT\n"
                    f"الوقت: {ts}"
                )
                print(msg)
                send_telegram(msg)
                last_alerts[sym] = ts
        time.sleep(0.15)  # احترام API

def main():
    print("🚀 Bybit MACD Scanner started.")
    if TELEGRAM_TOKEN:
        print("✅ TELEGRAM_TOKEN مضبوط.")
    else:
        print("⚠️ لا يوجد TELEGRAM_TOKEN في Environment.")
    if not CHAT_ID:
        print("⚠️ لا يوجد TELEGRAM_CHAT_ID في Environment.")

    interval_seconds = 60  # افحص كل دقيقة
    while True:
        try:
            scanner()
        except Exception as e:
            print("❗ Loop error:", e)
        time.sleep(interval_seconds)

if __name__ == "__main__":
    main()
