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
