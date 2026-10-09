"""Bozor holati: kayfiyat indeksi, BTC ulushi, funding rate.

Manbalar (bepul, kalitsiz):
  * alternative.me — Crypto Fear & Greed Index
  * CoinGecko — umumiy bozor qiymati va BTC ulushi
  * Birja (Bitget/Binance) futures — funding rate (ccxt orqali)
"""
from __future__ import annotations

import logging
import time

import ccxt
import requests

import config

log = logging.getLogger("market")

_cache: dict[str, tuple[float, object]] = {}
_UA = {"User-Agent": "SignalPro/1.0", "Accept": "application/json"}

FNG_UZ = {"Extreme Fear": "kuchli qo'rquv", "Fear": "qo'rquv", "Neutral": "neytral",
          "Greed": "ochko'zlik", "Extreme Greed": "kuchli ochko'zlik"}


def _cached(key: str, ttl: int, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception as e:  # noqa: BLE001
        log.warning("%s: %s", key, e)
        return hit[1] if hit else None    # eski qiymat bo'lsa — shuni qaytaramiz
    _cache[key] = (time.time(), val)
    return val


def fear_greed() -> dict | None:
    """{'value': 64, 'label': 'ochko'zlik', 'prev': 71}"""
    def get():
        j = requests.get("https://api.alternative.me/fng/?limit=2", headers=_UA, timeout=12).json()
        d = j.get("data") or []
        if not d:
            return None
        cls = d[0].get("value_classification", "")
        return {"value": int(d[0]["value"]), "label": FNG_UZ.get(cls, cls),
                "prev": int(d[1]["value"]) if len(d) > 1 else None}
    return _cached("fng", 1800, get)


def global_market() -> dict | None:
    """{'btc_dom': 57.1, 'mcap_change': -1.2, 'mcap': 3.1e12}"""
    def get():
        j = requests.get("https://api.coingecko.com/api/v3/global", headers=_UA, timeout=12).json()
        d = j.get("data") or {}
        if not d:
            return None
        return {"btc_dom": float((d.get("market_cap_percentage") or {}).get("btc") or 0),
                "mcap_change": float(d.get("market_cap_change_percentage_24h_usd") or 0),
                "mcap": float((d.get("total_market_cap") or {}).get("usd") or 0)}
    return _cached("global", 600, get)


_swap = None


def _swap_ex():
    global _swap
    if _swap is None:
        cls = getattr(ccxt, config.EXCHANGE)
        _swap = cls({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    return _swap


def funding(symbol: str) -> float | None:
    """Perpetual futures funding rate, foizda (8 soatlik). Musbat = ko'pchilik 'long'da."""
    if "/" not in symbol:
        return None
    base, quote = symbol.split("/")[:2]
    perp = f"{base}/{quote}:{quote}"

    def get():
        r = _swap_ex().fetch_funding_rate(perp)
        fr = r.get("fundingRate")
        return None if fr is None else float(fr) * 100
    return _cached(f"fund:{perp}", 600, get)


def buy_block_reason(symbol: str) -> str | None:
    """Kripto xaridini to'xtatish sababi (bozor haddan tashqari qizigan bo'lsa), aks holda None."""
    fg = fear_greed()
    if fg and fg["value"] >= config.FNG_MAX_BUY:
        return f"Bozor haddan tashqari qizigan (Fear & Greed {fg['value']} ≥ {config.FNG_MAX_BUY:g})"
    fr = funding(symbol)
    if fr is not None and fr >= config.FUNDING_MAX_BUY:
        return f"Funding rate juda yuqori ({fr:+.3f}% ≥ {config.FUNDING_MAX_BUY:g}%) — bozor 'long'larga to'lib ketgan"
    return None


def summary_text() -> str:
    lines = ["*🌡 Bozor holati*"]
    fg = fear_greed()
    if fg:
        trend = ""
        if fg["prev"] is not None:
            trend = f" (kecha {fg['prev']})"
        lines.append(f"Kayfiyat (Fear & Greed): *{fg['value']}/100* — {fg['label']}{trend}")
    else:
        lines.append("Kayfiyat indeksini olib bo'lmadi")
    g = global_market()
    if g:
        lines.append(f"Kripto bozori: ${g['mcap'] / 1e12:.2f} trln · 24s {g['mcap_change']:+.1f}%")
        lines.append(f"BTC ulushi: {g['btc_dom']:.1f}%")
    fr_lines = []
    for s in config.CRYPTO_SYMBOLS[:5]:
        fr = funding(s)
        if fr is not None:
            fr_lines.append(f"`{s.split('/')[0]}` {fr:+.4f}%")
    if fr_lines:
        lines.append("Funding (8s): " + " · ".join(fr_lines))
    lines.append("")
    if fg:
        if fg["value"] >= 75:
            lines.append("⚠️ Bozor qizigan — keskin tushish ehtimoli oshgan, yangi xaridda ehtiyot bo'ling.")
        elif fg["value"] <= 25:
            lines.append("ℹ️ Bozorda qo'rquv — narxlar past, lekin tushish davom etishi ham mumkin.")
        else:
            lines.append("ℹ️ Bozor kayfiyati o'rtacha.")
    lines.append(f"Avto-savdo filtri: F&G ≥ {config.FNG_MAX_BUY:g} yoki funding ≥ {config.FUNDING_MAX_BUY:g}% bo'lsa yangi xarid yo'q.")
    return "\n".join(lines)
