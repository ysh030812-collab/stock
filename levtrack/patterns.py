"""캔들 패턴 · 다이버전스."""
from __future__ import annotations

import numpy as np
import pandas as pd

# 이름: (한글, 방향 +1 상승 / -1 하락 / 0 중립)
PATTERNS = {
    "bull_engulfing": ("상승 장악형", 1),
    "bear_engulfing": ("하락 장악형", -1),
    "hammer": ("망치형", 1),
    "shooting_star": ("유성형", -1),
    "morning_star": ("샛별형", 1),
    "evening_star": ("석별형", -1),
    "doji": ("도지", 0),
    "inside_bar": ("인사이드바", 0),
}


def candle_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """봉별 패턴 발생 여부 (bool). 망치형/유성형은 직전 5봉 추세 맥락을 요구."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    avg_body = body.rolling(10, min_periods=3).mean()
    green, red = c > o, c < o
    down_ctx = c.shift(1) < c.shift(6)
    up_ctx = c.shift(1) > c.shift(6)
    small = body <= 0.35 * rng

    out = pd.DataFrame(index=df.index)
    out["bull_engulfing"] = red.shift(1, fill_value=False) & green & (c >= o.shift(1)) & (o <= c.shift(1)) & (body > body.shift(1))
    out["bear_engulfing"] = green.shift(1, fill_value=False) & red & (o >= c.shift(1)) & (c <= o.shift(1)) & (body > body.shift(1))
    out["hammer"] = (lower >= 2 * body) & (upper <= 0.25 * rng) & small & down_ctx
    out["shooting_star"] = (upper >= 2 * body) & (lower <= 0.25 * rng) & small & up_ctx
    mid2 = (o.shift(2) + c.shift(2)) / 2
    big2 = body.shift(2) > avg_body.shift(2)
    star1 = body.shift(1) < 0.5 * body.shift(2)
    out["morning_star"] = red.shift(2, fill_value=False) & big2 & star1 & green & (c > mid2)
    out["evening_star"] = green.shift(2, fill_value=False) & big2 & star1 & red & (c < mid2)
    out["doji"] = body <= 0.1 * rng
    out["inside_bar"] = (h < h.shift(1)) & (l > l.shift(1))
    return out.fillna(False).astype(bool)


def recent_patterns(pat: pd.DataFrame, n: int = 3) -> list[dict]:
    """최근 n봉 안에서 발생한 패턴 (최신순)."""
    rows = []
    for t, row in pat.iloc[-n:].iloc[::-1].iterrows():
        for name in PATTERNS:
            if row.get(name, False):
                label, bias = PATTERNS[name]
                rows.append({"time": t, "name": name, "label": label, "bias": bias,
                             "bars_ago": int(len(pat) - 1 - pat.index.get_loc(t))})
    return rows


# ---------------------------------------------------------------- divergence
def pivot_flags(s: pd.Series, left: int, right: int, kind: str) -> pd.Series:
    """s[i] 가 [i-left, i+right] 구간의 최저(low) / 최고(high) 인 봉. right 봉 뒤에야 확정된다."""
    win = left + right + 1
    ext = s.rolling(win).min() if kind == "low" else s.rolling(win).max()
    return (s == ext.shift(-right)) & s.notna()


def find_divergences(d: pd.DataFrame, osc_col: str, left: int = 3, right: int = 3,
                     lookback: int = 60, min_gap: int = 5) -> list[dict]:
    """정규 다이버전스 (가격 피벗 기준으로 오실레이터 비교).

    강세: 가격 저점 하락 + 오실레이터 저점 상승
    약세: 가격 고점 상승 + 오실레이터 고점 하락
    """
    sub = d.iloc[-lookback:]
    osc = sub[osc_col]
    n_total = len(d)
    offset = n_total - len(sub)
    out = []
    for kind, price_col, sign in (("bullish", "Low", 1), ("bearish", "High", -1)):
        piv = np.flatnonzero(pivot_flags(sub[price_col], left, right, "low" if sign > 0 else "high").to_numpy())
        for a, b in zip(piv[:-1], piv[1:]):
            if b - a < min_gap or np.isnan(osc.iloc[a]) or np.isnan(osc.iloc[b]):
                continue
            pa, pb = sub[price_col].iloc[a], sub[price_col].iloc[b]
            oa, ob = osc.iloc[a], osc.iloc[b]
            if (sign > 0 and pb < pa and ob > oa) or (sign < 0 and pb > pa and ob < oa):
                out.append({
                    "kind": kind, "osc": osc_col, "bias": sign,
                    "t1": sub.index[a], "t2": sub.index[b], "p1": float(pa), "p2": float(pb),
                    "o1": float(oa), "o2": float(ob), "bars_ago": int(n_total - 1 - (offset + b)),
                })
    return sorted(out, key=lambda x: x["bars_ago"])


def recent_divergences(d: pd.DataFrame, cfg: dict, oscillators=("rsi", "macd_hist")) -> list[dict]:
    """max_age 봉 이내의 다이버전스만 (오실레이터·방향별 최신 1개)."""
    seen, out = set(), []
    for osc in oscillators:
        if osc not in d:
            continue
        for dv in find_divergences(d, osc, cfg["pivot_left"], cfg["pivot_right"], cfg["lookback"], cfg["min_gap"]):
            key = (dv["osc"], dv["kind"])
            if dv["bars_ago"] <= cfg["max_age"] and key not in seen:
                seen.add(key)
                out.append(dv)
    return out
