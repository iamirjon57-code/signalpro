"""Telegram bot — signallar, tahlil, avto-savdo boshqaruvi (o'zbekcha)."""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config
import data
import dex
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
/dex `NOM yoki MANZIL` — DexScreener'dan tangani tekshirish (firibgarlik filtri bilan)
/stop — signallarni to'xtatish
/start — obuna bo'lish"""


# ---------------- Tugmalar ----------------

B_SIGNAL, B_SCAN = "📊 Signal", "🔍 Skaner"
B_TOP, B_NEWS = "🏆 Oxirgi signallar", "📰 Yangiliklar"
B_POS, B_TRADES = "💼 Pozitsiyalar", "📜 Savdolar"
B_BAL, B_MODE = "💰 Balans", "⚙️ Rejim"
B_INV, B_HELP = "🏦 Investorlar", "❓ Yordam"
B_DEX = "🦎 DEX tangalar"

MENU = ReplyKeyboardMarkup(
    [[B_SIGNAL, B_SCAN], [B_TOP, B_NEWS], [B_POS, B_TRADES], [B_BAL, B_MODE],
     [B_DEX, B_INV], [B_HELP]],
    resize_keyboard=True,
)

NEWS_TOPICS = [("₿ Kripto", "bitcoin crypto"), ("📈 Aksiyalar", "stock market"),
               ("💱 Forex", "forex dollar"), ("🥇 Oltin", "gold price")]


def _rows(items, per_row):
    return [items[i:i + per_row] for i in range(0, len(items), per_row)]


def symbol_picker() -> InlineKeyboardMarkup:
    """Aktiv tanlash tugmalari: kripto, aksiya, forex va qo'shimcha kuzatuv."""
    def btns(symbols):
        return [InlineKeyboardButton(s.replace("/USDT", ""), callback_data=f"sig:{s}") for s in symbols]
    rows = []
    for group, per_row in ((config.CRYPTO_SYMBOLS, 5), (config.STOCK_SYMBOLS, 5),
                           (config.FOREX_SYMBOLS, 4), (store.watchlist(), 4)):
        rows += _rows(btns(group), per_row)
    return InlineKeyboardMarkup(rows)


def signal_actions(sym: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yangilash", callback_data=f"sig:{sym}"),
        InlineKeyboardButton("⬅️ Boshqa aktiv", callback_data="pick"),
    ]])


def _admin(update: Update) -> bool:
    return not config.ADMIN_IDS or str(update.effective_user.id) in config.ADMIN_IDS


async def _only_admin(update: Update) -> bool:
    """Admin bo'lmasa xabar beradi va False qaytaradi."""
    if _admin(update):
        return True
    await update.effective_message.reply_text("🔒 Bu bo'lim faqat bot egasi uchun.")
    return False


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    store.add_subscriber(update.effective_chat.id, u.username or u.first_name or "")
    await update.message.reply_text(
        f"Assalomu alaykum, {u.first_name or ''}! Siz signallarga obuna bo'ldingiz.\n\n"
        "Pastdagi tugmalardan foydalaning 👇",
        reply_markup=MENU,
    )


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    store.remove_subscriber(update.effective_chat.id)
    await update.message.reply_text("Obuna bekor qilindi. Qayta yoqish: /start")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN, reply_markup=MENU)


async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "Qaysi aktivni tahlil qilay? Tanlang yoki nomini yozing (masalan `DOGE/USDT`, `GOOGL`):",
            parse_mode=ParseMode.MARKDOWN, reply_markup=symbol_picker())
        return
    sym = ctx.args[0].upper()
    m = await update.message.reply_text(f"⏳ `{sym}` tahlil qilinmoqda...", parse_mode=ParseMode.MARKDOWN)
    await _analyze_into(m, sym)


