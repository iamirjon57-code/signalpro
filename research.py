"""Global manbalar: dunyo yangiliklari, DeFi/DEX bozori, trend tangalar, kunlik hisobot.

Manbalar (bepul, kalitsiz):
  * Yangiliklar (RSS): Cointelegraph, CoinDesk, Decrypt, The Block, CNBC, MarketWatch, Google News
  * DefiLlama — DEX birjalar savdo hajmi, blokcheyn tarmoqlari TVL
  * CoinGecko — trenddagi tangalar
  * GeckoTerminal — trenddagi DEX hovuzlari
O'zbekchaga tarjima va xulosa — AI (Claude) orqali, kalit bo'lsa.
"""
from __future__ import annotations

import asyncio
import email.utils
import html
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

import config
import store

log = logging.getLogger("research")

_UA = {"User-Agent": "Mozilla/5.0 (SignalPro news reader)", "Accept": "*/*"}
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.RLock()

FEEDS = {
    "crypto": [
        ("Cointelegraph", "https://cointelegraph.com/rss"),
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Decrypt", "https://decrypt.co/feed"),
        ("The Block", "https://www.theblock.co/rss.xml"),
    ],
    "markets": [
        ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
        ("CNBC Investing", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069"),
        ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ],
}
GOOGLE = {
    "defi": "DeFi OR DEX OR Uniswap OR Raydium OR memecoin",
    "gold": "gold price OR forex dollar OR Fed rate",
    "crypto": "bitcoin OR ethereum OR crypto market",
    "markets": "stock market OR S&P 500 OR Nasdaq",
}
TOPIC_NAMES = {"crypto": "Kripto", "markets": "Aksiyalar va moliya", "defi": "DeFi va DEX",
               "gold": "Oltin, forex, foiz stavkalari"}


def _cached(key: str, ttl: int, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception as e:  # noqa: BLE001
        log.info("%s: %s", key, e)
        return hit[1] if hit else None
    _cache[key] = (time.time(), val)
    return val


# ---------------- Yangiliklar (RSS) ----------------

def _text(el, tag: str) -> str:
    for child in el:
        if child.tag.split("}")[-1] == tag:
            return (child.text or "").strip() or child.attrib.get("href", "")
    return ""


def _strip(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _ts(s: str) -> float:
    if not s:
        return 0.0
    try:
        dt = email.utils.parsedate_to_datetime(s)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return 0.0


def parse_feed(content: bytes, source: str) -> list[dict]:
    root = ET.fromstring(content)
    out = []
    for it in root.iter():
        if it.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title = _strip(_text(it, "title"))
        if not title:
            continue
        src = source
        if source == "Google News":   # "Sarlavha - Manba"
            m = re.match(r"^(.*) - ([^-]{2,40})$", title)
            if m:
                title, src = m.group(1).strip(), m.group(2).strip()
        out.append({
            "title": title[:220],
            "link": _text(it, "link"),
            "ts": _ts(_text(it, "pubDate") or _text(it, "published") or _text(it, "updated")),
            "source": src,
            "desc": _strip(_text(it, "description") or _text(it, "summary"))[:300],
        })
    return out


def _feed(source: str, url: str) -> list[dict]:
    def get():
        r = requests.get(url, headers=_UA, timeout=15)
        r.raise_for_status()
        return parse_feed(r.content, source)
    return _cached(f"feed:{url}", 900, get) or []


def _google(query: str) -> list[dict]:
    url = (f"https://news.google.com/rss/search?q={requests.utils.quote(query + ' when:2d')}"
           "&hl=en-US&gl=US&ceid=US:en")
    return _feed("Google News", url)


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower())[:60]


def headlines(topic: str = "crypto", limit: int = 15, max_age_h: float = 36) -> list[dict]:
    """Bir nechta manbadan birlashtirilgan, takrorlarsiz, eng yangi sarlavhalar."""
    items: list[dict] = []
    for src, url in FEEDS.get(topic, []):
        items += _feed(src, url)
    if topic in GOOGLE:
        items += _google(GOOGLE[topic])
    cutoff = time.time() - max_age_h * 3600
    seen, out = set(), []
    for i in sorted(items, key=lambda x: -x["ts"]):
        k = _norm(i["title"])
        if k in seen or (i["ts"] and i["ts"] < cutoff):
            continue
        seen.add(k)
        out.append(i)
    return out[:limit]


def about(keyword: str, limit: int = 6) -> list[dict]:
    """Aktiv bo'yicha yangiliklar: global lentalardan + Google News."""
    kws = [k.lower() for k in keyword.split("|") if k]
    pool = headlines("crypto", 60) + headlines("markets", 60)
    hits = [i for i in pool if any(k in i["title"].lower() for k in kws)]
    hits += _google(" OR ".join(keyword.split("|")))
    seen, out = set(), []
    for i in sorted(hits, key=lambda x: -x["ts"]):
        k = _norm(i["title"])
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out[:limit]


ALIASES = {"BTC": "bitcoin|BTC", "ETH": "ethereum|ether|ETH", "SOL": "solana|SOL", "BNB": "BNB|binance",
           "XRP": "XRP|ripple", "DOGE": "dogecoin|DOGE", "ADA": "cardano|ADA", "TON": "toncoin|TON",
           "AAPL": "Apple|AAPL", "MSFT": "Microsoft|MSFT", "NVDA": "Nvidia|NVDA", "TSLA": "Tesla|TSLA",
           "GOOGL": "Google|Alphabet", "AMZN": "Amazon|AMZN", "META": "Meta Platforms|META",
           "XAU": "gold", "EUR": "euro|EUR/USD", "GBP": "pound|GBP"}


def symbol_news(symbol: str, limit: int = 6) -> list[dict]:
    base = symbol.split("/")[0].upper()
    return about(ALIASES.get(base, base), limit)


def age(ts: float) -> str:
    if not ts:
        return ""
    m = (time.time() - ts) / 60
    return f"{m:.0f} daq" if m < 60 else (f"{m / 60:.0f} soat" if m < 48 * 60 else f"{m / 1440:.0f} kun")


# ---------------- DeFi / DEX / trend ----------------

def _json(url: str, ttl: int):
    def get():
        r = requests.get(url, headers={**_UA, "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        return r.json()
    return _cached(f"json:{url}", ttl, get)


def dex_volumes(n: int = 8) -> dict | None:
    j = _json("https://api.llama.fi/overview/dexs?excludeTotalDataChart=true"
              "&excludeTotalDataChartBreakdown=true", 1800)
    if not j:
        return None
    prots = sorted((p for p in j.get("protocols") or [] if p.get("total24h")),
                   key=lambda p: -float(p.get("total24h") or 0))[:n]
    return {"total24h": float(j.get("total24h") or 0), "change_1d": j.get("change_1d"),
            "change_7d": j.get("change_7d"),
            "top": [{"name": p.get("displayName") or p.get("name"), "vol": float(p.get("total24h") or 0),
                     "chg": p.get("change_1d"), "chains": (p.get("chains") or [])[:3]} for p in prots]}


def chain_tvl(n: int = 8) -> list[dict]:
    j = _json("https://api.llama.fi/v2/chains", 3600) or []
    rows = sorted((c for c in j if c.get("tvl")), key=lambda c: -float(c["tvl"]))[:n]
    return [{"name": c.get("name"), "tvl": float(c["tvl"])} for c in rows]


def trending_coins(n: int = 7) -> list[dict]:
    j = _json("https://api.coingecko.com/api/v3/search/trending", 900) or {}
    out = []
    for c in (j.get("coins") or [])[:n]:
        it = c.get("item") or {}
        d = it.get("data") or {}
        chg = (d.get("price_change_percentage_24h") or {}).get("usd")
        out.append({"name": it.get("name"), "symbol": (it.get("symbol") or "").upper(),
                    "rank": it.get("market_cap_rank"), "price": d.get("price"),
                    "chg24": float(chg) if chg is not None else None, "mcap": d.get("market_cap")})
    return out


def trending_pools(n: int = 8) -> list[dict]:
    j = _json("https://api.geckoterminal.com/api/v2/networks/trending_pools?page=1", 600) or {}
    out = []
    for p in (j.get("data") or [])[:n]:
        a = p.get("attributes") or {}
        chg = a.get("price_change_percentage") or {}
        vol = a.get("volume_usd") or {}
        out.append({"name": a.get("name"), "network": (p.get("id") or "").split("_")[0],
                    "price": a.get("base_token_price_usd"),
                    "chg1": _num(chg.get("h1")), "chg24": _num(chg.get("h24")),
                    "vol24": _num(vol.get("h24")), "liq": _num(a.get("reserve_in_usd"))})
    return out


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _money(x) -> str:
    x = float(x or 0)
    if x >= 1e9:
        return f"${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:.0f}"


def _pct(x) -> str:
    return "—" if x is None else f"{float(x):+.1f}%"


def global_text() -> str:
    """🌍 Global bozor: DEX hajmlari, tarmoqlar, trend tangalar va hovuzlar (AI'siz, raqamlar)."""
    lines = ["*🌍 Global kripto va DEX bozori*"]
    v = dex_volumes()
    if v:
        lines.append(f"\n*DEX savdo hajmi (24s):* {_money(v['total24h'])} · kun {_pct(v['change_1d'])} · "
                     f"hafta {_pct(v['change_7d'])}")
        lines += [f"{i}. {_clean(p['name'])} — {_money(p['vol'])} ({_pct(p['chg'])})"
                  for i, p in enumerate(v["top"], 1)]
    ch = chain_tvl(6)
    if ch:
        lines.append("\n*Eng katta tarmoqlar (TVL):* " + " · ".join(f"{_clean(c['name'])} {_money(c['tvl'])}" for c in ch))
    tc = trending_coins()
    if tc:
        lines.append("\n*🔥 CoinGecko trend:*")
        lines += [f"• {_clean(c['symbol'])} ({_clean(c['name'])}) #{c['rank'] or '?'} · 24s {_pct(c['chg24'])}" for c in tc]
    tp = trending_pools()
    if tp:
        lines.append("\n*🦎 DEX trend hovuzlari (GeckoTerminal):*")
        lines += [f"• {_clean(p['name'])} [{p['network']}] · 1s {_pct(p['chg1'])} · 24s {_pct(p['chg24'])} · "
                  f"hajm {_money(p['vol24'])} · likv. {_money(p['liq'])}" for p in tp]
    if len(lines) == 1:
        lines.append("Ma'lumot olib bo'lmadi, birozdan keyin urinib ko'ring.")
    lines.append("\nℹ️ Trenddagi tanga — xavfsiz degani emas. Har bir tangani 🦎 DEX filtridan o'tkazing.")
    return "\n".join(lines)


def _clean(s) -> str:
    return re.sub(r"[`*_\[\]]", "", str(s or "?"))[:40]


def global_context() -> str:
    """AI uchun qisqa global kontekst."""
    parts = []
    v = dex_volumes(5)
    if v:
        parts.append(f"DEX hajmi 24s {_money(v['total24h'])} ({_pct(v['change_1d'])}); top: " +
                     ", ".join(f"{p['name']} {_money(p['vol'])} {_pct(p['chg'])}" for p in v["top"]))
    tc = trending_coins(7)
    if tc:
        parts.append("CoinGecko trend: " + ", ".join(f"{c['symbol']} #{c['rank']} {_pct(c['chg24'])}" for c in tc))
    tp = trending_pools(6)
    if tp:
        parts.append("DEX trend hovuzlar: " + ", ".join(
            f"{p['name']} [{p['network']}] 24s {_pct(p['chg24'])} hajm {_money(p['vol24'])}" for p in tp))
    return "\n".join(parts) or "global ma'lumot yo'q"


def plain_news_text(topic: str, items: list[dict]) -> str:
    lines = [f"📰 {TOPIC_NAMES.get(topic, topic)} — so'nggi yangiliklar (inglizcha, AI ulanmagan)\n"]
    lines += [f"• {i['title']} — {i['source']}, {age(i['ts'])}\n{i['link']}" for i in items[:8]]
    return "\n".join(lines)


# ---------------- Kunlik hisobot va muhim yangiliklar ----------------

def _db():
    c = store.conn()
    c.execute("CREATE TABLE IF NOT EXISTS research_kv (k TEXT PRIMARY KEY, v TEXT)")
    return c


def kv_get(k: str) -> str | None:
    r = _db().execute("SELECT v FROM research_kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def kv_set(k: str, v: str):
    with _lock:
        c = _db()
        c.execute("INSERT OR REPLACE INTO research_kv VALUES (?,?)", (k, v))
        c.commit()


def _local_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=config.TZ_OFFSET_H)


def new_items(topics=("crypto", "markets", "defi")) -> list[dict]:
    """Oxirgi tekshiruvdan keyin chiqqan yangiliklar (birinchi ishga tushishda — hech narsa)."""
    last = float(kv_get("news_last_ts") or 0)
    items = []
    for t in topics:
        items += headlines(t, 40, max_age_h=6)
    seen, fresh = set(), []
    for i in sorted(items, key=lambda x: -x["ts"]):
        k = _norm(i["title"])
        if k not in seen and i["ts"] > last:
            seen.add(k)
            fresh.append(i)
    newest = max([i["ts"] for i in items] + [last])
    kv_set("news_last_ts", str(newest))
    return fresh[:25] if last else []


async def loop(broadcast):
    """Har kuni ertalab hisobot + muhim yangiliklar ogohlantirishi (AI kerak)."""
    import ai
    await asyncio.sleep(60)
    next_news = 0.0
    while True:
        try:
            if ai.enabled():
                now = _local_now()
                today = now.date().isoformat()
                if (config.DAILY_REPORT and now.hour >= config.DAILY_REPORT_HOUR
                        and kv_get("daily_sent") != today):
                    kv_set("daily_sent", today)
                    text = await asyncio.to_thread(ai.daily_report)
                    await broadcast(f"☀️ Kunlik hisobot — {today}\n\n{text}")
                if config.NEWS_ALERTS and time.time() >= next_news:
                    next_news = time.time() + config.NEWS_ALERT_INTERVAL_MIN * 60
                    fresh = await asyncio.to_thread(new_items)
                    if fresh:
                        for msg in await asyncio.to_thread(ai.important_news, fresh):
                            await broadcast(msg)
        except Exception as e:  # noqa: BLE001
            log.error("research loop: %s", e)
        await asyncio.sleep(120)
