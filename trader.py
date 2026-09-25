"""Binance avto-savdo (spot, market order) — risk cheklovlari bilan.

Xavfsizlik qoidalari:
  * AUTO_TRADE=false bo'lsa hech qachon order qo'yilmaydi (default).
  * BINANCE_TESTNET=true bo'lsa demo hisobda ishlaydi (default).
  * Bitta savdo hajmi TRADE_AMOUNT_USDT bilan cheklangan.
  * Ochiq pozitsiyalar soni va kunlik zarar limiti tekshiriladi.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import ccxt

import config, store

log = logging.getLogger("trader")

_ex: ccxt.binance | None = None
_positions: dict[str, dict] = {}   # symbol -> {amount, entry, tp, sl, ts}
_loaded = False


class TradeError(Exception):
    pass


def exchange() -> ccxt.binance:
    global _ex
    if _ex is None:
        if not (config.BINANCE_API_KEY and config.BINANCE_API_SECRET):
            raise TradeError("BINANCE_API_KEY / BINANCE_API_SECRET o'rnatilmagan")
        _ex = ccxt.binance({
            "apiKey": config.BINANCE_API_KEY,
            "secret": config.BINANCE_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if config.BINANCE_TESTNET:
            _ex.set_sandbox_mode(True)
        _ex.load_markets()
    return _ex


def mode() -> str:
    if not config.AUTO_TRADE:
        return "off"
    return "testnet" if config.BINANCE_TESTNET else "LIVE"


def balance(quote: str | None = None) -> dict:
    quote = quote or config.TRADE_QUOTE
    b = exchange().fetch_balance()
    return {
        "quote": quote,
        "free": float(b["free"].get(quote, 0)),
        "total": float(b["total"].get(quote, 0)),
        "assets": {k: v for k, v in b["total"].items() if v and float(v) > 0},
    }


def load_state():
    """Qayta ishga tushganda ochiq pozitsiyalarni bazadan tiklaydi."""
    global _loaded
    if _loaded:
        return
    _positions.update(store.load_positions())
    _loaded = True
    if _positions:
        log.info("Bazadan %d ta ochiq pozitsiya tiklandi: %s",
                 len(_positions), ", ".join(_positions))


def positions() -> dict[str, dict]:
    load_state()
    return dict(_positions)


def _daily_pnl() -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    rows = [t for t in store.recent_trades(300) if (t.get("created_at") or "").startswith(today)]
    pnl = 0.0
    for t in rows:
        cost = float(t.get("cost") or 0)
        pnl += cost if t["side"] == "sell" else -cost
    return pnl


def _min_cost(symbol: str) -> float:
    """Binance'ning shu juftlik uchun minimal buyurtma qiymati (USDT)."""
    try:
        m = exchange().market(symbol)
        return float((m.get("limits", {}).get("cost", {}) or {}).get("min") or 0) or 5.0
    except Exception:  # noqa: BLE001
        return 5.0


def _guard(symbol: str, side: str):
    load_state()
    if not config.AUTO_TRADE:
        raise TradeError("AUTO_TRADE=false — avto-savdo o'chiq")
    if side == "buy":
        if len(_positions) >= config.MAX_OPEN_POSITIONS:
            raise TradeError(f"Ochiq pozitsiyalar limiti ({config.MAX_OPEN_POSITIONS}) to'ldi")
        if symbol in _positions:
            raise TradeError(f"{symbol} bo'yicha pozitsiya allaqachon ochiq")
        if _daily_pnl() <= -abs(config.DAILY_LOSS_LIMIT_USDT):
            raise TradeError("Kunlik zarar limitiga yetildi — savdo to'xtatildi")
        need = _min_cost(symbol)
        if config.TRADE_AMOUNT_USDT < need:
            raise TradeError(
                f"{symbol} uchun minimal buyurtma {need:g} USDT, "
                f"TRADE_AMOUNT_USDT esa {config.TRADE_AMOUNT_USDT:g}")
        free = balance()["free"]
        if free < config.TRADE_AMOUNT_USDT:
            raise TradeError(f"Balans yetarli emas: {free:.2f} USDT")
    else:
        if symbol not in _positions:
            raise TradeError(f"{symbol} bo'yicha ochiq pozitsiya yo'q")


def execute(signal) -> dict:
    """BUY/SELL signalini Binance'da bajaradi. Faqat kripto juftliklari uchun."""
    symbol, action = signal.symbol, signal.action
    if signal.kind != "crypto":
        raise TradeError("Avto-savdo faqat Binance kripto juftliklari uchun (aksiya/forex — signal only)")
    side = action.lower()
    _guard(symbol, side)
    ex = exchange()

    if side == "buy":
        amount = config.TRADE_AMOUNT_USDT / signal.price
        amount = float(ex.amount_to_precision(symbol, amount))
        order = ex.create_order(symbol, "market", "buy", amount)
        pos = {
            "amount": amount, "entry": float(order.get("average") or signal.price),
            "tp": signal.take_profit, "sl": signal.stop_loss, "mode": mode(),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        _positions[symbol] = pos
        store.save_position(symbol, pos)
    else:
        amount = _positions[symbol]["amount"]
        order = ex.create_order(symbol, "market", "sell", amount)
        _positions.pop(symbol, None)
        store.delete_position(symbol)

    cost = float(order.get("cost") or amount * signal.price)
    store.save_trade(
        symbol=symbol, side=side, amount=amount, price=signal.price, cost=cost,
        status=order.get("status", "closed"), order_id=str(order.get("id", "")),
        mode=mode(), note=f"score={signal.score}",
    )
    log.info("SAVDO %s %s %s (%s)", side.upper(), amount, symbol, mode())
    return {"symbol": symbol, "side": side, "amount": amount, "cost": cost, "mode": mode(),
            "order_id": str(order.get("id", ""))}


def check_tp_sl(price_fn) -> list[dict]:
    """Ochiq pozitsiyalarda TP/SL ga yetganini tekshirib, kerak bo'lsa yopadi."""
    load_state()
    closed = []
    for symbol, pos in list(_positions.items()):
        price = price_fn(symbol)
        if price is None:
            continue
        hit = "TP" if price >= pos["tp"] else ("SL" if price <= pos["sl"] else None)
        if not hit:
            continue
        try:
            ex = exchange()
            order = ex.create_order(symbol, "market", "sell", pos["amount"])
            pnl = (price - pos["entry"]) * pos["amount"]
            store.save_trade(
                symbol=symbol, side="sell", amount=pos["amount"], price=price,
                cost=float(order.get("cost") or price * pos["amount"]),
                status="closed", order_id=str(order.get("id", "")), mode=mode(),
                note=f"{hit} hit, PnL={pnl:.2f}",
            )
            _positions.pop(symbol, None)
            store.delete_position(symbol)
            closed.append({"symbol": symbol, "reason": hit, "price": price, "pnl": round(pnl, 2)})
        except Exception as e:  # noqa: BLE001
            log.error("TP/SL yopishda xato %s: %s", symbol, e)
    return closed
