"""Telegram bot — signallar, tahlil, avto-savdo boshqaruvi (o'zbekcha)."""
from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes

import config
import data
import engine
import investors
import signals
import store
import trader

log = logging.getLogger("bot")

HELP = """*Signal Pro* — savdo signallari boti

/signal `SYMBOL` — bitta aktivni tahlil qilish (masalan `/signal BTC/USDT`, `/signal AAPL`, `/signal EUR/USD`)
/scan — barcha kuzatilayotgan aktivlarni tekshirish
/top — eng kuchli signallar
/watch `SYMBOL` — kuzatuvga qo'shish
/unwatch `SYMBOL` — kuzatuvdan olib tashlash
/list — kuzatuv ro'yxati
/news `SYMBOL` — aktiv bo'yicha yangiliklar
/investors — Baffet, Ekman, Dalio va b. portfeli (SEC 13F)
/balance — birja balansi
/positions — ochiq pozitsiyalar
/trades — oxirgi savdolar
/mode — avto-savdo rejimi
/stop — signallarni to'xtatish
/start — obuna bo'lish"""


def _admin(update: Update) -> bool:
    return not config.ADMIN_IDS or str(update.effective_user.id) in config.ADMIN_IDS


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    store.add_subscriber(update.effective_chat.id, u.username or u.first_name or "")
    await update.message.reply_text(
        f"Assalomu alaykum, {u.first_name or ''}! Siz signallarga obuna bo'ldingiz.\n\n" + HELP,
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    store.remove_subscriber(update.effective_chat.id)
    await update.message.reply_text("Obuna bekor qilindi. Qayta yoqish: /start")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)


