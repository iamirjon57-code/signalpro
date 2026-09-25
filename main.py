"""Signal Pro — bitta jarayonda web dashboard + Telegram bot + skaner.

Railway: `python main.py`
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn

import config
import engine
import store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
# httpx har bir so'rov URL'ini log qiladi — Telegram tokeni logda ko'rinib qolmasligi uchun o'chiriladi
for _noisy in ("httpx", "httpcore", "telegram.ext.Updater"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

log = logging.getLogger("main")


async def run_web():
    from webapp import app
    cfg = uvicorn.Config(app, host="0.0.0.0", port=config.PORT, log_level="info",
                         access_log=False)
    await uvicorn.Server(cfg).serve()


async def run_bot():
    if not config.TELEGRAM_TOKEN:
        log.warning("TELEGRAM_TOKEN yo'q — bot o'chiq, faqat web ishlaydi")
        return
    from telegram_bot import build_app
    app = build_app()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram bot ishga tushdi")
    try:
        await asyncio.Event().wait()
    finally:
        with contextlib.suppress(Exception):
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


async def amain():
    store.conn()
    log.info("Rejim: avto-savdo=%s, testnet=%s", config.AUTO_TRADE, config.BINANCE_TESTNET)
    if config.AUTO_TRADE:
        from trader import load_state
        try:
            load_state()
        except Exception as e:  # noqa: BLE001
            log.error("Pozitsiyalarni tiklashda xato: %s", e)
        if not config.BINANCE_TESTNET:
            log.warning("DIQQAT: REAL PUL bilan avto-savdo yoqilgan")
    await asyncio.gather(run_web(), run_bot(), engine.loop(), engine.tp_sl_loop())


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
