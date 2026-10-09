"""FastAPI web dashboard + JSON API."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

import config
import data
import engine
import investors
import signals
import store
import trader
import setup_page
import dex
from indicators import enrich

log = logging.getLogger("web")
BASE = Path(__file__).parent

app = FastAPI(title="Signal Pro", docs_url="/api/docs", redoc_url=None)


def _auth(request: Request):
    if not config.DASHBOARD_PASSWORD:
        return
    token = request.headers.get("x-token") or request.query_params.get("key")
    if token != config.DASHBOARD_PASSWORD:
        raise HTTPException(401, "Kalit noto'g'ri")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (BASE / "index.html").read_text(encoding="utf-8")


# ---------- Brauzer orqali sozlash ----------

@app.get("/setup", response_class=HTMLResponse)
async def setup_ui():
    if not setup_page.enabled():
        raise HTTPException(404, "Sozlash sahifasi o'chirilgan")
    return setup_page.PAGE


@app.get("/api/setup/status")
async def setup_status():
    if not setup_page.enabled():
        raise HTTPException(404)
    return {"fields": setup_page.status()}


@app.post("/api/setup")
async def setup_save(request: Request):
    if not setup_page.enabled():
        raise HTTPException(404)
    body = await request.json()
    if not setup_page.check_token(body.get("token", "")):
        raise HTTPException(401, "Sozlash kaliti noto'g'ri")
    values = body.get("values") or {}
    if not isinstance(values, dict):
        raise HTTPException(400, "Noto'g'ri format")
    n = setup_page.write_env(values)
    if n:
        setup_page.restart_service()
    return {"updated": n}


@app.post("/api/setup/close")
async def setup_close(request: Request):
    if not setup_page.enabled():
        raise HTTPException(404)
    body = await request.json()
    if not setup_page.check_token(body.get("token", "")):
        raise HTTPException(401, "Sozlash kaliti noto'g'ri")
    setup_page.close_setup()
    setup_page.restart_service()
    return {"closed": True}


@app.get("/health")
async def health():
    import ai
    return {"ok": True, "auto_trade": trader.mode(), "symbols": len(engine.symbols_to_scan()),
            "ai": ai.enabled()}


@app.get("/api/config")
async def api_config():
    return {
        "crypto": config.CRYPTO_SYMBOLS,
        "stocks": config.STOCK_SYMBOLS,
        "forex": config.FOREX_SYMBOLS,
        "watch": store.watchlist(),
        "timeframe": config.TIMEFRAME,
        "mode": trader.mode(),
        "scan_interval": config.SCAN_INTERVAL_SEC,
        "protected": bool(config.DASHBOARD_PASSWORD),
    }


@app.get("/api/signals")
async def api_signals(limit: int = 50, action: str | None = None):
    return {"live": engine.last_scan, "history": store.recent_signals(limit, action)}


@app.get("/api/analyze")
async def api_analyze(symbol: str = Query(...), timeframe: str | None = None):
    try:
        sig = await asyncio.to_thread(signals.analyze, symbol.upper(), timeframe)
        return sig.dict()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.get("/api/chart")
async def api_chart(symbol: str = Query(...), timeframe: str | None = None, limit: int = 200):
    try:
        df = enrich(await asyncio.to_thread(data.fetch, symbol.upper(), timeframe, limit))
        df = df.tail(limit)
        return {
            "symbol": symbol.upper(),
            "time": [t.isoformat() for t in df["time"]],
            "close": df["close"].round(6).tolist(),
            "ma_fast": df["ma_fast"].round(6).where(df["ma_fast"].notna(), None).tolist(),
            "ma_slow": df["ma_slow"].round(6).where(df["ma_slow"].notna(), None).tolist(),
            "rsi": df["rsi"].round(2).tolist(),
            "macd_hist": df["macd_hist"].round(6).tolist(),
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.post("/api/scan")
async def api_scan(request: Request):
    _auth(request)
    res = await engine.scan_once(full=True)
    return {"count": len(res), "signals": [s.dict() for s in res]}


@app.get("/api/investors")
async def api_investors():
    return {"funds": await asyncio.to_thread(investors.all_funds)}


@app.get("/api/news")
async def api_news(q: str = "stock market", limit: int = 8):
    return {"items": await asyncio.to_thread(investors.news, q, limit)}


@app.get("/api/trades")
async def api_trades(request: Request, limit: int = 50):
    _auth(request)
    return {"trades": store.recent_trades(limit), "positions": trader.positions(),
            "mode": trader.mode()}


@app.get("/api/balance")
async def api_balance(request: Request):
    _auth(request)
    try:
        return await asyncio.to_thread(trader.balance)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/stats")
async def api_stats():
    return store.stats()


# ---------- DEX (DexScreener) ----------

@app.get("/api/dex/trending")
async def api_dex_trending(refresh: bool = False):
    if refresh or not dex.last_scan:
        try:
            await asyncio.to_thread(dex.scan_trending)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"DexScreener: {e}")
    return {"at": dex.last_scan_at, "items": dex.last_scan[:60], "mode": dex.mode(),
            "filters": {"min_liquidity": config.DEX_MIN_LIQUIDITY, "min_volume": config.DEX_MIN_VOLUME,
                        "min_age_h": config.DEX_MIN_AGE_H, "min_score": config.DEX_MIN_SCORE,
                        "chains": config.DEX_CHAINS}}


@app.get("/api/dex/check")
async def api_dex_check(q: str = Query(...)):
    try:
        pairs = await asyncio.to_thread(dex.lookup, q)
        items = await asyncio.to_thread(lambda: [dex.evaluate(p) for p in pairs[:5]])
        return {"items": items}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.get("/api/dex/trades")
async def api_dex_trades(request: Request, limit: int = 50):
    _auth(request)
    return {"positions": dex.positions(), "trades": dex.trades(limit), "stats": dex.stats()}


# ---------- Strategiya testi ----------

@app.get("/api/backtest")
async def api_backtest():
    import strategies
    res = strategies.load() or {}
    slim = {"ts": res.get("ts"), "symbols": {}}
    for sym, s in (res.get("symbols") or {}).items():
        slim["symbols"][sym] = {k: s.get(k) for k in ("bars", "from", "to", "error", "best", "classic")}
        slim["symbols"][sym]["top"] = [{k: r[k] for k in ("strategy", "exit", "ok", "score", "all", "oos")}
                                       for r in s.get("top") or []]
    return slim


# ---------- Telegram webhook ----------

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    import telegram_bot as tb
    from telegram import Update
    if tb.APP is None:
        raise HTTPException(503, "bot hali tayyor emas")
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != tb.webhook_secret():
        raise HTTPException(403)
    data = await request.json()
    await tb.APP.update_queue.put(Update.de_json(data, tb.APP.bot))
    return {"ok": True}
