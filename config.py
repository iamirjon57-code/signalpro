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
# Qisman foyda: +PARTIAL_TP_PCT da pozitsiyaning PARTIAL_TP_SHARE qismi sotiladi (0 = o'chiq)
PARTIAL_TP_PCT = _f("PARTIAL_TP_PCT", 1.8)
PARTIAL_TP_SHARE = _f("PARTIAL_TP_SHARE", 0.5)
# Trailing stop: +TRAIL_ACTIVATE_PCT dan keyin stop narx ortidan TRAILING_STOP_PCT masofada yuradi (0 = o'chiq)
TRAIL_ACTIVATE_PCT = _f("TRAIL_ACTIVATE_PCT", 1.8)
TRAILING_STOP_PCT = _f("TRAILING_STOP_PCT", 1.0)

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

# --- DEX (DexScreener) ---
# Trenddagi tangalar haqida Telegram xabarlari (faqat firibgarlik filtridan o'tganlari)
DEX_ALERTS = _b("DEX_ALERTS", "true")
# Qog'oz avto-savdo: haqiqiy narxlar, lekin pul ishlatilmaydi
DEX_AUTO_TRADE = _b("DEX_AUTO_TRADE", "false")
DEX_CHAINS = [x.lower() for x in _list("DEX_CHAINS", "solana,ethereum,bsc,base")]
DEX_MIN_LIQUIDITY = _f("DEX_MIN_LIQUIDITY", 30000)   # USD
DEX_MIN_VOLUME = _f("DEX_MIN_VOLUME", 50000)         # USD / 24 soat
DEX_MIN_AGE_H = _f("DEX_MIN_AGE_H", 24)              # juftlik yoshi, soat
# Xavfsizlik bahosi (0-100). Har bir ogohlantirish -10: 85 = ko'pi bilan bitta ogohlantirish
DEX_MIN_SCORE = _f("DEX_MIN_SCORE", 85)
DEX_TRADE_USDT = _f("DEX_TRADE_USDT", 10)
DEX_MAX_POSITIONS = int(_f("DEX_MAX_POSITIONS", 3))
DEX_TP_PCT = _f("DEX_TP_PCT", 30)
DEX_SL_PCT = _f("DEX_SL_PCT", 15)
DEX_MAX_HOLD_H = _f("DEX_MAX_HOLD_H", 48)
DEX_DAILY_LOSS_LIMIT = _f("DEX_DAILY_LOSS_LIMIT", 30)
DEX_PAPER_BALANCE = _f("DEX_PAPER_BALANCE", 1000)
DEX_SCAN_INTERVAL_SEC = int(_f("DEX_SCAN_INTERVAL_SEC", 300))

# --- Telegram ulanish usuli ---
# auto: sayt manzili bo'lsa webhook (tavsiya), bo'lmasa polling. Webhook'da botni boshqa
# nusxa (masalan, eski serverdagi) "o'g'irlay olmaydi" — tugmalar doim shu nusxaga keladi.
TELEGRAM_MODE = _env("TELEGRAM_MODE", "auto").lower()
WEBHOOK_BASE = _env("WEBHOOK_BASE", "") or _env("RAILWAY_PUBLIC_DOMAIN", "")

# --- Bozor kayfiyati filtrlari (faqat kripto avto-savdo xaridlari uchun) ---
# Fear & Greed indeksi shu qiymatdan yuqori bo'lsa (haddan tashqari ochko'zlik) — yangi xarid yo'q
FNG_MAX_BUY = _f("FNG_MAX_BUY", 85)
# Funding rate (8 soatlik, %) shundan yuqori bo'lsa — bozor haddan tashqari "long"da, xarid yo'q
FUNDING_MAX_BUY = _f("FUNDING_MAX_BUY", 0.08)
# Signalni katta vaqt oralig'idagi trend bilan tasdiqlash (kripto). Bo'sh = o'chiq
SIGNAL_HTF = _env("SIGNAL_HTF", "4h")

# --- AI maslahatchi (Claude API) ---
# Kalit: console.anthropic.com → API Keys. Bo'sh bo'lsa AI o'chiq, bot odatdagidek ishlaydi.
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", "")
AI_MODEL = _env("AI_MODEL", "claude-sonnet-5")
# Avto-savdoda har bir xariddan oldin AI tekshiruvi (rad etsa — savdo qilinmaydi)
AI_TRADE_FILTER = _b("AI_TRADE_FILTER", "true")
# AI bilan erkin suhbat faqat adminlarga (xarajatni nazorat qilish uchun)
AI_ADMIN_ONLY = _b("AI_ADMIN_ONLY", "true")

# --- Global yangiliklar va kunlik hisobot (AI kerak) ---
TZ_OFFSET_H = _f("TZ_OFFSET_H", 5)              # Toshkent vaqti (UTC+5)
DAILY_REPORT = _b("DAILY_REPORT", "true")       # har kuni ertalab hisobot
DAILY_REPORT_HOUR = int(_f("DAILY_REPORT_HOUR", 9))
NEWS_ALERTS = _b("NEWS_ALERTS", "true")         # bozorni qimirlatadigan yangilik chiqsa — darhol xabar
NEWS_ALERT_INTERVAL_MIN = _f("NEWS_ALERT_INTERVAL_MIN", 60)
NEWS_ALERT_MAX = int(_f("NEWS_ALERT_MAX", 2))

# --- Signallar statistikasi ---
PERF_EVAL_HOURS = _f("PERF_EVAL_HOURS", 24)      # signal natijasi necha soat ichida baholanadi
PERF_DEDUP_HOURS = _f("PERF_DEDUP_HOURS", 6)     # shu oraliqdagi takroriy signal bitta sanaladi

# --- Iqtisodiy taqvim ---
ECON_CURRENCIES = [x.strip() for x in _env("ECON_CURRENCIES", "USD,All").split(",") if x.strip()]
ECON_PAUSE = _b("ECON_PAUSE", "true")             # muhim voqea atrofida yangi xarid yo'q
ECON_PAUSE_BEFORE_MIN = _f("ECON_PAUSE_BEFORE_MIN", 30)
ECON_PAUSE_AFTER_MIN = _f("ECON_PAUSE_AFTER_MIN", 60)
ECON_REMIND_MIN = _f("ECON_REMIND_MIN", 60)       # voqeadan necha daqiqa oldin eslatma (0 = yo'q)

# --- Gemini (Google AI, Google qidiruv bilan) — ikkinchi AI ---
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")
# Ikkala AI bo'lsa: savdo faqat Claude ham, Gemini ham ruxsat bersa ochiladi
AI_DUAL_CHECK = _b("AI_DUAL_CHECK", "true")

# --- Kitlar va futures ---
WHALE_TRACK = _b("WHALE_TRACK", "true")
WHALE_MIN_USD = _f("WHALE_MIN_USD", 250000)      # yirik savdo chegarasi (BTC x4, ETH x2)
WHALE_ALERTS = _b("WHALE_ALERTS", "true")
WHALE_ALERT_X = _f("WHALE_ALERT_X", 25)          # 15 daqiqada sof oqim chegara x shu son bo'lsa — xabar

# --- Narx ogohlantirishlari ---
MAX_ALERTS = int(_f("MAX_ALERTS", 20))
