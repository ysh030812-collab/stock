import math

import numpy as np
import pandas as pd
import pytest

from levtrack import leverage as lev
from levtrack.config import load_pairs, load_strategy
from levtrack.data import MarketData
from levtrack.indicators import add_indicators, atr, rsi
from levtrack.levels import stop_loss, take_profits
from levtrack.planner import build_plan
from levtrack.signals import evaluate, volatility_factor
from levtrack.sizing import risk_based_shares, size_position, split_entries, split_shares, vol_target_weight

STRAT = load_strategy()
PAIRS = load_pairs()


# ---------------- 레버리지 환산 ----------------
def test_convert_price_plan_example():
    # PLAN.md 4.6: QQQ 500→484, TQQQ 80 → 72.32
    assert lev.convert_price(500, 484, 80, 3) == pytest.approx(72.32)
    assert lev.convert_price(500, 516, 80, 3) == pytest.approx(87.68)


def test_convert_price_short_letf():
    # SQQQ: 기초 +3.2% → -9.6%
    assert lev.convert_price(500, 516, 20, -3) == pytest.approx(20 * (1 - 0.096))
    assert lev.convert_price(100, 140, 10, -3) == 0.0  # 0 아래로 내려가지 않음


def test_decay_matches_exact_path():
    # 등락을 반복하는 경로: 기초는 거의 제자리, 3x 는 감쇠
    r = np.tile([0.02, -0.02], 50)
    u = np.prod(1 + r)
    l_exact = np.prod(1 + 3 * r)
    sigma = r.std()
    l_approx = lev.project_price(1.0, u, 1.0, 3, sigma, len(r))
    assert l_exact < u < 1.0
    assert l_approx == pytest.approx(l_exact, rel=0.01)


def test_synthetic_letf_daily_returns():
    idx = pd.bdate_range("2024-01-01", periods=30)
    rng = np.random.default_rng(0)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 30)), index=idx)
    df = pd.DataFrame({"Open": close.shift().fillna(100), "High": close * 1.005,
                       "Low": close * 0.995, "Close": close})
    fee = 0.01
    l = lev.synthetic_letf_ohlc(df, 3, fee, start=50)
    lr, ur = l["Close"].pct_change().iloc[2:], close.pct_change().iloc[2:]
    expected = (1 + 3 * ur) * (1 - fee / 252) - 1
    assert np.allclose(lr, expected)
    s = lev.synthetic_letf_ohlc(df, -3, 0, start=50)
    assert (s["High"] >= s["Low"]).all()


def test_synthetic_letf_intraday_resets_daily():
    idx = pd.DatetimeIndex(["2024-01-02 10:30", "2024-01-02 15:30", "2024-01-03 10:30", "2024-01-03 15:30"])
    close = pd.Series([100, 102, 101, 104], index=idx, dtype=float)
    l = lev.synthetic_letf_close(close, 2, 0, start=10)
    # 첫날 기준은 첫 봉, 둘째 날 장중봉은 전일 종가(102) 기준
    day1 = 10 * (1 + 2 * (102 / 100 - 1))
    assert l.iloc[1] == pytest.approx(day1)
    assert l.iloc[2] == pytest.approx(day1 * (1 + 2 * (101 / 102 - 1)))
    assert l.iloc[3] == pytest.approx(day1 * (1 + 2 * (104 / 102 - 1)))


def test_tracking_error_zero_for_perfect_letf():
    idx = pd.bdate_range("2024-01-01", periods=60)
    u = pd.Series(100 * np.cumprod(1 + np.random.default_rng(1).normal(0, 0.01, 60)), index=idx)
    l = lev.synthetic_letf_close(u, 3, 0, start=30)
    te = lev.tracking_error(u, l, 3)
    assert te["daily"].dropna().abs().max() < 1e-12
    assert te["implied_k"].dropna().iloc[-1] == pytest.approx(3)


# ---------------- 지표 ----------------
def test_rsi_extremes():
    up = pd.Series(np.arange(1, 50, dtype=float))
    assert rsi(up).iloc[-1] == 100
    flat = pd.Series(np.full(40, 5.0))
    assert rsi(flat).iloc[-1] == 50


def test_atr_constant_range():
    n = 50
    df = pd.DataFrame({"High": np.full(n, 11.0), "Low": np.full(n, 9.0), "Close": np.full(n, 10.0)})
    assert atr(df, 14).iloc[-1] == pytest.approx(2.0)


# ---------------- 레벨 · 사이징 ----------------
def _ind_frame(direction=1, n=300):
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = pd.Series(np.linspace(100, 100 + direction * 60, n), index=idx)
    df = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1})
    return add_indicators(df, STRAT["indicators"])


def test_stop_loss_side_and_tps():
    d = _ind_frame()
    e = float(d["Close"].iloc[-1])
    for m in ("atr", "swing", "percent", "bollinger"):
        assert stop_loss(d, e, +1, STRAT["levels"], m) < e
        assert stop_loss(d, e, -1, STRAT["levels"], m) > e
    assert take_profits(500, 484, [1, 2]) == [516, 532]
    assert take_profits(500, 516, [1]) == [484]


