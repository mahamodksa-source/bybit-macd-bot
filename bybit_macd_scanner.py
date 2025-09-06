import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone   # ← استخدمنا timezone
from typing import Dict, List, Optional

# ====== الإعدادات من Environment ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")                # Render > Settings > Environment
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")                     # مثال: 618962376 (يقبل سترنق)
INTERVAL = os.getenv("INTERVAL_MIN", "15")                  # قيم Bybit المسموحة: 1,3,5,15,30,60,120,240,360,720,D,W,M
MIN_VOLUME = float(os.getenv("MIN_VOLUME_USD", "500000"))   # بالدولار

# ====== جلسة HTTP مع مهلة وإعادة محاولات ======
session = requests.Session()
session.headers.update({
    "User-Agent": "BybitMACDScanner/1.0",
    "Accept": "application/json",
})
TIMEOUT = 20
BASE_URL = os.getenv("BYBIT_API_BASE", "https://api.bybit.com")  # ← من الـEnv إن أردت

# كاش للرموز وآخر التنبيهات
spot_symbols_cache: List[str] = []
last_alerts: Dict[str, pd.Timestamp] = {}

# ====== دوال مساعدة للشبكة ======
def _get_json(path: str, params: Optional[dict] = None, max_retries: int = 3, timeout: int = TIMEOUT):
    """طلب GET مع تحقّق من HTTP/JSON وRetries بسيطة"""
    url = f"{BASE_URL}{path}"
    last_err = None
    for i in range(max_retries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            # تحقق من الكود
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}; ct={r.headers.get('Content-Type')} body={r.text[:300]}")
            # تأكد أن الرد JSON (تجنب صفحات HTML)
            if "application/json" not in (r.headers.get("Content-Type") or ""):
                raise RuntimeError(f"Non-JSON response; ct={r.headers.get('Content-Type')} body={r.text[:300]}")
            data = r.json()
            # Bybit v5 يرجّع retCode=0 في النجاح
            if isinstance(data, dict) and data.get("retCode") not in (0, None):
                raise RuntimeError(f"Bybit retCode={data.get('retCode')} msg={data.get('retMsg')}")
            return data
        except Exception as e:
            last_err = e
            print(f"⚠️ HTTP try {i+1} failed: {e}")
            time.sleep(1.5 * (i + 1))   # backoff بسيط
    raise RuntimeError(f"Failed after retries: {last_err}")

# ====== Telegram ======
def send_telegram(text: str) -> bool:
    token = TELEGRAM_TOKEN
    chat_id = CHAT_ID
    if not token or not chat_id:
        print("⚠️ TELEGRAM_TOKEN/CHAT_ID غير موجودين في Environment.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = session.post(url, json=payload, timeout=TIMEOUT)
        if r.status_code != 200:
            print("⚠️ Telegram error:", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        print("⚠️ Telegram send error:", e)
        return False

def test_telegram() -> None:
    ok = send_telegram("✅ تم تشغيل Bybit MACD Scanner (رسالة اختبار).")
    print("✅ Telegram test: OK." if ok else "❌ Telegram test: FAILED.")

# ====== Bybit ======
def get_spot_symbols(force_refresh: bool = False) -> List[str]:
    global spot_symbols_cache
    if spot_symbols_cache and not force_refresh:
        return spot_symbols_cache
    try:
        data = _get_json("/v5/market/instruments-info", params={"category": "spot"})
        items = (data.get("result") or {}).get("list") or []
        spot_symbols_cache = [
            it["symbol"]
            for it in items
            if it.get("quoteCoin") == "USDT" and it.get("status") == "Trading"
        ]
    except Exception as e:
        print("❗ get_spot_symbols error:", e)
        spot_symbols_cache = []
    return spot_symbols_cache

def get_klines(symbol: str, interval_min: str = "15", limit: int = 200) -> Optional[pd.DataFrame]:
    try:
        data = _get_json(
            "/v5/market/kline",
            params={"category": "spot", "symbol": symbol, "interval": str(interval_min), "limit": str(limit)}
        )
        rows = (data.get("result") or {}).get("list")
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
        # تحويل الأنواع
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close", "volume"]).sort_values("time").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"⚠️ get_klines error for {symbol}: {e}")
        return None

# ====== MACD ======
def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = df["ema_fast"] - df["ema_slow"]
    df["signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    return df

# ====== الماسح ======
def scanner():
    # اجلب الرموز مرة واحدة (الكاش يمنع إعادة الجلب المتكرر)
    symbols = get_spot_symbols()
    # ← استبدل utcnow بوقت واعٍ بالمنطقة
    print(f"✅ [{datetime.now(timezone.utc).isoformat()}] عدد أزواج USDT: {len(symbols)}")

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

        # احترام API (خفّض السرعة لتجنب Rate Limit)
        time.sleep(0.2)

def main():
    print("🚀 Bybit MACD Scanner started.")
    print("✅ TELEGRAM_TOKEN مضبوط." if TELEGRAM_TOKEN else "⚠️ لا يوجد TELEGRAM_TOKEN في Environment.")
    if not CHAT_ID:
        print("⚠️ لا يوجد TELEGRAM_CHAT_ID في Environment.")

    # اختبار تلغرام عند بدء التشغيل
    test_telegram()

    interval_seconds = 60  # افحص كل دقيقة
    while True:
        try:
            scanner()
        except Exception as e:
            print("❗ Loop error:", e)
        time.sleep(interval_seconds)

if __name__ == "__main__":
    main()
