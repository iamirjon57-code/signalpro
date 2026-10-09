"""Kitlar (yirik savdolar) va futures bozori ma'lumotlari.

* Kitlar: Binance spot'dagi yirik bozor savdolari (aggTrades) — kim kuch bilan sotib
  olayapti / sotayapti. Har daqiqada yig'iladi, oxirgi 1 soat xotirada.
* Futures (Binance USDⓈ-M, ochiq ma'lumot): ochiq pozitsiyalar (open interest) va
  24 soatlik o'zgarishi, long/short nisbati (oddiy va eng yirik treyderlar), xaridor/sotuvchi
  bosimi. Funding rate — market.py (birjadan).
Hammasi bepul, kalitsiz.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import requests

import config

log = logging.getLogger("flows")
_UA = {"User-Agent": "SignalPro/1.0"}
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()
_trades: dict[str, dict[int, tuple[float, float, bool]]] = {}   # sym -> {aggId: (ts, usd, is_buy)}
_alerted: dict[str, float] = {}


def _get(url: str, params: dict, ttl: int):
    key = url + str(sorted(params.items()))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        r = requests.get(url, params=params, headers=_UA, timeout=12)
        r.raise_for_status()
        val = r.json()
    except Exception as e:  # noqa: BLE001
        log.info("%s: %s", url.rsplit("/", 1)[-1], e)
        return hit[1] if hit else None
    _cache[key] = (time.time(), val)
    return val


def _pair(symbol: str) -> str:
    return symbol.replace("/", "").upper()


def whale_min(symbol: str) -> float:
    base = symbol.split("/")[0].upper()
    mult = {"BTC": 4, "ETH": 2}.get(base, 1)
    return config.WHALE_MIN_USD * mult


# ---------------- Futures ----------------

def futures(symbol: str) -> dict | None:
    """Ochiq pozitsiyalar, long/short nisbati, xaridor bosimi (Binance futures)."""
    if "/" not in symbol:
        return None
    p = _pair(symbol)
    base = "https://fapi.binance.com"
    oi = _get(f"{base}/futures/data/openInterestHist", {"symbol": p, "period": "1h", "limit": 25}, 300)
    ls = _get(f"{base}/futures/data/globalLongShortAccountRatio", {"symbol": p, "period": "1h", "limit": 1}, 300)
    top = _get(f"{base}/futures/data/topLongShortPositionRatio", {"symbol": p, "period": "1h", "limit": 1}, 300)
    tk = _get(f"{base}/futures/data/takerlongshortRatio", {"symbol": p, "period": "1h", "limit": 4}, 300)
    if not any((oi, ls, top, tk)):
        return None
    out: dict = {"symbol": symbol}
    if isinstance(oi, list) and oi:
        now_v, old_v = float(oi[-1]["sumOpenInterestValue"]), float(oi[0]["sumOpenInterestValue"])
        out["oi_usd"] = now_v
        out["oi_chg24"] = (now_v / old_v - 1) * 100 if old_v else None
    if isinstance(ls, list) and ls:
        out["ls"] = float(ls[-1]["longShortRatio"])
        out["long_pct"] = float(ls[-1]["longAccount"]) * 100
    if isinstance(top, list) and top:
        out["top_ls"] = float(top[-1]["longShortRatio"])
    if isinstance(tk, list) and tk:
        out["taker"] = sum(float(x["buySellRatio"]) for x in tk) / len(tk)
    return out


def futures_note(f: dict) -> str:
    notes = []
    if f.get("ls") and f["ls"] >= 2.5:
        notes.append("oddiy treyderlar haddan tashqari ko'p 'long'da — pastga silkinish xavfi")
    elif f.get("ls") and f["ls"] <= 0.7:
        notes.append("ko'pchilik 'short'da — keskin ko'tarilish (short squeeze) ehtimoli")
    if f.get("oi_chg24") is not None and f["oi_chg24"] >= 10:
        notes.append("ochiq pozitsiyalar tez o'smoqda — kuchli harakat kutilmoqda")
    if f.get("taker") and f["taker"] >= 1.15:
        notes.append("xaridorlar bosimi kuchli")
    elif f.get("taker") and f["taker"] <= 0.87:
        notes.append("sotuvchilar bosimi kuchli")
    return "; ".join(notes)


# ---------------- Kitlar ----------------

_last_id: dict[str, int] = {}


def _agg(symbol: str, from_id: int | None) -> list:
    params = {"symbol": _pair(symbol), "limit": 1000}
    if from_id is not None:
        params["fromId"] = from_id
    try:
        r = requests.get("https://api.binance.com/api/v3/aggTrades", params=params, headers=_UA, timeout=12)
        r.raise_for_status()
        j = r.json()
        return j if isinstance(j, list) else []
    except Exception as e:  # noqa: BLE001
        log.info("aggTrades %s: %s", symbol, e)
        return []


def collect(symbol: str, max_pages: int = 8) -> int:
    """Binance spot'dagi savdolarni (oxirgi tekshiruvdan beri) o'qib, yiriklarini xotiraga yig'adi."""
    rows: list = []
    last = _last_id.get(symbol)
    if last is None:
        rows = _agg(symbol, None)
    else:
        for _ in range(max_pages):
            page = _agg(symbol, last + 1)
            rows += page
            if len(page) < 1000:
                break
            last = page[-1]["a"]
        else:
            last = None   # juda ko'p savdo — keyingi safar eng oxiridan boshlaymiz
    if not rows:
        return 0
    if last is None and symbol in _last_id:
        _last_id.pop(symbol)
    else:
        _last_id[symbol] = rows[-1]["a"]
    thr, n = whale_min(symbol), 0
    with _lock:
        book = _trades.setdefault(symbol, {})
        for t in rows:
            usd = float(t["p"]) * float(t["q"])
            if usd >= thr and t["a"] not in book:
                book[t["a"]] = (t["T"] / 1000, usd, not t["m"])   # m=True: sotuvchi tashabbusi
                n += 1
        cutoff = time.time() - 3600
        for k in [k for k, v in book.items() if v[0] < cutoff]:
            book.pop(k)
    return n


