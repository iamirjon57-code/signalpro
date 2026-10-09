"""Avto-savdo (Binance yoki Bitget, spot, market order) — risk cheklovlari bilan.

Xavfsizlik qoidalari:
  * AUTO_TRADE=false bo'lsa hech qachon order qo'yilmaydi (default).
  * BINANCE_TESTNET=true bo'lsa sinov rejimi (default): Binance — testnet,
    Bitget — qog'oz savdo (haqiqiy narx, lekin buyurtma birjaga yuborilmaydi).
  * Bitta savdo hajmi TRADE_AMOUNT_USDT bilan cheklangan.
  * Ochiq pozitsiyalar soni va kunlik zarar limiti (yopilgan savdolar PnL i) tekshiriladi.
  * Sotishda qo'ldagi haqiqiy miqdor sotiladi (Binance komissiyasi hisobga olinadi).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import ccxt

import config, data, store

log = logging.getLogger("trader")

_ex = None
_positions: dict[str, dict] = {}   # symbol -> {amount, entry, tp, sl, ts}
_loaded = False
_lock = threading.RLock()   # skaner va TP/SL tsikli bir vaqtda order bermasin


class TradeError(Exception):
    pass


class NoPosition(TradeError):
    """SELL signali keldi, lekin ochiq pozitsiya yo'q — xato emas, jimgina o'tkaziladi."""


class PaperExchange:
    """Qog'oz savdo: haqiqiy narxlar, lekin buyurtma birjaga yuborilmaydi.

    Bitget'da spot uchun test birjasi yo'q, shuning uchun sinov rejimida shu ishlatiladi.
    Balans: boshlang'ich summa + yopilgan savdolar PnL i - ochiq pozitsiyalar qiymati.
    """
    FEE = 0.001  # 0.1% — Bitget spot komissiyasiga yaqin

    def __init__(self):
        self.pub = data.public_exchange()
        self.pub.load_markets()
        self.options = {"createMarketBuyOrderRequiresPrice": True}
        self.has = {"createMarketBuyOrderWithCost": True}
        self._n = 0

    def market(self, symbol):
        return self.pub.market(symbol)

    def amount_to_precision(self, symbol, amount):
        return self.pub.amount_to_precision(symbol, amount)

    def _price(self, symbol) -> float:
        return float(self.pub.fetch_ticker(symbol)["last"])

    def fetch_balance(self):
        pos = store.load_positions()
        spent = sum(float(p.get("cost") or p["entry"] * p["amount"]) for p in pos.values())
        usdt = config.PAPER_BALANCE_USDT + store.realized_pnl_since("0000") - spent
        free = {config.TRADE_QUOTE: usdt}
        for sym, p in pos.items():
            free[sym.split("/")[0]] = float(p["amount"])
        return {"free": dict(free), "total": dict(free)}

    def _order(self, symbol, side, amount, price):
        self._n += 1
        base = symbol.split("/")[0]
        cost = amount * price
        fee = ({"currency": base, "cost": amount * self.FEE} if side == "buy"
               else {"currency": config.TRADE_QUOTE, "cost": cost * self.FEE})
        return {"id": f"paper-{int(time.time())}-{self._n}", "status": "closed",
                "filled": amount, "average": price, "cost": cost, "fee": fee}

    def create_market_buy_order_with_cost(self, symbol, cost):
        price = self._price(symbol)
        amount = float(self.amount_to_precision(symbol, cost / price))
        return self._order(symbol, "buy", amount, price)

    def create_order(self, symbol, type_, side, amount):
        return self._order(symbol, side, float(amount), self._price(symbol))


def is_paper() -> bool:
    return config.EXCHANGE == "bitget" and config.TESTNET


def exchange():
    global _ex
    if _ex is not None:
        return _ex
    if is_paper():
        _ex = PaperExchange()
        return _ex
    if config.EXCHANGE == "bitget":
        if not (config.BITGET_API_KEY and config.BITGET_API_SECRET and config.BITGET_PASSPHRASE):
            raise TradeError("BITGET_API_KEY / BITGET_API_SECRET / BITGET_PASSPHRASE o'rnatilmagan")
        ex = ccxt.bitget({
            "apiKey": config.BITGET_API_KEY,
            "secret": config.BITGET_API_SECRET,
            "password": config.BITGET_PASSPHRASE,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
    else:
        if not (config.BINANCE_API_KEY and config.BINANCE_API_SECRET):
            raise TradeError("BINANCE_API_KEY / BINANCE_API_SECRET o'rnatilmagan")
        ex = ccxt.binance({
            "apiKey": config.BINANCE_API_KEY,
            "secret": config.BINANCE_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if config.TESTNET:
            ex.set_sandbox_mode(True)
    ex.load_markets()
    _ex = ex
    return _ex


def mode() -> str:
    if not config.AUTO_TRADE:
        return "off"
    if is_paper():
        return "paper"
    return "testnet" if config.TESTNET else "LIVE"


def _settle(ex, order: dict, symbol: str) -> dict:
    """Ba'zi birjalar (Bitget) market buyurtmaga faqat ID qaytaradi — to'liq natijani so'rab olamiz."""
    if order.get("average") and order.get("filled"):
        return order
    oid = order.get("id")
    if not oid or not hasattr(ex, "fetch_order"):
        return order
    for _ in range(6):
        time.sleep(0.7)
        try:
            o = ex.fetch_order(oid, symbol)
        except Exception as e:  # noqa: BLE001
            log.warning("fetch_order(%s): %s", oid, e)
            continue
        if o.get("filled") and o.get("average"):
            return o
    return order


