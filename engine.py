"""Skaner dvigateli: davriy tahlil → signal saqlash → Telegram'ga yuborish → avto-savdo."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging

import config
import data
import ai
import econ
import market
import signals
import store
import trader

log = logging.getLogger("engine")

# Telegram bot tomonidan o'rnatiladi (broadcast funksiyasi)
notifier = None
last_scan: list[dict] = []
_cycle = 0


direct = None   # telegram_bot o'rnatadi: async (chat_id, text)


async def send_to(chat_id, text: str):
    """Bitta foydalanuvchiga xabar (narx ogohlantirishi va h.k.)."""
    if direct is None:
        return
    try:
        await direct(chat_id, text)
    except Exception as e:  # noqa: BLE001
        log.warning("send_to(%s) xato: %s", chat_id, e)


async def broadcast(text: str):
    if notifier is None:
        return
    try:
        await notifier(text)
    except Exception as e:  # noqa: BLE001
        log.warning("broadcast xato: %s", e)


def symbols_to_scan() -> list[str]:
    """Kripto har safar; aksiya/forex esa har TD_EVERY_N_SCANS siklda bir marta."""
    syms = list(config.CRYPTO_SYMBOLS)
    if not config.TWELVE_DATA_KEY:
        td_turn = False
    else:
        td_turn = _cycle % max(1, config.TD_EVERY_N_SCANS) == 0
    if td_turn:
        syms += list(config.STOCK_SYMBOLS) + list(config.FOREX_SYMBOLS)
    syms += [s for s in store.watchlist() if td_turn or data.asset_class(s) == "crypto"]
    return list(dict.fromkeys(syms))


async def scan_once(force_notify: bool = False, full: bool = False) -> list[signals.Signal]:
    """full=True — qo'lda so'ralgan skan: aksiya va forex ham albatta tekshiriladi."""
    global _cycle
    if full:
        syms = list(dict.fromkeys(data.all_symbols() + store.watchlist()))
    else:
        syms = symbols_to_scan()
        _cycle += 1
    found = await asyncio.to_thread(signals.scan, syms)
    global last_scan
    last_scan = [s.dict() for s in found]

    for sig in found:
        if sig.action == "HOLD":
            continue
        # Takroriy xabarlarning oldini olish: bir xil signal SIGNAL_COOLDOWN_H ichida qayta yuborilmaydi
        last = store.last_signal(sig.symbol)
        if last and last["action"] == sig.action and not force_notify:
            try:
                age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last["created_at"])).total_seconds() / 3600
            except ValueError:
                age_h = 1e9
            if age_h < config.SIGNAL_COOLDOWN_H:
                continue
        store.save_signal(sig)
        msg = sig.text()
        block = None
        if config.AUTO_TRADE and sig.kind == "crypto" and sig.action == "BUY":
            block = (await asyncio.to_thread(econ.pause_reason)
                     or await asyncio.to_thread(market.buy_block_reason, sig.symbol))
            if block:
                msg += f"\n\n⏸ Avto-savdo o'tkazilmadi: {block}"
            elif ai.enabled() and config.AI_TRADE_FILTER:
                try:
                    ok, why = await asyncio.to_thread(ai.review_trade, sig)
                    if ok:
                        msg += f"\n\n🧠 AI tasdiqladi: {why}"
                    else:
                        block = f"AI rad etdi — {why}"
                        msg += f"\n\n⏸ Avto-savdo o'tkazilmadi: {block}"
                except Exception as e:  # noqa: BLE001
                    # AI ishlamasa savdo to'xtamaydi — oddiy filtrlar bilan davom etadi
                    log.warning("AI tekshiruvi: %s", e)
                    msg += f"\n\n🧠 AI tekshiruvi ishlamadi ({str(e)[:80]}) — oddiy filtrlar bilan davom etildi"
        if config.AUTO_TRADE and sig.kind == "crypto" and not block:
            try:
                res = await asyncio.to_thread(trader.execute, sig)
                msg += f"\n\n🤖 Avto-savdo ({res['mode']}): {res['side']} {res['amount']:g} @ {res['price']:g}"
            except trader.NoPosition:
                pass  # SELL signali, lekin pozitsiya yo'q — faqat signal yuboriladi
            except trader.TradeError as e:
                msg += f"\n\n⚠️ Avto-savdo o'tkazilmadi: {e}"
            except Exception as e:  # noqa: BLE001
                log.error("avto-savdo xato: %s", e)
                msg += f"\n\n❌ Savdo xatosi: {e}"
        await broadcast(msg)

    return found


async def tp_sl_loop():
    """Ochiq pozitsiyalarni har daqiqada tekshiradi (skanerdan mustaqil)."""
    if not config.AUTO_TRADE:
        return
    await asyncio.sleep(20)
    while True:
        try:
            if trader.positions():
                closed = await asyncio.to_thread(trader.check_tp_sl, data.last_price)
                for c in closed:
                    head = "💰 Qisman foyda olindi" if c.get("partial") else "📌 Pozitsiya yopildi"
                    await broadcast(
                        f"{head}: `{c['symbol']}` ({c['reason']}) "
                        f"narx `{c['price']:g}`, PnL `{c['pnl']:+.2f}` USDT"
                    )
        except Exception as ex:  # noqa: BLE001
            log.error("tp_sl_loop xato: %s", ex)
        await asyncio.sleep(60)


async def loop():
    log.info("Skaner ishga tushdi — har %s soniyada", config.SCAN_INTERVAL_SEC)
    await asyncio.sleep(5)
    while True:
        try:
            await scan_once()
        except Exception as e:  # noqa: BLE001
            log.error("scan_once xato: %s", e)
        await asyncio.sleep(config.SCAN_INTERVAL_SEC)
