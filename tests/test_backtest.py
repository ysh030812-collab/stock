import numpy as np
import pandas as pd
import pytest

from levtrack.backtest import BTConfig, perf_stats, run_backtest, yearly_returns
from levtrack.config import load_pairs, load_strategy
from levtrack.data import MarketData

STRAT = load_strategy()
PAIRS = load_pairs()


@pytest.fixture(scope="module")
def demo():
    md = MarketData(PAIRS, mode="demo")
    pair = PAIRS[2]  # SPY (기준지수 없음 → 빠름)
    return pair, md.ohlc(pair["signal"], "1d", "max")


def test_runs_and_reports(demo):
    pair, u = demo
    r = run_backtest(u, pair, STRAT, BTConfig())
    assert len(r.equity) > 1000 and r.equity.iloc[0] == pytest.approx(10_000, rel=0.02)
    assert {"CAGR", "MDD", "샤프", "거래 수", "승률", "평균 R"} <= set(r.stats)
    assert r.stats["MDD"] <= 0 and r.stats["거래 수"] == len(r.trades) > 0
    assert set(r.trades["청산 사유"]) <= {"손절", "트레일링", "익절1", "익절2", "국면 전환", "기간 종료"}
    assert (r.trades["청산일"] >= r.trades["진입일"]).all()
    assert list(r.benchmarks.columns)[0] == "전략" and len(r.bench_stats) == 4


def test_no_lookahead(demo):
    """기간을 잘라 돌린 결과 = 전체로 돌린 결과 (자른 날 직전까지)."""
    pair, u = demo
    full = run_backtest(u, pair, STRAT, BTConfig())
    cut = u.index[int(len(u) * 0.7)]
    part = run_backtest(u[u.index <= cut], pair, STRAT, BTConfig())
    common = part.equity.index[:-2]  # 마지막 날은 '기간 종료' 강제 청산이라 제외
    assert np.allclose(full.equity.loc[common], part.equity.loc[common])
    t_full = full.trades[full.trades["청산일"] < common[-1]].reset_index(drop=True)
    t_part = part.trades[part.trades["청산일"] < common[-1]].reset_index(drop=True)
    pd.testing.assert_frame_equal(t_full, t_part)


def test_long_only_and_regime_only(demo):
    pair, u = demo
    r = run_backtest(u, pair, STRAT, BTConfig(allow_short=False, use_timing=False))
    assert set(r.trades["방향"]) == {"롱"}
    assert set(r.trades["타이밍"]) == {"국면만"}


def test_start_date_and_costs(demo):
    pair, u = demo
    start = str(u.index[1500].date())
    a = run_backtest(u, pair, STRAT, BTConfig(start=start, cost_bps=0))
    b = run_backtest(u, pair, STRAT, BTConfig(start=start, cost_bps=50))
    assert a.equity.index[0] >= pd.Timestamp(start)
    assert b.equity.iloc[-1] < a.equity.iloc[-1]  # 비용이 크면 결과가 나빠짐


def test_uptrend_makes_money():
    idx = pd.bdate_range("2010-01-01", periods=900)
    rng = np.random.default_rng(3)
    c = 100 * np.exp(np.cumsum(0.0015 + rng.normal(0, 0.008, len(idx))))
    o = np.concatenate([[c[0]], c[:-1]])
    u = pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.003, "Low": np.minimum(o, c) * 0.997, "Close": c,
                      "Volume": 1e6}, index=idx)
    r = run_backtest(u, PAIRS[2], STRAT, BTConfig(use_timing=False))
    assert r.equity.iloc[-1] > 10_000 and (r.trades["방향"] == "롱").mean() > 0.8


def test_perf_stats_and_yearly():
    eq = pd.Series([100, 110, 99, 120.0], index=pd.to_datetime(["2020-12-30", "2020-12-31", "2021-06-01", "2021-12-31"]))
    s = perf_stats(eq)
    assert s["총수익률"] == pytest.approx(0.2) and s["MDD"] == pytest.approx(99 / 110 - 1)
    y = yearly_returns(eq.to_frame("전략"))
    assert y.loc[2021, "전략"] == pytest.approx(120 / 110 - 1)