def whales(symbol: str, minutes: float = 60) -> dict:
    cutoff = time.time() - minutes * 60
    with _lock:
        rows = [v for v in _trades.get(symbol, {}).values() if v[0] >= cutoff]
    buy = sum(u for _, u, b in rows if b)
    sell = sum(u for _, u, b in rows if not b)
    return {"n": len(rows), "buy": buy, "sell": sell, "net": buy - sell,
            "max": max((u for _, u, _ in rows), default=0), "min_usd": whale_min(symbol)}


def _money(x: float) -> str:
    x = abs(x)
    return f"${x / 1e9:.2f}B" if x >= 1e9 else f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}K"


def symbol_text(symbol: str) -> str:
    lines = [f"*{symbol}*"]
    f = futures(symbol)
    if f:
        if f.get("oi_usd"):
            ch = f" ({f['oi_chg24']:+.1f}% 24s)" if f.get("oi_chg24") is not None else ""
            lines.append(f"Ochiq pozitsiyalar: {_money(f['oi_usd'])}{ch}")
        if f.get("ls"):
            lines.append(f"Long/short: {f['ls']:.2f} ({f['long_pct']:.0f}% long) · yirik treyderlar: "
                         f"{f.get('top_ls', 0):.2f}")
        if f.get("taker"):
            lines.append(f"Xaridor/sotuvchi bosimi: {f['taker']:.2f}")
        note = futures_note(f)
        if note:
            lines.append(f"→ {note}")
    w = whales(symbol)
    if w["n"]:
        side = "🟢 kitlar sotib olmoqda" if w["net"] > 0 else "🔴 kitlar sotmoqda"
        lines.append(f"🐋 1 soatda {w['n']} ta yirik savdo (≥{_money(w['min_usd'])}): "
                     f"xarid {_money(w['buy'])} / sotuv {_money(w['sell'])} → {side}")
    else:
        lines.append(f"🐋 1 soatda yirik savdo (≥{_money(w['min_usd'])}) kuzatilmadi")
    return "\n".join(lines)


def overview_text() -> str:
    parts = ["*🐋 Kitlar va futures bozori*",
             "(Binance: yirik spot savdolar + futures ochiq ma'lumotlari)", ""]
    for s in config.CRYPTO_SYMBOLS[:5]:
        parts.append(symbol_text(s))
        parts.append("")
    parts.append("ℹ️ Long/short 2.5 dan yuqori — olomon haddan tashqari 'long'da (ko'pincha pastga silkinish oldidan). "
                 "Xaridor bosimi 1 dan yuqori — sotib olayotganlar kuchli.")
    return "\n".join(parts)


def context(symbol: str) -> str:
    """AI uchun qisqa matn."""
    out = []
    f = futures(symbol)
    if f:
        out.append(f"Futures: OI {_money(f.get('oi_usd', 0))} ({(f.get('oi_chg24') or 0):+.1f}% 24s), "
                   f"long/short {f.get('ls', 0):.2f}, yirik treyderlar {f.get('top_ls', 0):.2f}, "
                   f"xaridor bosimi {f.get('taker', 0):.2f}")
    w = whales(symbol)
    if w["n"]:
        out.append(f"Kitlar (1s): {w['n']} yirik savdo, xarid {_money(w['buy'])}, sotuv {_money(w['sell'])}")
    return "; ".join(out) or "futures/kit ma'lumoti yo'q"


async def loop(broadcast):
    if not config.WHALE_TRACK:
        return
    await asyncio.sleep(40)
    while True:
        for s in config.CRYPTO_SYMBOLS:
            try:
                await asyncio.to_thread(collect, s)
                w = whales(s, 15)
                limit = w["min_usd"] * config.WHALE_ALERT_X
                if (config.WHALE_ALERTS and abs(w["net"]) >= limit
                        and time.time() - _alerted.get(s, 0) > 3600):
                    _alerted[s] = time.time()
                    side = "🟢 SOTIB OLMOQDA" if w["net"] > 0 else "🔴 SOTMOQDA"
                    await broadcast(
                        f"🐋 *Kitlar faol: {s}* — {side}\n"
                        f"15 daqiqada {w['n']} ta yirik savdo: xarid {_money(w['buy'])}, sotuv {_money(w['sell'])}\n"
                        f"Sof oqim: {'+' if w['net'] > 0 else '-'}{_money(w['net'])}\n\n"
                        "Bu yo'nalish kafolat emas, lekin yirik o'yinchilar harakatini ko'rsatadi.")
            except Exception as e:  # noqa: BLE001
                log.info("whales %s: %s", s, e)
        await asyncio.sleep(60)
