import numpy as np
import pandas as pd
import pytest

from levtrack import trend as T
from levtrack.config import load_pairs
from levtrack.data import MarketData

PAIRS = load_pairs()


def frame(close):
    idx = pd.bdate_range("2015-01-01", periods=len(close))
    c = np.asarray(close, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.002, "Low": np.minimum(o, c) * 0.998, "Close": c,
                         "Volume": 1e6}, index=idx)


def test_trend_long_and_exit_band():
    up = 100 * np.exp(np.linspace(0, 0.6, 300))
    s = T.signals(frame(up))
    assert s["mode"].iloc[-1] == "TREND" and s["side"].iloc[-1] == 1
    # 이후 200일선 −3% 아래로 급락하면 추세 롱 해제
    down = np.concatenate([up, up[-1] * np.linspace(1, 0.6, 60)])
    s = T.signals(frame(down))
    assert s["mode"].iloc[-1] != "TREND"


def test_bounce_long_and_rally_short():
    rng = np.random.default_rng(0)
    base = 100 * np.exp(np.cumsum(-0.002 + rng.normal(0, 0.01, 320)))  # 하락 추세
    s = T.signals(frame(base), {"mr_gate": False})
    assert (s["mode"] == "TREND").sum() < 30
    modes = set(s["mode"].iloc[220:])
    assert "BOUNCE" in modes or "SHORT" in modes
    # 숏·과매도롱은 최대 5거래일
    for m in ("SHORT", "BOUNCE"):
        runs = (s["mode"] == m).astype(int).groupby((s["mode"] != m).cumsum()).sum()
        assert runs.max() <= 5
    assert (s.loc[s["mode"] == "BOUNCE", "weight"] <= s.loc[s["mode"] == "BOUNCE", "rv"].rdiv(0.40 / 3).clip(upper=1) * 0.5 + 1e-9).all()


def test_today_triggers():
    up = 100 * np.exp(np.linspace(0, 0.6, 300))
    td = T.today(frame(up))
    assert td["mode"] == "TREND" and 0 < td["weight"] <= 1
    assert any("청산" in x for x in td["triggers"])


@pytest.fixture(scope="module")
def demo():
    md = MarketData(PAIRS, mode="demo")
    return md.ohlc("QQQ", "1d", "max")


@pytest.mark.parametrize("execution", ["next_open", "close"])
def test_backtest_runs(demo, execution):
    r = T.backtest(demo, PAIRS[0], None, execution=execution)
    assert len(r.equity) > 1000 and r.stats["거래 수"] == len(r.trades) > 0
    assert set(r.trades["모드"]) <= set(T.MODES.values())
    assert set(r.trades.loc[r.trades["모드"] == T.MODES["SHORT"], "종목"]) <= {"SQQQ"}
    assert r.equity.min() > 0


def test_backtest_no_lookahead(demo):
    full = T.backtest(demo, PAIRS[0], None)
    cut = demo.index[int(len(demo) * 0.7)]
    part = T.backtest(demo[demo.index <= cut], PAIRS[0], None)
    common = part.equity.index[:-2]
    assert np.allclose(full.equity.loc[common], part.equity.loc[common])


def test_long_only_variant(demo):
    lo = T.backtest(demo, PAIRS[0], {"trend_ls": {"short_entry": 1e9}})
    assert (lo.trades["방향"] == "롱").all()


def test_levels_for_each_mode():
    up = frame(100 * np.exp(np.linspace(0, 0.6, 300)))
    td = T.today(up)
    rows = {r["구분"]: r for r in T.levels(up, td)["rows"]}
    assert td["mode"] == "TREND" and rows["손절"]["가격"] == pytest.approx(td["dn_band"]) and rows["익절"]["가격"] is None
    # 현금 상태: 진입 가격 제시
    cash = dict(td, mode="CASH", side=0)
    names = [r["구분"] for r in T.levels(up, cash)["rows"]]
    assert names[0] == "추세 롱 진입"
    # 과매도 롱 / 반등 숏: RSI 역산 익절가가 현재가의 올바른 쪽
    for mode, side in (("BOUNCE", 1), ("SHORT", -1)):
        st = dict(td, mode=mode, side=side, days_in=1)
        r = {x["구분"]: x for x in T.levels(up, st)["rows"]}
        tp = r["익절"]["가격"]
        assert (tp - td["close"]) * side > -1e-9
        assert "비상 손절(선택)" in r and "시간 청산" in r


def test_rsi_target_price_roundtrip():
    from levtrack.indicators import rsi
    c = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, 300))))
    for tgt in (10, 30, 70, 90):
        p = T.rsi_target_price(c, 2, tgt)
        assert rsi(pd.concat([c, pd.Series([p])], ignore_index=True), 2).iloc[-1] == pytest.approx(tgt, abs=1e-6)


def test_mr_gate_turns_off_losing_legs():
    """역추세 다리의 최근 성과가 마이너스면 반등 숏·과매도 롱을 쉬고, 추세 롱은 그대로."""
    rng = np.random.default_rng(5)
    # 하락장에서 추세가 계속 이어지는(되돌림 없는) 구간 → 과매도 롱이 계속 손해
    c = 100 * np.exp(np.cumsum(np.r_[0.002 + rng.normal(0, 0.008, 300), -0.004 + rng.normal(0, 0.012, 900)]))
    off = T.signals(frame(c), {"mr_gate": False})
    on = T.signals(frame(c), {"mr_gate": True, "gate_lookback": 252})
    assert (on["mode"] == "TREND").equals(off["mode"] == "TREND")
    mr_off = off["mode"].isin(["BOUNCE", "SHORT"]).sum()
    mr_on = on["mode"].isin(["BOUNCE", "SHORT"]).sum()
    assert mr_on < mr_off
    gated = on["raw_mode"].isin(["BOUNCE", "SHORT"]) & (on["mode"] == "CASH")
    assert (on.loc[gated, "mr_perf"].fillna(-1) <= 0).all()
