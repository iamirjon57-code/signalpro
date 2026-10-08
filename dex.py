"""DexScreener: tanga tekshirish, firibgarlik filtri, trend xabarlari va qog'oz avto-savdo.

MUHIM:
  * Filtr xavfni kamaytiradi, lekin KAFOLAT EMAS — filtrdan o'tgan tanga ham nolga tushishi mumkin.
  * Avto-savdo bu yerda faqat QOG'OZ rejimida: narxlar haqiqiy, lekin hech qanday pul
    ishlatilmaydi va hech qanday hamyon kaliti saqlanmaydi.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from datetime import datetime, timezone

import requests

import config
import store

log = logging.getLogger("dex")

API = "https://api.dexscreener.com"
_UA = {"User-Agent": "SignalPro/1.0", "Accept": "application/json"}
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.RLock()

EVM_CHAIN_IDS = {"ethereum": 1, "bsc": 56, "base": 8453, "arbitrum": 42161,
                 "polygon": 137, "optimism": 10, "avalanche": 43114}
MAJOR_QUOTES = {"SOL", "WSOL", "ETH", "WETH", "USDC", "USDT", "BNB", "WBNB", "DAI", "USDE"}
DEAD = {"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"}

EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

last_scan: list[dict] = []      # oxirgi trend skaneri natijasi (sayt va bot uchun)
last_scan_at: str = ""


def is_address(text: str) -> bool:
    t = (text or "").strip()
    return bool(EVM_RE.match(t) or SOL_RE.match(t))


# ---------------- HTTP ----------------

def _get(url: str, ttl: int = 45):
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    r = requests.get(url, headers=_UA, timeout=15)
    r.raise_for_status()
    j = r.json()
    _cache[url] = (time.time(), j)
    if len(_cache) > 400:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:150]:
            _cache.pop(k, None)
    return j


def _pairs(j) -> list[dict]:
    if isinstance(j, list):
        return [p for p in j if isinstance(p, dict)]
    if isinstance(j, dict):
        return [p for p in (j.get("pairs") or []) if isinstance(p, dict)]
    return []


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ---------------- DexScreener ----------------

def search(query: str) -> list[dict]:
    return _pairs(_get(f"{API}/latest/dex/search?q={requests.utils.quote(query)}"))


def token_pairs(addresses: list[str], ttl: int = 45) -> list[dict]:
    out: list[dict] = []
    addrs = list(dict.fromkeys(a for a in addresses if a))
    for i in range(0, len(addrs), 30):
        out += _pairs(_get(f"{API}/latest/dex/tokens/{','.join(addrs[i:i + 30])}", ttl))
    return out


def best_pair(pairs: list[dict], address: str | None = None, chain: str | None = None) -> dict | None:
    """Eng katta likvidlikka ega juftlik (berilgan tanga bazaviy token bo'lgan)."""
    cands = pairs
    if address:
        a = address.lower()
        cands = [p for p in cands if (p.get("baseToken") or {}).get("address", "").lower() == a]
    if chain:
        cands = [p for p in cands if p.get("chainId") == chain] or cands
    if not cands:
        return None
    return max(cands, key=lambda p: _f((p.get("liquidity") or {}).get("usd")))


def trending_addresses() -> list[tuple[str, str]]:
    """DexScreener'da reklama qilingan / yangi profil ochgan tangalar (chain, address)."""
    out: list[tuple[str, str]] = []
    for path in ("/token-boosts/top/v1", "/token-boosts/latest/v1", "/token-profiles/latest/v1"):
        try:
            for it in _get(API + path, ttl=120) or []:
                c, a = it.get("chainId"), it.get("tokenAddress")
                if c and a and c in config.DEX_CHAINS:
                    out.append((c, a))
        except Exception as e:  # noqa: BLE001
            log.warning("trend manbasi %s: %s", path, e)
    return list(dict.fromkeys(out))


def _clean(s: str) -> str:
    """Telegram Markdown'ni buzadigan belgilarni olib tashlaydi."""
    return re.sub(r"[`*_\[\]()~>#|]", "", str(s or "")).strip()[:40] or "?"


def summarize(p: dict) -> dict:
    """DexScreener juftligidan kerakli maydonlar."""
    base, quote = p.get("baseToken") or {}, p.get("quoteToken") or {}
    tx, vol, chg = p.get("txns") or {}, p.get("volume") or {}, p.get("priceChange") or {}
    info = p.get("info") or {}
    created = p.get("pairCreatedAt")
    age_h = (time.time() - _f(created) / 1000) / 3600 if created else None
    h24, h1 = tx.get("h24") or {}, tx.get("h1") or {}
    return {
        "chain": p.get("chainId", ""), "dex": p.get("dexId", ""),
        "address": base.get("address", ""), "pair": p.get("pairAddress", ""),
        "symbol": _clean(base.get("symbol")), "name": _clean(base.get("name")),
        "quote": (quote.get("symbol") or "").upper(),
        "url": p.get("url", ""),
        "price": _f(p.get("priceUsd")),
        "liq": _f((p.get("liquidity") or {}).get("usd")),
        "vol24": _f(vol.get("h24")), "vol1": _f(vol.get("h1")),
        "chg5": _f(chg.get("m5")), "chg1": _f(chg.get("h1")),
        "chg6": _f(chg.get("h6")), "chg24": _f(chg.get("h24")),
        "buys24": int(_f(h24.get("buys"))), "sells24": int(_f(h24.get("sells"))),
        "buys1": int(_f(h1.get("buys"))), "sells1": int(_f(h1.get("sells"))),
        "fdv": _f(p.get("fdv")), "mcap": _f(p.get("marketCap")),
        "age_h": age_h,
        "socials": len(info.get("socials") or []) + len(info.get("websites") or []),
    }


# ---------------- Firibgarlik filtri ----------------

THIN_LIQ = "Likvidlik qiymatga nisbatan juda kam"


def _only_thin(bad: list[str]) -> bool:
    """Yagona xavf — likvidlik/qiymat nisbati (yirik, birjalarda sotiladigan tangalarda oddiy hol)."""
    return bool(bad) and all(b.startswith(THIN_LIQ) for b in bad)


def market_check(t: dict) -> tuple[list[str], list[str]]:
    """Bozor ko'rsatkichlari bo'yicha (xavfli, ogohlantirish) ro'yxatlari."""
    bad, warn = [], []
    if t["price"] <= 0:
        bad.append("Narx ma'lumoti yo'q")
    if t["liq"] < config.DEX_MIN_LIQUIDITY:
        bad.append(f"Likvidlik past: ${t['liq']:,.0f} (kamida ${config.DEX_MIN_LIQUIDITY:,.0f} kerak)")
    if t["vol24"] < config.DEX_MIN_VOLUME:
        bad.append(f"Savdo hajmi past: ${t['vol24']:,.0f}/24s")
    if t["age_h"] is None:
        bad.append("Juftlik yoshi noma'lum")
    elif t["age_h"] < config.DEX_MIN_AGE_H:
        bad.append(f"Juda yangi: {t['age_h']:.1f} soat (kamida {config.DEX_MIN_AGE_H:g} soat kerak)")
    tx = t["buys24"] + t["sells24"]
    if tx < 300:
        bad.append(f"Savdolar kam: {tx} ta/24s")
    if tx and t["sells24"] / tx < 0.2:
        bad.append(f"Deyarli hech kim sotmayapti ({t['sells24']} sotuv / {t['buys24']} xarid) — honeypot belgisi")
    if t["chg24"] <= -50:
        bad.append(f"Narx 24 soatda {t['chg24']:.0f}% tushgan")
    if t["fdv"] > 0 and t["liq"] < 1_000_000:
        ratio = t["liq"] / t["fdv"]
        if ratio < 0.01:
            bad.append(f"{THIN_LIQ} ({ratio * 100:.1f}%)")
        elif ratio < 0.03:
            warn.append(f"Likvidlik qiymatga nisbatan kam ({ratio * 100:.1f}%)")
    if t["chg24"] >= 500:
        bad.append(f"24 soatda +{t['chg24']:.0f}% — pump, tushish xavfi juda yuqori")
    elif t["chg24"] >= 100:
        warn.append(f"24 soatda +{t['chg24']:.0f}% — keskin o'sgan, qaytishi mumkin")
    if t["liq"] > 0:
        turnover = t["vol24"] / t["liq"]
        if turnover > 40:
            bad.append(f"Hajm likvidlikdan {turnover:.0f} baravar ko'p — sun'iy savdo belgisi")
        elif turnover > 10:
            warn.append(f"Hajm likvidlikdan {turnover:.0f} baravar ko'p — juda qizigan")
    if t["age_h"] is not None and config.DEX_MIN_AGE_H <= t["age_h"] < 72:
        warn.append(f"3 kundan yosh ({t['age_h']:.0f} soat) — hali sinalmagan")
    if config.DEX_MIN_LIQUIDITY <= t["liq"] < 100000:
        warn.append(f"Likvidlik o'rtacha: {_money(t['liq'])} — katta sotuvda narx keskin tushadi")
    if not t["socials"]:
        warn.append("Sayti va ijtimoiy tarmoqlari yo'q")
    if t["quote"] and t["quote"] not in MAJOR_QUOTES:
        warn.append(f"Noodatiy juftlik valyutasi: {t['quote']}")
    return bad, warn


def _evm_security(chain: str, address: str) -> dict:
    cid = EVM_CHAIN_IDS[chain]
    j = _get(f"https://api.gopluslabs.io/api/v1/token_security/{cid}?contract_addresses={address}", ttl=1800)
    res = (j or {}).get("result") or {}
    d = res.get(address.lower()) or next(iter(res.values()), None)
    if not d:
        return {"ok": None, "bad": [], "warn": [], "source": "GoPlus"}
    on = lambda k: str(d.get(k, "")) == "1"  # noqa: E731
    bad, warn = [], []
    if on("is_honeypot"):
        bad.append("HONEYPOT — sotib bo'lmaydi")
    if on("cannot_buy"):
        bad.append("Sotib olib bo'lmaydi")
    if on("cannot_sell_all"):
        bad.append("Hammasini sotib bo'lmaydi")
    if str(d.get("is_open_source", "1")) == "0":
        bad.append("Kontrakt kodi yopiq")
    for k, label in (("buy_tax", "Xarid solig'i"), ("sell_tax", "Sotuv solig'i")):
        tax = _f(d.get(k), -1)
        if tax > 0.10:
            bad.append(f"{label} juda yuqori: {tax * 100:.0f}%")
        elif tax > 0.03:
            warn.append(f"{label}: {tax * 100:.0f}%")
    for k, label in (("hidden_owner", "Yashirin egasi bor"),
                     ("can_take_back_ownership", "Egalikni qaytarib olishi mumkin"),
                     ("selfdestruct", "Kontrakt o'zini yo'q qila oladi"),
                     ("owner_change_balance", "Egasi balanslarni o'zgartira oladi"),
                     ("honeypot_with_same_creator", "Yaratuvchisi avval honeypot chiqargan")):
        if on(k):
            bad.append(label)
    for k, label in (("is_mintable", "Yangi tanga chiqarish (mint) mumkin"),
                     ("is_proxy", "Proxy kontrakt — kodi o'zgartirilishi mumkin"),
                     ("transfer_pausable", "O'tkazmalarni to'xtatib qo'yish mumkin"),
                     ("is_blacklisted", "Qora ro'yxat funksiyasi bor"),
                     ("slippage_modifiable", "Soliqni o'zgartirish mumkin"),
                     ("trading_cooldown", "Savdo orasida kutish bor")):
        if on(k):
            warn.append(label)
    top = sum(_f(h.get("percent")) for h in (d.get("holders") or [])[:10]
              if str(h.get("is_contract", "0")) != "1" and str(h.get("is_locked", "0")) != "1"
              and (h.get("address") or "").lower() not in DEAD)
    if top > 0.5:
        warn.append(f"Eng yirik 10 hamyonda {top * 100:.0f}% tanga")
    holders = int(_f(d.get("holder_count")))
    if 0 < holders < 200:
        warn.append(f"Egalari kam: {holders} ta")
    hp = _honeypot(chain, address)
    bad, warn = bad + hp["bad"], warn + hp["warn"]
    ok = (not bad) if hp["sim"] else None     # sotuv simulyatsiyasi o'tmasa — "tekshirib bo'lmadi"
    return {"ok": ok, "bad": bad, "warn": warn, "source": "GoPlus + Honeypot.is"}


HONEYPOT_CHAINS = {"ethereum": 1, "bsc": 56, "base": 8453}


def _honeypot(chain: str, address: str) -> dict:
    """Honeypot.is: tangani amalda sotib olib-sotib ko'radi (simulyatsiya)."""
    out = {"sim": True, "bad": [], "warn": []}
    cid = HONEYPOT_CHAINS.get(chain)
    if not cid:
        return out            # bu tarmoqni Honeypot.is qo'llamaydi — faqat GoPlus natijasi
    try:
        j = _get(f"https://api.honeypot.is/v2/IsHoneypot?address={address}&chainID={cid}", ttl=1800) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("Honeypot.is (%s): %s", address, e)
        return {"sim": False, "bad": [], "warn": ["Sotuv simulyatsiyasini o'tkazib bo'lmadi (Honeypot.is javob bermadi)"]}
    if (j.get("honeypotResult") or {}).get("isHoneypot"):
        reason = _clean((j.get("honeypotResult") or {}).get("honeypotReason") or "")
        out["bad"].append("Honeypot.is: sotib BO'LMAYDI" + (f" ({reason})" if reason != "?" else ""))
    if not j.get("simulationSuccess"):
        out["sim"] = False
        out["warn"].append("Sotuv simulyatsiyasi muvaffaqiyatsiz — sotib bo'lishi tasdiqlanmadi")
        return out
    sim = j.get("simulationResult") or {}
    for k, label in (("buyTax", "Haqiqiy xarid solig'i"), ("sellTax", "Haqiqiy sotuv solig'i"),
                     ("transferTax", "O'tkazma solig'i")):
        tax = _f(sim.get(k), 0)          # foizda: 5 = 5%
        if tax > 10:
            out["bad"].append(f"{label}: {tax:.0f}%")
        elif tax > 3:
            out["warn"].append(f"{label}: {tax:.0f}%")
    ha = j.get("holderAnalysis") or {}
    holders, failed = _f(ha.get("holders")), _f(ha.get("failed"))
    if holders >= 20 and failed / holders > 0.10:
        out["bad"].append(f"Egalarning {failed / holders * 100:.0f}% i sota olmaydi")
    if _f(ha.get("siphoned")) > 0:
        out["bad"].append("Ba'zi hamyonlardan tanga tortib olingan")
    if _f(ha.get("highestTax")) > 25:
        out["warn"].append(f"Ayrim hamyonlarga soliq {_f(ha.get('highestTax')):.0f}% gacha")
    risk = str((j.get("summary") or {}).get("risk", "")).lower()
    if risk in ("honeypot", "very_high", "high"):
        out["bad"].append(f"Honeypot.is xavf darajasi: {risk}")
    elif risk == "medium":
        out["warn"].append("Honeypot.is xavf darajasi: o'rtacha")
    return out


def _sol_security(address: str) -> dict:
    """RugCheck to'liq hisoboti: xavflar, yaratuvchi ulushi, insayderlar, eng yirik egalar."""
    j = _get(f"https://api.rugcheck.xyz/v1/tokens/{address}/report", ttl=1800) or {}
    if "risks" not in j and "score" not in j:
        return {"ok": None, "bad": [], "warn": [], "source": "RugCheck"}
    bad, warn = [], []
    if j.get("rugged"):
        bad.append("RugCheck: bu tanga allaqachon RUG bo'lgan")
    tok = j.get("token") or {}
    if j.get("mintAuthority") or tok.get("mintAuthority"):
        bad.append("Egasi yangi tanga chiqara oladi (mint yopilmagan)")
    if j.get("freezeAuthority") or tok.get("freezeAuthority"):
        bad.append("Egasi hamyoningizdagi tangani muzlatib qo'ya oladi")
    for r in j.get("risks") or []:
        name = _clean(r.get("name"))
        (bad if str(r.get("level", "")).lower() == "danger" else warn).append(f"RugCheck: {name}")
    norm = _f(j.get("score_normalised"), -1)
    if norm >= 40:
        bad.append(f"RugCheck xavf bahosi yuqori: {norm:.0f}/100")
    supply = _f(tok.get("supply"))
    if supply > 0:
        cpct = _f(j.get("creatorBalance")) / supply * 100
        if cpct > 20:
            bad.append(f"Yaratuvchida {cpct:.0f}% tanga — xohlagan payt sotib yuborishi mumkin")
        elif cpct > 5:
            warn.append(f"Yaratuvchida {cpct:.0f}% tanga")
    holders = [h for h in (j.get("topHolders") or []) if isinstance(h, dict)]
    insiders = sum(_f(h.get("pct")) for h in holders if h.get("insider"))
    if insiders > 15:
        bad.append(f"Insayder hamyonlarda {insiders:.0f}% tanga")
    elif insiders > 3:
        warn.append(f"Insayder hamyonlarda {insiders:.0f}% tanga")
    if _f(j.get("graphInsidersDetected")) >= 20:
        warn.append(f"Bir-biriga bog'langan {_f(j.get('graphInsidersDetected')):.0f} ta hamyon topildi")
    # Eng yirik egalar (birinchi o'rindagi ko'pincha likvidlik hovuzi bo'ladi — uni hisobga olmaymiz)
    pcts = sorted((_f(h.get("pct")) for h in holders), reverse=True)
    if len(pcts) > 1 and pcts[1] > 15:
        warn.append(f"Bitta hamyonda {pcts[1]:.0f}% tanga")
    if sum(pcts[1:11]) > 50:
        warn.append(f"Eng yirik 10 hamyonda {sum(pcts[1:11]):.0f}% tanga")
    total = _f(j.get("totalHolders"))
    if 0 < total < 300:
        warn.append(f"Egalari kam: {total:.0f} ta")
    lps = [_f(((m or {}).get("lp") or {}).get("lpLockedPct"), -1) for m in (j.get("markets") or [])]
    lps = [x for x in lps if x >= 0]
    if lps and max(lps) < 50:
        warn.append(f"Likvidlikning ko'pi qulflanmagan (eng yaxshisi {max(lps):.0f}%)")
    return {"ok": not bad, "bad": bad, "warn": warn[:7], "source": "RugCheck"}


def security(chain: str, address: str) -> dict:
    """Kontrakt xavfsizligi. ok=None — tekshirib bo'lmadi (avto-savdo bunday tangani olmaydi)."""
    try:
        if chain in EVM_CHAIN_IDS:
            return _evm_security(chain, address)
        if chain == "solana":
            return _sol_security(address)
    except Exception as e:  # noqa: BLE001
        log.warning("xavfsizlik tekshiruvi (%s %s): %s", chain, address, e)
    return {"ok": None, "bad": [], "warn": [], "source": ""}


GECKO_NETWORKS = {"solana": "solana", "ethereum": "eth", "bsc": "bsc", "base": "base",
                  "arbitrum": "arbitrum", "polygon": "polygon_pos", "optimism": "optimism",
                  "avalanche": "avax"}


def cross_check(t: dict) -> dict:
    """GeckoTerminal bilan solishtiradi: narx/likvidlik mos keladimi, CoinGecko ro'yxatida bormi."""
    out = {"warn": [], "coingecko": "", "found": False}
    net = GECKO_NETWORKS.get(t["chain"])
    if not net or not t["address"]:
        return out
    try:
        j = _get(f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{t['address']}", ttl=600) or {}
    except Exception as e:  # noqa: BLE001
        log.info("GeckoTerminal (%s): %s", t["address"], e)
        return out
    a = ((j.get("data") or {}).get("attributes")) or {}
    if not a:
        return out
    out["found"] = True
    out["coingecko"] = str(a.get("coingecko_coin_id") or "")
    gp, greserve = _f(a.get("price_usd")), _f(a.get("total_reserve_in_usd"))
    if gp > 0 and t["price"] > 0 and abs(gp - t["price"]) / t["price"] > 0.15:
        out["warn"].append(f"Narx manbalarda farq qiladi: DexScreener ${t['price']:.6g}, GeckoTerminal ${gp:.6g}")
    if greserve > 0 and t["liq"] > 0 and greserve < t["liq"] * 0.5:
        out["warn"].append(f"Likvidlik ikkinchi manbada ancha kam: {_money(greserve)}")
    return out


def evaluate(pair: dict, deep: bool = True) -> dict:
    """Tangani to'liq baholaydi: bozor filtri + (deep bo'lsa) kontrakt tekshiruvi."""
    t = summarize(pair)
    bad, warn = market_check(t)
    sec = {"ok": None, "bad": [], "warn": [], "source": ""}
    if deep and t["address"]:
        sec = security(t["chain"], t["address"])
        bad, warn = bad + sec["bad"], warn + sec["warn"]
    if deep and sec["ok"] is None:
        warn = warn + ["Kontraktni tekshirib bo'lmadi"]
    cg = ""
    if deep and (not bad or _only_thin(bad)):   # boshqa xavfli tangalar uchun qo'shimcha so'rov yo'q
        x = cross_check(t)
        warn, cg = warn + x["warn"], x["coingecko"]
        if cg and _only_thin(bad):
            # CoinGecko ro'yxatidagi yirik tanga: asosiy savdosi yirik birjalarda, DEX hovuzi kichik bo'lishi tabiiy
            bad, warn = [], warn + ["DEX'dagi likvidligi qiymatiga nisbatan kam (asosiy savdosi yirik birjalarda)"]
    score = max(0, 100 - 30 * len(bad) - 10 * len(warn))
    if cg:
        score = min(100, score + 5)   # CoinGecko ro'yxatida bo'lishi — ijobiy belgi
    if bad:
        score = min(score, 30)   # bitta jiddiy xavf bo'lsa ham baho past ko'rinsin
    verdict = "bad" if bad else ("ok" if (sec["ok"] is True and score >= config.DEX_MIN_SCORE) else "warn")
    t.update(bad=bad, warn=warn, score=score, verdict=verdict,
             sec_ok=sec["ok"], sec_source=sec["source"], coingecko=cg)
    return t


def lookup(query: str) -> list[dict]:
    """Nom yoki manzil bo'yicha qidirish — har bir tanga uchun eng yaxshi juftlik."""
    q = query.strip()
    pairs = token_pairs([q]) if is_address(q) else search(q)
    best: dict[tuple[str, str], dict] = {}
    for p in pairs:
        base = p.get("baseToken") or {}
        k = (p.get("chainId", ""), base.get("address", ""))
        if not k[1]:
            continue
        cur = best.get(k)
        if cur is None or _f((p.get("liquidity") or {}).get("usd")) > _f((cur.get("liquidity") or {}).get("usd")):
            best[k] = p
    return sorted(best.values(), key=lambda p: -_f((p.get("liquidity") or {}).get("usd")))


def check_token(chain: str, address: str) -> dict | None:
    p = best_pair(token_pairs([address]), address, chain)
    return evaluate(p) if p else None


# ---------------- Matn ----------------

def _money(x: float) -> str:
    if x >= 1e9:
        return f"${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"${x / 1e6:.2f}M"
    if x >= 1e3:
        return f"${x / 1e3:.1f}K"
    return f"${x:.0f}"


def _age(h) -> str:
    if h is None:
        return "noma'lum"
    return f"{h:.0f} soat" if h < 48 else f"{h / 24:.0f} kun"


VERDICT = {"ok": "✅ Filtrdan o'tdi", "warn": "⚠️ Ehtiyot bo'ling", "bad": "⛔ XAVFLI"}


def report(t: dict, title: str = "") -> str:
    lines = []
    if title:
        lines.append(title)
    lines += [
        f"*{t['symbol']}* — {t['name']} ({t['chain']}, {t['dex']})",
        f"Narx: `${t['price']:.10g}`",
        f"O'zgarish: 1s {t['chg1']:+.1f}% · 6s {t['chg6']:+.1f}% · 24s {t['chg24']:+.1f}%",
        f"Likvidlik: {_money(t['liq'])} · Hajm 24s: {_money(t['vol24'])}",
        f"Qiymat (FDV): {_money(t['fdv'])} · Yoshi: {_age(t['age_h'])}",
        f"Savdolar 24s: {t['buys24']} xarid / {t['sells24']} sotuv",
        "",
        f"*{VERDICT[t['verdict']]}* — xavfsizlik bahosi {t['score']}/100",
    ]
    lines += [f"⛔ {b}" for b in t["bad"][:6]]
    lines += [f"⚠️ {w}" for w in t["warn"][:6]]
    if t.get("coingecko"):
        lines.append("✅ CoinGecko ro'yxatida bor")
    if t.get("sec_source"):
        lines.append(f"Tekshirildi: {t['sec_source']}")
    if t["verdict"] == "ok":
        lines.append("Filtr kafolat emas — tanga baribir qadrsizlanishi mumkin.")
    lines.append(f"`{t['address']}`")
    return "\n".join(lines)


# ---------------- Baza ----------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS dex_seen (address TEXT PRIMARY KEY, kind TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS dex_positions (
  address TEXT PRIMARY KEY, chain TEXT, symbol TEXT, url TEXT, amount REAL, entry REAL,
  cost REAL, tp REAL, sl REAL, peak REAL, opened_at TEXT
);
CREATE TABLE IF NOT EXISTS dex_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT, chain TEXT, symbol TEXT, side TEXT,
  amount REAL, price REAL, cost REAL, pnl REAL, note TEXT, mode TEXT, created_at TEXT
);
"""
_ready = False


def _db():
    global _ready
    c = store.conn()
    if not _ready:
        with _lock:
            c.executescript(SCHEMA)
            c.commit()
            _ready = True
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seen_recently(address: str, kind: str, hours: float) -> bool:
    r = _db().execute("SELECT ts FROM dex_seen WHERE address=? AND kind=?", (address + ":" + kind, kind)).fetchone()
    return bool(r and time.time() - r["ts"] < hours * 3600)


def _mark(address: str, kind: str):
    with _lock:
        _db().execute("INSERT OR REPLACE INTO dex_seen VALUES (?,?,?)", (address + ":" + kind, kind, time.time()))
        _db().commit()


def positions() -> list[dict]:
    return [dict(r) for r in _db().execute("SELECT * FROM dex_positions ORDER BY opened_at").fetchall()]


def trades(limit: int = 20) -> list[dict]:
    return [dict(r) for r in _db().execute(
        "SELECT * FROM dex_trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def realized_pnl(since: str = "0000") -> float:
    r = _db().execute("SELECT COALESCE(SUM(pnl),0) s FROM dex_trades WHERE pnl IS NOT NULL AND created_at >= ?",
                      (since,)).fetchone()
    return float(r["s"] or 0)


def paper_balance() -> float:
    spent = sum(float(p["cost"]) for p in positions())
    return config.DEX_PAPER_BALANCE + realized_pnl() - spent


def mode() -> str:
    return "paper" if config.DEX_AUTO_TRADE else "off"


def stats() -> dict:
    rows = _db().execute("SELECT pnl FROM dex_trades WHERE side='sell' AND pnl IS NOT NULL").fetchall()
    wins = sum(1 for r in rows if r["pnl"] > 0)
    return {"mode": mode(), "balance": round(paper_balance(), 2), "open": len(positions()),
            "closed": len(rows), "wins": wins, "pnl": round(realized_pnl(), 2),
            "pnl_today": round(realized_pnl(datetime.now(timezone.utc).date().isoformat()), 2)}


# ---------------- Qog'oz avto-savdo ----------------

SLIPPAGE = 0.01   # har ikki tomonda 1% — DEX'dagi haqiqiy narx farqiga yaqin


def _momentum_ok(t: dict) -> bool:
    return t["chg1"] > 0 and t["chg5"] > -3 and t["buys1"] > t["sells1"] and t["chg24"] < 300


def try_buy(t: dict) -> str | None:
    """Filtrdan o'tgan tangani qog'ozda sotib oladi. Sotib olsa xabar matnini qaytaradi."""
    if not config.DEX_AUTO_TRADE or t["verdict"] != "ok" or not _momentum_ok(t):
        return None
    with _lock:
        pos = positions()
        if any(p["address"] == t["address"] for p in pos) or len(pos) >= config.DEX_MAX_POSITIONS:
            return None
        if _seen_recently(t["address"], "trade", 24):
            return None
        today = datetime.now(timezone.utc).date().isoformat()
        if realized_pnl(today) <= -abs(config.DEX_DAILY_LOSS_LIMIT):
            return None
        usd = config.DEX_TRADE_USDT
        if paper_balance() < usd:
            return None
        entry = t["price"] * (1 + SLIPPAGE)
        amount = usd / entry
        tp, sl = entry * (1 + config.DEX_TP_PCT / 100), entry * (1 - config.DEX_SL_PCT / 100)
        _db().execute(
            "INSERT OR REPLACE INTO dex_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (t["address"], t["chain"], t["symbol"], t["url"], amount, entry, usd, tp, sl, entry, _now()))
        _db().execute(
            "INSERT INTO dex_trades (address,chain,symbol,side,amount,price,cost,pnl,note,mode,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (t["address"], t["chain"], t["symbol"], "buy", amount, entry, usd, None,
             f"score={t['score']}", "paper", _now()))
        _db().commit()
        _mark(t["address"], "trade")
    log.info("DEX qog'oz xarid: %s (%s) @ %s", t["symbol"], t["chain"], entry)
    return (f"🦎 *DEX qog'oz xarid*: {t['symbol']} ({t['chain']})\n"
            f"Summa: `${usd:g}` · Narx: `${entry:.10g}`\n"
            f"🎯 TP `+{config.DEX_TP_PCT:g}%` · 🛑 SL `-{config.DEX_SL_PCT:g}%` · xavfsizlik {t['score']}/100\n"
            f"{t['url']}")


def _close(p: dict, price: float, reason: str) -> str:
    out_price = price * (1 - SLIPPAGE)
    proceeds = p["amount"] * out_price
    pnl = proceeds - p["cost"]
    with _lock:
        _db().execute("DELETE FROM dex_positions WHERE address=?", (p["address"],))
        _db().execute(
            "INSERT INTO dex_trades (address,chain,symbol,side,amount,price,cost,pnl,note,mode,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (p["address"], p["chain"], p["symbol"], "sell", p["amount"], out_price, proceeds,
             round(pnl, 4), reason, "paper", _now()))
        _db().commit()
    log.info("DEX qog'oz sotuv: %s %s PnL %.2f", p["symbol"], reason, pnl)
    icon = "🟢" if pnl >= 0 else "🔴"
    return (f"{icon} *DEX pozitsiya yopildi*: {p['symbol']} ({p['chain']}) — {reason}\n"
            f"Narx: `${out_price:.10g}` · PnL: `{pnl:+.2f}$` ({pnl / p['cost'] * 100:+.1f}%)")


def check_positions() -> list[str]:
    """Ochiq qog'oz pozitsiyalarni tekshiradi: TP, SL, trailing, likvidlik qochishi, muddat."""
    pos = positions()
    if not pos:
        return []
    msgs = []
    pairs = token_pairs([p["address"] for p in pos], ttl=20)
    for p in pos:
        bp = best_pair(pairs, p["address"], p["chain"])
        if not bp:
            continue
        t = summarize(bp)
        price = t["price"]
        if price <= 0:
            continue
        peak = max(float(p["peak"] or p["entry"]), price)
        if peak > float(p["peak"] or 0):
            with _lock:
                _db().execute("UPDATE dex_positions SET peak=? WHERE address=?", (peak, p["address"]))
                _db().commit()
        held_h = (datetime.now(timezone.utc) - datetime.fromisoformat(p["opened_at"])).total_seconds() / 3600
        reason = None
        if t["liq"] < config.DEX_MIN_LIQUIDITY * 0.5:
            reason = "likvidlik keskin kamaydi"
        elif price >= p["tp"]:
            reason = "TP"
        elif price <= p["sl"]:
            reason = "SL"
        elif peak >= p["entry"] * 1.15 and price <= peak * 0.90:
            reason = "trailing (cho'qqidan -10%)"
        elif held_h >= config.DEX_MAX_HOLD_H:
            reason = f"muddat tugadi ({config.DEX_MAX_HOLD_H:g} soat)"
        if reason:
            msgs.append(_close(p, price, reason))
    return msgs


