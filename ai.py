"""AI maslahatchi (Claude API): signal tahlili, savdoni tekshirish, savollarga javob.

Kalit Railway Variables'da: ANTHROPIC_API_KEY (console.anthropic.com → API Keys).
Kalit bo'lmasa bot oddiy ishlayveradi — AI qismlari shunchaki o'chiq bo'ladi.
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests

import config

log = logging.getLogger("ai")

API_URL = "https://api.anthropic.com/v1/messages"
FALLBACK_MODEL = "claude-haiku-4-5-20251001"

SYSTEM = (
    "Sen tajribali kripto/aksiya/forex treyder-tahlilchisan. Foydalanuvchi — O'zbekistondan, "
    "javoblarni sodda, tabiiy o'zbek tilida (lotin yozuvida) yoz. Qisqa va aniq bo'l. "
    "Markdown belgilarini (*, _, #, `) ishlatma — oddiy matn va emoji yetarli. "
    "Hech qachon foyda kafolatlama; xavflarni ochiq ayt. Bu moliyaviy maslahat emas."
)

_last_error = ""
_usage = {"calls": 0, "in": 0, "out": 0}


def claude_enabled() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def gemini_enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


def enabled() -> bool:
    return claude_enabled() or gemini_enabled()


def status() -> str:
    if not enabled():
        return "o'chiq (ANTHROPIC_API_KEY yoki GEMINI_API_KEY yo'q)"
    parts = []
    if claude_enabled():
        parts.append(f"Claude ({config.AI_MODEL}) · {_usage['calls']} so'rov")
    if gemini_enabled():
        parts.append(f"Gemini + Google qidiruv ({config.GEMINI_MODEL}) · {_gusage['calls']} so'rov")
    s = " | ".join(parts)
    if _last_error:
        s += f" · oxirgi xato: {_last_error[:80]}"
    return s


def _call(prompt: str, max_tokens: int = 700, system: str = SYSTEM, search: bool = False) -> str:
    """Asosiy AI: Claude; u yo'q bo'lsa — Gemini."""
    if not claude_enabled():
        if gemini_enabled():
            return gemini(prompt, max_tokens, system, search=search)
        raise RuntimeError("AI ulanmagan: Railway Variables'ga ANTHROPIC_API_KEY yoki GEMINI_API_KEY qo'shing")
    return _claude(prompt, max_tokens, system)


def _claude(prompt: str, max_tokens: int = 700, system: str = SYSTEM) -> str:
    global _last_error
    headers = {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    model = config.AI_MODEL
    for attempt in range(3):
        body = {"model": model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
        try:
            r = requests.post(API_URL, headers=headers, json=body, timeout=60)
        except requests.RequestException as e:
            _last_error = str(e)
            if attempt < 2:
                time.sleep(2)
                continue
            raise RuntimeError(f"AI bilan aloqa yo'q: {e}") from e
        if r.status_code == 200:
            j = r.json()
            u = j.get("usage") or {}
            _usage["calls"] += 1
            _usage["in"] += int(u.get("input_tokens") or 0)
            _usage["out"] += int(u.get("output_tokens") or 0)
            _last_error = ""
            config.AI_MODEL = model   # zaxira model ishlagan bo'lsa — keyingi safar shuni ishlatamiz
            return "".join(b.get("text", "") for b in j.get("content") or [] if b.get("type") == "text").strip()
        try:
            err = (r.json().get("error") or {}).get("message", r.text[:200])
        except ValueError:
            err = r.text[:200]
        _last_error = f"{r.status_code}: {err}"
        if r.status_code == 404 and model != FALLBACK_MODEL:
            log.warning("AI modeli topilmadi (%s) — %s bilan urinib ko'ramiz", model, FALLBACK_MODEL)
            model = FALLBACK_MODEL
            continue
        if r.status_code in (429, 500, 502, 503, 529) and attempt < 2:
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code == 401:
            raise RuntimeError("AI kaliti noto'g'ri (401). ANTHROPIC_API_KEY ni tekshiring")
        if r.status_code in (400, 402) and "credit" in err.lower():
            raise RuntimeError("Claude API hisobida kredit tugagan — console.anthropic.com → Billing")
        raise RuntimeError(f"AI xatosi {r.status_code}: {err}")
    raise RuntimeError(f"AI javob bermadi: {_last_error}")


# ---------------- Gemini (Google) ----------------

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_FALLBACK = "gemini-flash-latest"
_gusage = {"calls": 0}
_web_cache: dict[str, tuple[float, str]] = {}


def gemini(prompt: str, max_tokens: int = 800, system: str = SYSTEM, search: bool = False) -> str:
    """Gemini; search=True — Google qidiruvi bilan (jonli internet ma'lumotlari)."""
    global _last_error
    if not gemini_enabled():
        raise RuntimeError("GEMINI_API_KEY yo'q")
    model = config.GEMINI_MODEL
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"maxOutputTokens": max(max_tokens, 256) * 4, "temperature": 0.4}}
    if search:
        body["tools"] = [{"google_search": {}}]
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "content-type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(GEMINI_URL.format(model=model), headers=headers, json=body, timeout=90)
        except requests.RequestException as e:
            _last_error = f"Gemini: {e}"
            if attempt < 2:
                time.sleep(2)
                continue
            raise RuntimeError(f"Gemini bilan aloqa yo'q: {e}") from e
        if r.status_code == 200:
            j = r.json()
            _gusage["calls"] += 1
            cand = (j.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or []).strip()
            if not text:
                raise RuntimeError(f"Gemini bo'sh javob qaytardi ({cand.get('finishReason', '?')})")
            config.GEMINI_MODEL = model
            return text
        try:
            err = (r.json().get("error") or {}).get("message", r.text[:200])
        except ValueError:
            err = r.text[:200]
        _last_error = f"Gemini {r.status_code}: {err}"
        if r.status_code == 404 and model != GEMINI_FALLBACK:
            model = GEMINI_FALLBACK
            continue
        if r.status_code in (429, 500, 503) and attempt < 2:
            time.sleep(4 * (attempt + 1))
            continue
        if r.status_code in (400, 403) and "key" in err.lower():
            raise RuntimeError("Gemini kaliti noto'g'ri — GEMINI_API_KEY ni tekshiring")
        if r.status_code == 429:
            raise RuntimeError("Gemini bepul limiti tugadi — birozdan keyin urinib ko'ring")
        raise RuntimeError(f"Gemini xatosi {r.status_code}: {err}")
    raise RuntimeError(f"Gemini javob bermadi: {_last_error}")


