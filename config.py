"""Markaziy sozlamalar — hammasi ENV o'zgaruvchilardan o'qiladi (Railway Variables)."""
import os

try:  # lokal ishga tushirishda .env faylini o'qiydi (Railway'da kerak emas)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass


def _b(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes", "on")


def _f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _list(key: str, default: str):
    raw = os.getenv(key, default)
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


# --- Telegram ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
# Signal yuboriladigan chat ID lar (vergul bilan). Bo'sh bo'lsa faqat /start bosganlarga.
TELEGRAM_CHAT_IDS = [x.strip() for x in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]
ADMIN_IDS = {x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

# --- Binance ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
BINANCE_TESTNET = _b("BINANCE_TESTNET", "true")      # default: testnet (xavfsiz)
AUTO_TRADE = _b("AUTO_TRADE", "false")               # default: o'chiq
TRADE_QUOTE = os.getenv("TRADE_QUOTE", "USDT")
TRADE_AMOUNT_USDT = _f("TRADE_AMOUNT_USDT", 15.0)    # bitta savdoga ajratiladigan summa
MAX_OPEN_POSITIONS = int(_f("MAX_OPEN_POSITIONS", 3))
DAILY_LOSS_LIMIT_USDT = _f("DAILY_LOSS_LIMIT_USDT", 50.0)
TAKE_PROFIT_PCT = _f("TAKE_PROFIT_PCT", 3.0)
STOP_LOSS_PCT = _f("STOP_LOSS_PCT", 1.5)

# --- Bozor ma'lumotlari ---
TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY", "").strip()   # aksiya + forex (bepul tarif bor)
CRYPTO_SYMBOLS = _list("CRYPTO_SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT")
STOCK_SYMBOLS = _list("STOCK_SYMBOLS", "AAPL,MSFT,NVDA,TSLA,AMZN")
FOREX_SYMBOLS = _list("FOREX_SYMBOLS", "EUR/USD,GBP/USD,USD/JPY,XAU/USD")
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
SCAN_INTERVAL_SEC = int(_f("SCAN_INTERVAL_SEC", 900))   # 15 daqiqa

# --- Signal chegaralari ---
RSI_PERIOD = int(_f("RSI_PERIOD", 14))
RSI_OVERSOLD = _f("RSI_OVERSOLD", 32)
RSI_OVERBOUGHT = _f("RSI_OVERBOUGHT", 68)
MIN_SCORE = _f("MIN_SCORE", 2)   # signal chiqishi uchun kerakli minimal ball (max 4)

# --- SEC 13F ---
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "SignalPro/1.0 (contact@example.com)")

# --- Web ---
PORT = int(_f("PORT", 8000))
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()  # bo'sh = ochiq
DB_PATH = os.getenv("DB_PATH", "signals.db")
