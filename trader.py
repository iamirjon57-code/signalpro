"""Binance avto-savdo (spot, market order) — risk cheklovlari bilan.

Xavfsizlik qoidalari:
  * AUTO_TRADE=false bo'lsa hech qachon order qo'yilmaydi (default).
  * BINANCE_TESTNET=true bo'lsa demo hisobda ishlaydi (default).
  * Bitta savdo hajmi TRADE_AMOUNT_USDT bilan cheklangan.
  * Ochiq pozitsiyalar soni va kunlik zarar limiti (yopilgan savdolar PnL i) tekshiriladi.
  * Sotishda qo'ldagi haqiqiy miqdor sotiladi (Binance komissiyasi hisobga olinadi).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

import ccxt

import config, store

log = logging.getLogger("trader")

_ex: ccxt.binance | None = None
_positions: dict[str, dict] = {}   # symbol -> {amount, entry, tp, sl, ts}
_loaded = False
_lock = threading.RLock()   # skaner va TP/SL tsikli bir vaqtda order bermasin


class TradeError(Exception):
    pass


class NoPosition(TradeError):
    """SELL signali keldi, lekin ochiq pozitsiya yo'q — xato emas, jimgina o'tkaziladi."""


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


def daily_pnl() -> float:
    """Bugun (UTC) yopilgan savdolarning haqiqiy foyda/zarari.

    Avval xarid summasi ham "zarar" deb sanalardi — 3-4 xariddan keyin limit
    noto'g'ri ishga tushib, savdo to'xtab qolardi.
    """
    return store.realized_pnl_since(datetime.now(timezone.utc).date().isoformat())


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
        if daily_pnl() <= -abs(config.DAILY_LOSS_LIMIT_USDT):
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
            raise NoPosition(f"{symbol} bo'yicha ochiq pozitsiya yo'q")


def _filled_base(order: dict, symbol: str, fallback: float) -> float:
    """Xariddan keyin qo'lga tushgan sof miqdor (komissiya base aktivdan olingan bo'lsa ayiriladi)."""
    filled = float(order.get("filled") or fallback)
    base = symbol.split("/")[0]
    fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
    for f in fees:
        if f and f.get("currency") == base and f.get("cost"):
            filled -= float(f["cost"])
    return max(filled, 0.0)


def _sellable(ex, symbol: str, want: float) -> float:
    """Sotish mumkin bo'lgan miqdor: pozitsiya va bo'sh balansning kichigi, aniqlikka yaxlitlangan."""
    base = symbol.split("/")[0]
    try:
        free = float(ex.fetch_balance()["free"].get(base, 0) or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("balans olinmadi (%s): %s", base, e)
        free = want
    amount = float(ex.amount_to_precision(symbol, min(want, free)))
    if amount > free:  # yaxlitlash yuqoriga ketgan bo'lsa
        amount = float(ex.amount_to_precision(symbol, free * 0.999))
    min_amt = ((ex.market(symbol).get("limits") or {}).get("amount") or {}).get("min") or 0
    if amount <= 0 or amount < float(min_amt):
        raise TradeError(f"{symbol}: sotish uchun miqdor yetarli emas ({amount:g} {base})")
    return amount


def _close(symbol: str, pos: dict, price: float, note: str) -> dict:
    ex = exchange()
    amount = _sellable(ex, symbol, float(pos["amount"]))
    order = ex.create_order(symbol, "market", "sell", amount)
    fill = float(order.get("average") or price)
    proceeds = float(order.get("cost") or fill * amount)
    spent = float(pos.get("cost") or pos["entry"] * pos["amount"])
    pnl = proceeds - spent
    store.save_trade(
        symbol=symbol, side="sell", amount=amount, price=fill, cost=proceeds,
        status=order.get("status", "closed"), order_id=str(order.get("id", "")),
        mode=mode(), note=note, pnl=round(pnl, 4),
    )
    _positions.pop(symbol, None)
    store.delete_position(symbol)
    log.info("SOTILDI %s %s @ %s, PnL %.2f (%s)", amount, symbol, fill, pnl, note)
    return {"symbol": symbol, "side": "sell", "amount": amount, "price": fill, "cost": proceeds,
            "pnl": round(pnl, 2), "mode": mode(), "order_id": str(order.get("id", ""))}


def execute(signal) -> dict:
    """BUY/SELL signalini Binance'da bajaradi. Faqat kripto juftliklari uchun."""
    symbol, side = signal.symbol, signal.action.lower()
    if signal.kind != "crypto":
        raise TradeError("Avto-savdo faqat Binance kripto juftliklari uchun (aksiya/forex — signal only)")
    if side not in ("buy", "sell"):
        raise TradeError("HOLD — savdo yo'q")

    with _lock:
        _guard(symbol, side)
        if side == "sell":
            return _close(symbol, _positions[symbol], signal.price,
                          f"SELL signal, score={signal.score}")

        ex = exchange()
        amount = float(ex.amount_to_precision(symbol, config.TRADE_AMOUNT_USDT / signal.price))
        order = ex.create_order(symbol, "market", "buy", amount)
        entry = float(order.get("average") or signal.price)
        cost = float(order.get("cost") or entry * amount)
        held = _filled_base(order, symbol, amount)
        pos = {
            "amount": held, "entry": entry, "cost": cost,
            # TP/SL haqiqiy kirish narxidan hisoblanadi
            "tp": entry * (1 + config.TAKE_PROFIT_PCT / 100),
            "sl": entry * (1 - config.STOP_LOSS_PCT / 100),
            "mode": mode(), "ts": datetime.now(timezone.utc).isoformat(),
        }
        _positions[symbol] = pos
        store.save_position(symbol, pos)
        store.save_trade(
            symbol=symbol, side="buy", amount=held, price=entry, cost=cost,
            status=order.get("status", "closed"), order_id=str(order.get("id", "")),
            mode=mode(), note=f"score={signal.score}",
        )
        log.info("SOTIB OLINDI %s %s @ %s (%s)", held, symbol, entry, mode())
        return {"symbol": symbol, "side": "buy", "amount": held, "price": entry, "cost": cost,
                "mode": mode(), "order_id": str(order.get("id", ""))}


def check_tp_sl(price_fn) -> list[dict]:
    """Ochiq pozitsiyalarda TP/SL ga yetganini tekshirib, kerak bo'lsa yopadi."""
    load_state()
    closed = []
    with _lock:
        for symbol, pos in list(_positions.items()):
            price = price_fn(symbol)
            if price is None:
                continue
            hit = "TP" if price >= pos["tp"] else ("SL" if price <= pos["sl"] else None)
            if not hit:
                continue
            try:
                res = _close(symbol, pos, price, f"{hit} hit")
                closed.append({**res, "reason": hit})
            except Exception as e:  # noqa: BLE001
                log.error("TP/SL yopishda xato %s: %s", symbol, e)
    return closed