async def _analyze_into(m, sym: str):
    """Aktivni tahlil qilib, natijani berilgan xabarga yozadi (tugmalar bilan)."""
    try:
        sig = await asyncio.to_thread(signals.analyze, sym)
        store.save_signal(sig)
        text, kb = sig.text(), signal_actions(sym)
        try:
            await m.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return  # "Yangilash" bosildi, lekin natija o'zgarmagan
            await m.edit_text(text, reply_markup=kb)
    except Exception as e:  # noqa: BLE001
        await m.edit_text(f"❌ Xato: {e}", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Boshqa aktiv", callback_data="pick")]]))


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Xabar ostidagi tugmalar: aktiv tanlash, yangilash, yangiliklar mavzusi."""
    q = update.callback_query
    d = q.data or ""
    await q.answer()
    if d == "pick":
        await q.edit_message_text("Qaysi aktivni tahlil qilay?", reply_markup=symbol_picker())
    elif d.startswith("sig:"):
        sym = d[4:]
        try:
            await q.edit_message_text(f"⏳ {sym} tahlil qilinmoqda...")
        except BadRequest:
            pass
        await _analyze_into(q.message, sym)
    elif d.startswith("news:"):
        await _send_news(q.message, d[5:])
    elif d.startswith("dx:"):
        _, chain, address = d.split(":", 2)
        await _dex_token(q.message, chain, address, edit=True)
    elif d == "dex:trend":
        await _dex_trend(q.message)
    elif d in ("dex:pos", "dex:trades", "dex:cfg"):
        if not await _only_admin(update):
            return
        await {"dex:pos": _dex_positions, "dex:trades": _dex_trades, "dex:cfg": _dex_config}[d](q.message)


async def _send_news(message, query: str):
    items = await asyncio.to_thread(investors.news, query, 6)
    if not items:
        await message.reply_text("Yangilik topilmadi.")
        return
    lines = "\n\n".join(f"• [{i['title'][:110]}]({i['link']})" for i in items)
    try:
        await message.reply_text(f"*{query}* bo'yicha yangiliklar:\n\n" + lines,
                                 parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except BadRequest:  # sarlavhada Markdown'ni buzadigan belgi bo'lsa
        plain = "\n\n".join(f"• {i['title'][:110]}\n{i['link']}" for i in items)
        await message.reply_text(f"{query} bo'yicha yangiliklar:\n\n" + plain,
                                 disable_web_page_preview=True)


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
        await update.message.reply_text("Hali signal yo'q. «🔍 Skaner» tugmasini bosing.")
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
    if not ctx.args:
        kb = InlineKeyboardMarkup(_rows(
            [InlineKeyboardButton(t, callback_data=f"news:{q}") for t, q in NEWS_TOPICS], 2))
        await update.message.reply_text("Qaysi mavzu bo'yicha yangiliklar kerak?", reply_markup=kb)
        return
    await _send_news(update.message, " ".join(ctx.args))


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
    if not await _only_admin(update):
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
    if not await _only_admin(update):
        return
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
    if not await _only_admin(update):
        return
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


# ---------------- DEX (DexScreener) ----------------

async def _say(message, text: str, kb=None, edit: bool = False):
    """Markdown bilan yuboradi; belgilar buzsa — oddiy matn."""
    send = message.edit_text if edit else message.reply_text
    try:
        await send(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        await send(text.replace("*", "").replace("`", ""), reply_markup=kb, disable_web_page_preview=True)


def _dex_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Trenddagi tangalar", callback_data="dex:trend")],
        [InlineKeyboardButton("💼 DEX pozitsiyalar", callback_data="dex:pos"),
         InlineKeyboardButton("📜 DEX savdolar", callback_data="dex:trades")],
        [InlineKeyboardButton("⚙️ Filtr va rejim", callback_data="dex:cfg")],
    ])


def _dx_button(t: dict, label: str) -> InlineKeyboardButton | None:
    data_ = f"dx:{t['chain']}:{t['address']}"
    return InlineKeyboardButton(label, callback_data=data_) if len(data_.encode()) <= 64 else None


async def cmd_dex(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not ctx.args:
        await _say(msg, "*DEX tangalar* (DexScreener)\n\n"
                        "Tangani tekshirish uchun nomini yoki kontrakt manzilini yozing, masalan:\n"
                        "`/dex PEPE` yoki manzilni shunchaki yuboring.\n\n"
                        "Har bir tanga firibgarlik filtridan o'tkaziladi. Filtr kafolat emas.",
                   _dex_menu())
        return
    q = " ".join(ctx.args)
    m = await msg.reply_text("⏳ DexScreener'dan qidirilmoqda...")
    try:
        pairs = await asyncio.to_thread(dex.lookup, q)
    except Exception as e:  # noqa: BLE001
        await m.edit_text(f"❌ DexScreener xatosi: {e}")
        return
    if not pairs:
        await m.edit_text("Hech narsa topilmadi. Nomni yoki manzilni tekshirib ko'ring.")
        return
    if dex.is_address(q) or len(pairs) == 1:
        t = await asyncio.to_thread(dex.evaluate, pairs[0])
        await _dex_show(m, t, edit=True)
        return
    # Bir xil nomli tangalar ko'p bo'ladi (soxta nusxalar) — foydalanuvchi o'zi tanlaydi
    rows = []
    for p in pairs[:6]:
        t = dex.summarize(p)
        b = _dx_button(t, f"{t['symbol']} · {t['chain']} · likv. {dex._money(t['liq'])}")
        if b:
            rows.append([b])
    await _say(m, f"*{len(pairs)} ta tanga topildi.* Bir xil nomli soxta nusxalar ko'p bo'ladi — "
                  "likvidligi eng kattasi odatda asl tanga. Tanlang:",
               InlineKeyboardMarkup(rows), edit=True)


async def _dex_show(message, t: dict, edit: bool = False):
    row = [InlineKeyboardButton("📈 DexScreener", url=t["url"])] if t.get("url") else []
    b = _dx_button(t, "🔄 Yangilash")
    if b:
        row.insert(0, b)
    await _say(message, dex.report(t), InlineKeyboardMarkup([row]) if row else None, edit=edit)


async def _dex_token(message, chain: str, address: str, edit: bool = False):
    try:
        t = await asyncio.to_thread(dex.check_token, chain, address)
    except Exception as e:  # noqa: BLE001
        await message.reply_text(f"❌ DexScreener xatosi: {e}")
        return
    if not t:
        await message.reply_text("Bu tanga bo'yicha ma'lumot topilmadi.")
        return
    await _dex_show(message, t, edit=edit)


async def _dex_trend(message):
    m = await message.reply_text("⏳ Trenddagi tangalar filtrdan o'tkazilmoqda (bir daqiqagacha)...")
    try:
        found = await asyncio.to_thread(dex.scan_trending)
    except Exception as e:  # noqa: BLE001
        await m.edit_text(f"❌ DexScreener xatosi: {e}")
        return
    ok = [t for t in found if t["verdict"] == "ok"]
    warn = [t for t in found if t["verdict"] == "warn"]
    bad = len(found) - len(ok) - len(warn)
    lines = [f"*DEX trend*: {len(found)} ta tanga tekshirildi",
             f"✅ {len(ok)} ta filtrdan o'tdi · ⚠️ {len(warn)} ta shubhali · ⛔ {bad} ta xavfli (ko'rsatilmaydi)", ""]
    rows = []
    for t in (ok + warn)[:8]:
        icon = "✅" if t["verdict"] == "ok" else "⚠️"
        lines.append(f"{icon} *{t['symbol']}* ({t['chain']}) — 24s {t['chg24']:+.0f}%, "
                     f"likv. {dex._money(t['liq'])}, baho {t['score']}")
        b = _dx_button(t, f"{icon} {t['symbol']} ({t['chain']})")
        if b:
            rows.append(b)
    if not ok and not warn:
        lines.append("Hozir filtrdan o'tgan tanga yo'q. Bu normal — trenddagi tangalarning ko'pi xavfli.")
    await _say(m, "\n".join(lines), InlineKeyboardMarkup(_rows(rows, 2)) if rows else None, edit=True)


async def _dex_positions(message):
    pos = dex.positions()
    st = dex.stats()
    head = (f"*DEX qog'oz savdo* ({st['mode']})\nBalans: `${st['balance']:.2f}` · "
            f"Jami PnL: `{st['pnl']:+.2f}$` · Bugun: `{st['pnl_today']:+.2f}$`\n"
            f"Yopilgan: {st['closed']} ta ({st['wins']} tasi foydali)\n")
    if not pos:
        await _say(message, head + "\nOchiq pozitsiya yo'q.")
        return
    lines = []
    for p in pos:
        lines.append(f"`{p['symbol']}` ({p['chain']}) ${p['cost']:g} @ {p['entry']:.8g} · "
                     f"TP {p['tp']:.8g} · SL {p['sl']:.8g}")
    await _say(message, head + "\n*Ochiq pozitsiyalar*\n" + "\n".join(lines))


async def _dex_trades(message):
    rows = dex.trades(12)
    if not rows:
        await message.reply_text("DEX savdolari hali yo'q.")
        return
    lines = []
    for t in rows:
        if t["side"] == "buy":
            lines.append(f"🟢 `{t['symbol']}` xarid ${t['cost']:g}")
        else:
            lines.append(f"{'✅' if (t['pnl'] or 0) >= 0 else '🔴'} `{t['symbol']}` sotuv — "
                         f"PnL {t['pnl'] or 0:+.2f}$ ({t['note']})")
    await _say(message, "*DEX savdolar (qog'oz)*\n" + "\n".join(lines))


async def _dex_config(message):
    c = config
    alerts = "yoqiq" if c.DEX_ALERTS else "o'chiq"
    await _say(message,
               f"*DEX sozlamalari*\nXabarlar: *{alerts}* · "
               f"Avto-savdo: *{dex.mode()}*\nTarmoqlar: {', '.join(c.DEX_CHAINS)}\n\n"
               f"*Filtr*\nLikvidlik ≥ ${c.DEX_MIN_LIQUIDITY:,.0f}\nHajm 24s ≥ ${c.DEX_MIN_VOLUME:,.0f}\n"
               f"Yoshi ≥ {c.DEX_MIN_AGE_H:g} soat\nXavfsizlik bahosi ≥ {c.DEX_MIN_SCORE:g}\n"
               "Kontrakt: honeypot, soliq, yashirin egasi, mint (GoPlus / RugCheck)\n\n"
               f"*Savdo (qog'oz)*\n${c.DEX_TRADE_USDT:g} / savdo · max {c.DEX_MAX_POSITIONS} pozitsiya\n"
               f"TP +{c.DEX_TP_PCT:g}% · SL -{c.DEX_SL_PCT:g}% · muddat {c.DEX_MAX_HOLD_H:g} soat\n"
               f"Kunlik zarar limiti ${c.DEX_DAILY_LOSS_LIMIT:g}")


_SYMBOL_RE = re.compile(r"^[A-Za-z0-9]{2,10}(/[A-Za-z]{3,5})?$")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Pastki menyu tugmalari va oddiy matn (aktiv nomi) uchun."""
    text = (update.message.text or "").strip()
    routes = {
        B_SIGNAL: cmd_signal, B_SCAN: cmd_scan, B_TOP: cmd_top, B_NEWS: cmd_news,
        B_POS: cmd_positions, B_TRADES: cmd_trades, B_BAL: cmd_balance, B_MODE: cmd_mode,
        B_INV: cmd_investors, B_HELP: cmd_help, B_DEX: cmd_dex,
    }
    fn = routes.get(text)
    if fn:
        ctx.args = []
        await fn(update, ctx)
    elif dex.is_address(text):
        ctx.args = [text]
        await cmd_dex(update, ctx)
    elif _SYMBOL_RE.match(text):
        ctx.args = [text]
        await cmd_signal(update, ctx)
    else:
        await update.message.reply_text("Pastdagi tugmalardan birini tanlang 👇", reply_markup=MENU)


def build_app() -> Application:
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    handlers = {
        "start": cmd_start, "help": cmd_help, "stop": cmd_stop, "signal": cmd_signal,
        "scan": cmd_scan, "top": cmd_top, "watch": cmd_watch, "unwatch": cmd_unwatch,
        "list": cmd_list, "news": cmd_news, "investors": cmd_investors,
        "balance": cmd_balance, "positions": cmd_positions, "trades": cmd_trades,
        "mode": cmd_mode, "dex": cmd_dex,
    }
    for name, fn in handlers.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text))

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