def _market_buy(ex, symbol: str, usdt: float, price: float) -> dict:
    """USDT summasiga market xarid. Bitget'da summa (cost) bilan, Binance'da miqdor bilan."""
    opts, has = getattr(ex, "options", None) or {}, getattr(ex, "has", None) or {}
    if opts.get("createMarketBuyOrderRequiresPrice") and has.get("createMarketBuyOrderWithCost"):
        order = ex.create_market_buy_order_with_cost(symbol, usdt)
    else:
        amount = float(ex.amount_to_precision(symbol, usdt / price))
        order = ex.create_order(symbol, "market", "buy", amount)
    return _settle(ex, order, symbol)


def balance(quote: str | None = None) -> dict:
    quote = quote or config.TRADE_QUOTE
    b = exchange().fetch_balance()
    return {
        "quote": quote,
        "free": float(b["free"].get(quote, 0) or 0),
        "total": float(b["total"].get(quote, 0) or 0),
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
    """Birjaning shu juftlik uchun minimal buyurtma qiymati (USDT)."""
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
    fees = [f for f in (order.get("fees") or ([order["fee"]] if order.get("fee") else []))
            if f and f.get("cost")]
    if not fees:
        # Komissiya haqida ma'lumot kelmadi — ehtiyot uchun 0.1% ayiramiz,
        # aks holda sotishda hisobdagi boshqa tangalarga tegib ketishi mumkin.
        return max(filled * 0.999, 0.0)
    for f in fees:
        if f.get("currency") == base:
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


def _close(symbol: str, pos: dict, price: float, note: str, share: float = 1.0) -> dict:
    """Pozitsiyani (yoki uning share qismini) sotadi."""
    ex = exchange()
    partial = share < 0.999
    want = float(pos["amount"]) * share
    amount = _sellable(ex, symbol, want)
    order = _settle(ex, ex.create_order(symbol, "market", "sell", amount), symbol)
    fill = float(order.get("average") or price)
    proceeds = float(order.get("cost") or fill * amount)
    # Sotishdagi komissiya USDT'dan olinadi — sof tushumni hisoblaymiz
    for f in (order.get("fees") or ([order["fee"]] if order.get("fee") else [])):
        if f and f.get("currency") == config.TRADE_QUOTE and f.get("cost"):
            proceeds -= float(f["cost"])
    total_cost = float(pos.get("cost") or pos["entry"] * pos["amount"])
    frac = min(1.0, amount / float(pos["amount"])) if pos["amount"] else 1.0
    spent = total_cost * (frac if partial else 1.0)
    pnl = proceeds - spent
    store.save_trade(
        symbol=symbol, side="sell", amount=amount, price=fill, cost=proceeds,
        status=order.get("status", "closed"), order_id=str(order.get("id", "")),
        mode=mode(), note=note, pnl=round(pnl, 4),
    )
    if partial:
        pos = {**pos, "amount": float(pos["amount"]) - amount, "cost": total_cost - spent, "partial": 1}
        _positions[symbol] = pos
        store.save_position(symbol, pos)
    else:
        _positions.pop(symbol, None)
        store.delete_position(symbol)
    log.info("SOTILDI %s %s @ %s, PnL %.2f (%s)", amount, symbol, fill, pnl, note)
    return {"symbol": symbol, "side": "sell", "amount": amount, "price": fill, "cost": proceeds,
            "pnl": round(pnl, 2), "mode": mode(), "order_id": str(order.get("id", "")),
            "partial": partial}


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
        order = _market_buy(ex, symbol, config.TRADE_AMOUNT_USDT, signal.price)
        entry = float(order.get("average") or signal.price)
        cost = float(order.get("cost") or config.TRADE_AMOUNT_USDT)
        held = _filled_base(order, symbol, cost / entry)
        # Signal o'z TP/SL ini bergan bo'lsa (ATR asosida) — shu masofalar ishlatiladi
        tp_pct, sl_pct, atr_based = config.TAKE_PROFIT_PCT / 100, config.STOP_LOSS_PCT / 100, False
        try:
            sp = float(signal.price)
            t_ = float(getattr(signal, "take_profit", 0) or 0) / sp - 1
            s_ = 1 - float(getattr(signal, "stop_loss", 0) or 0) / sp
            if 0.002 < t_ < 0.5 and 0.002 < s_ < 0.3:
                tp_pct, sl_pct, atr_based = t_, s_, True
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        pos = {
            "amount": held, "entry": entry, "cost": cost,
            # TP/SL haqiqiy kirish narxidan hisoblanadi
            "tp": entry * (1 + tp_pct),
            "sl": entry * (1 - sl_pct),
            # qisman foyda va trailing — TP masofasining ~55% ida
            "tp1": entry * (1 + max(tp_pct * 0.55, config.PARTIAL_TP_PCT / 100 if not atr_based else 0.004)),
            "trail": max(config.TRAILING_STOP_PCT / 100, sl_pct * 0.6) if atr_based else config.TRAILING_STOP_PCT / 100,
            "mode": mode(), "ts": datetime.now(timezone.utc).isoformat(),
            "peak": entry, "partial": 0,
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


def manage(pos: dict, price: float) -> tuple[str | None, dict]:
    """Bitta pozitsiya uchun qaror: (harakat, yangilangan pozitsiya).

    harakat: None | "partial" (TP1 — qisman foyda) | "TP" | "SL" | "Trailing SL" | "Breakeven"
      * narx eng yuqori nuqtasi (peak) kuzatiladi;
      * +PARTIAL_TP_PCT da PARTIAL_TP_SHARE qismi sotiladi, stop kirish narxiga ko'chadi (zararsiz);
      * +TRAIL_ACTIVATE_PCT dan keyin stop narx ortidan TRAILING_STOP_PCT masofada ergashadi.
    """
    pos = dict(pos)
    entry = float(pos["entry"])
    pos["peak"] = max(float(pos.get("peak") or entry), price)
    moved = None
    tp1 = float(pos.get("tp1") or entry * (1 + config.PARTIAL_TP_PCT / 100))
    act = float(pos.get("tp1") or entry * (1 + config.TRAIL_ACTIVATE_PCT / 100))
    dist = float(pos.get("trail") or config.TRAILING_STOP_PCT / 100)
    if config.TRAILING_STOP_PCT > 0 and pos["peak"] >= act:
        trail = pos["peak"] * (1 - dist)
        if trail > pos["sl"]:
            pos["sl"], moved = trail, "Trailing SL"
    if price >= pos["tp"]:
        return "TP", pos
    if price <= pos["sl"]:
        if moved == "Trailing SL" or pos["sl"] > entry * 1.0005:
            return ("Trailing SL" if pos["sl"] > entry * 1.003 else "Breakeven"), pos
        return "SL", pos
    if not pos.get("partial") and config.PARTIAL_TP_SHARE > 0 and price >= tp1:
        return "partial", pos
    return None, pos


def check_tp_sl(price_fn) -> list[dict]:
    """Ochiq pozitsiyalarni boshqaradi: qisman foyda, trailing stop, TP/SL."""
    load_state()
    closed = []
    with _lock:
        for symbol, pos in list(_positions.items()):
            price = price_fn(symbol)
            if price is None:
                continue
            action, new = manage(pos, price)
            try:
                if action == "partial":
                    res = _close(symbol, new, price, f"TP1: {config.PARTIAL_TP_SHARE * 100:.0f}% sotildi",
                                 share=config.PARTIAL_TP_SHARE)
                    p = _positions.get(symbol)
                    if p:   # qolgan qism uchun stop — kirish narxi (zararsiz)
                        p["sl"] = max(p["sl"], float(p["entry"]) * 1.002)
                        store.save_position(symbol, p)
                    closed.append({**res, "reason": f"TP1 +{(price / float(pos['entry']) - 1) * 100:.1f}% "
                                                    f"({config.PARTIAL_TP_SHARE * 100:.0f}% sotildi, stop zararsiz nuqtada)"})
                elif action:
                    res = _close(symbol, new, price, f"{action} hit")
                    closed.append({**res, "reason": action})
                elif new["peak"] != pos.get("peak") or new["sl"] != pos["sl"]:
                    _positions[symbol] = new
                    store.save_position(symbol, new)
            except Exception as e:  # noqa: BLE001
                log.error("Pozitsiyani boshqarishda xato %s: %s", symbol, e)
    return closed
