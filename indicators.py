"""Texnik indikatorlar — faqat pandas/numpy, tashqi TA kutubxonasiz."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def macd(s: pd.Series, fast=12, slow=26, signal=9):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(s: pd.Series, period=20, mult=2.0):
    mid = sma(s, period)
    std = s.rolling(period).std()
    return mid + mult * std, mid, mid - mult * std


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def enrich(df: pd.DataFrame, rsi_period: int = 14) -> pd.DataFrame:
    """OHLCV dataframe ga barcha indikatorlarni qo'shadi."""
    df = df.copy()
    c = df["close"]
    df["rsi"] = rsi(c, rsi_period)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c)
    df["ma_fast"] = ema(c, 20)
    df["ma_slow"] = ema(c, 50)
    df["bb_up"], df["bb_mid"], df["bb_low"] = bollinger(c)
    if {"high", "low"}.issubset(df.columns):
        df["atr"] = atr(df)
    df["vol_ma"] = df["volume"].rolling(20).mean() if "volume" in df else np.nan
    return df
