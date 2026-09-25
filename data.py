"""Bozor ma'lumotlari: Binance (ccxt) + Twelve Data (aksiya/forex, MetaTrader o'rniga)."""
from __future__ import annotations

import logging
import threading
import time

import ccxt
import pandas as pd
import requests

import config

log = logging.getLogger("data")

_TD_URL = "https://api.twelvedata.com/time_series"
_cache: dict[str, tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 240          # kripto uchun (soniya)
TD_CACHE_TTL = 1800      # Twelve Data uchun — bepul tarif kreditini tejaydi

# Twelve Data bepul tarifi: daqiqasiga 8 so'rov. Shu sabab so'rovlar oralig'i cheklanadi.
_td_lock = threading.Lock()
_td_last = 0.0
TD_MIN_GAP = 8.0


def _cached(key: str, ttl: float = CACHE_TTL):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _td_throttle():
    """Twelve Data so'rovlari orasida kamida TD_MIN_GAP soniya bo'lishini ta'minlaydi."""
    global _td_last
    with _td_lock:
        wait = TD_MIN_GAP - (time.time() - _td_last)
        if wait > 0:
            time.sleep(wait)
        _td_last = time.time()


def _put(key: str, df: pd.DataFrame):
    _cache[key] = (time.time(), df)
    return df


# ---------------- Binance (kripto) ----------------

_public_ex: ccxt.binance | None = None


def public_exchange() -> ccxt.binance:
    """Kalitsiz, faqat o'qish uchun Binance ulanishi."""
    global _public_ex
    if _public_ex is None:
        _public_ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    return _public_ex


def fetch_crypto(symbol: str, timeframe: str = "1h", limit: int = 300) -> pd.DataFrame:
    key = f"c:{symbol}:{timeframe}:{limit}"
    hit = _cached(key)
    if hit is not None:
        return hit
    raw = public_exchange().fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return _put(key, df)


# ---------------- Twelve Data (aksiya + forex) ----------------

_TF_MAP = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1day"}


def fetch_twelve(symbol: str, timeframe: str = "1h", limit: int = 300) -> pd.DataFrame:
    if not config.TWELVE_DATA_KEY:
        raise RuntimeError("TWELVE_DATA_KEY o'rnatilmagan — aksiya/forex tahlili ishlamaydi")
    key = f"t:{symbol}:{timeframe}:{limit}"
    hit = _cached(key, TD_CACHE_TTL)
    if hit is not None:
        return hit
    _td_throttle()
    r = requests.get(
        _TD_URL,
        params={
            "symbol": symbol,
            "interval": _TF_MAP.get(timeframe, "1h"),
            "outputsize": limit,
            "apikey": config.TWELVE_DATA_KEY,
            "format": "JSON",
        },
        timeout=20,
    )
    j = r.json()
    if j.get("status") == "error" or "values" not in j:
        raise RuntimeError(f"Twelve Data xato ({symbol}): {j.get('message', j)}")
    df = pd.DataFrame(j["values"]).iloc[::-1].reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Forex/metall juftliklarida Twelve Data hajm (volume) bermaydi
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    else:
        df["volume"] = 0.0
    df["time"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    return _put(key, df[["time", "open", "high", "low", "close", "volume"]])


# ---------------- Umumiy interfeys ----------------

def asset_class(symbol: str) -> str:
    s = symbol.upper()
    if s in config.CRYPTO_SYMBOLS or (("/" in s) and s.split("/")[1] in ("USDT", "BUSD", "FDUSD", "BTC")):
        return "crypto"
    if "/" in s:
        return "forex"
    return "stock"


def fetch(symbol: str, timeframe: str | None = None, limit: int = 300) -> pd.DataFrame:
    """Har qanday aktiv uchun OHLCV. MetaTrader juftliklari (EUR/USD, XAU/USD) forex sifatida."""
    timeframe = timeframe or config.TIMEFRAME
    kind = asset_class(symbol)
    if kind == "crypto":
        return fetch_crypto(symbol, timeframe, limit)
    return fetch_twelve(symbol, timeframe, limit)


def all_symbols() -> list[str]:
    return list(config.CRYPTO_SYMBOLS) + list(config.STOCK_SYMBOLS) + list(config.FOREX_SYMBOLS)


def last_price(symbol: str) -> float | None:
    try:
        if asset_class(symbol) == "crypto":
            return float(public_exchange().fetch_ticker(symbol)["last"])
        return float(fetch(symbol, limit=2)["close"].iloc[-1])
    except Exception as e:  # noqa: BLE001
        log.warning("last_price(%s): %s", symbol, e)
        return None
