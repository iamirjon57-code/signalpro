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


def enabled() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def status() -> str:
    if not enabled():
        return "o'chiq (ANTHROPIC_API_KEY yo'q)"
    s = f"yoqilgan · model {config.AI_MODEL} · {_usage['calls']} so'rov"
    if _last_error:
        s += f" · oxirgi xato: {_last_error[:80]}"
    return s


def _call(prompt: str, max_tokens: int = 700, system: str = SYSTEM) -> str:
    global _last_error
    if not enabled():
        raise RuntimeError("AI ulanmagan: Railway Variables'ga ANTHROPIC_API_KEY qo'shing")
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


def _news_context(symbol: str, n: int = 6) -> str:
    import investors
    base = symbol.split("/")[0]
    q = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "BNB binance",
         "XRP": "XRP ripple"}.get(base, base)
    items = investors.news(q + (" crypto" if "/" in symbol else ""), n)
    return "\n".join(f"- {i['title'][:140]} ({i.get('date', '')[:16]})" for i in items) or "yangilik topilmadi"


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
        "Javob tuzilishi (har biri 1-3 qisqa gap):\n"
        "🧠 Xulosa: SOTIB OLISH / SOTISH / KUTISH — va nega\n"
        "📊 Texnik holat\n📰 Yangiliklar ta'siri\n⚠️ Asosiy xavflar\n"
        "🎯 Reja: kirish narxi, stop-loss, take-profit (raqamlar bilan)\n"
        "Ishonch darajasi: past / o'rta / yuqori"
    )
    return _call(prompt, 800)


def review_trade(sig) -> tuple[bool, str]:
    """Avto-savdodan oldin AI tekshiruvi. (ruxsat, qisqa sabab) qaytaradi."""
    prompt = (
        "Avtomatik bot quyidagi savdoni ochmoqchi. Sen xavf nazoratchisisan: faqat aniq jiddiy "
        "sabab bo'lsa (yomon yangilik, kuchli qarama-qarshi trend, bozor qulashi, haddan tashqari "
        "qizigan bozor) rad et; oddiy holatda ruxsat ber.\n\n"
        f"SAVDO: {sig.action} {sig.symbol}\n{_signal_context(sig)}\n\n"
        f"BOZOR:\n{_market_context(sig.symbol)}\n\nYANGILIKLAR:\n{_news_context(sig.symbol, 5)}\n\n"
        'Faqat JSON qaytar: {"approve": true yoki false, "reason": "o\'zbekcha, 1 gap"}'
    )
    out = _call(prompt, 200, system=SYSTEM + " Faqat so'ralgan JSON formatida javob ber.")
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError(f"AI javobi tushunarsiz: {out[:100]}")
    j = json.loads(m.group(0))
    return bool(j.get("approve")), str(j.get("reason") or "").strip()[:300]


def ask(question: str) -> str:
    """Erkin savol (bozor, aktiv, strategiya haqida)."""
    ctx = _market_context()
    prompt = (f"Hozirgi bozor holati:\n{ctx}\n\nKuzatuvdagi aktivlar: "
              f"{', '.join(config.CRYPTO_SYMBOLS + config.STOCK_SYMBOLS + config.FOREX_SYMBOLS)}\n\n"
              f"Foydalanuvchi savoli: {question[:1500]}")
    return _call(prompt, 800)