def web_research(query: str, ttl: int = 1800) -> str:
    """Google qidiruvi orqali jonli ma'lumot (Gemini). Gemini yo'q bo'lsa — bo'sh."""
    if not gemini_enabled():
        return ""
    hit = _web_cache.get(query)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        txt = gemini(f"Google'da qidirib, quyidagi mavzu bo'yicha ENG SO'NGGI (oxirgi 24-48 soat) faktlarni "
                     f"qisqa yoz (8-10 qator, raqamlar va sanalar bilan, manba nomi bilan): {query}",
                     700, "Sen moliyaviy tadqiqotchisan. Faqat tekshirilgan faktlar, taxmin yo'q. O'zbekcha yoz.",
                     search=True)
    except Exception as e:  # noqa: BLE001
        log.info("web_research: %s", e)
        return ""
    _web_cache[query] = (time.time(), txt)
    return txt


def _web_block(query: str) -> str:
    if not (gemini_enabled() and claude_enabled()):
        return ""
    w = web_research(query)
    return f"\n\nGOOGLE QIDIRUV (Gemini, jonli ma'lumot):\n{w}" if w else ""


def _extra_context(symbol: str | None = None) -> str:
    """Iqtisodiy taqvim, signallar statistikasi, futures/kitlar."""
    parts = []
    try:
        import econ
        parts.append("IQTISODIY TAQVIM (48s): " + econ.upcoming_context())
    except Exception as e:  # noqa: BLE001
        log.info("econ ctx: %s", e)
    try:
        import perf
        parts.append("BOT SIGNALLARI STATISTIKASI: " + perf.context_text())
    except Exception as e:  # noqa: BLE001
        log.info("perf ctx: %s", e)
    if symbol and "/" in symbol:
        try:
            import flows
            parts.append("FUTURES/KITLAR: " + flows.context(symbol))
        except Exception as e:  # noqa: BLE001
            log.info("flows ctx: %s", e)
    return "\n".join(parts)


# ---------------- Kontekst ----------------

def _market_context(symbol: str | None = None) -> str:
    import market
    parts = []
    fg = market.fear_greed()
    if fg:
        parts.append(f"Fear & Greed: {fg['value']}/100 ({fg['label']}), kecha {fg['prev']}")
    g = market.global_market()
    if g:
        parts.append(f"Kripto bozori: ${g['mcap'] / 1e12:.2f} trln, 24s {g['mcap_change']:+.1f}%, BTC ulushi {g['btc_dom']:.1f}%")
    if symbol and "/" in symbol:
        fr = market.funding(symbol)
        if fr is not None:
            parts.append(f"{symbol} funding rate: {fr:+.4f}%")
    return "\n".join(parts) or "ma'lumot yo'q"


