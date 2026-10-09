"""Signallar statistikasi: bot bergan BUY/SELL signallari keyin haqiqatda qanday natija berdi.

Har bir signal uchun signal chiqqandan keyingi PERF_EVAL_HOURS soat ichidagi 1 soatlik
shamlar tekshiriladi:
  * avval TP ga yetsa — "yutuq", avval SL ga yetsa — "yutqazish" (bitta shamda ikkalasi
    bo'lsa — ehtiyot uchun yutqazish deb olinadi);
  * hech biriga yetmasa — "neytral", natija = oxirgi narx bo'yicha foiz.
Ketma-ket bir xil signallar (har 15 daqiqada takrorlanadigan) bitta deb sanaladi.
Hozircha faqat kripto (aksiya/forex Twelve Data limitini tejash uchun).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
import data
import store

log = logging.getLogger("perf")
_lock = threading.RLock()
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_results (
  signal_id INTEGER PRIMARY KEY, symbol TEXT, action TEXT, outcome TEXT,
  ret_pct REAL, hours REAL, created_at TEXT, evaluated_at TEXT
);
"""


def _db():
    global _ready
    c = store.conn()
    if not _ready:
        with _lock:
            c.executescript(SCHEMA)
            c.commit()
            _ready = True
    return c


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pending(limit: int = 60) -> list[dict]:
    """Baholanmagan, yetarlicha eski, takrorlanmagan kripto BUY/SELL signallari."""
    c = _db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=config.PERF_EVAL_HOURS)).isoformat()
    rows = c.execute(
        "SELECT * FROM signals WHERE action IN ('BUY','SELL') AND kind='crypto' AND created_at <= ? "
        "AND id NOT IN (SELECT signal_id FROM signal_results) ORDER BY id LIMIT ?",
        (cutoff, limit * 4)).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        since = (_parse(r["created_at"]) - timedelta(hours=config.PERF_DEDUP_HOURS)).isoformat()
        dup = c.execute(
            "SELECT 1 FROM signals WHERE symbol=? AND action=? AND id<? AND created_at>=? LIMIT 1",
            (r["symbol"], r["action"], r["id"], since)).fetchone()
        if dup:   # takroriy signal — natijasiz belgilab qo'yamiz, statistikaga kirmaydi
            _save(r, "dup", None, 0)
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _save(sig: dict, outcome: str, ret: float | None, hours: float):
    with _lock:
        c = _db()
        c.execute("INSERT OR REPLACE INTO signal_results VALUES (?,?,?,?,?,?,?,?)",
                  (sig["id"], sig["symbol"], sig["action"], outcome, ret, hours,
                   sig["created_at"], datetime.now(timezone.utc).isoformat()))
        c.commit()


def judge(sig: dict, df: pd.DataFrame) -> tuple[str, float, float] | None:
    """(natija, foiz, necha soatda). Ma'lumot yetmasa None."""
    start = _parse(sig["created_at"])
    end = start + timedelta(hours=config.PERF_EVAL_HOURS)
    win = df[(df["time"] > start) & (df["time"] <= end)]
    if win.empty:
        return None
    entry, tp, sl = float(sig["price"]), float(sig["take_profit"]), float(sig["stop_loss"])
    buy = sig["action"] == "BUY"
    for _, c in win.iterrows():
        hrs = (c["time"] - start).total_seconds() / 3600
        hit_sl = c["low"] <= sl if buy else c["high"] >= sl
        hit_tp = c["high"] >= tp if buy else c["low"] <= tp
        if hit_sl:
            return "loss", (sl / entry - 1) * 100 * (1 if buy else -1), hrs
        if hit_tp:
            return "win", (tp / entry - 1) * 100 * (1 if buy else -1), hrs
    last = float(win["close"].iloc[-1])
    return "flat", (last / entry - 1) * 100 * (1 if buy else -1), config.PERF_EVAL_HOURS


def evaluate_pending() -> int:
    n = 0
    by_sym: dict[str, list[dict]] = {}
    for s in pending():
        by_sym.setdefault(s["symbol"], []).append(s)
    for sym, sigs in by_sym.items():
        try:
            df = data.fetch(sym, "1h", 500)
        except Exception as e:  # noqa: BLE001
            log.info("perf %s: %s", sym, e)
            continue
        first = df["time"].iloc[0]
        for s in sigs:
            if _parse(s["created_at"]) < first:   # juda eski — shamlar yo'q
                _save(s, "nodata", None, 0)
                continue
            res = judge(s, df)
            if res:
                _save(s, res[0], round(res[1], 3), round(res[2], 1))
                n += 1
    return n


