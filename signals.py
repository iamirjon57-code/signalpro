"""Signal mantiqi: RSI + MACD + MA kesishuvi + Bollinger → BUY / SELL / HOLD."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import config, data
from indicators import enrich

log = logging.getLogger("signals")


@dataclass
class Signal:
    symbol: str
    kind: str            # crypto | stock | forex
    action: str          # BUY | SELL | HOLD
    price: float
    score: int           # -4 .. +4
    confidence: int      # 0..100
    rsi: float
    macd_hist: float
    trend: str           # up | down | flat
    take_profit: float
    stop_loss: float
    reasons: list[str]
    timeframe: str
    created_at: str

    def dict(self):
        return asdict(self)

    def text(self) -> str:
        icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪️"}[self.action]
        lines = [
            f"{icon} *{self.action}* — `{self.symbol}` ({self.kind}, {self.timeframe})",
            f"Narx: `{self.price:g}`  |  Ishonch: *{self.confidence}%*",
            f"RSI: {self.rsi:.1f}  |  MACD hist: {self.macd_hist:+.4f}  |  Trend: {self.trend}",
        ]
        if self.action != "HOLD":
            lines.append(f"🎯 TP: `{self.take_profit:g}`   🛑 SL: `{self.stop_loss:g}`")
        lines.append("Sabablar:\n" + "\n".join(f"• {r}" for r in self.reasons))
        return "\n".join(lines)


def analyze(symbol: str, timeframe: str | None = None) -> Signal:
    timeframe = timeframe or config.TIMEFRAME
    if (config.STRATEGY != "classic" and timeframe == "1h"
            and data.asset_class(symbol) == "crypto"):
        return analyze_strategy(symbol)
    return analyze_classic(symbol, timeframe)


def analyze_strategy(symbol: str) -> Signal:
    """Tarixiy sinovda tasdiqlangan strategiya bo'yicha signal (yopilgan 1s sham asosida)."""
    import strategies
    df = strategies.prepare(symbol, 300)
    r = df.iloc[-1]
    try:
        price = float(data.fetch(symbol, "1h", 3)["close"].iloc[-1])   # joriy narx
    except Exception:  # noqa: BLE001
        price = float(r["close"])
    trend = "up" if r["ma_fast"] > r["ma_slow"] else ("down" if r["ma_fast"] < r["ma_slow"] else "flat")
    state = [f"4s trend: {'yuqoriga' if r['htf'] == 1 else 'pastga' if r['htf'] == -1 else '—'} · "
             f"1s trend: {'yuqoriga' if trend == 'up' else 'pastga' if trend == 'down' else 'yon'} · RSI {r['rsi']:.0f}"]
    ch = strategies.choice(symbol)
    side, conf = 0, 0
    if not ch:
        reasons = ["Tarixiy sinovda bu tanga uchun foydali strategiya topilmadi — savdo qilinmaydi"] + state
        exit_name = "atr15"
    else:
        exit_name = ch["exit"]
        side = int(strategies.raw_signals(df, ch["strategy"]).iloc[-1])
        oos = (ch.get("oos") or {})
        conf = int(oos.get("winrate") or 55)
        name = strategies.NAMES.get(ch["strategy"], ch["strategy"])
        if side:
            reasons = [f"Strategiya: {name}", f"Stop: {strategies.EXITS.get(exit_name, exit_name)}"]
            if oos.get("n"):
                reasons.append(f"Tarixiy tekshiruv: {oos['n']} savdo, foydali {oos['winrate']:.0f}%, "
                               f"o'rtacha {oos['avg']:+.2f}%")
        else:
            reasons = [f"Strategiya: {name} — hozir kirish sharti yo'q, kutilmoqda"]
        reasons += state
    action = {1: "BUY", -1: "SELL"}.get(side, "HOLD")
    tp, sl = strategies.levels(price, float(r.get("atr") or 0), side or 1, exit_name)
    return Signal(
        symbol=symbol, kind="crypto", action=action, price=price, score=side * 3,
        confidence=conf if side else 0, rsi=float(r["rsi"]), macd_hist=float(r["macd_hist"]),
        trend=trend, take_profit=round(tp, 8), stop_loss=round(sl, 8), reasons=reasons,
        timeframe="1h", created_at=datetime.now(timezone.utc).isoformat(),
    )