def _news_context(symbol: str, n: int = 8) -> str:
    """Global lentalar (Cointelegraph, CoinDesk, CNBC, MarketWatch...) + Google News."""
    import research
    items = research.symbol_news(symbol, n)
    lines = [f"- {i['title'][:160]} ({i['source']}, {research.age(i['ts'])} oldin)" for i in items]
    if len(lines) < 3:   # aktivga oid kam bo'lsa — umumiy bozor yangiliklari
        topic = "crypto" if "/" in symbol and symbol.split("/")[1] in ("USDT", "USDC", "USD") else "markets"
        lines += [f"- [umumiy] {i['title'][:160]} ({i['source']})" for i in research.headlines(topic, 5)]
    return "\n".join(lines) or "yangilik topilmadi"


def _signal_context(sig) -> str:
    return (f"Aktiv: {sig.symbol} ({sig.kind}), vaqt oralig'i {sig.timeframe}\n"
            f"Texnik signal: {sig.action}, ball {sig.score:+d}, ishonch {sig.confidence}%\n"
            f"Narx {sig.price:g}, RSI {sig.rsi:.1f}, MACD hist {sig.macd_hist:+.5f}, trend {sig.trend}\n"
            f"TP {sig.take_profit:g}, SL {sig.stop_loss:g}\n"
            "Sabablar: " + "; ".join(sig.reasons))


# ---------------- Funksiyalar ----------------

def analyze_signal(sig) -> str:
    """Texnik signal + bozor + yangiliklar asosida o'zbekcha AI xulosasi."""
    prompt = (
        "Quyidagi ma'lumotlar asosida aktivni tahlil qil.\n\n"
        f"TEXNIK TAHLIL:\n{_signal_context(sig)}\n\n"
        f"BOZOR HOLATI:\n{_market_context(sig.symbol)}\n\n"
        f"SO'NGGI YANGILIKLAR:\n{_news_context(sig.symbol)}\n\n"
        f"{_extra_context(sig.symbol)}{_web_block(sig.symbol.split('/')[0] + ' narxi va yangiliklari')}\n\n"
        "Javob tuzilishi (har biri 1-3 qisqa gap):\n"
        "🧠 Xulosa: SOTIB OLISH / SOTISH / KUTISH — va nega\n"
        "📊 Texnik holat\n📰 Yangiliklar ta'siri\n⚠️ Asosiy xavflar\n"
        "🎯 Reja: kirish narxi, stop-loss, take-profit (raqamlar bilan)\n"
        "Ishonch darajasi: past / o'rta / yuqori"
    )
    return _call(prompt, 800, search=True)


def review_trade(sig) -> tuple[bool, str]:
    """Avto-savdodan oldin AI tekshiruvi. (ruxsat, qisqa sabab) qaytaradi."""
    prompt = (
        "Avtomatik bot quyidagi savdoni ochmoqchi. Sen xavf nazoratchisisan: faqat aniq jiddiy "
        "sabab bo'lsa (yomon yangilik, kuchli qarama-qarshi trend, bozor qulashi, haddan tashqari "
        "qizigan bozor) rad et; oddiy holatda ruxsat ber.\n\n"
        f"SAVDO: {sig.action} {sig.symbol}\n{_signal_context(sig)}\n\n"
        f"BOZOR:\n{_market_context(sig.symbol)}\n\nYANGILIKLAR:\n{_news_context(sig.symbol, 5)}\n\n"
        f"{_extra_context(sig.symbol)}\n\n"
        'Faqat JSON qaytar: {"approve": true yoki false, "reason": "o\'zbekcha, 1 gap"}'
    )
    return _review(prompt)


def _parse_review(out: str) -> tuple[bool, str]:
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError(f"AI javobi tushunarsiz: {out[:100]}")
    j = json.loads(m.group(0))
    return bool(j.get("approve")), str(j.get("reason") or "").strip()[:300]


