"""Iqtisodiy taqvim (ForexFactory): Fed, inflyatsiya (CPI), ish o'rinlari (NFP) va boshqalar.

* Muhim (High) voqea oldidan va keyin avto-savdo yangi xarid qilmaydi — bozor keskin
  qimirlaydi, stop-loss'lar sirpanib ketadi.
* Voqeadan ECON_REMIND_MIN daqiqa oldin obunachilarga eslatma yuboriladi.
Manba: nfs.faireconomy.media (bepul, kalitsiz, soatiga bir martadan ko'p so'ralmaydi).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

import config

log = logging.getLogger("econ")
URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_cache: dict = {"ts": 0.0, "events": []}

UZ = [  # (kalit so'z, o'zbekcha nom)
    ("FOMC Meeting Minutes", "Fed yig'ilishi bayonnomasi"), ("Fed Chair", "Fed raisi nutqi"),
    ("FOMC", "Fed foiz stavkasi qarori"), ("Federal Funds Rate", "Fed foiz stavkasi qarori"),
    ("Core CPI", "Asosiy inflyatsiya (Core CPI)"), ("CPI", "Inflyatsiya (CPI)"),
    ("Core PPI", "Ishlab chiqaruvchi narxlari (Core PPI)"), ("PPI", "Ishlab chiqaruvchi narxlari (PPI)"),
    ("Core PCE", "Fed sevimli inflyatsiyasi (Core PCE)"), ("PCE", "Iste'mol xarajatlari narxi (PCE)"),
    ("Non-Farm", "AQSh yangi ish o'rinlari (NFP)"), ("Unemployment Rate", "Ishsizlik darajasi"),
    ("Unemployment Claims", "Ishsizlik nafaqasi arizalari"), ("JOLTS", "Bo'sh ish o'rinlari (JOLTS)"),
    ("ADP", "Xususiy sektor ish o'rinlari (ADP)"), ("Average Hourly Earnings", "O'rtacha soatlik maosh"),
    ("GDP", "YaIM (iqtisodiy o'sish)"), ("Retail Sales", "Chakana savdo"),
    ("ISM Manufacturing", "Sanoat faolligi (ISM)"), ("ISM Services", "Xizmatlar faolligi (ISM)"),
    ("PMI", "Biznes faolligi (PMI)"), ("Consumer Sentiment", "Iste'molchi kayfiyati"),
    ("Consumer Confidence", "Iste'molchi ishonchi"), ("Main Refinancing Rate", "ECB foiz qarori"),
    ("Official Bank Rate", "Angliya banki foiz qarori"), ("BOJ Policy Rate", "Yaponiya banki foiz qarori"),
    ("Crude Oil Inventories", "Neft zaxiralari"), ("Bank Holiday", "Bayram (bozor yopiq)"),
    ("Speaks", "nutq"),
]
FLAGS = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CNY": "🇨🇳", "CAD": "🇨🇦",
         "AUD": "🇦🇺", "CHF": "🇨🇭", "NZD": "🇳🇿", "All": "🌍"}


def uz_title(t: str) -> str:
    for k, v in UZ:
        if k.lower() in t.lower():
            return v if k != "Speaks" else f"{t.replace('Speaks', '').strip()} nutqi"
    return t


def _parse(d: str) -> float | None:
    try:
        return datetime.fromisoformat(d).timestamp()
    except (TypeError, ValueError):
        return None


def events() -> list[dict]:
    if time.time() - _cache["ts"] < 3600 and _cache["events"]:
        return _cache["events"]
    try:
        r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 SignalPro"}, timeout=15)
        r.raise_for_status()
        out = []
        for e in r.json():
            ts = _parse(e.get("date", ""))
            if ts is None:
                continue
            out.append({"ts": ts, "title": e.get("title", ""), "uz": uz_title(e.get("title", "")),
                        "country": e.get("country", ""), "impact": e.get("impact", ""),
                        "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
        out.sort(key=lambda x: x["ts"])
        _cache.update(ts=time.time(), events=out)
    except Exception as e:  # noqa: BLE001
        log.info("taqvim: %s", e)
        _cache["ts"] = time.time() - 3000   # 10 daqiqadan keyin qayta urinadi
    return _cache["events"]


def _important(e: dict) -> bool:
    return e["impact"] == "High" and e["country"] in config.ECON_CURRENCIES


def pause_reason(now: float | None = None) -> str | None:
    """Muhim voqea yaqin bo'lsa — yangi xarid to'xtatiladi."""
    if not config.ECON_PAUSE:
        return None
    now = now or time.time()
    for e in events():
        if not _important(e):
            continue
        before = e["ts"] - config.ECON_PAUSE_BEFORE_MIN * 60
        after = e["ts"] + config.ECON_PAUSE_AFTER_MIN * 60
        if before <= now <= after:
            mins = (e["ts"] - now) / 60
            when = f"{mins:.0f} daqiqadan keyin" if mins > 0 else f"{-mins:.0f} daqiqa oldin bo'ldi"
            return f"muhim iqtisodiy voqea: {FLAGS.get(e['country'], '')} {e['uz']} ({when})"
    return None


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=config.TZ_OFFSET_H)


DAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]


def week_text(show_medium: bool = True) -> str:
    ev = [e for e in events() if e["impact"] in (("High", "Medium") if show_medium else ("High",))
          and e["country"] in config.ECON_CURRENCIES and e["ts"] >= time.time() - 3 * 3600]
    if not ev:
        return "📅 Bu hafta qolgan muhim iqtisodiy voqea topilmadi (yoki manba javob bermadi)."
    lines = ["*📅 Iqtisodiy taqvim* (Toshkent vaqti)", "🔴 — juda muhim (bozor keskin qimirlaydi), 🟠 — o'rtacha"]
    day = None
    for e in ev[:30]:
        t = _local(e["ts"])
        d = f"{DAYS[t.weekday()]}, {t:%d.%m}"
        if d != day:
            lines.append(f"\n*{d}*")
            day = d
        fp = ""
        if e["forecast"] or e["previous"]:
            fp = f" · kutilgan {e['forecast'] or '—'}, oldingi {e['previous'] or '—'}"
        lines.append(f"{'🔴' if e['impact'] == 'High' else '🟠'} {t:%H:%M} {FLAGS.get(e['country'], e['country'])} "
                     f"{e['uz']}{fp}")
    if config.ECON_PAUSE:
        lines.append(f"\n⏸ 🔴 voqealardan {config.ECON_PAUSE_BEFORE_MIN:g} daq oldin va "
                     f"{config.ECON_PAUSE_AFTER_MIN:g} daq keyin avto-savdo yangi xarid qilmaydi.")
    return "\n".join(lines)


def upcoming_context(hours: float = 48) -> str:
    now = time.time()
    ev = [e for e in events() if _important(e) and now - 3600 <= e["ts"] <= now + hours * 3600]
    return "; ".join(f"{_local(e['ts']):%d.%m %H:%M} {e['country']} {e['title']} "
                     f"(kutilgan {e['forecast'] or '-'}, oldingi {e['previous'] or '-'})" for e in ev) \
        or "yaqin 48 soatda muhim voqea yo'q"


async def loop(broadcast):
    """Muhim voqeadan oldin eslatma."""
    import research
    await asyncio.sleep(45)
    while True:
        try:
            now = time.time()
            for e in await asyncio.to_thread(events):
                if not _important(e) or not config.ECON_REMIND_MIN:
                    continue
                left = (e["ts"] - now) / 60
                key = f"econ:{e['ts']:.0f}:{e['title'][:30]}"
                if 0 < left <= config.ECON_REMIND_MIN and not research.kv_get(key):
                    research.kv_set(key, "1")
                    t = _local(e["ts"])
                    await broadcast(
                        f"📅 *Muhim voqea {left:.0f} daqiqadan keyin* ({t:%H:%M})\n"
                        f"{FLAGS.get(e['country'], '')} {e['uz']}\n"
                        f"Kutilgan: {e['forecast'] or '—'} · oldingi: {e['previous'] or '—'}\n\n"
                        "Bozor keskin qimirlashi mumkin — yangi savdo ochishdan ehtiyot bo'ling."
                        + ("\n⏸ Avto-savdo bu vaqtda yangi xarid qilmaydi." if config.ECON_PAUSE else ""))
        except Exception as e:  # noqa: BLE001
            log.error("econ loop: %s", e)
        await asyncio.sleep(120)
