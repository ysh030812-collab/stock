"""단타 전략 — RSI(2) 단기 되돌림, 1~5거래일 보유, 3배 레버리지 ETF.

기초지수 일봉 종가로 판단하고 다음 거래일에 실행한다.

1) 🟢 눌림 매수 : 종가 > 200일선 & RSI(2) < 10  → 3배 롱
2) 🟡 과매도 롱 : 종가 < 200일선 & RSI(2) < 10  → 3배 롱 (비중 절반)
3) 🔴 반등 숏   : 종가 < 200일선 & 50일선 < 200일선 & RSI(2) > 90 → 3배 숏
청산: 롱은 RSI(2) > 70 또는 5거래일째, 숏은 RSI(2) < 30 · 5거래일째 · 하락 조건 해제
비중 = min(100%, 목표 변동성 ÷ (3 × 최근 20일 변동성)) (× 0.5 과매도 롱)

자동 점검: 이 규칙을 '했다면'의 최근 3년(756거래일) 성과가 마이너스면 신호를 쉬고 현금.
  (단기 되돌림은 2000년 이후 여러 시장에서 통했지만 1999년 이전엔 크게 손해였음 → 효과가 사라지면 멈춤)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .backtest import BTResult, perf_stats
from .indicators import rsi as _rsi
from .trend import _bdays_after, rsi_target_price, simulate

DEFAULTS = {
    "ma": 200, "rsi_n": 2, "long_entry": 10, "long_exit": 70, "short_entry": 90, "short_exit": 30, "hold": 5,
    "bounce_scale": 0.5, "vt": 0.40, "vol_window": 20,
    "use_dip": True, "use_bounce": True, "use_short": True,
    "gate": True, "gate_lookback": 756, "rebalance_band": 0.10,
}
MODES = {"DIP": "🟢 눌림 매수", "BOUNCE": "🟡 과매도 롱", "SHORT": "🔴 반등 숏", "CASH": "💤 대기"}


def params(strat: dict | None = None) -> dict:
    return {**DEFAULTS, **((strat or {}).get("dant") or {})}


def signals(u: pd.DataFrame, p: dict | None = None) -> pd.DataFrame:
    """봉별 상태(오늘 종가 기준 '내일 보유할' 포지션)와 목표 비중."""
    p = {**DEFAULTS, **(p or {})}
    c = u["Close"]
    sma = c.rolling(p["ma"]).mean()
    s50 = c.rolling(50).mean()
    r2 = _rsi(c, p["rsi_n"])
    rv = c.pct_change().rolling(p["vol_window"]).std() * math.sqrt(252)
    C, SMA, S50, R2 = (x.to_numpy() for x in (c, sma, s50, r2))
    n = len(c)
    raw = np.array(["CASH"] * n, dtype=object)
    days_in = np.zeros(n, dtype=int)
    st, days, kind = 0, 0, None
    for i in range(n):
        if np.isnan(SMA[i]) or np.isnan(R2[i]):
            continue
        bear = C[i] < SMA[i] and S50[i] < SMA[i]
        if st:
            days += 1
            if kind in ("DIP", "BOUNCE") and (R2[i] > p["long_exit"] or days >= p["hold"]):
                st = 0
            elif kind == "SHORT" and (R2[i] < p["short_exit"] or days >= p["hold"] or not bear):
                st = 0
        if not st:
            if p["use_dip"] and C[i] > SMA[i] and R2[i] < p["long_entry"]:
                st, days, kind = 1, 0, "DIP"
            elif p["use_bounce"] and C[i] < SMA[i] and R2[i] < p["long_entry"]:
                st, days, kind = 1, 0, "BOUNCE"
            elif p["use_short"] and bear and R2[i] > p["short_entry"]:
                st, days, kind = 1, 0, "SHORT"
        if st:
            raw[i], days_in[i] = kind, days + 1
    w = (p["vt"] / (3 * rv)).clip(upper=1.0).fillna(0).to_numpy()
    scale = np.where(raw == "BOUNCE", p["bounce_scale"], np.where(raw == "CASH", 0.0, 1.0))
    side = np.where(raw == "SHORT", -1, np.where(raw == "CASH", 0, 1))
    # 자동 점검: 규칙을 그대로 따랐을 때의 가상 성과(기초 기준 3배) 최근 lookback 합
    r = c.pct_change().fillna(0).to_numpy()
    held = np.concatenate([[0.0], (w * scale * side)[:-1]])
    perf = pd.Series(held * 3 * r, index=c.index).rolling(int(p["gate_lookback"]), min_periods=int(p["gate_lookback"]) // 2).sum()
    gate_on = (perf > 0).to_numpy() if p["gate"] else np.ones(n, dtype=bool)
    mode = np.where((raw != "CASH") & ~gate_on, "CASH", raw)
    on = mode != "CASH"
    return pd.DataFrame({
        "Close": c, "sma": sma, "sma50": s50, "rsi2": r2, "rv": rv,
        "raw_mode": raw, "mode": mode, "side": np.where(on, side, 0), "weight": np.where(on, w * scale, 0.0),
        "days_in": days_in, "gate_on": gate_on, "gate_perf": perf,
    }, index=u.index)


def today(u: pd.DataFrame, p: dict | None = None) -> dict:
    """마지막 종가 기준 상태, 다음 진입 가격(RSI 역산), 보유 중이면 익절·시간청산·손절."""
    p = {**DEFAULTS, **(p or {})}
    s = signals(u, p)
    last, prev = s.iloc[-1], s.iloc[-2]
    c = u["Close"]
    close, sma, s50 = float(last["Close"]), float(last["sma"]), float(last["sma50"])
    mode, raw = last["mode"], last["raw_mode"]
    sd = float(c.pct_change().iloc[-20:].std())
    n = p["rsi_n"]
    rows = []
    if mode in ("DIP", "BOUNCE"):
        tp = rsi_target_price(c, n, p["long_exit"])
        left = max(1, p["hold"] - int(last["days_in"]) + 1)  # 오늘 종가 이후 남은 거래일 (그날 종가에 청산)
        rows += [
            {"구분": "익절", "가격": max(tp, close) if tp else None, "기준": "장중",
             "설명": f"이 가격 이상이면 RSI({n}) > {p['long_exit']} → 청산 (매일 재계산, 지정가로 걸어두기)"},
            {"구분": "시간 청산", "가격": None, "기준": "종가",
             "설명": f"익절이 안 되면 약 {_bdays_after(last.name, left).date()} 종가에 청산 ({left}거래일 후, 최대 {p['hold']}일 보유)"},
            {"구분": "비상 손절(선택)", "가격": close * (1 - 4 * sd), "기준": "장중",
             "설명": "규칙에는 없음 — 넣으면 백테스트상 수익이 약간 줄어듦 (−4σ)"},
        ]
    elif mode == "SHORT":
        tp = rsi_target_price(c, n, p["short_exit"])
        left = max(1, p["hold"] - int(last["days_in"]) + 1)
        rows += [
            {"구분": "익절", "가격": min(tp, close) if tp else None, "기준": "장중",
             "설명": f"이 가격 이하면 RSI({n}) < {p['short_exit']} → 청산 (매일 재계산)"},
            {"구분": "손절", "가격": sma, "기준": "종가", "설명": "종가가 200일선 위로 올라오면 하락 조건 해제 → 청산"},
            {"구분": "시간 청산", "가격": None, "기준": "종가",
             "설명": f"익절이 안 되면 약 {_bdays_after(last.name, left).date()} 종가에 청산 ({left}거래일 후, 최대 {p['hold']}일 보유)"},
            {"구분": "비상 손절(선택)", "가격": close * (1 + 4 * sd), "기준": "장중",
             "설명": "규칙에는 없음 — 넣으면 백테스트상 수익이 약간 줄어듦 (+4σ)"},
        ]
    else:
        lp = rsi_target_price(c, n, p["long_entry"])
        if close > sma and p["use_dip"]:
            rows.append({"구분": "🟢 눌림 매수 진입", "가격": lp, "기준": "종가",
                         "설명": f"종가가 이 가격 이하면 RSI({n}) < {p['long_entry']} → 다음 날 3배 롱"})
        if close < sma and p["use_bounce"]:
            rows.append({"구분": "🟡 과매도 롱 진입", "가격": lp, "기준": "종가",
                         "설명": f"종가가 이 가격 이하면 RSI({n}) < {p['long_entry']} → 다음 날 3배 롱(절반)"})
        if close < sma and s50 < sma and p["use_short"]:
            rows.append({"구분": "🔴 반등 숏 진입", "가격": rsi_target_price(c, n, p["short_entry"]), "기준": "종가",
                         "설명": f"종가가 이 가격 이상이면 RSI({n}) > {p['short_entry']} → 다음 날 3배 숏"})
    new_signal = mode != "CASH" and (prev["mode"] != mode or int(last["days_in"]) == 1)
    exit_signal = mode == "CASH" and prev["mode"] != "CASH"
    return {
        "date": last.name, "mode": mode, "label": MODES[mode], "side": int(last["side"]), "weight": float(last["weight"]),
        "days_in": int(last["days_in"]), "new": bool(new_signal), "exit": bool(exit_signal), "prev_mode": prev["mode"],
        "close": close, "sma": sma, "above": close > sma, "rsi2": float(last["rsi2"]), "rv": float(last["rv"]),
        "gate_on": bool(last["gate_on"]), "gate_perf": None if pd.isna(last["gate_perf"]) else float(last["gate_perf"]),
        "gated": MODES[raw] if (raw != "CASH" and mode == "CASH") else None, "levels": rows, "sigma_daily": sd,
    }


def backtest(u: pd.DataFrame, pair: dict, strat: dict | None = None, equity0: float = 10_000.0,
             start: str | None = None, cost_bps: float = 10.0, execution: str = "next_open",
             rf: pd.Series | None = None, borrow_spread: float = 0.005) -> BTResult:
    p = params(strat)
    s = signals(u, p)
    r = simulate(u, s, pair, rebalance_band=p["rebalance_band"], warm=p["ma"] + 5, modes=MODES, equity0=equity0,
                 start=start, cost_bps=cost_bps, execution=execution, rf=rf, borrow_spread=borrow_spread)
    # 단타의 비교 기준: 기초 1배 보유 · 3배 보유
    keep = [c for c in r.benchmarks.columns if c == "전략" or c.endswith(" 보유")]
    r.benchmarks = r.benchmarks[keep]
    r.bench_stats = pd.DataFrame({c: perf_stats(r.benchmarks[c]) for c in keep}).T
    return r