def summary(days: int = 30) -> dict:
    c = _db()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM signal_results WHERE outcome IN ('win','loss','flat') AND created_at >= ?",
        (since,)).fetchall()]
    def agg(rs):
        w = sum(r["outcome"] == "win" for r in rs)
        l_ = sum(r["outcome"] == "loss" for r in rs)
        f = sum(r["outcome"] == "flat" for r in rs)
        decided = w + l_
        return {"n": len(rs), "win": w, "loss": l_, "flat": f,
                "winrate": round(100 * w / decided, 1) if decided else None,
                "avg": round(sum(r["ret_pct"] or 0 for r in rs) / len(rs), 2) if rs else None,
                "total": round(sum(r["ret_pct"] or 0 for r in rs), 2)}
    per = {}
    for r in rows:
        per.setdefault(r["symbol"], []).append(r)
    trades = [dict(r) for r in c.execute(
        "SELECT pnl FROM trades WHERE pnl IS NOT NULL AND created_at >= ?", (since,)).fetchall()]
    tw = sum(t["pnl"] > 0 for t in trades)
    return {
        "days": days, "all": agg(rows),
        "buy": agg([r for r in rows if r["action"] == "BUY"]),
        "sell": agg([r for r in rows if r["action"] == "SELL"]),
        "symbols": {s: agg(v) for s, v in sorted(per.items())},
        "trades": {"n": len(trades), "win": tw,
                   "winrate": round(100 * tw / len(trades), 1) if trades else None,
                   "pnl": round(sum(t["pnl"] for t in trades), 2)},
    }


def _line(a: dict) -> str:
    if not a["n"]:
        return "hali ma'lumot yo'q"
    wr = f"{a['winrate']:.0f}%" if a["winrate"] is not None else "—"
    return (f"{a['n']} ta · ✅ {a['win']} / ❌ {a['loss']} / ➖ {a['flat']} · "
            f"aniqlik {wr} · o'rtacha {a['avg']:+.2f}%")


def summary_text(days: int = 30) -> str:
    s = summary(days)
    lines = [f"*📈 Signallar statistikasi — oxirgi {days} kun*",
             f"(har bir signal {config.PERF_EVAL_HOURS:g} soat ichida TP yoki SL ga yetdimi — shu tekshiriladi)", "",
             f"*Hammasi:* {_line(s['all'])}",
             f"🟢 BUY: {_line(s['buy'])}",
             f"🔴 SELL: {_line(s['sell'])}"]
    if s["symbols"]:
        lines.append("\n*Aktivlar bo'yicha:*")
        lines += [f"`{k}` — {_line(v)}" for k, v in s["symbols"].items()]
    t = s["trades"]
    lines.append(f"\n*Avto-savdo natijasi:* {t['n']} ta yopilgan savdo"
                 + (f" · foydali {t['winrate']:.0f}% · jami PnL {t['pnl']:+.2f} USDT" if t["n"] else ""))
    a = s["all"]
    lines.append("")
    if a["n"] < 20:
        lines.append("ℹ️ Ishonchli xulosa uchun kamida 20-30 ta signal kerak — bot ishlagani sari to'planadi.")
    elif a["avg"] is not None and a["avg"] <= 0:
        lines.append("⚠️ Signallar o'rtacha zararli — haqiqiy pulga o'tmang, sozlamalarni o'zgartirish kerak.")
    elif a["winrate"] and a["winrate"] >= 55 and a["avg"] > 0:
        lines.append("✅ Signallar hozircha foydali. Baribir kichik summadan boshlang.")
    else:
        lines.append("➖ Natija o'rtacha — qog'oz rejimda kuzatishni davom ettiring.")
    return "\n".join(lines)


def context_text() -> str:
    """AI uchun qisqa statistika."""
    s = summary(30)
    a = s["all"]
    if not a["n"]:
        return "statistika hali yo'q"
    parts = [f"oxirgi 30 kun: {a['n']} signal, aniqlik {a['winrate']}%, o'rtacha {a['avg']:+.2f}%"]
    parts += [f"{k}: aniqlik {v['winrate']}%, o'rtacha {v['avg']:+.2f}% ({v['n']} ta)"
              for k, v in s["symbols"].items() if v["n"] >= 3]
    return "; ".join(parts)


async def loop():
    await asyncio.sleep(90)
    while True:
        try:
            n = await asyncio.to_thread(evaluate_pending)
            if n:
                log.info("Statistika: %d ta signal baholandi", n)
        except Exception as e:  # noqa: BLE001
            log.error("perf loop: %s", e)
        await asyncio.sleep(1800)
