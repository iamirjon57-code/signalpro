"""Brauzer orqali sozlash sahifasi — .env faylini tahrirlaydi va xizmatni qayta yoqadi.

Faqat SETUP_TOKEN o'rnatilgan bo'lsa ishlaydi.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess

log = logging.getLogger("setup")

SETUP_TOKEN = os.getenv("SETUP_TOKEN", "").strip()
ENV_PATH = os.getenv("ENV_PATH", "/opt/signalpro/.env")
SERVICE = os.getenv("SERVICE_NAME", "signalpro")

# Sahifa orqali o'zgartirishga ruxsat etilgan kalitlar
FIELDS = [
    ("TELEGRAM_TOKEN", "Telegram bot tokeni", True),
    ("ADMIN_IDS", "Sizning Telegram ID ingiz (@userinfobot dan) — /balance va boshqaruv faqat sizga", False),
    ("TELEGRAM_CHAT_IDS", "Signal yuboriladigan chat ID lar (vergul bilan, ixtiyoriy)", False),
    ("DASHBOARD_PASSWORD", "Sayt paroli (savdolar va balansni yopish uchun)", True),
    ("TWELVE_DATA_KEY", "Twelve Data kaliti (aksiya/forex)", True),
    ("EXCHANGE", "Birja: binance yoki bitget", False),
    ("BITGET_API_KEY", "Bitget API Key", True),
    ("BITGET_API_SECRET", "Bitget Secret Key", True),
    ("BITGET_PASSPHRASE", "Bitget Passphrase (kalit yaratishda o'zingiz qo'ygan parol)", True),
    ("BINANCE_API_KEY", "Binance API Key (faqat Binance uchun)", True),
    ("BINANCE_API_SECRET", "Binance API Secret (faqat Binance uchun)", True),
    ("BINANCE_TESTNET", "Sinov rejimi: true = haqiqiy pul ishlatilmaydi, false = REAL pul", False),
    ("AUTO_TRADE", "Avto-savdo (true/false)", False),
    ("TRADE_AMOUNT_USDT", "Bitta savdo summasi (USDT)", False),
    ("MAX_OPEN_POSITIONS", "Maksimal ochiq pozitsiya", False),
    ("DAILY_LOSS_LIMIT_USDT", "Kunlik zarar limiti (USDT)", False),
    ("TAKE_PROFIT_PCT", "Take Profit (%)", False),
    ("STOP_LOSS_PCT", "Stop Loss (%)", False),
    ("MIN_SCORE", "Signal uchun minimal ball", False),
]
KEYS = {k for k, _, _ in FIELDS}
VALID_KEY = re.compile(r"^[A-Z0-9_]+$")


def enabled() -> bool:
    return bool(SETUP_TOKEN)


def check_token(given: str) -> bool:
    return bool(SETUP_TOKEN) and secrets.compare_digest(given or "", SETUP_TOKEN)


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return out


def status() -> list[dict]:
    """Har bir maydonning holati — maxfiy qiymatlar ko'rsatilmaydi."""
    cur = read_env()
    rows = []
    for key, label, secret in FIELDS:
        val = cur.get(key, "")
        rows.append({
            "key": key, "label": label, "secret": secret,
            "value": "" if secret else val,
            "filled": bool(val), "length": len(val) if secret else None,
        })
    return rows


def write_env(updates: dict[str, str]) -> int:
    """.env dagi qatorlarni yangilaydi. Bo'sh qiymatlar e'tiborsiz qoldiriladi."""
    clean = {}
    for k, v in updates.items():
        k = (k or "").strip()
        v = (v or "").strip()
        if not v or k not in KEYS or not VALID_KEY.match(k):
            continue
        clean[k] = v.replace("\n", "").replace("\r", "")
    if not clean:
        return 0

    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []

    seen = set()
    for i, line in enumerate(lines):
        if "=" not in line or line.strip().startswith("#"):
            continue
        k = line.split("=", 1)[0].strip()
        if k in clean:
            lines[i] = f"{k}={clean[k]}"
            seen.add(k)
    for k, v in clean.items():
        if k not in seen:
            lines.append(f"{k}={v}")

    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV_PATH)
    log.info("Sozlamalar yangilandi: %s", ", ".join(sorted(clean)))
    return len(clean)


def close_setup():
    """SETUP_TOKEN ni .env dan o'chiradi — qayta ishga tushgach /setup sahifasi yopiladi."""
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return
    lines = [ln for ln in lines if not ln.strip().startswith("SETUP_TOKEN=")]
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV_PATH)
    log.info("Sozlash sahifasi yopildi (SETUP_TOKEN o'chirildi)")


def restart_service():
    """Xizmatni qayta ishga tushiradi (javob yuborilgandan keyin)."""
    subprocess.Popen(
        ["/bin/sh", "-c", f"sleep 2 && systemctl restart {SERVICE}"],
        start_new_session=True,
    )


