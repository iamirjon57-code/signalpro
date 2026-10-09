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
    from telegram import Update
    from telegram_bot import build_app, webhook_secret
    app = build_app()
    await app.initialize()
    await app.start()
    use_webhook = config.TELEGRAM_MODE == "webhook" or (
        config.TELEGRAM_MODE == "auto" and bool(config.WEBHOOK_BASE))
    if not use_webhook:
        await app.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram bot ishga tushdi (polling)")
        try:
            await asyncio.Event().wait()
        finally:
            with contextlib.suppress(Exception):
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
        return

    host = config.WEBHOOK_BASE.replace("https://", "").replace("http://", "").strip("/")
    url = f"https://{host}/telegram/webhook"
    secret = webhook_secret()

    async def _set():
        await app.bot.set_webhook(url, secret_token=secret, allowed_updates=Update.ALL_TYPES,
                                  max_connections=20)

    await asyncio.sleep(3)   # sayt ishga tushib olsin
    await _set()
    log.info("Telegram bot ishga tushdi (webhook: %s)", url)
    try:
        while True:
            # Boshqa nusxa (eski server) webhook'ni o'chirib qo'ysa — qayta o'rnatamiz
            await asyncio.sleep(120)
            try:
                info = await app.bot.get_webhook_info()
                if info.url != url:
                    log.warning("Webhook o'zgartirilgan (%r) — boshqa bot nusxasi ishlayapti. Qayta o'rnatildi.",
                                info.url)
                    await _set()
            except Exception as e:  # noqa: BLE001
                log.warning("webhook tekshiruvi: %s", e)
    finally:
        with contextlib.suppress(Exception):
            await app.stop()
            await app.shutdown()


async def amain():
    store.conn()
    log.info("Birja: %s | avto-savdo=%s | sinov rejimi=%s",
             config.EXCHANGE, config.AUTO_TRADE, config.TESTNET)
    if config.AUTO_TRADE:
        from trader import load_state
        try:
            load_state()
        except Exception as e:  # noqa: BLE001
            log.error("Pozitsiyalarni tiklashda xato: %s", e)
        if not config.TESTNET:
            log.warning("DIQQAT: REAL PUL bilan avto-savdo yoqilgan")
    import dex
    import research
    await asyncio.gather(run_web(), run_bot(), engine.loop(), engine.tp_sl_loop(),
                         dex.loop(engine.broadcast), research.loop(engine.broadcast))


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