def test_sizing_plan_example():
    # PLAN.md 4.6: $10,000, 1%, TQQQ 80 → 72.32 → 13주, 중립(×0.5) → 6주
    assert risk_based_shares(10_000, 0.01, 80, 72.32) == 13
    assert vol_target_weight(0.20, 3, 0.22 / math.sqrt(252)) == pytest.approx(0.303, abs=1e-3)
    s = size_position(10_000, 80, 72.32, 3, 0.22 / math.sqrt(252), 0.01, 0.20, 0.30, regime_mult=0.5)
    assert s.binding == "리스크 기준"
    assert s.shares == 6
    assert s.risk_usd == pytest.approx(6 * 7.68)


def test_split_helpers():
    assert split_shares(10, [0.33, 0.33]) == [3, 3, 4]
    plan = split_entries([100, 95, 90], [0.4, 0.3, 0.3], stop=85, total_shares=10)
    assert [t["shares"] for t in plan["tranches"]] == [4, 3, 3]
    assert plan["avg_price"] == pytest.approx((400 + 285 + 270) / 10)


def test_volatility_factor_bounds():
    cfg = STRAT["volatility"]
    assert volatility_factor(0.3, 15, cfg) == 1.0
    assert volatility_factor(1.0, None, cfg) == pytest.approx(cfg["min_factor"])
    assert volatility_factor(0.2, 50, cfg) == pytest.approx(cfg["min_factor"])


# ---------------- 신호 · 플랜 (데모 데이터) ----------------
def test_signal_uptrend_is_bull():
    sig = evaluate(_ind_frame(+1), STRAT)
    assert sig.regime == "BULL" and sig.above_sma200
    assert sig.multiplier(+1, STRAT) == 1.0
    assert sig.multiplier(-1, STRAT) == 0.0
    sig = evaluate(_ind_frame(-1), STRAT)
    assert sig.regime == "BEAR"
    assert sig.multiplier(-1, STRAT) == 1.0


@pytest.mark.parametrize("interval", ["1d", "1h"])
def test_end_to_end_demo(interval):
    md = MarketData(PAIRS, mode="demo")
    pair = PAIRS[0]
    d = add_indicators(md.ohlc(pair["signal"], interval), STRAT["indicators"])
    sig = evaluate(add_indicators(md.ohlc(pair["signal"], "1d"), STRAT["indicators"]), STRAT)
    for ins in pair["instruments"]:
        k = ins["leverage"]
        plan = build_plan(d, pair["signal"], ins["ticker"], k, md.last(ins["ticker"], interval), 10_000, STRAT,
                          regime_mult=1.0, vol_factor=sig.vol_factor, expense=ins["expense"])
        assert plan.l_stop < plan.l_entry < plan.l_tps[0] < plan.l_tps[1]
        assert (plan.u_stop - plan.u_entry) * plan.direction < 0
        assert plan.sizing.risk_usd <= 10_000 * STRAT["sizing"]["risk_pct"] + 1e-6
        assert sum(plan.exit_shares) == plan.sizing.shares
        sheet = plan.order_sheet()
        assert 2 <= len(sheet) <= 5
        assert sheet.loc[sheet.index.difference(["1", "2"]), "수량"].sum() == plan.sizing.shares
        assert not plan.levels_table().empty


def test_fetch_yahoo_normalizes(monkeypatch, tmp_path):
    import sys
    import types

    idx = pd.date_range("2025-01-02 09:30", periods=3, freq="h", tz="America/New_York")
    raw = pd.DataFrame({"Open": [1.0, 2, 3], "High": [1.0, 2, 3], "Low": [1.0, 2, 3], "Close": [1.0, 2, 3],
                        "Adj Close": [1.0, 2, 3], "Volume": [1, 2, 3]}, index=idx)

    class FakeTicker:
        def __init__(self, t):
            pass

        def history(self, **kw):
            assert kw["auto_adjust"] is False
            return raw

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))
    md = MarketData(PAIRS, mode="yahoo", cache_dir=tmp_path)
    h = md.ohlc("QQQ", "1h")
    assert list(h.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert h.index.tz is None and h.index[0] == pd.Timestamp("2025-01-02 09:30")
    d = MarketData(PAIRS, mode="yahoo", cache_dir=tmp_path).ohlc("QQQ", "1d")
    assert d.index[0] == pd.Timestamp("2025-01-02")
    # 캐시 파일 재사용
    assert (tmp_path / "QQQ_1h.csv").exists()
    cached = MarketData(PAIRS, mode="yahoo", cache_dir=tmp_path).ohlc("QQQ", "1h")
    assert cached["Close"].tolist() == [1.0, 2.0, 3.0]


def test_sizing_explain_and_bottleneck():
    # 변동성 큰 종목: 리스크 기준은 크지만 변동성 타깃에 걸리고, 중립(×0.5)·변동성(×0.5)으로 더 줄어듦
    s = size_position(10_000, 50, 49, 3, 0.03, 0.03, 0.20, 0.30, regime_mult=0.5, vol_factor=0.5)
    assert s.binding == "변동성 타깃"
    assert s.w_vol == pytest.approx(0.20 / (3 * 0.03 * math.sqrt(252)))
    assert "변동성 타깃" in s.explain()
    assert s.bottleneck().startswith("변동성 타깃 제한")
    plain = size_position(10_000, 80, 72.32, 3, 0.22 / math.sqrt(252), 0.01, 0.20, 0.30)
    assert plain.bottleneck() is None
