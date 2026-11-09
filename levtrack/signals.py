"""방향 점수 · 국면 · 변동성 계수 (기초자산 기준)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# 카테고리 배점 (합 100)
TREND, MOMENTUM, STRUCTURE = 50, 30, 20


def _clip01(x):
    return np.clip(x, 0.0, 1.0)


def score_frame(d: pd.DataFrame) -> pd.DataFrame:
    """add_indicators 결과로 봉별 bull/bear 점수를 계산.

    추세 50: 종가>SMA200 20, 종가>SMA50 12, SMA50 기울기(10봉) 6, EMA20>EMA50 12
    모멘텀 30: RSI 12 (40→70 선형, 숏은 60→30), MACD 히스토그램 부호 9, ROC 부호 9
    구조 20: 52주 범위 내 위치 10, 돈치안(20) 중간선 위/아래 10
    """
    c = d["Close"]
    slope = d["sma_fast"].diff(10)
    valid = d[["sma_slow", "rsi", "macd_hist", "range_high"]].notna().all(axis=1)

    def side(sign: int) -> pd.DataFrame:
        gt = (lambda a, b: a > b) if sign > 0 else (lambda a, b: a < b)
        trend = (
            20 * gt(c, d["sma_slow"])
            + 12 * gt(c, d["sma_fast"])
            + 6 * gt(slope, 0)
            + 12 * gt(d["ema_fast"], d["ema_slow"])
        )
        rsi_part = _clip01((d["rsi"] - 40) / 30) if sign > 0 else _clip01((60 - d["rsi"]) / 30)
        mom = 12 * rsi_part + 9 * gt(d["macd_hist"], 0) + 9 * gt(d["roc"], 0)
        rng = (d["range_high"] - d["range_low"]).replace(0, np.nan)
        pos = _clip01((c - d["range_low"]) / rng).fillna(0.5)
        struct = 10 * (pos if sign > 0 else 1 - pos) + 10 * gt(c, d["donchian_mid"])
        return pd.DataFrame({"trend": trend, "momentum": mom, "structure": struct})

    bull, bear = side(+1), side(-1)
    out = pd.DataFrame(index=d.index)
    for name, part in (("bull", bull), ("bear", bear)):
        out[name] = part.sum(axis=1).astype(float).where(valid)
        for col in part:
            out[f"{name}_{col}"] = part[col].astype(float).where(valid)
    return out


def volatility_factor(rvol_rank: float | None, vix: float | None, cfg: dict) -> float:
    """실현변동성 백분위와 변동성지수(VIX/VXN)로 비중 감액 계수(min_factor~1)."""
    lo = cfg["min_factor"]
    factors = [1.0]
    if rvol_rank is not None and not np.isnan(rvol_rank):
        start = cfg["rank_start"]
        factors.append(1 - (1 - lo) * float(_clip01((rvol_rank - start) / (1 - start))))
    if vix is not None and not np.isnan(vix):
        factors.append(1 - (1 - lo) * float(_clip01((vix - cfg["vix_start"]) / (cfg["vix_full"] - cfg["vix_start"]))))
    return min(factors)


def regime_of(bull: float, bear: float, threshold: float) -> str:
    if bull >= threshold and bull >= bear:
        return "BULL"
    if bear >= threshold:
        return "BEAR"
    return "NEUTRAL"


@dataclass
class Signal:
    bull: float
    bear: float
    regime: str
    above_sma200: bool
    vol_factor: float
    components: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)

    @property
    def preferred_direction(self) -> int:
        """+1 롱(기초 상승 베팅), -1 숏."""
        return 1 if self.bull >= self.bear else -1

    def multiplier(self, direction: int, strat: dict) -> float:
        """국면 계수 × 역추세 상한."""
        key = "long" if direction > 0 else "short"
        m = strat["regime_multiplier"][self.regime][key]
        counter = (direction > 0 and not self.above_sma200) or (direction < 0 and self.above_sma200)
        if counter:
            m = min(m, strat["counter_trend_cap"])
        return float(m)


def evaluate(d: pd.DataFrame, strat: dict, vix: float | None = None) -> Signal:
    """마지막 봉 기준 신호."""
    sf = score_frame(d)
    last = sf.dropna(subset=["bull"])
    if last.empty:
        raise ValueError("지표 계산에 필요한 데이터가 부족합니다 (최소 sma_slow 봉 수 이상 필요)")
    row = last.iloc[-1]
    di = d.loc[last.index[-1]]
    bull, bear = float(row["bull"]), float(row["bear"])
    return Signal(
        bull=round(bull, 1),
        bear=round(bear, 1),
        regime=regime_of(bull, bear, strat["score"]["regime_threshold"]),
        above_sma200=bool(di["Close"] > di["sma_slow"]),
        vol_factor=round(volatility_factor(di["rvol_rank"], vix, strat["volatility"]), 3),
        components={k: round(float(v), 1) for k, v in row.items() if k not in ("bull", "bear")},
        values={
            "close": float(di["Close"]),
            "sma50": float(di["sma_fast"]),
            "sma200": float(di["sma_slow"]),
            "rsi": float(di["rsi"]),
            "atr": float(di["atr"]),
            "atr_pct": float(di["atr_pct"]),
            "rvol_rank": float(di["rvol_rank"]) if pd.notna(di["rvol_rank"]) else None,
            "drawdown": float(di["Close"] / di["range_high"] - 1),
            "vix": vix,
        },
    )