def _review(prompt: str) -> tuple[bool, str]:
    """Bitta yoki ikkita AI (Claude + Gemini Google qidiruv bilan) tekshiruvi.

    AI_DUAL_CHECK=true va ikkala kalit bo'lsa — ikkalasi ham ruxsat bersagina savdo ochiladi.
    Gemini ishlamay qolsa — Claude qarori bilan davom etiladi.
    """
    sysj = SYSTEM + " Faqat so'ralgan JSON formatida javob ber."
    ok, why = _parse_review(_call(prompt, 200, system=sysj, search=True))
    if not (config.AI_DUAL_CHECK and claude_enabled() and gemini_enabled()):
        return ok, why
    try:
        g_ok, g_why = _parse_review(gemini(
            prompt + "\n\nGoogle'da shu aktiv bo'yicha oxirgi 24 soat yangiliklarini ham tekshir.",
            300, sysj, search=True))
    except Exception as e:  # noqa: BLE001
        log.info("Gemini tekshiruvi: %s", e)
        return ok, why
    if ok and g_ok:
        return True, f"Claude: {why} | Gemini: {g_why}"
    if not ok:
        return False, f"Claude: {why}"
    return False, f"Gemini (Google): {g_why}"


def ask(question: str) -> str:
    """Erkin savol (bozor, aktiv, strategiya haqida)."""
    ctx = _market_context()
    prompt = (f"Hozirgi bozor holati:\n{ctx}\n\nKuzatuvdagi aktivlar: "
              f"{', '.join(config.CRYPTO_SYMBOLS + config.STOCK_SYMBOLS + config.FOREX_SYMBOLS)}\n\n"
              f"Foydalanuvchi savoli: {question[:1500]}{_web_block(question[:300])}")
    return _call(prompt, 900, search=True)


# ---------------- DEX (DexScreener tangalari) ----------------

def _money(x: float) -> str:
    return f"${x / 1e6:.2f}M" if x >= 1e6 else f"${x / 1e3:.1f}K" if x >= 1e3 else f"${x:.0f}"


def _dex_context(t: dict) -> str:
    age = f"{t['age_h']:.0f} soat" if t.get("age_h") is not None else "noma'lum"
    lines = [
        f"Tanga: {t['symbol']} ({t['name']}), tarmoq {t['chain']}, birja {t['dex']}",
        f"Narx ${t['price']:.10g}; o'zgarish 5daq {t['chg5']:+.1f}%, 1s {t['chg1']:+.1f}%, "
        f"6s {t['chg6']:+.1f}%, 24s {t['chg24']:+.1f}%",
        f"Likvidlik {_money(t['liq'])}, hajm 24s {_money(t['vol24'])}, 1s {_money(t['vol1'])}",
        f"FDV {_money(t['fdv'])}, market cap {_money(t['mcap'])}, yoshi {age}",
        f"Savdolar 24s: {t['buys24']} xarid / {t['sells24']} sotuv; 1s: {t['buys1']} / {t['sells1']}",
        f"Ijtimoiy tarmoq/sayt havolalari: {t.get('socials', 0)}",
        f"Xavfsizlik filtri: {t.get('verdict')} ({t.get('score')}/100), manba: {t.get('sec_source') or '-'}",
        f"CoinGecko ro'yxatida: {'ha' if t.get('coingecko') else 'yo`q'}",
    ]
    if t.get("bad"):
        lines.append("Jiddiy xavflar: " + "; ".join(t["bad"][:6]))
    if t.get("warn"):
        lines.append("Ogohlantirishlar: " + "; ".join(t["warn"][:6]))
    return "\n".join(lines)


def analyze_dex(t: dict) -> str:
    """DEX tangasi bo'yicha o'zbekcha AI xulosasi (firibgarlik xavfi + savdo holati)."""
    import investors
    news = investors.news(f"{t['symbol']} {t['name']} crypto", 4)
    news_txt = "\n".join(f"- {i['title'][:140]}" for i in news) or "yangilik topilmadi"
    prompt = (
        "DEX'dagi (memecoin bo'lishi mumkin) tangani tahlil qil. Firibgarlik (rug pull, honeypot, "
        "pump-and-dump) belgilariga alohida e'tibor ber.\n\n"
        f"MA'LUMOTLAR:\n{_dex_context(t)}\n\nYANGILIKLAR:\n{news_txt}\n\n"
        f"BOZOR:\n{_market_context()}{_web_block(t['symbol'] + ' ' + t['name'] + ' token ' + t['chain'] + ' scam rug yangiliklar')}\n\n"
        "Javob tuzilishi (har biri 1-3 qisqa gap):\n"
        "🧠 Xulosa: OLISH MUMKIN / KUTISH / UZOQ TURING — va nega\n"
        "🛡 Firibgarlik xavfi: past / o'rta / yuqori — sabablari\n"
        "📊 Savdo holati (hajm, xarid/sotuv, narx harakati)\n"
        "⚠️ Asosiy xavflar\n"
        "🎯 Agar olinsa: qancha qism (kichik!), stop-loss va foyda olish darajalari (%)"
    )
    return _call(prompt, 800)


