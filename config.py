"""Markaziy sozlamalar — hammasi ENV o'zgaruvchilardan o'qiladi (Railway Variables)."""
import os
import re

try:  # lokal ishga tushirishda .env faylini o'qiydi (Railway'da kerak emas)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass


def _env(key: str, default: str = "") -> str:
    """ENV qiymati; qator oxiridagi `  # izoh` olib tashlanadi.

    systemd EnvironmentFile inline izohni qiymatning bir qismi deb o'qiydi
    (`BINANCE_TESTNET=true  # izoh` -> "true  # izoh" -> false = REAL pul!). Shuning uchun tozalaymiz.
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    raw = re.split(r"(?:^|\s)#", raw, maxsplit=1)[0]
    return raw.strip().strip('"').strip("'").strip()


def _b(key: str, default: str = "false") -> bool:
    # bo'sh qiymat = default (bo'sh BINANCE_TESTNET testnet bo'lib qoladi)
    return (_env(key, default) or default).lower() in ("1", "true", "yes", "on")


def _f(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _list(key: str, default: str):
    raw = _env(key, default)
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


# --- Telegram ---
TELEGRAM_TOKEN = _env("TELEGRAM_TOKEN", "")
# Signal yuboriladigan chat ID lar (vergul bilan). Bo'sh bo'lsa faqat /start bosganlarga.
TELEGRAM_CHAT_IDS = [x.strip() for x in _env("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]
ADMIN_IDS = {x.strip() for x in _env("ADMIN_IDS", "").split(",") if x.strip()}

# --- Birja ---
# binance yoki bitget. Narxlar ham, avto-savdo ham shu birjadan.
EXCHANGE = _env("EXCHANGE", "binance").lower()
if EXCHANGE not in ("binance", "bitget"):
    EXCHANGE = "binance"
BITGET_API_KEY = _env("BITGET_API_KEY", "")
BITGET_API_SECRET = _env("BITGET_API_SECRET", "")
BITGET_PASSPHRASE = _env("BITGET_PASSPHRASE", "")
# Bitget sinov rejimida "qog'oz savdo" uchun boshlang'ich xayoliy balans
PAPER_BALANCE_USDT = _f("PAPER_BALANCE_USDT", 1000.0)

# --- Binance ---
BINANCE_API_KEY = _env("BINANCE_API_KEY", "")
BINANCE_API_SECRET = _env("BINANCE_API_SECRET", "")
# Sinov rejimi (ikkala birja uchun): true = haqiqiy pul ishlatilmaydi.
# Binance — testnet; Bitget — qog'oz savdo (buyurtma birjaga yuborilmaydi).
BINANCE_TESTNET = _b("BINANCE_TESTNET", "true")      # default: sinov (xavfsiz)
TESTNET = BINANCE_TESTNET
AUTO_TRADE = _b("AUTO_TRADE", "false")               # default: o'chiq
TRADE_QUOTE = _env("TRADE_QUOTE", "USDT")
TRADE_AMOUNT_USDT = _f("TRADE_AMOUNT_USDT", 15.0)    # bitta savdoga ajratiladigan summa
MAX_OPEN_POSITIONS = int(_f("MAX_OPEN_POSITIONS", 3))
DAILY_LOSS_LIMIT_USDT = _f("DAILY_LOSS_LIMIT_USDT", 50.0)
TAKE_PROFIT_PCT = _f("TAKE_PROFIT_PCT", 3.0)
STOP_LOSS_PCT = _f("STOP_LOSS_PCT", 1.5)

# --- Bozor ma'lumotlari ---
TWELVE_DATA_KEY = _env("TWELVE_DATA_KEY", "")   # aksiya + forex (bepul tarif bor)
CRYPTO_SYMBOLS = _list("CRYPTO_SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT")
STOCK_SYMBOLS = _list("STOCK_SYMBOLS", "AAPL,MSFT,NVDA,TSLA,AMZN")
FOREX_SYMBOLS = _list("FOREX_SYMBOLS", "EUR/USD,GBP/USD,USD/JPY,XAU/USD")
TIMEFRAME = _env("TIMEFRAME", "1h")
SCAN_INTERVAL_SEC = int(_f("SCAN_INTERVAL_SEC", 900))   # 15 daqiqa
# Aksiya/forex har necha skanda bir marta tekshiriladi (Twelve Data kunlik limiti uchun)
TD_EVERY_N_SCANS = int(_f("TD_EVERY_N_SCANS", 2))

# --- Signal chegaralari ---
RSI_PERIOD = int(_f("RSI_PERIOD", 14))
RSI_OVERSOLD = _f("RSI_OVERSOLD", 32)
RSI_OVERBOUGHT = _f("RSI_OVERBOUGHT", 68)
MIN_SCORE = _f("MIN_SCORE", 2)   # signal chiqishi uchun kerakli minimal ball (max 4)

# --- SEC 13F ---
SEC_USER_AGENT = _env("SEC_USER_AGENT", "SignalPro/1.0 (contact@example.com)")

# --- Web ---
PORT = int(_f("PORT", 8000))
DASHBOARD_PASSWORD = _env("DASHBOARD_PASSWORD", "")  # bo'sh = ochiq
DB_PATH = _env("DB_PATH", "signals.db")
