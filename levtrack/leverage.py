"""레버리지 환산 엔진.

기초자산 가격 레벨(진입, 손절, 익절)을 일일 리셋형 레버리지 ETF 가격으로 바꾼다.
k 는 일일 목표 배수 (TQQQ = 3, SQQQ = -3).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def convert_price(u_entry: float, u_target: float, l_entry: float, k: float) -> float:
    """당일(리셋 전) 환산: 기초가 u_entry→u_target 으로 움직일 때 LETF 가격."""
    return max(0.0, l_entry * (1 + k * (u_target / u_entry - 1)))


def decay_factor(k: float, sigma_daily: float, days: float, expense: float = 0.0) -> float:
    """변동성 감쇠 × 보수 차감 계수.  exp(-(k²-k)/2·σ²·t) · exp(-fee·t/252)"""
    return math.exp(-(k * k - k) / 2 * sigma_daily**2 * days) * math.exp(-expense * days / TRADING_DAYS)


def project_price(
    u_entry: float,
    u_target: float,
    l_entry: float,
    k: float,
    sigma_daily: float,
    days: float,
    expense: float = 0.0,
) -> float:
    """여러 날 보유 후 기초가가 u_target 일 때 LETF 예상가 (연속시간 근사).

    LETF_t / LETF_0 ≈ (U_t/U_0)^k · exp(-(k²-k)/2·σ²·t) · exp(-fee·t)
    """
    ratio = u_target / u_entry
    if ratio <= 0:
        return 0.0
    return l_entry * ratio**k * decay_factor(k, sigma_daily, days, expense)


def projection_table(
    u_entry: float,
    u_target: float,
    l_entry: float,
    k: float,
    sigma_daily: float,
    days_list=(1, 5, 10, 20),
    sigma_mults=(0.5, 1.0, 1.5),
    expense: float = 0.0,
) -> pd.DataFrame:
    """보유일 × 변동성 시나리오별 LETF 예상가 표."""
    rows = {}
    for m in sigma_mults:
        rows[f"σ×{m:g}"] = [
            project_price(u_entry, u_target, l_entry, k, sigma_daily * m, d, expense) for d in days_list
        ]
    return pd.DataFrame(rows, index=[f"{d}일" for d in days_list]).T


def _day_keys(index: pd.Index) -> pd.Index:
    if isinstance(index, pd.DatetimeIndex):
        return index.normalize()
    return pd.Index(np.arange(len(index)))  # 날짜 정보가 없으면 봉 = 하루로 간주


def synthetic_letf_close(close: pd.Series, k: float, expense: float = 0.0, start: float = 100.0) -> pd.Series:
    """기초 종가(일봉 또는 장중봉)로 일일 리셋 LETF 가격을 합성.

    장중봉은 '전일 종가' 기준으로 k 배 (리셋은 하루 한 번).
    """
    return synthetic_letf_ohlc(close.to_frame("Close"), k, expense, start)["Close"]


def synthetic_letf_ohlc(df: pd.DataFrame, k: float, expense: float = 0.0, start: float = 100.0) -> pd.DataFrame:
    keys = _day_keys(df.index)
    close = df["Close"]
    u_day_close = close.groupby(keys).last()
    u_ref = u_day_close.shift(1)
    u_ref.iloc[0] = close.iloc[0] if "Open" not in df else df["Open"].iloc[0]
    fee = 1 - expense / TRADING_DAYS
    day_mult = (1 + k * (u_day_close / u_ref - 1)).clip(lower=0) * fee
    l_day_close = start * day_mult.cumprod()
    l_ref = l_day_close.shift(1)
    l_ref.iloc[0] = start

    u_ref_bar = pd.Series(u_ref.reindex(keys).to_numpy(), index=df.index)
    l_ref_bar = pd.Series(l_ref.reindex(keys).to_numpy(), index=df.index)

    def conv(u: pd.Series) -> pd.Series:
        return (l_ref_bar * (1 + k * (u / u_ref_bar - 1)) * fee).clip(lower=0)

    out = pd.DataFrame(index=df.index)
    out["Close"] = conv(close)
    if {"Open", "High", "Low"} <= set(df.columns):
        out["Open"] = conv(df["Open"])
        hi, lo = conv(df["High"]), conv(df["Low"])
        out["High"], out["Low"] = (hi, lo) if k > 0 else (lo, hi)
    if "Volume" in df:
        out["Volume"] = df["Volume"]
    return out[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in out]]


def daily_close(s: pd.Series) -> pd.Series:
    if isinstance(s.index, pd.DatetimeIndex):
        return s.groupby(s.index.normalize()).last()
    return s


def tracking_error(u_close: pd.Series, l_close: pd.Series, k: float, window: int = 20) -> pd.DataFrame:
    """일간 추적오차 = LETF 일간수익률 − k × 기초 일간수익률.

    반환: daily (일별 괴리), rolling_sum (window 일 누적 괴리), implied_k (window 일 회귀 기울기)
    """
    u = daily_close(u_close).pct_change()
    l = daily_close(l_close).pct_change()
    u, l = u.align(l, join="inner")
    diff = l - k * u
    cov = u.rolling(window).cov(l)
    var = u.rolling(window).var()
    return pd.DataFrame({"daily": diff, "rolling_sum": diff.rolling(window).sum(), "implied_k": cov / var})
