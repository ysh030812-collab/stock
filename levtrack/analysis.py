"""페어 단위 분석: 데이터 준비 → 일봉 신호 → 하위 봉 진입 타이밍."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .data import MarketData
from .indicators import add_indicators, add_relative_strength
from .signals import Signal, evaluate
from .timing import Timing, entry_timing


def prepare(md: MarketData, pair: dict, interval: str, strat: dict) -> pd.DataFrame:
    """기초 OHLC + 지표 + (기준지수 있으면) 상대강도."""
    p = strat["indicators"]
    d = add_indicators(md.ohlc(pair["signal"], interval), p)
    bench = pair.get("benchmark")
    if bench:
        try:
            d = add_relative_strength(d, md.ohlc(bench, interval)["Close"], p.get("rs_sma", 50), p.get("rs_roc", 20))
        except Exception:  # noqa: BLE001  기준지수를 못 받으면 상대강도 항목만 제외
            pass
    return d


@dataclass
class PairAnalysis:
    pair: dict
    daily: pd.DataFrame
    signal: Signal
    lower: pd.DataFrame | None
    timing: dict  # {+1: Timing, -1: Timing}

    @property
    def direction(self) -> int:
        return self.signal.preferred_direction

    def instrument(self, direction: int | None = None) -> dict:
        d = self.direction if direction is None else direction
        return next(i for i in self.pair["instruments"] if (i["leverage"] > 0) == (d > 0))


def analyze_pair(md: MarketData, pair: dict, strat: dict, timing_interval: str | None = None) -> PairAnalysis:
    daily = prepare(md, pair, "1d", strat)
    sig = evaluate(daily, strat, md.try_last(pair.get("vol_index")))
    ti = timing_interval or strat["timing"].get("interval", "1h")
    try:
        lower = prepare(md, pair, ti, strat)
    except Exception:  # noqa: BLE001  하위 봉을 못 받으면 일봉으로 대체
        lower = daily
    timing: dict[int, Timing] = {}
    for d in (1, -1):
        timing[d] = entry_timing(lower, d, sig.multiplier(d, strat) > 0, strat["timing"], strat["divergence"])
    return PairAnalysis(pair, daily, sig, lower, timing)