def analyze_classic(symbol: str, timeframe: str) -> Signal:
    df = enrich(data.fetch(symbol, timeframe), config.RSI_PERIOD)
    r = df.iloc[-1]
    p = df.iloc[-2]

    score = 0
    reasons: list[str] = []

    # 1) RSI
    if r["rsi"] < config.RSI_OVERSOLD:
        score += 1
        reasons.append(f"RSI {r['rsi']:.1f} — haddan tashqari sotilgan (oversold)")
    elif r["rsi"] > config.RSI_OVERBOUGHT:
        score -= 1
        reasons.append(f"RSI {r['rsi']:.1f} — haddan tashqari sotib olingan (overbought)")

    # 2) MACD kesishuvi
    if p["macd_hist"] <= 0 < r["macd_hist"]:
        score += 1
        reasons.append("MACD signal chizig'ini yuqoriga kesib o'tdi")
    elif p["macd_hist"] >= 0 > r["macd_hist"]:
        score -= 1
        reasons.append("MACD signal chizig'ini pastga kesib o'tdi")

    # 3) MA trend
    if r["ma_fast"] > r["ma_slow"]:
        score += 1
        trend = "up"
        reasons.append("EMA20 > EMA50 — ko'tarilish trendi")
    elif r["ma_fast"] < r["ma_slow"]:
        score -= 1
        trend = "down"
        reasons.append("EMA20 < EMA50 — tushish trendi")
    else:
        trend = "flat"

    # 4) Bollinger
    if r["close"] <= r["bb_low"]:
        score += 1
        reasons.append("Narx Bollinger pastki chizig'ida — qaytish ehtimoli")
    elif r["close"] >= r["bb_up"]:
        score -= 1
        reasons.append("Narx Bollinger yuqori chizig'ida — korreksiya ehtimoli")

    # 5) Katta vaqt oralig'idagi trend bilan tasdiqlash (faqat kripto — aksiya/forex API limitini tejaymiz)
    htf = config.SIGNAL_HTF
    if htf and htf != timeframe and data.asset_class(symbol) == "crypto" and abs(score) >= config.MIN_SCORE:
        try:
            h = enrich(data.fetch(symbol, htf, 120), config.RSI_PERIOD).iloc[-1]
            if score > 0 and h["ma_fast"] < h["ma_slow"]:
                score -= 1
                reasons.append(f"{htf} trend pastga — xarid signali kuchsizlandi")
            elif score < 0 and h["ma_fast"] > h["ma_slow"]:
                score += 1
                reasons.append(f"{htf} trend yuqoriga — sotuv signali kuchsizlandi")
            else:
                reasons.append(f"{htf} trend signalni tasdiqlaydi")
        except Exception as e:  # noqa: BLE001
            log.warning("HTF %s %s: %s", symbol, htf, e)

    if score >= config.MIN_SCORE:
        action = "BUY"
    elif score <= -config.MIN_SCORE:
        action = "SELL"

    else:
        action = "HOLD"
        if not reasons:
            reasons.append("Aniq signal yo'q — kuzatishda")

    price = float(r["close"])
    tp_pct, sl_pct = config.TAKE_PROFIT_PCT / 100, config.STOP_LOSS_PCT / 100
    if action == "SELL":
        tp, sl = price * (1 - tp_pct), price * (1 + sl_pct)
    else:
        tp, sl = price * (1 + tp_pct), price * (1 - sl_pct)

    return Signal(
        symbol=symbol,
        kind=data.asset_class(symbol),
        action=action,
        price=price,
        score=int(score),
        confidence=min(100, int(abs(score) / 4 * 100)),
        rsi=float(r["rsi"]),
        macd_hist=float(r["macd_hist"]),
        trend=trend,
        take_profit=round(tp, 8),
        stop_loss=round(sl, 8),
        reasons=reasons,
        timeframe=timeframe,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def scan(symbols: list[str] | None = None, timeframe: str | None = None) -> list[Signal]:
    out = []
    for s in symbols or data.all_symbols():
        try:
            out.append(analyze(s, timeframe))
        except Exception as e:  # noqa: BLE001
            log.warning("analyze(%s) xato: %s", s, e)
    out.sort(key=lambda x: (-abs(x.score), x.symbol))
    return out
