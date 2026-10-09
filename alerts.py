"""Narx ogohlantirishlari va shaxsiy investitsiya portfeli (har bir foydalanuvchi uchun alohida)."""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from datetime import datetime, timezone

import config
import data
import store

log = logging.getLogger("alerts")
_lock = threading.RLock()
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, symbol TEXT, op TEXT, price REAL,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS portfolio (
  chat_id TEXT, symbol TEXT, amount REAL, avg_price REAL, updated_at TEXT,
  PRIMARY KEY (chat_id, symbol)
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(s: str) -> float:
    s = s.strip().lower().replace(" ", "").replace("$", "")
    s = re.sub(r",(?=\d{3}(\D|$))", "", s)   # 3,500 -> 3500 (minglik ajratgich)
    s = s.replace(",", ".")                     # 3,5 -> 3.5
    mult = 1.0
    if s.endswith("k"):
        mult, s = 1e3, s[:-1]
    elif s.endswith("m"):
        mult, s = 1e6, s[:-1]
    return float(s) * mult


def norm_symbol(s: str) -> str:
    """BTC -> BTC/USDT (kripto), AAPL -> AAPL (aksiya), EURUSD -> EUR/USD."""
    s = s.strip().upper()
    if "/" in s:
        return s
    if s in config.STOCK_SYMBOLS:
        return s
    if len(s) == 6 and s[:3] in ("EUR", "GBP", "USD", "XAU", "AUD", "NZD", "CAD", "CHF", "JPY"):
        return f"{s[:3]}/{s[3:]}"
    try:
        ex = data.public_exchange()
        if not ex.markets:
            ex.load_markets()
        if f"{s}/USDT" in ex.markets:
            return f"{s}/USDT"
    except Exception:  # noqa: BLE001
        pass
    return s


# ---------------- Narx ogohlantirishlari ----------------

def parse_alert(text: str) -> tuple[str, float] | None:
    """'BTC 90000', 'ETH/USDT 3.5k', 'SOL > 250' -> (symbol, narx)."""
    m = re.match(r"^\s*([A-Za-z0-9]{2,12}(?:/[A-Za-z]{3,5})?)\s*[<>=]*\s*\$?([\d.,]+\s*[kKmM]?)\s*$", text or "")
    if not m:
        return None
    try:
        return norm_symbol(m.group(1)), _num(m.group(2))
    except ValueError:
        return None


def add_alert(chat_id, symbol: str, price: float) -> dict:
    cur = data.last_price(symbol)
    if cur is None:
        raise ValueError(f"{symbol} narxini olib bo'lmadi — nomini tekshiring")
    op = ">=" if price >= cur else "<="
    with _lock:
        c = _db()
        n = c.execute("SELECT COUNT(*) FROM price_alerts WHERE chat_id=?", (str(chat_id),)).fetchone()[0]
        if n >= config.MAX_ALERTS:
            raise ValueError(f"Ko'pi bilan {config.MAX_ALERTS} ta ogohlantirish — eskisini o'chiring")
        c.execute("INSERT INTO price_alerts (chat_id,symbol,op,price,created_at) VALUES (?,?,?,?,?)",
                  (str(chat_id), symbol, op, price, _now()))
        c.commit()
    return {"symbol": symbol, "op": op, "price": price, "current": cur}


def list_alerts(chat_id) -> list[dict]:
    return [dict(r) for r in _db().execute(
        "SELECT * FROM price_alerts WHERE chat_id=? ORDER BY id", (str(chat_id),)).fetchall()]


def delete_alert(chat_id, alert_id: int) -> bool:
    with _lock:
        c = _db()
        n = c.execute("DELETE FROM price_alerts WHERE id=? AND chat_id=?", (alert_id, str(chat_id))).rowcount
        c.commit()
    return n > 0


def check_alerts() -> list[tuple[str, str]]:
    """Yetgan ogohlantirishlar: [(chat_id, matn)] — va ular o'chiriladi."""
    rows = [dict(r) for r in _db().execute("SELECT * FROM price_alerts").fetchall()]
    prices: dict[str, float | None] = {}
    out = []
    for r in rows:
        sym = r["symbol"]
        if sym not in prices:
            prices[sym] = data.last_price(sym)
        p = prices[sym]
        if p is None:
            continue
        if (r["op"] == ">=" and p >= r["price"]) or (r["op"] == "<=" and p <= r["price"]):
            arrow = "📈 ko'tarilib" if r["op"] == ">=" else "📉 tushib"
            out.append((r["chat_id"], f"🔔 *Narx ogohlantirishi*\n`{sym}` {arrow} `{r['price']:g}` ga yetdi.\n"
                                      f"Hozirgi narx: `{p:g}`"))
            with _lock:
                c = _db()
                c.execute("DELETE FROM price_alerts WHERE id=?", (r["id"],))
                c.commit()
    return out


# ---------------- Portfel ----------------

def parse_holding(text: str) -> tuple[str, float, float | None] | None:
    """'BTC 0.05 60000' yoki 'AAPL 10' -> (symbol, miqdor, o'rtacha narx)."""
    parts = (text or "").split()
    if len(parts) not in (2, 3) or not re.match(r"^[A-Za-z0-9/]{2,12}$", parts[0]):
        return None
    try:
        amt = _num(parts[1])
        price = _num(parts[2]) if len(parts) == 3 else None
    except ValueError:
        return None
    return norm_symbol(parts[0]), amt, price


def add_holding(chat_id, symbol: str, amount: float, price: float | None) -> dict:
    if price is None:
        price = data.last_price(symbol)
        if price is None:
            raise ValueError(f"{symbol} narxini olib bo'lmadi")
    with _lock:
        c = _db()
        r = c.execute("SELECT amount, avg_price FROM portfolio WHERE chat_id=? AND symbol=?",
                      (str(chat_id), symbol)).fetchone()
        if r:   # qo'shimcha xarid — o'rtacha narx qayta hisoblanadi
            tot = r["amount"] + amount
            avg = (r["amount"] * r["avg_price"] + amount * price) / tot if tot else price
        else:
            tot, avg = amount, price
        c.execute("INSERT OR REPLACE INTO portfolio VALUES (?,?,?,?,?)", (str(chat_id), symbol, tot, avg, _now()))
        c.commit()
    return {"symbol": symbol, "amount": tot, "avg": avg}


def remove_holding(chat_id, symbol: str) -> bool:
    with _lock:
        c = _db()
        n = c.execute("DELETE FROM portfolio WHERE chat_id=? AND symbol=?", (str(chat_id), symbol)).rowcount
        c.commit()
    return n > 0


def holdings(chat_id) -> list[dict]:
    rows = [dict(r) for r in _db().execute(
        "SELECT * FROM portfolio WHERE chat_id=? ORDER BY symbol", (str(chat_id),)).fetchall()]
    total = 0.0
    for r in rows:
        p = data.last_price(r["symbol"])
        r["price"] = p
        r["value"] = (p or r["avg_price"]) * r["amount"]
        r["cost"] = r["avg_price"] * r["amount"]
        r["pnl"] = r["value"] - r["cost"]
        r["pnl_pct"] = (r["value"] / r["cost"] - 1) * 100 if r["cost"] else 0
        total += r["value"]
    for r in rows:
        r["share"] = r["value"] / total * 100 if total else 0
    return rows


def portfolio_text(chat_id) -> str:
    rows = holdings(chat_id)
    if not rows:
        return ("🗂 *Portfel bo'sh.*\n\nQo'shish uchun yozing: `/port BTC 0.05 60000`\n"
                "(aktiv, miqdor, o'rtacha xarid narxi; narxni yozmasangiz — hozirgi narx olinadi)")
    total = sum(r["value"] for r in rows)
    cost = sum(r["cost"] for r in rows)
    lines = ["*🗂 Investitsiya portfeli*", ""]
    for r in sorted(rows, key=lambda x: -x["value"]):
        icon = "🟢" if r["pnl"] >= 0 else "🔴"
        price = f"{r['price']:g}" if r["price"] else "?"
        lines.append(f"{icon} `{r['symbol']}` {r['amount']:g} × {price} = ${r['value']:,.2f} "
                     f"({r['pnl_pct']:+.1f}%, {r['share']:.0f}%)")
    pnl = total - cost
    lines += ["", f"*Jami:* ${total:,.2f} · foyda/zarar {pnl:+,.2f}$ ({(total / cost - 1) * 100 if cost else 0:+.1f}%)"]
    top = max(rows, key=lambda x: x["share"])
    if top["share"] >= 60 and len(rows) > 1:
        lines.append(f"⚠️ Portfelning {top['share']:.0f}% bitta aktivda ({top['symbol']}) — xavf yuqori.")
    lines.append("\nO'chirish: `/port del BTC` · Qo'shish: `/port ETH 1.2 3000`")
    return "\n".join(lines)


def portfolio_context(chat_id) -> str:
    rows = holdings(chat_id)
    return "\n".join(f"- {r['symbol']}: {r['amount']:g} dona, o'rtacha {r['avg_price']:g}, hozir "
                     f"{r['price'] or '?'}, foyda {r['pnl_pct']:+.1f}%, portfelda {r['share']:.0f}%"
                     for r in rows) or "portfel bo'sh"


async def loop(send):
    await asyncio.sleep(30)
    while True:
        try:
            for chat_id, text in await asyncio.to_thread(check_alerts):
                await send(chat_id, text)
        except Exception as e:  # noqa: BLE001
            log.error("alerts loop: %s", e)
        await asyncio.sleep(60)