PAGE = """<!DOCTYPE html>
<html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signal Pro — Sozlamalar</title>
<style>
:root{--bg:#0b0f17;--surface:#131a26;--surface2:#1a2333;--line:#243044;
 --text:#e7edf7;--muted:#8b9ab1;--accent:#5b9cff;--ok:#28c07f;--warn:#f5a524;--err:#f2555a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
 font-size:15px;padding:24px 16px}
.wrap{max-width:640px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
h1 span{color:var(--accent)}
p.sub{color:var(--muted);margin:0 0 20px;font-size:14px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:14px}
label{display:block;font-size:13px;color:var(--muted);margin:0 0 5px}
input{width:100%;background:var(--surface2);border:1px solid var(--line);color:var(--text);
 padding:11px 12px;border-radius:9px;font-size:15px;font-family:inherit;margin-bottom:14px}
input:focus{outline:none;border-color:var(--accent)}
.tag{font-size:11px;padding:2px 8px;border-radius:99px;margin-left:8px;vertical-align:middle}
.set{background:rgba(40,192,127,.15);color:var(--ok)}
.unset{background:rgba(242,85,90,.15);color:var(--err)}
button{width:100%;background:var(--accent);border:0;color:#04101f;padding:13px;border-radius:10px;
 font-size:16px;font-weight:700;cursor:pointer;font-family:inherit}
button:disabled{opacity:.5;cursor:default}
#msg{margin-top:14px;padding:12px;border-radius:9px;display:none;font-size:14px;line-height:1.5}
.warn{background:rgba(245,165,36,.12);border:1px solid var(--warn);color:var(--warn);
 padding:12px;border-radius:9px;font-size:13px;line-height:1.5;margin-bottom:16px}
</style></head><body><div class="wrap">
<h1>Signal<span>Pro</span> — sozlamalar</h1>
<p class="sub">Kalitlarni shu yerga qo'ying. Saqlagandan so'ng bot avtomatik qayta yonadi.</p>

<div class="warn">Bu sahifa faqat sozlash uchun. Ishingiz tugagach pastdagi
<b>“Sozlashni yakunlash”</b> tugmasini bosing — sahifa butunlay yopiladi.</div>

<form id="f">
<div class="card">
  <label>Sozlash kaliti (SETUP_TOKEN)</label>
  <input type="password" id="token" autocomplete="off" placeholder="serverda chiqqan kalit" required>
</div>
<div class="card" id="fields">Yuklanmoqda…</div>
<button type="submit" id="btn">Saqlash va qayta ishga tushirish</button>
</form>
<div id="msg"></div>
<button type="button" id="close" style="margin-top:14px;background:var(--surface2);color:var(--text);border:1px solid var(--line)">
Sozlashni yakunlash (sahifani yopish)</button>
</div>
<script>
const $=s=>document.querySelector(s);
let rows=[];
async function load(){
  const r=await fetch('/api/setup/status');
  rows=(await r.json()).fields;
  $('#fields').innerHTML=rows.map(f=>`
    <label>${f.label}
      <span class="tag ${f.filled?'set':'unset'}">${f.filled?'kiritilgan':'bo\\'sh'}</span>
    </label>
    <input id="i_${f.key}" type="${f.secret?'password':'text'}" autocomplete="off"
      value="${f.secret?'':(f.value||'')}"
      placeholder="${f.secret?(f.filled?'o\\'zgartirmaslik uchun bo\\'sh qoldiring':'qiymatni qo\\'ying'):''}">`).join('');
}
$('#f').onsubmit=async e=>{
  e.preventDefault();
  const btn=$('#btn'); btn.disabled=true; btn.textContent='Saqlanmoqda…';
  const values={};
  rows.forEach(f=>{const v=$('#i_'+f.key).value.trim(); if(v) values[f.key]=v;});
  const msg=$('#msg');
  const live=(values.BINANCE_TESTNET||'').toLowerCase()==='false' && (values.AUTO_TRADE||'').toLowerCase()==='true';
  if(live && !confirm("DIQQAT: bot REAL pul bilan avtomatik savdo qiladi. Davom etasizmi?")){
    btn.disabled=false; btn.textContent='Saqlash va qayta ishga tushirish'; return;
  }
  try{
    const r=await fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:$('#token').value,values})});
    const d=await r.json();
    if(!r.ok) throw new Error(d.detail||'xato');
    msg.style.display='block';
    msg.style.background='rgba(40,192,127,.12)';
    msg.style.color='#28c07f';
    msg.textContent=d.updated+' ta sozlama saqlandi. Bot qayta ishga tushmoqda — 15 soniyadan keyin bosh sahifani yangilang.';
    rows.forEach(f=>{const el=$('#i_'+f.key); if(f.secret) el.value='';});
    setTimeout(load,12000);
  }catch(err){
    msg.style.display='block';
    msg.style.background='rgba(242,85,90,.12)';
    msg.style.color='#f2555a';
    msg.textContent='Xato: '+err.message;
  }
  btn.disabled=false; btn.textContent='Saqlash va qayta ishga tushirish';
};
$('#close').onclick=async()=>{
  if(!confirm("Sozlash sahifasi yopiladi. Keyin kalitlarni o'zgartirish uchun serverda o'rnatish buyrug'ini qayta ishga tushirish kerak bo'ladi. Yopilsinmi?")) return;
  const msg=$('#msg'); msg.style.display='block';
  try{
    const r=await fetch('/api/setup/close',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:$('#token').value})});
    const d=await r.json(); if(!r.ok) throw new Error(d.detail||'xato');
    msg.style.background='rgba(40,192,127,.12)'; msg.style.color='#28c07f';
    msg.textContent='Sozlash sahifasi yopildi. Bot qayta ishga tushmoqda.';
  }catch(err){msg.style.background='rgba(242,85,90,.12)'; msg.style.color='#f2555a'; msg.textContent='Xato: '+err.message;}
};
load();
</script></body></html>"""
