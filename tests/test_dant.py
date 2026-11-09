import numpy as np
import pandas as pd
import pytest

from levtrack import dant as D
from levtrack.config import load_pairs
from levtrack.data import MarketData
from levtrack.indicators import rsi

PAIRS = load_pairs()


def frame(close):
    idx = pd.bdate_range("2015-01-01", periods=len(close))
    c = np.asarray(close, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.002, "Low": np.minimum(o, c) * 0.998, "Close": c,
                         "Volume": 1e6}, index=idx)


def zigzag(n=600, drift=0.0008, seed=2):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.012, n)))


def test_signal_rules_and_hold_limit():
    u = frame(zigzag())
    s = D.signals(u, {"gate": False})
    r2 = rsi(u["Close"], 2)
    starts = s.index[(s["mode"] != "CASH") & (s["days_in"] == 1)]
    assert len(starts) > 5
    for t in starts:
        m = s.at[t, "mode"]
        if m == "DIP":
            assert u.at[t, "Close"] > s.at[t, "sma"] and r2[t] < 10
        elif m == "BOUNCE":
            assert u.at[t, "Close"] < s.at[t, "sma"] and r2[t] < 10
        else:
            assert m == "SHORT" and r2[t] > 90
    assert s["days_in"].max() <= 5
    assert (s.loc[s["mode"] == "BOUNCE", "weight"] <= 0.5 + 1e-9).all()


def test_toggles():
    u = frame(zigzag())
    only_dip = D.signals(u, {"gate": False, "use_bounce": False, "use_short": False})
    assert set(only_dip["mode"]) <= {"DIP", "CASH"}
    no_short = D.signals(u, {"gate": False, "use_short": False})
    assert "SHORT" not in set(no_short["mode"])


def test_gate_stops_losing_rule():
    # 강한 추세(되돌림 없이 계속 오르고 내림) → 단기 되돌림 규칙이 손해 → 점검이 끔
    rng = np.random.default_rng(9)
    seg = np.r_[0.004 + rng.normal(0, 0.006, 500), -0.004 + rng.normal(0, 0.006, 500)]
    u = frame(100 * np.exp(np.cumsum(seg)))
    off = D.signals(u, {"gate": False, "gate_lookback": 200})
    on = D.signals(u, {"gate": True, "gate_lookback": 200})
    assert (on["mode"] != "CASH").sum() <= (off["mode"] != "CASH").sum()
    gated = (on["raw_mode"] != "CASH") & (on["mode"] == "CASH")
    assert (on.loc[gated, "gate_perf"].fillna(-1) <= 0).all()


def test_today_levels():
    u = frame(zigzag())
    td = D.today(u, {"gate": False})
    assert td["label"] in D.MODES.values() and isinstance(td["levels"], list)
    if td["mode"] == "CASH":
        assert all("진입" in r["구분"] for r in td["levels"])
    else:
        assert any(r["구분"] == "익절" for r in td["levels"])


@pytest.fixture(scope="module")
def demo():
    return MarketData(PAIRS, mode="demo").ohlc("QQQ", "1d", "max")


def test_backtest_and_no_lookahead(demo):
    full = D.backtest(demo, PAIRS[0], {"dant": {"gate": False}})
    assert full.stats["거래 수"] > 10 and set(full.trades["모드"]) <= set(D.MODES.values())
    assert list(full.benchmarks.columns) == ["전략", "QQQ 보유", "TQQQ 보유"]
    cut = demo.index[int(len(demo) * 0.7)]
    part = D.backtest(demo[demo.index <= cut], PAIRS[0], {"dant": {"gate": False}})
    common = part.equity.index[:-2]
    assert np.allclose(full.equity.loc[common], part.equity.loc[common])
