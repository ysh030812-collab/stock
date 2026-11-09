"""가격 구조: 피보나치, 매물대(볼륨 프로파일), 스윙 고저점 → 지지/저항 레벨."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .patterns import pivot_flags

FIB_RETR = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_EXT = (1.272, 1.618)


@dataclass
class Fib:
    up: bool  # True: 저점→고점 상승 스윙
    hi: float
    lo: float
    t_hi: pd.Timestamp
    t_lo: pd.Timestamp
    retr: dict
    ext: dict


def fibonacci(d: pd.DataFrame, lookback: int = 120) -> Fib:
    sub = d.iloc[-lookback:]
    t_hi, t_lo = sub["High"].idxmax(), sub["Low"].idxmin()
    hi, lo = float(sub["High"].max()), float(sub["Low"].min())
    rng = hi - lo
    up = t_lo < t_hi
    if up:
        retr = {r: hi - r * rng for r in FIB_RETR}
        ext = {e: lo + e * rng for e in FIB_EXT}
    else:
        retr = {r: lo + r * rng for r in FIB_RETR}
        ext = {e: hi - e * rng for e in FIB_EXT}
    return Fib(up, hi, lo, t_hi, t_lo, retr, ext)


@dataclass
class VolumeProfile:
    edges: np.ndarray
    volume: np.ndarray
    poc: float
    vah: float
    val: float
    hvn: list = field(default_factory=list)

    @property
    def centers(self) -> np.ndarray:
        return (self.edges[:-1] + self.edges[1:]) / 2


def volume_profile(d: pd.DataFrame, bars: int = 120, bins: int = 40, value_area: float = 0.70) -> VolumeProfile | None:
    """각 봉의 거래량을 고가~저가 구간에 균등 배분한 가격대별 거래량."""
    sub = d.iloc[-bars:]
    if "Volume" not in sub or sub["Volume"].fillna(0).sum() <= 0:
        return None
    lo, hi = float(sub["Low"].min()), float(sub["High"].max())
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    e_lo, e_hi = edges[:-1][None, :], edges[1:][None, :]
    L = sub["Low"].to_numpy()[:, None]
    H = sub["High"].to_numpy()[:, None]
    V = sub["Volume"].fillna(0).to_numpy(dtype=float)[:, None]
    span = np.maximum(H - L, 1e-12)
    overlap = np.clip(np.minimum(H, e_hi) - np.maximum(L, e_lo), 0, None)
    w = overlap / span
    flat = (H - L).ravel() <= 0  # 고가=저가 봉은 종가 구간에 전부
    if flat.any():
        idx = np.clip(np.searchsorted(edges, sub["Close"].to_numpy()[flat]) - 1, 0, bins - 1)
        w[flat] = 0
        w[np.flatnonzero(flat), idx] = 1
    vol = (w * V).sum(axis=0)

    poc_i = int(vol.argmax())
    lo_i = hi_i = poc_i
    target = vol.sum() * value_area
    acc = vol[poc_i]
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        down = vol[lo_i - 1] if lo_i > 0 else -1
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if up >= down:
            hi_i += 1
            acc += vol[hi_i]
        else:
            lo_i -= 1
            acc += vol[lo_i]
    centers = (edges[:-1] + edges[1:]) / 2
    thresh = vol.mean() * 1.2
    hvn = [float(centers[i]) for i in range(1, bins - 1)
           if i != poc_i and vol[i] >= vol[i - 1] and vol[i] >= vol[i + 1] and vol[i] > thresh]
    return VolumeProfile(edges, vol, float(centers[poc_i]), float(edges[hi_i + 1]), float(edges[lo_i]), hvn)


def swing_points(d: pd.DataFrame, n: int = 5, lookback: int = 120) -> pd.DataFrame:
    sub = d.iloc[-lookback:]
    hi = sub.loc[pivot_flags(sub["High"], n, n, "high"), "High"]
    lo = sub.loc[pivot_flags(sub["Low"], n, n, "low"), "Low"]
    return pd.concat([pd.DataFrame({"price": hi, "kind": "swing_high"}),
                      pd.DataFrame({"price": lo, "kind": "swing_low"})]).sort_index()


def key_levels(d: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """지지/저항 후보 레벨 표 (price, label, source)."""
    rows = []
    fib = fibonacci(d, cfg["fib_lookback"])
    for r, p in fib.retr.items():
        rows.append((p, f"피보 {r:.3f}", "fib"))
    for e, p in fib.ext.items():
        rows.append((p, f"피보 확장 {e:.3f}", "fib"))
    rows += [(fib.hi, "스윙 고점(피보 기준)", "fib"), (fib.lo, "스윙 저점(피보 기준)", "fib")]
    vp = volume_profile(d, cfg["vp_bars"], cfg["vp_bins"], cfg["value_area"])
    if vp:
        rows += [(vp.poc, "매물대 POC", "vp"), (vp.vah, "매물대 VAH", "vp"), (vp.val, "매물대 VAL", "vp")]
        rows += [(p, "매물 집중(HVN)", "vp") for p in vp.hvn]
    sw = swing_points(d, cfg["swing_pivot"], cfg["fib_lookback"])
    for t, r in sw.iloc[-6:].iterrows():
        rows.append((float(r["price"]), "스윙 고점" if r["kind"] == "swing_high" else "스윙 저점", "swing"))
    last = d.iloc[-1]
    if "range_high" in d and pd.notna(last.get("range_high")):
        rows += [(float(last["range_high"]), "52주 고점", "range"), (float(last["range_low"]), "52주 저점", "range")]
    out = pd.DataFrame(rows, columns=["price", "label", "source"]).drop_duplicates("price")
    return out.sort_values("price", ascending=False).reset_index(drop=True)