async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Foydalanish: `/signal BTC/USDT`", parse_mode=ParseMode.MARKDOWN)
        return
    sym = ctx.args[0].upper()
    m = await update.message.reply_text(f"⏳ `{sym}` tahlil qilinmoqda...", parse_mode=ParseMode.MARKDOWN)
    try:
        sig = await asyncio.to_thread(signals.analyze, sym)
        store.save_signal(sig)
        await m.edit_text(sig.text(), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:  # noqa: BLE001
        await m.edit_text(f"❌ Xato: {e}")


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = await update.message.reply_text(
        "⏳ Barcha aktivlar tekshirilmoqda (kripto + aksiya + forex, bir daqiqagacha)...")
    try:
        res = await engine.scan_once(force_notify=False, full=True)
        act = [s for s in res if s.action != "HOLD"]
        if not act:
            by = {}
            for s in res:
                by[s.kind] = by.get(s.kind, 0) + 1
            detail = ", ".join(f"{v} {k}" for k, v in sorted(by.items()))
            await m.edit_text(
                f"Tekshirildi: {len(res)} ta aktiv ({detail}). Hozircha aniq signal yo'q — "
                "barchasi HOLD holatida.")
            return
        txt = "\n\n".join(s.text() for s in act[:8])
        await m.edit_text(txt, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:  # noqa: BLE001
        await m.edit_text(f"❌ Xato: {e}")


async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = store.recent_signals(15)
    rows = [r for r in rows if r["action"] != "HOLD"][:8]
    if not rows:
        await update.message.reply_text("Hali signal yo'q. /scan buyrug'ini bering.")
        return
    lines = [
        f"{'🟢' if r['action']=='BUY' else '🔴'} `{r['symbol']}` {r['action']} — "
        f"{r['price']:g} ({r['confidence']}%)"
        for r in rows
    ]
    await update.message.reply_text("*Oxirgi signallar*\n" + "\n".join(lines),
                                    parse_mode=ParseMode.MARKDOWN)


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Foydalanish: `/watch SOL/USDT`", parse_mode=ParseMode.MARKDOWN)
        return
    s = ctx.args[0].upper()
    store.add_watch(s)
    await update.message.reply_text(f"✅ `{s}` kuzatuvga qo'shildi", parse_mode=ParseMode.MARKDOWN)


async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return
    s = ctx.args[0].upper()
    store.remove_watch(s)
    await update.message.reply_text(f"🗑 `{s}` olib tashlandi", parse_mode=ParseMode.MARKDOWN)


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = (
        "*Kripto:* " + ", ".join(config.CRYPTO_SYMBOLS) +
        "\n*Aksiya:* " + ", ".join(config.STOCK_SYMBOLS) +
        "\n*Forex/Metall:* " + ", ".join(config.FOREX_SYMBOLS) +
        "\n*Qo'shimcha:* " + (", ".join(store.watchlist()) or "—")
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = " ".join(ctx.args) if ctx.args else "stock market"
    items = await asyncio.to_thread(investors.news, q, 6)
    if not items:
        await update.message.reply_text("Yangilik topilmadi.")
        return
    txt = f"*{q}* bo'yicha yangiliklar:\n\n" + "\n\n".join(
        f"• [{i['title'][:110]}]({i['link']})" for i in items)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN,
                                    disable_web_page_preview=True)


async def cmd_investors(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = await update.message.reply_text("⏳ SEC 13F hisobotlari yuklanmoqda...")
    funds = await asyncio.to_thread(investors.all_funds)
    if not funds:
        await m.edit_text("Ma'lumot olinmadi.")
        return
    parts = []
    for f in funds[:6]:
        top = ", ".join(f"{h['name'][:18]} ({h['pct']}%)" for h in f["top"][:5])
        parts.append(f"*{f['fund']}*\n_{f['filed']}_ · {f['positions']} pozitsiya\n{top}")
    await m.edit_text("\n\n".join(parts), parse_mode=ParseMode.MARKDOWN)


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _admin(update):
        return
    try:
        b = await asyncio.to_thread(trader.balance)
        assets = ", ".join(f"{k}: {v:g}" for k, v in list(b["assets"].items())[:10]) or "—"
        await update.message.reply_text(
            f"*{config.EXCHANGE.capitalize()} balans* ({trader.mode()})\n{b['quote']} bo'sh: `{b['free']:g}`\n{assets}",
            parse_mode=ParseMode.MARKDOWN)
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"❌ {e}")


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pos = trader.positions()
    if not pos:
        await update.message.reply_text("Ochiq pozitsiya yo'q.")
        return
    lines = []
    for s, p in pos.items():
        cur = await asyncio.to_thread(data.last_price, s)
        pnl = (cur - p["entry"]) * p["amount"] if cur else 0
        lines.append(f"`{s}` {p['amount']:g} @ {p['entry']:g} → {cur or '?'} | PnL `{pnl:+.2f}`")
    await update.message.reply_text("*Ochiq pozitsiyalar*\n" + "\n".join(lines),
                                    parse_mode=ParseMode.MARKDOWN)


async def cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = store.recent_trades(10)
    if not rows:
        await update.message.reply_text("Savdolar yo'q.")
        return
    lines = [f"{'🟢' if t['side']=='buy' else '🔴'} `{t['symbol']}` {t['side']} "
             f"{t['amount']:g} @ {t['price']:g} ({t['mode']})" for t in rows]
    await update.message.reply_text("*Oxirgi savdolar*\n" + "\n".join(lines),
                                    parse_mode=ParseMode.MARKDOWN)


async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = trader.mode()
    warn = "\n\n⚠️ *DIQQAT: real pul bilan savdo yoqilgan!*" if m == "LIVE" else ""
    await update.message.reply_text(
        f"Birja: *{config.EXCHANGE}*\nAvto-savdo rejimi: *{m}*\nSumma: `{config.TRADE_AMOUNT_USDT}` USDT / savdo\n"
        f"TP `{config.TAKE_PROFIT_PCT}%` · SL `{config.STOP_LOSS_PCT}%` · "
        f"max {config.MAX_OPEN_POSITIONS} pozitsiya\n"
        f"Bugungi PnL: `{trader.daily_pnl():+.2f}` USDT (limit -{config.DAILY_LOSS_LIMIT_USDT:g}){warn}",
        parse_mode=ParseMode.MARKDOWN)


def build_app() -> Application:
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    handlers = {
        "start": cmd_start, "help": cmd_help, "stop": cmd_stop, "signal": cmd_signal,
        "scan": cmd_scan, "top": cmd_top, "watch": cmd_watch, "unwatch": cmd_unwatch,
        "list": cmd_list, "news": cmd_news, "investors": cmd_investors,
        "balance": cmd_balance, "positions": cmd_positions, "trades": cmd_trades,
        "mode": cmd_mode,
    }
    for name, fn in handlers.items():
        app.add_handler(CommandHandler(name, fn))

    async def _notify(text: str):
        for chat_id in store.subscribers():
            try:
                await app.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN,
                                           disable_web_page_preview=True)
            except BadRequest:
                # Markdown buzilgan bo'lsa (`_`, `*` belgilar) — oddiy matn sifatida
                try:
                    await app.bot.send_message(chat_id, text, disable_web_page_preview=True)
                except Exception as e:  # noqa: BLE001
                    log.warning("send(%s): %s", chat_id, e)
            except Exception as e:  # noqa: BLE001
                log.warning("send(%s): %s", chat_id, e)

    engine.notifier = _notify

    async def _post_init(a: Application):
        await a.bot.set_my_commands([
            BotCommand("signal", "Bitta aktivni tahlil qilish"),
            BotCommand("scan", "Barchasini tekshirish"),
            BotCommand("top", "Oxirgi signallar"),
            BotCommand("investors", "Yirik investorlar portfeli"),
            BotCommand("news", "Yangiliklar"),
            BotCommand("positions", "Ochiq pozitsiyalar"),
            BotCommand("balance", "Birja balansi"),
            BotCommand("mode", "Avto-savdo rejimi"),
            BotCommand("help", "Yordam"),
        ])

    app.post_init = _post_init
    return app