def review_dex(t: dict) -> tuple[bool, str]:
    """DEX qog'oz avto-xariddan oldin AI tekshiruvi."""
    prompt = (
        "Avtomatik bot quyidagi DEX tangasini kichik summaga sotib olmoqchi (tanga xavfsizlik "
        "filtridan o'tgan). Sen xavf nazoratchisisan: pump-and-dump, sun'iy hajm, sotuvchilar ko'payishi, "
        "haddan tashqari tez o'sish yoki boshqa jiddiy xavf bo'lsa rad et; aks holda ruxsat ber.\n\n"
        f"{_dex_context(t)}\n\n"
        'Faqat JSON qaytar: {"approve": true yoki false, "reason": "o\'zbekcha, 1 gap"}'
    )
    return _review(prompt)


# ---------------- Global yangiliklar va investitsiya ----------------

_digest_cache: dict[str, tuple[float, str]] = {}


def _cached_call(key: str, ttl: int, fn) -> str:
    hit = _digest_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _digest_cache[key] = (time.time(), val)
    return val


def _items_text(items: list[dict]) -> str:
    import research
    return "\n".join(f"{n}. {i['title']} [{i['source']}, {research.age(i['ts'])} oldin]"
                      + (f" — {i['desc'][:160]}" if i.get("desc") else "")
                      for n, i in enumerate(items, 1))


def news_digest(topic: str) -> str:
    """Mavzu bo'yicha global yangiliklar — o'zbekcha qisqa sharh, bozorga ta'siri bilan."""
    import research

    def make():
        items = research.headlines(topic, 18)
        if not items:
            return "Yangilik topilmadi (manbalar javob bermadi)."
        prompt = (
            f"Mavzu: {research.TOPIC_NAMES.get(topic, topic)}. Quyida dunyo nashrlaridagi eng so'nggi "
            "yangiliklar (inglizcha). Investor va treyder uchun eng muhim 7-8 tasini tanla va o'zbekchaga "
            "o'gir.\n\n" + _items_text(items) + "\n\n"
            "Format:\n"
            "Boshida 1-2 gapda umumiy kayfiyat (📈 ijobiy / 📉 salbiy / ➖ neytral).\n"
            "Keyin har bir yangilik alohida qatorda: belgi (📈/📉/➖) + qisqa o'zbekcha mazmun "
            "(1-2 gap) + qaysi aktivlarga ta'sir qiladi + (manba).\n"
            "Oxirida: 💡 Investor uchun xulosa — 2-3 gap."
        )
        return _call(prompt, 1300)
    return _cached_call(f"digest:{topic}", 1800, make)


def invest_ideas() -> str:
    """Global ma'lumotlar + texnik signallar asosida qisqa va uzoq muddatli g'oyalar."""
    import research
    import store

    def make():
        sigs = store.recent_signals(15)
        sig_txt = "\n".join(f"- {s['symbol']}: {s['action']} (ishonch {s['confidence']}%, RSI {s['rsi']:.0f}, "
                            f"trend {s['trend']})" for s in sigs) or "signal yo'q"
        news = research.headlines("crypto", 10) + research.headlines("markets", 8)
        prompt = (
            "Sen investitsiya tahlilchisisan. Quyidagi ma'lumotlar asosida o'zbek investori uchun "
            "g'oyalar tayyorla.\n\n"
            f"BOZOR KAYFIYATI:\n{_market_context()}\n\n"
            f"GLOBAL KRIPTO/DEX:\n{research.global_context()}\n\n"
            f"BOTNING TEXNIK SIGNALLARI:\n{sig_txt}\n\n"
            f"SO'NGGI YANGILIKLAR:\n{_items_text(news)}\n\n"
            f"{_extra_context('BTC/USDT')}{_web_block('bugungi kripto va aksiya bozori, eng kuchli o`sayotgan aktivlar')}\n\n"
            "Format:\n"
            "🌍 Umumiy holat — 2 gap\n"
            "⚡ Qisqa muddatli savdo g'oyalari (1-7 kun) — 2-3 ta: aktiv, yo'nalish, kirish/stop/maqsad, sabab\n"
            "🏦 Uzoq muddatli investitsiya (6+ oy) — 2-3 ta: aktiv yoki soha, nega, portfeldagi ulushi\n"
            "🚫 Hozir nimadan uzoq turish kerak — 1-2 ta\n"
            "Har bir g'oyaga xavf darajasi (past/o'rta/yuqori). Portfelni bo'lish va stop-loss haqida eslat."
        )
        return _call(prompt, 1500, search=True)
    return _cached_call("ideas", 3600, make)