# ---------------- Trend skaneri ----------------

def scan_trending() -> list[dict]:
    """Trenddagi tangalarni filtrdan o'tkazadi. Faqat bozor filtridan o'tganlar chuqur tekshiriladi."""
    global last_scan, last_scan_at
    by_addr = dict((a, c) for c, a in trending_addresses())
    if not by_addr:
        return []
    pairs = token_pairs(list(by_addr), ttl=60)
    out = []
    for addr, chain in by_addr.items():
        bp = best_pair(pairs, addr, chain)
        if not bp:
            continue
        t = evaluate(bp, deep=False)
        if t["bad"] and not _only_thin(t["bad"]):
            out.append(t)
            continue
        out.append(evaluate(bp, deep=True))
    out.sort(key=lambda t: (-{"ok": 2, "warn": 1, "bad": 0}[t["verdict"]], -t["score"], -t["vol24"]))
    last_scan, last_scan_at = out, _now()
    return out


async def loop(broadcast):
    """Har daqiqada pozitsiyalarni, har DEX_SCAN_INTERVAL_SEC da trendni tekshiradi."""
    if not (config.DEX_ALERTS or config.DEX_AUTO_TRADE):
        log.info("DEX moduli o'chiq (DEX_ALERTS=false, DEX_AUTO_TRADE=false)")
        return
    log.info("DEX skaneri ishga tushdi: xabarlar=%s, qog'oz savdo=%s, tarmoqlar=%s",
             config.DEX_ALERTS, config.DEX_AUTO_TRADE, ",".join(config.DEX_CHAINS))
    await asyncio.sleep(30)
    next_scan = 0.0
    while True:
        try:
            for m in await asyncio.to_thread(check_positions):
                await broadcast(m)
            if time.time() >= next_scan:
                next_scan = time.time() + config.DEX_SCAN_INTERVAL_SEC
                found = await asyncio.to_thread(scan_trending)
                sent = 0
                for t in found:
                    if t["verdict"] != "ok":
                        continue
                    msg = await asyncio.to_thread(try_buy, t)
                    if msg:
                        await broadcast(msg)
                    if config.DEX_ALERTS and sent < 3 and not _seen_recently(t["address"], "alert", 24):
                        _mark(t["address"], "alert")
                        await broadcast(report(t, "🔥 *DEX trend — filtrdan o'tdi*") + f"\n{t['url']}")
                        sent += 1
        except Exception as e:  # noqa: BLE001
            log.error("DEX loop xato: %s", e)
        await asyncio.sleep(60)
