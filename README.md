# Signal Pro — savdo signallari sayti + Telegram bot

Binance (kripto), jahon aksiyalari va forex/MetaTrader juftliklarini tahlil qilib,
**BUY / SELL** signallarini beradi, Telegram'ga yuboradi va (ixtiyoriy) Binance'da
**avtomatik savdo** qiladi. GitHub → Railway'ga bir marta deploy qilinadi.

## Nimalar bor

| Qism | Tavsif |
|---|---|
| **Web dashboard** | Signallar jadvali, narx/EMA/RSI grafiklari, 13F, yangiliklar, savdolar tarixi |
| **Telegram bot** | `/signal`, `/scan`, `/top`, `/investors`, `/news`, `/balance`, `/positions`, `/mode` |
| **Skaner** | Har 15 daqiqada barcha aktivlarni tekshiradi, signal o'zgarganda xabar beradi |
| **Avto-savdo** | Binance spot market order + TP/SL + risk limitlari (testnet default) |
| **Aksiya tahlili** | Twelve Data (narx) + SEC 13F (Baffet, Ekman, Burry, Dalio, Cathie Wood, Ikan…) + Google News |

**Strategiya:** RSI + MACD kesishuvi + EMA20/EMA50 trendi + Bollinger — har biri 1 ball.
Ball ≥ `MIN_SCORE` → BUY, ≤ `-MIN_SCORE` → SELL.

MetaTrader juftliklari (EUR/USD, GBP/USD, XAU/USD…) **signal rejimida** tahlil qilinadi —
order MT5 terminalida o'zingiz qo'yasiz. Avto-savdo faqat Binance kripto uchun.

## 1. GitHub'ga yuklash

```bash
cd signalpro
git init
git add .
git commit -m "Signal Pro: trading signal bot + dashboard"
git branch -M main
git remote add origin https://github.com/FOYDALANUVCHI/signalpro.git
git push -u origin main
```

## 2. Railway'ga deploy

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → shu repo.
2. **Variables** bo'limiga `.env.example` dagi o'zgaruvchilarni qo'ying (pastdagi ro'yxat).
3. **Settings → Networking → Generate Domain** — sayt manzili chiqadi.
4. Deploy tugagach `https://sizning-domen.up.railway.app` ochiladi, bot esa avtomatik ishlaydi.

> Railway `PORT` ni o'zi beradi — qo'lda yozish shart emas.
> SQLite fayli qayta deploy'da o'chadi; tarix saqlansin desangiz Railway **Volume** qo'shib
> `DB_PATH=/data/signals.db` qiling.

## 3. Kerakli kalitlar

| O'zgaruvchi | Qayerdan | Majburiy |
|---|---|---|
| `TELEGRAM_TOKEN` | Telegram'da [@BotFather](https://t.me/BotFather) → `/newbot` | bot uchun ha |
| `TWELVE_DATA_KEY` | [twelvedata.com](https://twelvedata.com/pricing) bepul tarif | aksiya/forex uchun ha |
| `BINANCE_API_KEY` / `SECRET` | Binance → API Management | avto-savdo uchun |
| Qolganlari | `.env.example` ga qarang | yo'q |

Kripto **narxlari** uchun kalit kerak emas — Binance public API ishlatiladi.

## 4. Avto-savdoni yoqish (ketma-ketlik muhim)

1. `AUTO_TRADE=false`, `BINANCE_TESTNET=true` — bir hafta signallarni kuzating.
2. [testnet.binance.vision](https://testnet.binance.vision) dan demo kalit oling,
   `AUTO_TRADE=true` qiling — bot demo pulda savdo qiladi.
3. Natija qoniqarli bo'lsagina real kalitga o'ting: `BINANCE_TESTNET=false`.
   Binance API kalitida **faqat "Spot Trading"** ruxsatini yoqing, **"Withdrawal" ni yoqmang**.
4. `TRADE_AMOUNT_USDT` ni kichik qoldiring (10–20), `DAILY_LOSS_LIMIT_USDT` ni belgilang.

Himoya mexanizmlari: bitta savdo summasi cheklangan, bir vaqtda max `MAX_OPEN_POSITIONS`
pozitsiya, kunlik zarar limiti, har pozitsiyada TP/SL avtomatik tekshiriladi.

## 5. Lokal sinash

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # kalitlarni to'ldiring
python main.py          # http://localhost:8000
```

## API

| Endpoint | Tavsif |
|---|---|
| `GET /api/signals` | joriy + tarixiy signallar |
| `GET /api/analyze?symbol=BTC/USDT` | bitta aktiv tahlili |
| `GET /api/chart?symbol=AAPL&timeframe=1h` | grafik ma'lumotlari |
| `GET /api/investors` | 13F portfellari |
| `GET /api/news?q=bitcoin` | yangiliklar |
| `POST /api/scan` | qo'lda tekshirish |
| `GET /health` | Railway healthcheck |

Saytni yopish uchun `DASHBOARD_PASSWORD` qo'ying — keyin `?key=PAROL` bilan ochiladi.

## Ogohlantirish

Bu dastur moliyaviy maslahat bermaydi. Signallar texnik indikatorlarga asoslangan va
xato bo'lishi mumkin. Avto-savdo real pul yo'qotishga olib kelishi mumkin — javobgarlik
to'liq foydalanuvchi zimmasida.