def daily_report() -> str:
    import research
    news = research.headlines("crypto", 10) + research.headlines("markets", 8) + research.headlines("defi", 5)
    prompt = (
        "Ertalabki investor hisobotini tayyorla (o'zbekcha, Telegram uchun, 25-35 qator).\n\n"
        f"BOZOR:\n{_market_context('BTC/USDT')}\n\nGLOBAL:\n{research.global_context()}\n\n"
        f"YANGILIKLAR:\n{_items_text(news)}\n\n{_extra_context('BTC/USDT')}\n\n"
        "Bo'limlar: 🌡 Bozor kayfiyati · 📰 Eng muhim 5 yangilik (ta'siri bilan) · 🦎 DEX/DeFi'da nima "
        "bo'lyapti · 📅 Bugun nimaga e'tibor berish kerak · 💡 Kun g'oyasi (xavfi bilan)."
    )
    return _call(prompt, 1600, search=True)


def portfolio_review(chat_id) -> str:
    """Foydalanuvchi portfelini AI ko'rib chiqadi."""
    import alerts
    port = alerts.portfolio_context(chat_id)
    if port == "portfel bo'sh":
        return "Portfel bo'sh — avval aktiv qo'shing: /port BTC 0.05 60000"
    prompt = (
        "Investorning portfelini ko'rib chiq va o'zbekcha maslahat ber.\n\n"
        f"PORTFEL:\n{port}\n\nBOZOR:\n{_market_context('BTC/USDT')}\n\n{_extra_context()}"
        f"{_web_block('portfeldagi aktivlar bo`yicha so`nggi yangiliklar: ' + port[:300])}\n\n"
        "Format: 📊 Umumiy baho (diversifikatsiya, xavf darajasi) · ✅ Yaxshi tomonlari · ⚠️ Xavflar · "
        "🔄 Nima qilish kerak (har bir aktiv bo'yicha: ushlab turish / qisman foyda olish / qo'shish / "
        "stop qo'yish — sababi bilan) · 💡 Portfelga nima qo'shish mumkin."
    )
    return _call(prompt, 1300, search=True)


def important_news(items: list[dict]) -> list[str]:
    """Yangi sarlavhalardan faqat bozorni qimirlatadiganlarini tanlab, o'zbekcha xabar qiladi."""
    if not items:
        return []
    prompt = (
        "Quyida yangi chiqqan yangiliklar. Faqat bozorga KUCHLI ta'sir qiladiganlarini tanla (masalan: "
        "Fed qarori, yirik birja buzilishi/xakerlik, ETF qarori, yirik kompaniya hisobotida katta "
        "kutilmagan natija, regulyator taqiqi, urush/sanksiya). Oddiy tahlil va reklama maqolalarini tashla. "
        f"Ko'pi bilan {config.NEWS_ALERT_MAX} ta. Hech biri muhim bo'lmasa — bo'sh ro'yxat.\n\n"
        + _items_text(items) + "\n\n"
        'Faqat JSON: {"alerts": [{"n": raqam, "uz": "o\'zbekcha 2-3 gap: nima bo\'ldi va bozorga ta\'siri", '
        '"impact": "up|down|mixed", "assets": "BTC, ETH..."}]}'
    )
    out = _call(prompt, 900, system=SYSTEM + " Faqat so'ralgan JSON formatida javob ber.")
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return []
    try:
        alerts = json.loads(m.group(0)).get("alerts") or []
    except ValueError:
        return []
    icon = {"up": "📈", "down": "📉"}
    msgs = []
    for a in alerts[: config.NEWS_ALERT_MAX]:
        try:
            src = items[int(a.get("n", 0)) - 1]
        except (ValueError, IndexError):
            continue
        msgs.append(f"🚨 Muhim yangilik {icon.get(a.get('impact'), '⚖️')}\n\n{a.get('uz', '').strip()}\n\n"
                    f"Ta'sir: {a.get('assets', '-')}\nManba: {src['source']} — {src['link']}")
    return msgs
