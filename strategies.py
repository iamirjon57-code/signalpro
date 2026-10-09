"""Savdo strategiyalari (vektorlashgan) va tarixiy sinov (backtest).

Har bir strategiya yopilgan 1 soatlik shamlar bo'yicha +1 (BUY), -1 (SELL), 0 qaytaradi.
4 soatlik trend faqat YOPILGAN 4s shamdan olinadi (kelajakka qarash yo'q).

Chiqish (exit) variantlari:
  * fixed — TAKE_PROFIT_PCT / STOP_LOSS_PCT (eski usul)
  * atr15 — SL 1.5×ATR, TP 2.5×ATR
  * atr2  — SL 2×ATR,   TP 3×ATR
Savdo ko'pi bilan BT_MAX_HOLD_H soat ushlanadi, komissiya + sirpanish BT_FEE_PCT (ikki tomonga).

Tanlash (har bir tanga uchun alohida): tarix 65% / 35% ga bo'linadi. Strategiya ikkala qismda ham
o'rtacha foydali bo'lishi va yetarlicha savdo bo'lishi shart; eng yaxshisi — ikkala qismdagi
o'rtacha natijaning kichigi eng katta bo'lgani. Hech biri o'tmasa — o'sha tangada savdo yo'q.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
import data
from indicators import enrich

log = logging.getLogger("strategies")

NAMES = {
    "classic": "Klassik ball (RSI+MACD+EMA+Bollinger)",
    "pullback": "Trend bo'ylab pullback (RSI 40/60 qaytishi)",
    "macd_trend": "MACD kesishuvi trend bo'ylab",
    "breakout": "20 soatlik cho'qqini hajm bilan yorib o'tish",
    "dip": "Ko'tarilish trendida chuqur tushishni sotib olish",
}
EXITS = {"fixed": "TP/SL foizda", "atr15": "SL 1.5×ATR · TP 2.5×ATR", "atr2": "SL 2×ATR · TP 3×ATR"}


# ---------------- Ma'lumot ----------------

def closed(df: pd.DataFrame, tf_hours: float) -> pd.DataFrame:
    """Hali yopilmagan oxirgi shamni olib tashlaydi."""
    if df.empty:
        return df
    end = df["time"].iloc[-1] + pd.Timedelta(hours=tf_hours)
    if end > pd.Timestamp.now(tz="UTC"):
        return df.iloc[:-1].reset_index(drop=True)
    return df


_hist_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_TF_MS = {"1h": 3600_000, "4h": 4 * 3600_000}


def history(symbol: str, tf: str, bars: int) -> pd.DataFrame:
    """Uzun tarix: birja bir so'rovda kam sham bersa ham, sahifalab yig'adi."""
    key = f"{symbol}:{tf}:{bars}"
    hit = _hist_cache.get(key)
    if hit and time.time() - hit[0] < 1200:
        return hit[1]
    if bars <= 300:
        df = data.fetch(symbol, tf, bars)
    else:
        ex = data.public_exchange()
        step = _TF_MS[tf]
        since = int(time.time() * 1000) - bars * step
        rows: list = []
        for _ in range(20):
            chunk = ex.fetch_ohlcv(symbol, timeframe=tf, since=since, limit=200)
            if not chunk:
                break
            rows += chunk
            nxt = chunk[-1][0] + step
            if nxt <= since or nxt > time.time() * 1000:
                break
            since = nxt
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    _hist_cache[key] = (time.time(), df)
    return df


def prepare(symbol: str, limit: int = 1000) -> pd.DataFrame:
    """1s shamlar + indikatorlar + yopilgan 4s trend (htf: +1 / -1)."""
    d1 = closed(enrich(history(symbol, "1h", limit), config.RSI_PERIOD), 1)
    d4 = closed(enrich(history(symbol, "4h", max(120, limit // 4 + 60)), config.RSI_PERIOD), 4)
    return attach_htf(d1, d4)


def attach_htf(d1: pd.DataFrame, d4: pd.DataFrame) -> pd.DataFrame:
    d1 = d1.copy()
    h = pd.DataFrame({"close_t": d4["time"] + pd.Timedelta(hours=4),
                      "htf": np.where(d4["ma_fast"] > d4["ma_slow"], 1, -1)})
    d1["close_t"] = d1["time"] + pd.Timedelta(hours=1)
    m = pd.merge_asof(d1.sort_values("close_t"), h.sort_values("close_t"), on="close_t", direction="backward")
    m["htf"] = m["htf"].fillna(0).astype(int)
    return m.drop(columns=["close_t"]).reset_index(drop=True)


# ---------------- Strategiyalar ----------------

def _cross_up(s: pd.Series, level) -> pd.Series:
    return (s.shift(1) < level) & (s >= level)


def _cross_down(s: pd.Series, level) -> pd.Series:
    return (s.shift(1) > level) & (s <= level)


def raw_signals(df: pd.DataFrame, name: str) -> pd.Series:
    c, up, dn = df["close"], df["htf"] == 1, df["htf"] == -1
    trend_up = (df["ma_fast"] > df["ma_slow"]) & (c > df["ma_slow"])
    trend_dn = (df["ma_fast"] < df["ma_slow"]) & (c < df["ma_slow"])
    if name == "classic":
        s = pd.Series(0, index=df.index)
        s += np.where(df["rsi"] < config.RSI_OVERSOLD, 1, np.where(df["rsi"] > config.RSI_OVERBOUGHT, -1, 0))
        h = df["macd_hist"]
        s += np.where((h.shift(1) <= 0) & (h > 0), 1, np.where((h.shift(1) >= 0) & (h < 0), -1, 0))
        s += np.where(df["ma_fast"] > df["ma_slow"], 1, np.where(df["ma_fast"] < df["ma_slow"], -1, 0))
        s += np.where(c <= df["bb_low"], 1, np.where(c >= df["bb_up"], -1, 0))
        return pd.Series(np.where(s >= config.MIN_SCORE, 1, np.where(s <= -config.MIN_SCORE, -1, 0)), index=df.index)
    if name == "pullback":
        buy = up & trend_up & _cross_up(df["rsi"], 40)
        sell = dn & trend_dn & _cross_down(df["rsi"], 60)
    elif name == "macd_trend":
        h = df["macd_hist"]
        buy = up & trend_up & (h.shift(1) <= 0) & (h > 0)
        sell = dn & trend_dn & (h.shift(1) >= 0) & (h < 0)
    elif name == "breakout":
        hi = df["high"].shift(1).rolling(20).max()
        lo = df["low"].shift(1).rolling(20).min()
        vol_ok = df["volume"] > 1.5 * df["vol_ma"]
        buy = up & (c > hi) & vol_ok
        sell = dn & (c < lo) & vol_ok
    elif name == "dip":
        buy = up & (df["rsi"] < 30) & (c <= df["bb_low"])
        sell = dn & (df["rsi"] > 70) & (c >= df["bb_up"])
    else:
        raise ValueError(name)
    return pd.Series(np.where(buy, 1, np.where(sell, -1, 0)), index=df.index)


def levels(price: float, atr: float, side: int, exit_name: str) -> tuple[float, float]:
    """(tp, sl) narxlari."""
    if exit_name == "fixed" or not atr or np.isnan(atr):
        tp_d, sl_d = price * config.TAKE_PROFIT_PCT / 100, price * config.STOP_LOSS_PCT / 100
    elif exit_name == "atr15":
        tp_d, sl_d = 2.5 * atr, 1.5 * atr
    else:
        tp_d, sl_d = 3.0 * atr, 2.0 * atr
    return (price + side * tp_d, price - side * sl_d)


# ---------------- Backtest ----------------

def simulate(df: pd.DataFrame, sig: pd.Series, exit_name: str, start: int = 60) -> list[dict]:
    hi, lo, cl, at = df["high"].values, df["low"].values, df["close"].values, df["atr"].values
    sv = sig.values
    fee = config.BT_FEE_PCT
    H = int(config.BT_MAX_HOLD_H)
    trades, i, n = [], start, len(df)
    while i < n - 1:
        side = int(sv[i])
        if side == 0:
            i += 1
            continue
        entry = cl[i]
        tp, sl = levels(entry, at[i], side, exit_name)
        ret, j_end = None, min(n - 1, i + H)
        for j in range(i + 1, j_end + 1):
            if (lo[j] <= sl) if side == 1 else (hi[j] >= sl):
                ret = (sl / entry - 1) * 100 * side
                break
            if (hi[j] >= tp) if side == 1 else (lo[j] <= tp):
                ret = (tp / entry - 1) * 100 * side
                break
        else:
            j = j_end
            if j_end < i + H:     # tarix tugadi — savdo yakunlanmagan, hisobga olmaymiz
                break
            ret = (cl[j] / entry - 1) * 100 * side
        trades.append({"i": i, "side": side, "ret": ret - fee})
        i = j + 1
    return trades


def _agg(tr: list[dict]) -> dict:
    if not tr:
        return {"n": 0, "win": 0, "winrate": None, "avg": None, "total": 0.0}
    r = [t["ret"] for t in tr]
    w = int(sum(x > 0 for x in r))
    return {"n": len(r), "win": w, "winrate": round(100.0 * w / len(r), 1),
            "avg": round(float(np.mean(r)), 3), "total": round(float(np.sum(r)), 2)}


def evaluate_symbol(df: pd.DataFrame) -> list[dict]:
    split = int(len(df) * 0.65)
    rows = []
    for name in NAMES:
        sig = raw_signals(df, name)
        for ex in EXITS:
            tr = simulate(df, sig, ex)
            is_ = _agg([t for t in tr if t["i"] < split])
            oos = _agg([t for t in tr if t["i"] >= split])
            al = _agg(tr)
            ok = (is_["n"] >= config.BT_MIN_TRADES_IS and oos["n"] >= config.BT_MIN_TRADES_OOS
                  and (is_["avg"] or 0) > 0 and (oos["avg"] or 0) > 0)
            rows.append({"strategy": name, "exit": ex, "all": al, "is": is_, "oos": oos, "ok": ok,
                         "score": min(is_["avg"] or -9, oos["avg"] or -9)})
    rows.sort(key=lambda r: (r["ok"], r["score"]), reverse=True)
    return rows


def run_all() -> dict:
    out = {"ts": datetime.now(timezone.utc).isoformat(), "symbols": {}}
    for sym in config.CRYPTO_SYMBOLS:
        try:
            df = prepare(sym)
            if len(df) < 300:
                raise RuntimeError(f"tarix juda qisqa ({len(df)} sham)")
            rows = evaluate_symbol(df)
            best = rows[0] if rows and rows[0]["ok"] else None
            out["symbols"][sym] = {
                "bars": len(df), "from": str(df["time"].iloc[0])[:16], "to": str(df["time"].iloc[-1])[:16],
                "best": best, "top": rows[:5],
                "classic": next((r for r in rows if r["strategy"] == "classic" and r["exit"] == "fixed"), None),
            }
        except Exception as e:  # noqa: BLE001
            log.warning("backtest %s: %s", sym, e)
            out["symbols"][sym] = {"error": str(e)}
    _save(out)
    return out


# ---------------- Saqlash va tanlov ----------------

def _save(res: dict):
    import research
    research.kv_set("backtest", json.dumps(res, default=float))


def load() -> dict | None:
    import research
    raw = research.kv_get("backtest")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def choice(symbol: str) -> dict | None:
    """Shu tanga uchun tanlangan (tasdiqlangan) strategiya yoki None."""
    if config.STRATEGY not in ("auto", ""):
        return {"strategy": config.STRATEGY, "exit": config.STRATEGY_EXIT, "forced": True}
    res = load()
    if not res:
        return None
    s = (res.get("symbols") or {}).get(symbol) or {}
    return s.get("best")


def _fmt(a: dict) -> str:
    if not a or not a["n"]:
        return "savdo yo'q"
    return f"{a['n']} savdo · foydali {a['winrate']:.0f}% · o'rtacha {a['avg']:+.2f}%"


def report_text() -> str:
    res = load()
    if not res:
        return "🧪 Strategiya testi hali o'tkazilmagan — bir necha daqiqadan keyin qayta urinib ko'ring."
    lines = ["*🧪 Strategiya testi (tarixiy ma'lumotda)*",
             f"Oxirgi yangilanish: {res['ts'][:16].replace('T', ' ')} UTC",
             "Har bir tanga uchun 5 strategiya × 3 xil stop sinaldi. Tarixning 65% ida topilib, "
             "qolgan 35% ida ham foyda bergan variantgina tanlanadi.", ""]
    for sym, s in res["symbols"].items():
        if s.get("error"):
            lines.append(f"`{sym}` — ❌ {s['error']}")
            continue
        b = s.get("best")
        lines.append(f"*{sym}* ({s['bars']} soat)")
        if b:
            lines.append(f"✅ {NAMES[b['strategy']]} · {EXITS[b['exit']]}")
            lines.append(f"   tekshiruv qismi: {_fmt(b['oos'])}")
            lines.append(f"   butun tarix: {_fmt(b['all'])}")
        else:
            lines.append("⛔ Foydali strategiya topilmadi — bu tangada savdo qilinmaydi")
        c = s.get("classic")
        if c:
            lines.append(f"   (eski usul: {_fmt(c['all'])})")
        lines.append("")
    lines.append("ℹ️ O'tmishdagi natija kelajakni kafolatlamaydi. Test har kuni yangilanadi.")
    return "\n".join(lines)


async def loop():
    """Har BT_REFRESH_H soatda qayta sinash."""
    await asyncio.sleep(20)
    while True:
        res = load()
        age_h = 1e9
        if res:
            try:
                age_h = (time.time() - datetime.fromisoformat(res["ts"]).timestamp()) / 3600
            except ValueError:
                pass
        if age_h >= config.BT_REFRESH_H:
            try:
                await asyncio.to_thread(run_all)
                log.info("Strategiya testi yangilandi")
            except Exception as e:  # noqa: BLE001
                log.error("backtest loop: %s", e)
        await asyncio.sleep(1800)
