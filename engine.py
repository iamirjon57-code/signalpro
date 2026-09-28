"""Skaner dvigateli: davriy tahlil → signal saqlash → Telegram'ga yuborish → avto-savdo."""
from __future__ import annotations

import asyncio
import logging

import config
import data
import signals
import store
import trader

log = logging.getLogger("engine")

# Telegram bot tomonidan o'rnatiladi (broadcast funksiyasi)
notifier = None
last_scan: list[dict] = []
_cycle = 0


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
        prev = store.last_action(sig.symbol)
        store.save_signal(sig)
        # Takroriy xabarlarning oldini olish: faqat signal o'zgarganda yuboriladi
        if prev == sig.action and not force_notify:
            continue
        msg = sig.text()
        if config.AUTO_TRADE and sig.kind == "crypto":
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
                    await broadcast(
                        f"📌 Pozitsiya yopildi: `{c['symbol']}` ({c['reason']}) "
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
