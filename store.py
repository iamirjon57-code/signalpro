"""SQLite saqlash: signallar, savdolar, obunachilar, kuzatuv ro'yxati."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT, kind TEXT, action TEXT, price REAL, score INT, confidence INT,
  rsi REAL, macd_hist REAL, trend TEXT, take_profit REAL, stop_loss REAL,
  reasons TEXT, timeframe TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT, side TEXT, amount REAL, price REAL, cost REAL,
  status TEXT, order_id TEXT, mode TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS subscribers (
  chat_id TEXT PRIMARY KEY, username TEXT, active INT DEFAULT 1, created_at TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
  symbol TEXT PRIMARY KEY, added_at TEXT
);
CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT PRIMARY KEY, amount REAL, entry REAL, tp REAL, sl REAL, mode TEXT, ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_sig_time ON signals(created_at DESC);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        # Eski bazalar uchun migratsiya
        for table, col in (("trades", "pnl"), ("positions", "cost"), ("positions", "peak"),
                           ("positions", "partial")):
            cols = {r["name"] for r in _conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} REAL")
        _conn.commit()
    return _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_signal(sig) -> int:
    d = sig.dict()
    d["reasons"] = json.dumps(d["reasons"], ensure_ascii=False)
    with _lock:
        c = conn().execute(
            """INSERT INTO signals (symbol,kind,action,price,score,confidence,rsi,macd_hist,
               trend,take_profit,stop_loss,reasons,timeframe,created_at)
               VALUES (:symbol,:kind,:action,:price,:score,:confidence,:rsi,:macd_hist,
               :trend,:take_profit,:stop_loss,:reasons,:timeframe,:created_at)""",
            d,
        )
        conn().commit()
        return c.lastrowid


def recent_signals(limit: int = 50, action: str | None = None) -> list[dict]:
    q = "SELECT * FROM signals"
    args: list = []
    if action:
        q += " WHERE action = ?"
        args.append(action.upper())
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = conn().execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["reasons"] = json.loads(d["reasons"] or "[]")
        except Exception:  # noqa: BLE001
            d["reasons"] = []
        out.append(d)
    return out


def last_action(symbol: str) -> str | None:
    r = conn().execute(
        "SELECT action FROM signals WHERE symbol=? ORDER BY id DESC LIMIT 1", (symbol,)
    ).fetchone()
    return r["action"] if r else None


def save_trade(**kw) -> int:
    kw.setdefault("created_at", _now())
    keys = ",".join(kw)
    ph = ",".join(f":{k}" for k in kw)
    with _lock:
        c = conn().execute(f"INSERT INTO trades ({keys}) VALUES ({ph})", kw)
        conn().commit()
        return c.lastrowid


def recent_trades(limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn().execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def add_subscriber(chat_id: str, username: str = ""):
    with _lock:
        conn().execute(
            "INSERT OR REPLACE INTO subscribers (chat_id,username,active,created_at) VALUES (?,?,1,?)",
            (str(chat_id), username, _now()),
        )
        conn().commit()


def remove_subscriber(chat_id: str):
    with _lock:
        conn().execute("UPDATE subscribers SET active=0 WHERE chat_id=?", (str(chat_id),))
        conn().commit()


def subscribers() -> list[str]:
    rows = conn().execute("SELECT chat_id FROM subscribers WHERE active=1").fetchall()
    ids = {r["chat_id"] for r in rows} | set(config.TELEGRAM_CHAT_IDS)
    return sorted(ids)


def add_watch(symbol: str):
    with _lock:
        conn().execute("INSERT OR IGNORE INTO watchlist VALUES (?,?)", (symbol.upper(), _now()))
        conn().commit()


def remove_watch(symbol: str):
    with _lock:
        conn().execute("DELETE FROM watchlist WHERE symbol=?", (symbol.upper(),))
        conn().commit()


def watchlist() -> list[str]:
    return [r["symbol"] for r in conn().execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()]


def save_position(symbol: str, pos: dict):
    with _lock:
        conn().execute(
            "INSERT OR REPLACE INTO positions (symbol,amount,entry,tp,sl,mode,ts,cost,peak,partial) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (symbol, pos["amount"], pos["entry"], pos["tp"], pos["sl"], pos.get("mode", ""),
             pos.get("ts", _now()), pos.get("cost"), pos.get("peak"), pos.get("partial") or 0),
        )
        conn().commit()


def delete_position(symbol: str):
    with _lock:
        conn().execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        conn().commit()


def load_positions() -> dict[str, dict]:
    rows = conn().execute("SELECT * FROM positions").fetchall()
    return {r["symbol"]: {"amount": r["amount"], "entry": r["entry"], "tp": r["tp"],
                          "sl": r["sl"], "mode": r["mode"], "ts": r["ts"],
                          "cost": r["cost"], "peak": r["peak"] or r["entry"],
                          "partial": int(r["partial"] or 0)} for r in rows}


def realized_pnl_since(day: str) -> float:
    """Berilgan sanadan (YYYY-MM-DD, UTC) beri yopilgan savdolarning jami PnL i."""
    r = conn().execute(
        "SELECT COALESCE(SUM(pnl),0) s FROM trades WHERE pnl IS NOT NULL AND created_at >= ?",
        (day,),
    ).fetchone()
    return float(r["s"] or 0)


def stats() -> dict:
    c = conn()
    return {
        "signals_total": c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"],
        "buy": c.execute("SELECT COUNT(*) n FROM signals WHERE action='BUY'").fetchone()["n"],
        "sell": c.execute("SELECT COUNT(*) n FROM signals WHERE action='SELL'").fetchone()["n"],
        "trades": c.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"],
        "open_positions": c.execute("SELECT COUNT(*) n FROM positions").fetchone()["n"],
        "realized_pnl": round(float(c.execute(
            "SELECT COALESCE(SUM(pnl),0) s FROM trades WHERE pnl IS NOT NULL").fetchone()["s"]), 2),
        "subscribers": c.execute("SELECT COUNT(*) n FROM subscribers WHERE active=1").fetchone()["n"],
    }
