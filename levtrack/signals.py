"""방향 점수 · 국면 · 변동성 계수 (기초자산 기준)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .patterns import candle_patterns, recent_divergences, recent_patterns

# 카테고리 최대 배점 (합 100). 데이터가 없는 카테고리(거래량 0, 기준지수 없음)는 빼고 100점으로 환산.
WEIGHTS = {"trend": 40, "momentum": 25, "structure": 15, "volume": 10, "rs": 10}


def _clip01(x):
    return np.clip(x, 0.0, 1.0)


def score_frame(d: pd.DataFrame) -> pd.DataFrame:
    """봉별 bull/bear 점수.

    추세 40: 종가>SMA200 15, 종가>SMA50 10, SMA50 기울기(10봉) 5, EMA20>EMA50 10
    모멘텀 25: RSI 10 (40→70 선형, 숏은 60→30), MACD 히스토그램 부호 8, ROC 부호 7
    구조 15: 52주 범위 내 위치 8, 돈치안(20) 중간선 위/아래 7
    거래량 10: OBV > OBV EMA 6, 상승봉 거래량 비중 > 50% 4
    상대강도 10: 기준지수 대비 RS > RS SMA50 6, RS 20봉 변화율 > 0 4
    """
    c = d["Close"]
    slope = d["sma_fast"].diff(10)
    valid = d[["sma_slow", "rsi", "macd_hist", "range_high"]].notna().all(axis=1)
    has_vol = "obv_ema" in d and "updown_vol" in d
    has_rs = "rs_sma" in d

    def side(sign: int) -> pd.DataFrame:
        gt = (lambda a, b: a > b) if sign > 0 else (lambda a, b: a < b)
        parts = {}
        parts["trend"] = (15 * gt(c, d["sma_slow"]) + 10 * gt(c, d["sma_fast"]) + 5 * gt(slope, 0)
                          + 10 * gt(d["ema_fast"], d["ema_slow"]))
        rsi_part = _clip01((d["rsi"] - 40) / 30) if sign > 0 else _clip01((60 - d["rsi"]) / 30)
        parts["momentum"] = 10 * rsi_part + 8 * gt(d["macd_hist"], 0) + 7 * gt(d["roc"], 0)
        rng = (d["range_high"] - d["range_low"]).replace(0, np.nan)
        pos = _clip01((c - d["range_low"]) / rng).fillna(0.5)
        parts["structure"] = 8 * (pos if sign > 0 else 1 - pos) + 7 * gt(c, d["donchian_mid"])
        if has_vol:
            parts["volume"] = (6 * gt(d["obv"], d["obv_ema"]) + 4 * gt(d["updown_vol"], 0.5)).where(
                d["updown_vol"].notna())
        if has_rs:
            parts["rs"] = (6 * gt(d["rs"], d["rs_sma"]) + 4 * gt(d["rs_roc"], 0)).where(d["rs_sma"].notna())
        return pd.DataFrame(parts).astype(float)

    bull, bear = side(+1), side(-1)
    max_pts = pd.DataFrame({k: bull[k].notna() * WEIGHTS[k] for k in bull}).sum(axis=1)
    out = pd.DataFrame(index=d.index)
    for name, part in (("bull", bull), ("bear", bear)):
        out[name] = (part.sum(axis=1) / max_pts * 100).where(valid)
        for col in part:
            out[f"{name}_{col}"] = part[col].where(valid)
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
    divergences: list = field(default_factory=list)
    patterns: list = field(default_factory=list)

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
    """마지막 봉 기준 신호. 최근 다이버전스는 해당 방향 점수에 가산, 반대 방향에서 차감."""
    sf = score_frame(d)
    last = sf.dropna(subset=["bull"])
    if last.empty:
        raise ValueError("지표 계산에 필요한 데이터가 부족합니다 (최소 sma_slow 봉 수 이상 필요)")
    row = last.iloc[-1]
    di = d.loc[last.index[-1]]
    bull, bear = float(row["bull"]), float(row["bear"])

    dcfg = strat["divergence"]
    divs = recent_divergences(d, dcfg)
    adj = sum(dv["bias"] * dcfg["points"] for dv in divs)
    bull = float(np.clip(bull + adj, 0, 100))
    bear = float(np.clip(bear - adj, 0, 100))
    pats = recent_patterns(candle_patterns(d.iloc[-30:]), 3)

    return Signal(
        bull=round(bull, 1),
        bear=round(bear, 1),
        regime=regime_of(bull, bear, strat["score"]["regime_threshold"]),
        above_sma200=bool(di["Close"] > di["sma_slow"]),
        vol_factor=round(volatility_factor(di["rvol_rank"], vix, strat["volatility"]), 3),
        components={k: round(float(v), 1) for k, v in row.items() if k not in ("bull", "bear") and pd.notna(v)},
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
            "rs_above": bool(di["rs"] > di["rs_sma"]) if "rs_sma" in d and pd.notna(di.get("rs_sma")) else None,
            "obv_up": bool(di["obv"] > di["obv_ema"]) if "obv_ema" in d and pd.notna(di.get("obv_ema")) else None,
            "vol_ratio": float(di["vol_ratio"]) if "vol_ratio" in d and pd.notna(di.get("vol_ratio")) else None,
            "divergence_adj": adj,
        },
        divergences=divs,
        patterns=pats,
    )
