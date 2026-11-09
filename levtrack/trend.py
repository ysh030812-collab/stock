"""추세 롱숏 전략 (3배 롱 / 3배 숏) — 실제 데이터 백테스트로 고른 기본 전략.

기초지수 일봉 종가로 판단하고 다음 거래일에 실행한다.

1) 추세 롱  : 종가 > 200일선×(1+buf) 진입, 종가 < 200일선×(1−buf) 청산 → 3배 롱 ETF
2) 반등 숏  : 200일선 아래 & 50일선 < 200일선 & RSI(2) > 90 진입,
              RSI(2) < 30 · 5거래일 · 하락 조건 해제 중 하나면 청산 → 3배 숏 ETF
3) 과매도 롱: 200일선 아래 & RSI(2) < 10 진입, RSI(2) > 70 · 5거래일이면 청산 → 3배 롱 ETF (비중 절반)
4) 그 외    : 현금

비중 = min(1, 목표 연변동성 / (3 × 최근 20일 실현변동성)) × (과매도 롱이면 0.5)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .backtest import BTResult, benchmarks, perf_stats
from .indicators import rsi as _rsi
from .leverage import synthetic_letf_ohlc

DEFAULTS = {
    "ma": 200, "buf": 0.03, "vt": 0.40, "vol_window": 20,
    "rsi_n": 2, "short_entry": 90, "short_exit": 30, "short_hold": 5,
    "bounce_entry": 10, "bounce_exit": 70, "bounce_hold": 5, "bounce_scale": 0.5,
    "rebalance_band": 0.10,
}
MODES = {"TREND": "📈 추세 롱", "BOUNCE": "🔁 과매도 롱", "SHORT": "📉 반등 숏", "CASH": "💤 현금"}


def params(strat: dict | None = None) -> dict:
    return {**DEFAULTS, **((strat or {}).get("trend_ls") or {})}


def signals(u: pd.DataFrame, p: dict | None = None) -> pd.DataFrame:
    """봉별 상태(오늘 종가 기준으로 '내일 보유할' 포지션)와 목표 비중."""
    p = {**DEFAULTS, **(p or {})}
    c = u["Close"]
    sma = c.rolling(p["ma"]).mean()
    s50 = c.rolling(50).mean()
    r2 = _rsi(c, p["rsi_n"])
    rv = c.pct_change().rolling(p["vol_window"]).std() * math.sqrt(252)
    up_band, dn_band = sma * (1 + p["buf"]), sma * (1 - p["buf"])
    C, UP, DN, SMA, S50, R2 = (x.to_numpy() for x in (c, up_band, dn_band, sma, s50, r2))
    n = len(c)
    mode = np.array(["CASH"] * n, dtype=object)
    trend = short = bounce = 0
    s_days = b_days = 0
    for i in range(n):
        if np.isnan(SMA[i]) or np.isnan(R2[i]):
            continue
        # 1) 추세 (히스테리시스)
        if trend == 0 and C[i] > UP[i]:
            trend = 1
        elif trend == 1 and C[i] < DN[i]:
            trend = 0
        below = C[i] < SMA[i]
        bear = below and S50[i] < SMA[i]
        # 2) 반등 숏
        if short:
            s_days += 1
            if R2[i] < p["short_exit"] or s_days >= p["short_hold"] or not bear:
                short = 0
        elif bear and R2[i] > p["short_entry"]:
            short, s_days = 1, 0
        # 3) 과매도 롱
        if bounce:
            b_days += 1
            if R2[i] > p["bounce_exit"] or b_days >= p["bounce_hold"]:
                bounce = 0
        elif below and R2[i] < p["bounce_entry"]:
            bounce, b_days = 1, 0
        if trend:
            mode[i] = "TREND"
        elif bounce:
            mode[i] = "BOUNCE"
        elif short:
            mode[i] = "SHORT"
    w = (p["vt"] / (3 * rv)).clip(upper=1.0).fillna(0)
    scale = np.where(mode == "BOUNCE", p["bounce_scale"], np.where(mode == "CASH", 0.0, 1.0))
    out = pd.DataFrame({
        "Close": c, "sma": sma, "sma50": s50, "up_band": up_band, "dn_band": dn_band, "rsi2": r2, "rv": rv,
        "mode": mode, "side": np.where(mode == "SHORT", -1, np.where(mode == "CASH", 0, 1)),
        "weight": w.to_numpy() * scale,
    }, index=u.index)
    return out


def today(u: pd.DataFrame, p: dict | None = None) -> dict:
    """마지막 종가 기준 상태 · 목표 비중 · 다음 신호까지의 조건."""
    p = {**DEFAULTS, **(p or {})}
    s = signals(u, p)
    last, prev = s.iloc[-1], s.iloc[-2]
    mode = last["mode"]
    c, sma = float(last["Close"]), float(last["sma"])
    since = s.index[-1]
    run = s["mode"].iloc[::-1]
    days_in = int((run == mode).cummin().sum()) if mode != "CASH" else 0
    triggers = []
    if mode == "TREND":
        triggers.append(f"종가 < {last['dn_band']:,.2f} (200일선 −{p['buf']:.0%}) 이면 추세 롱 청산")
    else:
        triggers.append(f"종가 > {last['up_band']:,.2f} (200일선 +{p['buf']:.0%}) 이면 추세 롱 진입")
    if c < sma:
        if mode != "SHORT" and last["sma50"] < sma:
            triggers.append(f"RSI({p['rsi_n']}) > {p['short_entry']} (반등 과열) 이면 반등 숏")
        if mode != "BOUNCE":
            triggers.append(f"RSI({p['rsi_n']}) < {p['bounce_entry']} (과매도) 이면 과매도 롱")
    if mode == "SHORT":
        triggers.append(f"RSI({p['rsi_n']}) < {p['short_exit']} 또는 {p['short_hold']}거래일째({max(0, p['short_hold'] - days_in)}일 남음) 청산")
    if mode == "BOUNCE":
        triggers.append(f"RSI({p['rsi_n']}) > {p['bounce_exit']} 또는 {p['bounce_hold']}거래일째({max(0, p['bounce_hold'] - days_in)}일 남음) 청산")
    return {
        "date": since, "mode": mode, "label": MODES[mode], "side": int(last["side"]),
        "weight": float(last["weight"]), "changed": mode != prev["mode"], "prev_mode": prev["mode"],
        "days_in": days_in, "close": c, "sma": sma, "dist": c / sma - 1, "rsi2": float(last["rsi2"]),
        "rv": float(last["rv"]), "up_band": float(last["up_band"]), "dn_band": float(last["dn_band"]),
        "triggers": triggers,
    }


def backtest(u: pd.DataFrame, pair: dict, strat: dict | None = None, equity0: float = 10_000.0,
             start: str | None = None, cost_bps: float = 10.0, execution: str = "next_open") -> BTResult:
    """추세 롱숏 백테스트 (합성 3배 ETF, 비중이 band 이상 바뀔 때만 조정).

    execution='next_open': i 일 종가 신호 → i+1 일 시가에 체결 (한국에서 아침 확인 후 밤 개장 주문하는 현실)
    execution='close'    : i 일 종가에 체결 (장 마감 동시호가 주문 가정)
    """
    p = params(strat)
    s = signals(u, p)
    long_ins = next((i for i in pair["instruments"] if i["leverage"] == 3), pair["instruments"][0])
    short_ins = next((i for i in pair["instruments"] if i["leverage"] == -3), None)
    C_ = u["Close"].to_numpy()
    O_ = u["Open"].to_numpy() if "Open" in u else C_
    r = u["Close"].pct_change().fillna(0).to_numpy()
    fee_l = long_ins.get("expense", 0.0) / 252
    fee_s = (short_ins or long_ins).get("expense", 0.0) / 252
    side, tw = s["side"].to_numpy(), s["weight"].to_numpy()
    if short_ins is None:
        tw = np.where(side < 0, 0.0, tw)
    n = len(s)
    warm = p["ma"] + 5
    i0 = max(warm, int(s.index.searchsorted(pd.Timestamp(start)))) if start else warm
    cost = cost_bps / 10_000
    eq = np.empty(n - i0)
    w_cur, side_cur, e = 0.0, 0, equity0
    pend = None  # next_open: (side, weight) 를 다음 날 시가에 적용
    expo = np.zeros(n - i0)
    trades, open_t = [], None

    def hold(w, sd, ret_u, fee):
        """보유 비중 w, 방향 sd 로 기초 수익 ret_u 구간을 지날 때 (계좌 배수, 새 비중)."""
        if sd == 0 or w <= 0:
            return 1.0, w
        lr = (3 * ret_u if sd > 0 else -3 * ret_u) - fee
        g = 1 + w * lr
        return g, (w * (1 + lr) / g if g > 0 else 0.0)

    for j, i in enumerate(range(i0, n)):
        fee = fee_l if side_cur > 0 else fee_s
        if execution == "next_open" and pend is not None and i > 0:
            # 갭 구간(전일 종가→오늘 시가)은 기존 포지션, 시가에 새 포지션으로 교체, 장중은 새 포지션
            gap = O_[i] / C_[i - 1] - 1
            g, w_cur = hold(w_cur, side_cur, gap, 0.0)
            e *= g
            tgt_side, tgt_w, change, mode_i = pend
            e *= 1 - change * cost
            w_cur, side_cur = (tgt_w, tgt_side) if tgt_side != 0 else (0.0, 0)
            fee = fee_l if side_cur > 0 else fee_s
            # 장중 LETF 수익: 시가 대비 종가 (일일 리셋은 전일 종가 기준)
            k = 3 if side_cur > 0 else -3
            l_open = 1 + k * gap
            intra = ((1 + k * (C_[i] / C_[i - 1] - 1)) / l_open - 1) if l_open > 0 else 0.0
            if side_cur != 0 and w_cur > 0:
                g2 = 1 + w_cur * (intra - fee)
                w_cur = w_cur * (1 + intra - fee) / g2 if g2 > 0 else 0.0
                e *= g2
            pend = None
        else:
            g, w_cur = hold(w_cur, side_cur, r[i], fee)
            e *= g
        eq[j] = e
        expo[j] = w_cur * 3 * side_cur
        # 오늘 종가 신호 → 내일 포지션
        tgt_side, tgt_w = int(side[i]), float(tw[i])
        if tgt_side != side_cur or (tgt_side != 0 and abs(tgt_w - w_cur) > p["rebalance_band"]):
            change = abs(tgt_w - w_cur) if tgt_side == side_cur else w_cur + tgt_w
            if execution == "next_open" and i < n - 1:
                pend = (tgt_side, tgt_w, change, s["mode"].iloc[i])
            else:
                e *= 1 - change * cost
            if tgt_side != side_cur or s["mode"].iloc[i] != (open_t or {}).get("mode"):
                if open_t:
                    open_t.update(exit=s.index[i], exit_eq=e)
                    trades.append(open_t)
                    open_t = None
                if tgt_side != 0:
                    open_t = {"mode": s["mode"].iloc[i], "entry": s.index[i], "entry_eq": e}
            if not (execution == "next_open" and i < n - 1):
                w_cur, side_cur = (tgt_w, tgt_side) if tgt_side != 0 else (0.0, 0)
        elif tgt_side != 0 and s["mode"].iloc[i] != (open_t or {}).get("mode"):
            # 같은 방향이지만 모드 전환(예: 과매도 롱 → 추세 롱)
            if open_t:
                open_t.update(exit=s.index[i], exit_eq=e)
                trades.append(open_t)
            open_t = {"mode": s["mode"].iloc[i], "entry": s.index[i], "entry_eq": e}
    if open_t:
        open_t.update(exit=s.index[-1], exit_eq=e)
        trades.append(open_t)

    dates = s.index[i0:]
    equity = pd.Series(eq, index=dates, name="전략")
    tr = pd.DataFrame([{
        "진입일": t["entry"], "청산일": t["exit"], "모드": MODES[t["mode"]],
        "방향": "숏" if t["mode"] == "SHORT" else "롱",
        "종목": (short_ins if t["mode"] == "SHORT" else long_ins)["ticker"],
        "계좌 기여": t["exit_eq"] / t["entry_eq"] - 1, "보유일": (t["exit"] - t["entry"]).days,
    } for t in trades])
    d = pd.DataFrame({"Close": u["Close"], "sma_slow": s["sma"]})
    letf = synthetic_letf_ohlc(u, long_ins["leverage"], long_ins.get("expense", 0.0), 100.0)["Close"]
    bm = benchmarks(d, letf, i0, equity0, long_ins["ticker"], pair["signal"])
    bm.insert(0, "전략", equity)
    bstats = pd.DataFrame({c: perf_stats(bm[c]) for c in bm}).T
    stats = {**perf_stats(equity), **_trade_stats(tr), "노출 비율": float(np.mean(expo != 0))}
    return BTResult(equity, tr, bm, stats, bstats, pd.Series(expo, index=dates))


def _trade_stats(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"거래 수": 0}
    win = tr["계좌 기여"] > 0
    by = tr.groupby("모드")["계좌 기여"]
    out = {"거래 수": int(len(tr)), "승률": float(win.mean()), "평균 보유일": float(tr["보유일"].mean()),
           "평균 거래 기여": float(tr["계좌 기여"].mean())}
    for m, g in by:
        out[f"{m} 횟수"] = int(len(g))
        out[f"{m} 합산 기여"] = float((1 + g).prod() - 1)
    return out


# ------------------------------------------------------------------ 규칙 → 가격 (TP/SL 표시용)
def rsi_target_price(close: pd.Series, n: int, target: float) -> float | None:
    """내일 종가가 얼마면 RSI(n) 이 target 이 되는지 (Wilder RSI 역산). 해가 없으면 None."""
    d = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().iloc[-1]
    al = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().iloc[-1]
    c0 = float(close.iloc[-1])
    if not (0 < target < 100) or np.isnan(ag) or np.isnan(al):
        return None
    rs = target / (100 - target)
    # 상승(d>0):  (AG(n−1)+d) / (AL(n−1)) = RS
    up = rs * al * (n - 1) - ag * (n - 1)
    if up > 0:
        return c0 + up
    # 하락(d<0):  AG(n−1) / (AL(n−1) − d) = RS
    dn = al * (n - 1) - ag * (n - 1) / rs
    if dn < 0:
        return c0 + dn
    return c0


def _bdays_after(day: pd.Timestamp, k: int) -> pd.Timestamp:
    return pd.bdate_range(day, periods=k + 1)[-1]


def levels(u: pd.DataFrame, td: dict, p: dict | None = None, emergency_sigma: float = 4.0) -> dict:
    """현재 상태의 손절·익절·시간청산·비상손절과 (현금일 때) 다음 진입 가격 — 모두 기초 가격.

    kind: '종가' = 그날 종가로 판단(알림 후 다음 날 실행), '장중' = 장중 지정가/감시가로 걸 수 있음
    """
    p = {**DEFAULTS, **(p or {})}
    c = u["Close"]
    sd = float(c.pct_change().iloc[-20:].std())
    close = td["close"]
    rows: list[dict] = []
    mode = td["mode"]
    if mode == "TREND":
        rows.append({"구분": "손절", "가격": td["dn_band"], "기준": "종가",
                     "설명": f"종가가 200일선 −{p['buf']:.0%} 아래면 청산 (200일선이 오르면 함께 올라가는 추격 손절)"})
        rows.append({"구분": "익절", "가격": None, "기준": "-", "설명": "없음 — 추세가 꺾일 때까지 보유"})
    elif mode == "BOUNCE":
        tp = rsi_target_price(c, p["rsi_n"], p["bounce_exit"])
        tp = max(tp, close) if tp is not None else None  # 이미 넘었으면 현재가 (오늘 종가로 청산 조건 충족)
        rows.append({"구분": "익절", "가격": tp, "기준": "장중",
                     "설명": f"이 가격 이상이면 RSI({p['rsi_n']}) > {p['bounce_exit']} → 청산 (매일 재계산)"})
        left = max(0, p["bounce_hold"] - td["days_in"])
        rows.append({"구분": "시간 청산", "가격": None, "기준": "종가",
                     "설명": f"{p['bounce_hold']}거래일째 종가에 청산 · 약 {_bdays_after(td['date'], left).date()} ({left}거래일 남음)"})
        rows.append({"구분": "비상 손절(선택)", "가격": close * (1 - emergency_sigma * sd), "기준": "장중",
                     "설명": f"규칙에는 없음. 넣으면 백테스트상 연 0~1.5%p 낮아짐 (−{emergency_sigma:g}σ)"})
    elif mode == "SHORT":
        tp = rsi_target_price(c, p["rsi_n"], p["short_exit"])
        tp = min(tp, close) if tp is not None else None
        rows.append({"구분": "익절", "가격": tp, "기준": "장중",
                     "설명": f"이 가격 이하면 RSI({p['rsi_n']}) < {p['short_exit']} → 청산 (매일 재계산)"})
        rows.append({"구분": "손절", "가격": td["sma"], "기준": "종가", "설명": "종가가 200일선 위로 올라오면 하락 조건 해제 → 청산"})
        left = max(0, p["short_hold"] - td["days_in"])
        rows.append({"구분": "시간 청산", "가격": None, "기준": "종가",
                     "설명": f"{p['short_hold']}거래일째 종가에 청산 · 약 {_bdays_after(td['date'], left).date()} ({left}거래일 남음)"})
        rows.append({"구분": "비상 손절(선택)", "가격": close * (1 + emergency_sigma * sd), "기준": "장중",
                     "설명": f"규칙에는 없음. 넣으면 백테스트상 연 0~1.5%p 낮아짐 (+{emergency_sigma:g}σ)"})
    else:  # 현금: 다음 진입 가격
        rows.append({"구분": "추세 롱 진입", "가격": td["up_band"], "기준": "종가",
                     "설명": f"종가가 이 가격(200일선 +{p['buf']:.0%}) 위면 다음 날 3배 롱"})
        if close < td["sma"]:
            rows.append({"구분": "과매도 롱 진입", "가격": rsi_target_price(c, p["rsi_n"], p["bounce_entry"]), "기준": "종가",
                         "설명": f"종가가 이 가격 이하면 RSI({p['rsi_n']}) < {p['bounce_entry']} → 다음 날 3배 롱(절반)"})
            if float(u["Close"].rolling(50).mean().iloc[-1]) < td["sma"]:
                rows.append({"구분": "반등 숏 진입", "가격": rsi_target_price(c, p["rsi_n"], p["short_entry"]), "기준": "종가",
                             "설명": f"종가가 이 가격 이상이면 RSI({p['rsi_n']}) > {p['short_entry']} → 다음 날 3배 숏"})
    return {"rows": rows, "sigma_daily": sd}
