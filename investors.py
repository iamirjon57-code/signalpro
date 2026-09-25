"""Jahon aksiya tahlili: SEC 13F (yirik investorlar portfeli) + yangiliklar sarlavhalari."""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

import config

log = logging.getLogger("investors")

# Mashhur investorlar va ularning SEC CIK raqamlari
FUNDS = {
    "Warren Buffett (Berkshire Hathaway)": "0001067983",
    "Bill Ackman (Pershing Square)": "0001336528",
    "Michael Burry (Scion)": "0001649339",
    "Ray Dalio (Bridgewater)": "0001350694",
    "Cathie Wood (ARK Invest)": "0001697748",
    "Carl Icahn (Icahn Capital)": "0000921669",
    "David Tepper (Appaloosa)": "0001656456",
    "George Soros (Soros Fund)": "0001029160",
}

_HEAD = {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
_cache: dict[str, tuple[float, object]] = {}
TTL = 3600 * 6


def _cget(k):
    v = _cache.get(k)
    return v[1] if v and time.time() - v[0] < TTL else None


def _cput(k, v):
    _cache[k] = (time.time(), v)
    return v


def latest_13f(cik: str) -> dict | None:
    """Fondning eng oxirgi 13F-HR hisobotidagi eng yirik pozitsiyalari."""
    key = f"13f:{cik}"
    if (hit := _cget(key)) is not None:
        return hit
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=_HEAD, timeout=25)
        r.raise_for_status()
        rec = r.json()["filings"]["recent"]
        idx = next((i for i, f in enumerate(rec["form"]) if f.startswith("13F-HR")), None)
        if idx is None:
            return _cput(key, None)
        acc = rec["accessionNumber"][idx].replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}"
        listing = requests.get(base + "/", headers=_HEAD, timeout=25).text
        xmls = re.findall(r'href="([^"]+\.xml)"', listing)
        info_xml = next((x for x in xmls if "primary_doc" not in x.lower()), None)
        if not info_xml:
            return _cput(key, None)
        url = info_xml if info_xml.startswith("http") else "https://www.sec.gov" + info_xml
        root = ET.fromstring(requests.get(url, headers=_HEAD, timeout=30).content)
        holdings = []
        for it in root.iter():
            if not it.tag.endswith("infoTable"):
                continue
            g = {c.tag.split("}")[-1]: c for c in it}
            try:
                val = float(g["value"].text)
            except Exception:  # noqa: BLE001
                continue
            holdings.append({
                "name": g["nameOfIssuer"].text,
                "cusip": g.get("cusip").text if g.get("cusip") is not None else "",
                "value_usd": val,
            })
        holdings.sort(key=lambda h: -h["value_usd"])
        total = sum(h["value_usd"] for h in holdings) or 1
        for h in holdings:
            h["pct"] = round(h["value_usd"] / total * 100, 2)
        out = {
            "filed": rec["filingDate"][idx],
            "period": rec.get("reportDate", [""] * (idx + 1))[idx],
            "top": holdings[:15],
            "positions": len(holdings),
        }
        return _cput(key, out)
    except Exception as e:  # noqa: BLE001
        log.warning("13F(%s) xato: %s", cik, e)
        return _cput(key, None)


def all_funds() -> list[dict]:
    out = []
    for name, cik in FUNDS.items():
        d = latest_13f(cik)
        if d:
            out.append({"fund": name, "cik": cik, **d})
    return out


def news(query: str, limit: int = 8) -> list[dict]:
    """Google News RSS orqali sarlavhalar (kalit talab qilinmaydi)."""
    key = f"news:{query}:{limit}"
    if (hit := _cget(key)) is not None:
        return hit
    try:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        root = ET.fromstring(requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).content)
        items = []
        for it in root.iter("item"):
            items.append({
                "title": (it.findtext("title") or "").strip(),
                "link": it.findtext("link") or "",
                "date": it.findtext("pubDate") or "",
                "source": (it.findtext("source") or "").strip(),
            })
            if len(items) >= limit:
                break
        return _cput(key, items)
    except Exception as e:  # noqa: BLE001
        log.warning("news(%s): %s", query, e)
        return []
