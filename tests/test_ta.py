import numpy as np
import pandas as pd
import pytest

from levtrack.config import load_strategy
from levtrack.indicators import add_indicators, add_relative_strength, obv, updown_volume_ratio
from levtrack.levels import stop_loss, take_profit_levels
from levtrack.patterns import candle_patterns, find_divergences, pivot_flags
from levtrack.signals import WEIGHTS, evaluate, score_frame
from levtrack.structure import fibonacci, key_levels, volume_profile
from levtrack.timing import entry_timing

STRAT = load_strategy()


def ohlc(rows, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx, dtype=float)


# ---------------- 거래량 · 상대강도 ----------------
def test_obv_and_updown():
    df = pd.DataFrame({"Close": [10, 11, 10, 12, 12.0], "Volume": [100, 200, 50, 300, 80.0]})
    assert obv(df).tolist() == [0, 200, 150, 450, 450]
    r = updown_volume_ratio(df, 4)
    assert r.iloc[-1] == pytest.approx((200 + 300) / (200 + 50 + 300 + 80))


def test_relative_strength_alignment():
    idx = pd.bdate_range("2024-01-01", periods=80)
    d = pd.DataFrame({"Close": np.linspace(100, 180, 80)}, index=idx)
    bench = pd.Series(np.linspace(100, 120, 80), index=idx)
    out = add_relative_strength(d, bench)
    assert out["rs"].iloc[-1] == pytest.approx(1.5)
    assert out["rs"].iloc[-1] > out["rs_sma"].iloc[-1] and out["rs_roc"].iloc[-1] > 0


# ---------------- 캔들 패턴 ----------------
def test_engulfing_and_star_patterns():
    p = candle_patterns(ohlc([[10, 10.2, 9.4, 9.5], [9.4, 10.6, 9.3, 10.5]]))
    assert p["bull_engulfing"].iloc[-1] and not p["bear_engulfing"].iloc[-1]
    p = candle_patterns(ohlc([[9.5, 10.6, 9.4, 10.5], [10.6, 10.7, 9.3, 9.4]]))
    assert p["bear_engulfing"].iloc[-1]
    rows = [[12, 12.1, 11.9, 12]] * 3 + [[12, 12.1, 10.0, 10.1], [10.0, 10.1, 9.8, 9.95], [10.0, 11.4, 9.9, 11.3]]
    p = candle_patterns(ohlc(rows))
    assert p["morning_star"].iloc[-1]


def test_hammer_requires_downtrend():
    down = [[c, c + 0.1, c - 0.1, c - 0.05] for c in (15, 14, 13, 12, 11, 10)]
    hammer = [10, 10.05, 9.0, 10.0]  # 긴 아래꼬리, 작은 몸통
    assert candle_patterns(ohlc(down + [hammer]))["hammer"].iloc[-1]
    up = [[c, c + 0.1, c - 0.1, c + 0.05] for c in (5, 6, 7, 8, 9, 10)]
    assert not candle_patterns(ohlc(up + [hammer]))["hammer"].iloc[-1]


# ---------------- 다이버전스 ----------------
def test_pivot_flags():
    s = pd.Series([5, 4, 3, 2, 3, 4, 5, 4, 3, 4.0])
    assert np.flatnonzero(pivot_flags(s, 2, 2, "low")).tolist() == [3]
    assert np.flatnonzero(pivot_flags(s, 2, 2, "high")).tolist() == [6]


def test_bullish_divergence_detected():
    # 가격: 저점 10 → 9 (더 낮음), RSI: 저점 20 → 30 (더 높음)
    low = [12, 11, 10, 11, 12, 13, 12, 11, 9, 11, 12, 13, 13]
    rsi = [40, 30, 20, 30, 40, 50, 45, 38, 30, 40, 50, 55, 55]
    idx = pd.bdate_range("2024-01-01", periods=len(low))
    d = pd.DataFrame({"Low": low, "High": [x + 1 for x in low], "rsi": rsi}, index=idx, dtype=float)
    divs = find_divergences(d, "rsi", left=2, right=2, lookback=60, min_gap=3)
    bull = [x for x in divs if x["kind"] == "bullish"]
    assert len(bull) == 1
    assert bull[0]["p1"] == 10 and bull[0]["p2"] == 9 and bull[0]["o2"] > bull[0]["o1"]
    assert bull[0]["bars_ago"] == 4


# ---------------- 피보나치 · 매물대 ----------------
def test_fibonacci_up_swing():
    rows = [[100, 101, 99, 100]] + [[100 + i, 101 + i, 99 + i, 100 + i] for i in range(1, 50)]
    fib = fibonacci(ohlc(rows), 120)
    assert fib.up and fib.lo == 99 and fib.hi == 150
    assert fib.retr[0.5] == pytest.approx(124.5)
    assert fib.ext[1.618] == pytest.approx(99 + 1.618 * 51)


def test_volume_profile_poc_and_value_area():
    rows = [[100, 101, 99, 100]] * 30 + [[110, 111, 109, 110]] * 5
    df = ohlc(rows).assign(Volume=[1000.0] * 30 + [100.0] * 5)
    vp = volume_profile(df, 120, 24, 0.7)
    assert 99 <= vp.poc <= 101
    assert vp.val >= 98.9 and vp.vah <= 101.5
    assert volume_profile(df.assign(Volume=0.0)) is None


