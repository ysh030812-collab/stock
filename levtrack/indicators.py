"""기술적 지표. 입력 DataFrame 컬럼: Open, High, Low, Close, (Volume)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = _wilder(d.clip(lower=0), n)
    loss = _wilder(-d.clip(upper=0), n)
    out = 100 - 100 / (1 + gain / loss)
    out = out.mask((loss == 0) & (gain > 0), 100.0)
    out = out.mask((loss == 0) & (gain == 0), 50.0)
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["Close"].shift()
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return _wilder(true_range(df), n)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + k * sd, "bb_lower": mid - k * sd})


def realized_vol(close: pd.Series, n: int = 20) -> pd.Series:
    """봉 단위 로그수익률 표준편차 (연율화 전)."""
    return np.log(close).diff().rolling(n, min_periods=n).std()


def rolling_rank(s: pd.Series, window: int) -> pd.Series:
    """창 안에서 현재 값의 백분위 (0~1)."""
    minp = max(20, window // 4)
    return s.rolling(window, min_periods=minp).apply(lambda x: (x <= x[-1]).mean(), raw=True)


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume."""
    sign = np.sign(df["Close"].diff()).fillna(0)
    return (sign * df["Volume"]).cumsum()


def updown_volume_ratio(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """n봉 동안 상승봉 거래량 / 전체 거래량 (0.5 초과면 매수 우위)."""
    up = df["Volume"].where(df["Close"].diff() > 0, 0.0)
    tot = df["Volume"].rolling(n, min_periods=n).sum()
    return up.rolling(n, min_periods=n).sum() / tot.replace(0, np.nan)


def add_relative_strength(d: pd.DataFrame, bench_close: pd.Series, n_sma: int = 50, n_roc: int = 20) -> pd.DataFrame:
    """기준지수 대비 상대강도 (예: SOXX / QQQ)."""
    out = d.copy()
    b = bench_close.reindex(out.index, method="ffill")
    out["rs"] = out["Close"] / b
    out["rs_sma"] = sma(out["rs"], n_sma)
    out["rs_roc"] = out["rs"].pct_change(n_roc)
    return out


def add_indicators(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """strategy.yaml 의 indicators 섹션으로 지표 컬럼을 추가한 사본을 반환."""
    out = df.copy()
    c = out["Close"]
    out["sma_fast"] = sma(c, p["sma_fast"])
    out["sma_slow"] = sma(c, p["sma_slow"])
    out["ema_fast"] = ema(c, p["ema_fast"])
    out["ema_slow"] = ema(c, p["ema_slow"])
    out["rsi"] = rsi(c, p["rsi"])
    out["atr"] = atr(out, p["atr"])
    out["atr_pct"] = out["atr"] / c
    out = out.join(macd(c, *p["macd"]))
    out = out.join(bollinger(c, *p["bollinger"]))
    out["roc"] = c.pct_change(p["roc"])
    rw = p["range_window"]
    out["range_high"] = out["High"].rolling(rw, min_periods=min(rw, 20)).max()
    out["range_low"] = out["Low"].rolling(rw, min_periods=min(rw, 20)).min()
    dn = p["donchian"]
    out["donchian_mid"] = (out["High"].rolling(dn).max() + out["Low"].rolling(dn).min()) / 2
    out["dc_high"] = out["High"].rolling(dn).max().shift(1)  # 직전 n봉 고가 (돌파 판단용)
    out["dc_low"] = out["Low"].rolling(dn).min().shift(1)
    if "Volume" in out and out["Volume"].fillna(0).sum() > 0:
        vol = out["Volume"].astype(float)
        out["obv"] = obv(out)
        out["obv_ema"] = ema(out["obv"], p.get("obv_ema", 20))
        out["vol_ratio"] = vol / sma(vol, p.get("vol_avg", 20)).replace(0, np.nan)
        out["updown_vol"] = updown_volume_ratio(out, p.get("vol_avg", 20))
    out["rvol"] = realized_vol(c, p["vol_window"])
    out["rvol_rank"] = rolling_rank(out["rvol"], p["vol_rank_window"])
    return out