def _trend_frame(direction=1, n=320, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(direction * 0.002 + rng.normal(0, 0.01, n)))
    o = np.concatenate([[close[0]], close[:-1]])
    df = pd.DataFrame({"Open": o, "High": np.maximum(o, close) * 1.004, "Low": np.minimum(o, close) * 0.996,
                       "Close": close, "Volume": rng.integers(1e6, 2e6, n).astype(float)},
                      index=pd.bdate_range("2022-01-03", periods=n))
    return add_indicators(df, STRAT["indicators"])


def test_key_levels_and_structure_stops():
    d = _trend_frame()
    kl = key_levels(d, STRAT["structure"])
    assert {"fib", "vp", "range"} <= set(kl["source"])
    e = float(d["Close"].iloc[-1])
    lv = STRAT["levels"]
    sl = stop_loss(d, e, +1, lv, "support", kl)
    assert sl < e - lv["support_min_atr"] * d["atr"].iloc[-1]
    assert stop_loss(d, e, -1, lv, "support", kl) > e
    for m in ("r", "fib", "resistance"):
        tps = take_profit_levels(e, sl, lv, m, kl)
        prices = [p for p, _ in tps]
        assert len(prices) == len(lv["r_multiples"])
        assert all(b > a for a, b in zip([e] + prices, prices))  # 롱: 단조 증가
        assert prices[0] - e >= lv["tp_min_r"] * (e - sl) - 1e-9


# ---------------- 점수 정규화 ----------------
def test_score_normalized_without_optional_categories():
    d = _trend_frame()
    full = score_frame(d)
    no_vol = score_frame(d.drop(columns=["obv", "obv_ema", "updown_vol", "vol_ratio"]))
    assert full["bull"].max() <= 100 and no_vol["bull"].max() <= 100
    assert sum(WEIGHTS.values()) == 100
    sig = evaluate(d, STRAT)
    assert 0 <= sig.bull <= 100 and "bull_volume" in sig.components


# ---------------- 진입 타이밍 ----------------
TP = {**STRAT["indicators"], "sma_slow": 50, "range_window": 50, "vol_rank_window": 50}


def _zigzag_uptrend(n=80):
    """등락을 섞은 완만한 상승 추세 (마지막 종가 122.9)."""
    rows = []
    for i in range(n):
        c = 100 + 0.3 * i + 0.8 * (-1) ** i
        o = c - 1.2 * (-1) ** i
        rows.append([o, max(o, c) + 0.4, min(o, c) - 0.4, c])
    return rows


def _frame(rows, vol_last=None):
    df = ohlc(rows).assign(Volume=1000.0)
    if vol_last:
        df.iloc[-1, df.columns.get_loc("Volume")] = vol_last
    return add_indicators(df, TP)


def test_timing_blocked_and_overheated():
    straight = [[100 + i * 0.5, 100.6 + i * 0.5, 99.6 + i * 0.5, 100.4 + i * 0.5] for i in range(80)]
    h = _frame(straight)
    t = entry_timing(h, +1, allowed=False, cfg=STRAT["timing"], div_cfg=STRAT["divergence"])
    assert t.state.startswith("🚫") and not t.ok
    t = entry_timing(h, +1, allowed=True, cfg=STRAT["timing"], div_cfg=STRAT["divergence"])
    assert t.state.startswith("⚠️")  # 일방적 상승 → RSI 과열


def test_timing_breakout_with_volume():
    last = 122.9
    flat = [[last + 0.3 * (-1) ** i, last + 0.6, last - 0.6, last - 0.3 * (-1) ** i] for i in range(30)]
    h = _frame(_zigzag_uptrend() + flat + [[last, last + 2.6, last - 0.1, last + 2.4]], vol_last=3000.0)
    t = entry_timing(h, +1, allowed=True, cfg=STRAT["timing"], div_cfg=STRAT["divergence"])
    assert t.state == "✅ 돌파 진입" and t.ok
    # 거래량이 늘지 않으면 돌파로 인정하지 않음
    h2 = _frame(_zigzag_uptrend() + flat + [[last, last + 2.6, last - 0.1, last + 2.4]])
    assert entry_timing(h2, +1, True, STRAT["timing"], STRAT["divergence"]).state != "✅ 돌파 진입"


def test_timing_pullback_trigger():
    # 상승 추세 중 EMA20 아래로 눌렸다가 상승 장악형으로 회복
    last = 122.9
    pull = [[last, last + 0.2, last - 2.0, last - 1.8], [last - 1.8, last - 1.6, last - 3.2, last - 3.0],
            [last - 3.1, last + 0.2, last - 3.3, last + 0.1]]
    t = entry_timing(_frame(_zigzag_uptrend() + pull), +1, True, STRAT["timing"], STRAT["divergence"])
    assert t.ok and t.state == "✅ 눌림 반등 진입"
    assert "상승 장악형" in t.reasons and "EMA20 회복" in t.reasons
