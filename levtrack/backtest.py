"""백테스트: 앱과 같은 규칙(국면 · 타이밍 · 손절/익절/분할/트레일 · 사이징)을 일봉으로 재현.

가정
- 데이터: 기초자산 일봉. 레버리지 ETF 는 기초자산으로 일일 리셋 가격을 합성(보수 반영) → 상장 전 기간도 검증 가능.
- 신호는 i 일 종가로 판단, i+1 일 시가에 진입 (미래 데이터 미사용).
- 손절·익절은 기초 가격 기준. 갭이면 시가 체결, 같은 날 손절·익절이 겹치면 손절 우선(보수적).
- 진입 타이밍: 실제 앱은 시간봉을 쓰지만, 백테스트는 같은 규칙을 일봉에 적용(근사).
- 다이버전스 보정은 피벗이 확정된 날부터 반영.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import add_indicators, add_relative_strength
from .leverage import synthetic_letf_ohlc
from .levels import stop_loss, take_profit_levels
from .patterns import candle_patterns, pivot_flags
from .signals import score_frame
from .sizing import vol_target_weight
from .structure import key_levels
from .timing import entry_timing

TD = 252


@dataclass
class BTConfig:
    allow_short: bool = True
    use_timing: bool = True
    equity0: float = 10_000.0
    start: str | None = None          # 'YYYY-MM-DD' (지표 계산은 그 이전 데이터도 사용)
    cost_bps: float = 10.0            # 편도 수수료+슬리피지 (bp)
    exit_on_regime: bool = True       # 국면이 반대로 바뀌면(계수 0) 종가 청산


@dataclass
class BTResult:
    equity: pd.Series
    trades: pd.DataFrame
    benchmarks: pd.DataFrame
    stats: dict
    bench_stats: pd.DataFrame
    exposure: pd.Series = field(default_factory=pd.Series)


# ------------------------------------------------------------------ 신호 시계열 (벡터화)
def divergence_active(d: pd.DataFrame, cfg: dict, oscs=("rsi", "macd_hist")) -> dict:
    """{(osc, +1/-1): 봉별 활성 여부} — 피벗 확정일(b+right)부터 b+max_age 까지."""
    n = len(d)
    out = {}
    L, R, lb, gap, age = cfg["pivot_left"], cfg["pivot_right"], cfg["lookback"], cfg["min_gap"], cfg["max_age"]
    for osc in oscs:
        if osc not in d:
            continue
        o = d[osc].to_numpy()
        for col, sign in (("Low", 1), ("High", -1)):
            price = d[col].to_numpy()
            piv = np.flatnonzero(pivot_flags(d[col], L, R, "low" if sign > 0 else "high").to_numpy())
            active = np.zeros(n, dtype=bool)
            for a, b in zip(piv[:-1], piv[1:]):
                if b - a < gap or b - a > lb or np.isnan(o[a]) or np.isnan(o[b]):
                    continue
                if (sign > 0 and price[b] < price[a] and o[b] > o[a]) or (sign < 0 and price[b] > price[a] and o[b] < o[a]):
                    s, e = b + R, min(n - 1, b + age)
                    if s <= e:
                        active[s:e + 1] = True
            out[(osc, sign)] = active
    return out


def divergence_adjust(d: pd.DataFrame, cfg: dict, active: dict | None = None) -> pd.Series:
    """봉별 다이버전스 점수 보정."""
    active = divergence_active(d, cfg) if active is None else active
    adj = np.zeros(len(d))
    for (_, sign), a in active.items():
        adj += sign * cfg["points"] * a
    return pd.Series(adj, index=d.index)


def vol_factor_series(d: pd.DataFrame, vix: pd.Series | None, cfg: dict) -> pd.Series:
    lo = cfg["min_factor"]
    rank = d["rvol_rank"]
    f_rank = 1 - (1 - lo) * ((rank - cfg["rank_start"]) / (1 - cfg["rank_start"])).clip(0, 1)
    f = f_rank.fillna(1.0)
    if vix is not None:
        v = vix.reindex(d.index, method="ffill")
        f_vix = 1 - (1 - lo) * ((v - cfg["vix_start"]) / (cfg["vix_full"] - cfg["vix_start"])).clip(0, 1)
        f = pd.concat([f, f_vix.fillna(1.0)], axis=1).min(axis=1)
    return f


def signal_frame(u: pd.DataFrame, strat: dict, bench_close: pd.Series | None = None,
                 vix: pd.Series | None = None) -> pd.DataFrame:
    """지표 + 봉별 bull/bear(보정 후)·국면·국면계수·변동성계수·샹들리에."""
    return _signal_frame(u, strat, bench_close, vix)[0]


def _signal_frame(u, strat, bench_close=None, vix=None):
    p = strat["indicators"]
    d = add_indicators(u, p)
    if bench_close is not None:
        d = add_relative_strength(d, bench_close, p.get("rs_sma", 50), p.get("rs_roc", 20))
    sf = score_frame(d)
    act = divergence_active(d, strat["divergence"])
    adj = divergence_adjust(d, strat["divergence"], act)
    d["bull"] = (sf["bull"] + adj).clip(0, 100)
    d["bear"] = (sf["bear"] - adj).clip(0, 100)
    th = strat["score"]["regime_threshold"]
    regime = np.where((d["bull"] >= th) & (d["bull"] >= d["bear"]), "BULL",
                      np.where(d["bear"] >= th, "BEAR", "NEUTRAL"))
    d["regime"] = pd.Series(regime, index=d.index).where(sf["bull"].notna())
    above = d["Close"] > d["sma_slow"]
    rm, cap = strat["regime_multiplier"], strat["counter_trend_cap"]
    for side, sign in (("long", 1), ("short", -1)):
        m = d["regime"].map(lambda r: rm[r][side] if isinstance(r, str) else 0.0).astype(float)
        counter = ~above if sign > 0 else above
        d[f"mult_{side}"] = np.where(counter, np.minimum(m, cap), m)
    d["vol_factor"] = vol_factor_series(d, vix, strat["volatility"])
    lv = strat["levels"]
    n, mult = lv["chandelier_n"], lv["chandelier_mult"]
    d["chand_long"] = d["High"].rolling(n).max() - mult * d["atr"]
    d["chand_short"] = d["Low"].rolling(n).min() + mult * d["atr"]
    d["sigma_d"] = d["Close"].pct_change().rolling(20).std()
    return d, act


# ------------------------------------------------------------------ 시뮬레이션
def run_backtest(u: pd.DataFrame, pair: dict, strat: dict, cfg: BTConfig | None = None,
                 bench_close: pd.Series | None = None, vix: pd.Series | None = None) -> BTResult:
    cfg = cfg or BTConfig()
    d, DIVA = _signal_frame(u, strat, bench_close, vix)
    long_ins = next(i for i in pair["instruments"] if i["leverage"] > 0 and abs(i["leverage"]) == 3) \
        if any(i["leverage"] == 3 for i in pair["instruments"]) else next(i for i in pair["instruments"] if i["leverage"] > 0)
    short_ins = next((i for i in pair["instruments"] if i["leverage"] < 0), None)
    letf = {1: synthetic_letf_ohlc(u, long_ins["leverage"], long_ins.get("expense", 0.0), 100.0)["Close"]}
    if short_ins is not None:
        letf[-1] = synthetic_letf_ohlc(u, short_ins["leverage"], short_ins.get("expense", 0.0), 100.0)["Close"]
    ins_of = {1: long_ins, -1: short_ins}

    idx = d.index
    O, H, Lo, C = (d[c].to_numpy() for c in ("Open", "High", "Low", "Close"))
    MULT = {1: d["mult_long"].to_numpy(), -1: d["mult_short"].to_numpy()}
    BULL, BEAR = d["bull"].to_numpy(), d["bear"].to_numpy()
    REG = d["regime"].to_numpy()
    VF, SIG = d["vol_factor"].to_numpy(), d["sigma_d"].fillna(0).to_numpy()
    CH = {1: d["chand_long"].to_numpy(), -1: d["chand_short"].to_numpy()}
    LP = {k: v.to_numpy() for k, v in letf.items()}
    PATS = candle_patterns(d)  # 전체 기간 한 번만
    warm = max(strat["indicators"]["sma_slow"], strat["indicators"]["range_window"] // 2) + 5
    start_i = warm
    if cfg.start:
        start_i = max(start_i, int(idx.searchsorted(pd.Timestamp(cfg.start))))
    if start_i >= len(idx) - 2:
        raise ValueError("백테스트 기간이 너무 짧습니다 (지표 준비에 약 200일 필요)")

    lv, sz = strat["levels"], strat["sizing"]
    cost = cfg.cost_bps / 10_000
    cash = cfg.equity0
    pos = None
    pending = None  # 다음 날 시가 진입 예약 (direction)
    eq, expo, trades = [], [], []

    def conv(direction, i, u_price):
        """i 일 장중 기초 가격 → LETF 가격 (전일 종가 기준 일일 리셋)."""
        k = ins_of[direction]["leverage"]
        lp = LP[direction][i - 1]
        return max(0.0, lp * (1 + k * (u_price / C[i - 1] - 1)))

    def close_part(i, frac_shares, l_price, reason):
        nonlocal cash
        val = frac_shares * l_price
        cash += val * (1 - cost)
        pos["exits"].append((idx[i], frac_shares, l_price, reason))
        pos["rem"] -= frac_shares

    def finish(i):
        exits = pos["exits"]
        qty = sum(x[1] for x in exits)
        avg = sum(x[1] * x[2] for x in exits) / qty if qty else float("nan")
        pnl = sum(x[1] * x[2] for x in exits) * (1 - cost) - pos["shares"] * pos["l_entry"] * (1 + cost)
        trades.append({
            "진입일": pos["date"], "청산일": idx[i], "방향": "롱" if pos["dir"] > 0 else "숏",
            "종목": ins_of[pos["dir"]]["ticker"], "진입가": pos["l_entry"], "평균 청산가": avg,
            "수량": pos["shares"], "손익($)": pnl, "R": pnl / pos["risk"] if pos["risk"] > 0 else float("nan"),
            "수익률": pnl / (pos["shares"] * pos["l_entry"]), "보유일": (idx[i] - pos["date"]).days,
            "청산 사유": exits[-1][3], "국면": pos["regime"], "타이밍": pos["timing"],
            "익절1": any(x[3] == "익절1" for x in exits), "익절2": any(x[3] == "익절2" for x in exits),
        })

    for i in range(start_i, len(idx)):
        # 1) 예약된 진입: 오늘 시가
        if pending is not None and pos is None:
            dr, sig_i, timing_state = pending
            pending = None
            k = ins_of[dr]["leverage"]
            u_entry = O[i]
            l_entry = conv(dr, i, u_entry)
            hist = d.iloc[max(0, sig_i - 300): sig_i + 1]  # 레벨 계산에 필요한 만큼만 (최대 252봉 참조)
            kl = key_levels(hist, strat["structure"])
            u_stop = stop_loss(hist, u_entry, dr, lv, lv["sl_method"], kl)
            tps = [p for p, _ in take_profit_levels(u_entry, u_stop, lv, lv.get("tp_method", "r"), kl)]
            l_stop = max(0.0, l_entry * (1 + k * (u_stop / u_entry - 1)))
            equity_now = cash
            # 앱과 같은 공식이지만 정수 주식 반올림 없이 비중으로 계산 (합성 가격 수준과 무관하게)
            w_risk = sz["risk_pct"] * l_entry / (l_entry - l_stop) if l_entry > l_stop else 0.0
            w_vol = vol_target_weight(sz["target_vol"], k, float(SIG[sig_i]))
            w = min(w_risk, w_vol, sz["max_pair_weight"]) * float(MULT[dr][sig_i]) * float(VF[sig_i])
            shares = w * equity_now / l_entry if l_entry > 0 else 0.0
            if shares > 0 and l_entry > l_stop:
                cash -= shares * l_entry * (1 + cost)
                f1, f2 = lv["tp_fractions"]
                pos = {"dir": dr, "date": idx[i], "u_entry": u_entry, "l_entry": l_entry, "u_stop": u_stop,
                       "tps": tps[:2], "fracs": [f1, f2], "shares": shares, "rem": shares, "trail": None,
                       "hit": set(), "exits": [], "risk": shares * (l_entry - l_stop),
                       "regime": REG[sig_i], "timing": timing_state}

        # 2) 보유 중이면 오늘 봉으로 손절·익절 처리
        if pos is not None and i > 0:
            dr = pos["dir"]
            trail_active = pos["trail"] is not None and (pos["trail"] - pos["u_stop"]) * dr > 0
            eff = pos["trail"] if trail_active else pos["u_stop"]
            first_day = pos["date"] == idx[i]
            open_px = pos["u_entry"] if first_day else O[i]
            stop_hit = (Lo[i] <= eff) if dr > 0 else (H[i] >= eff)
            if stop_hit:
                fill = min(open_px, eff) if dr > 0 else max(open_px, eff)
                close_part(i, pos["rem"], conv(dr, i, fill), "트레일링" if trail_active else "손절")
            else:
                for j, (tp, frac) in enumerate(zip(pos["tps"], pos["fracs"]), 1):
                    name = f"익절{j}"
                    if name in pos["hit"] or pos["rem"] <= 1e-12:
                        continue
                    hit = (H[i] >= tp) if dr > 0 else (Lo[i] <= tp)
                    if hit:
                        fill = max(open_px, tp) if dr > 0 else min(open_px, tp)
                        q = min(pos["rem"], pos["shares"] * frac)
                        if q > 0:
                            close_part(i, q, conv(dr, i, fill), name)
                        pos["hit"].add(name)
            if pos["rem"] > 1e-12 and cfg.exit_on_regime:
                if MULT[dr][i] == 0:
                    close_part(i, pos["rem"], float(LP[dr][i]), "국면 전환")
            if pos["rem"] <= 1e-12:
                finish(i)
                pos = None
            else:  # 트레일 상향 (종가 기준, 유리한 쪽으로만)
                ch = CH[dr][i]
                if not math.isnan(ch) and (pos["trail"] is None or (ch - pos["trail"]) * dr > 0):
                    pos["trail"] = float(ch)

        # 3) 평가
        val = pos["rem"] * float(LP[pos["dir"]][i]) if pos else 0.0
        eq.append(cash + val)
        expo.append(val * ins_of[pos["dir"]]["leverage"] / (cash + val) if pos and cash + val > 0 else 0.0)

        # 4) 진입 신호 (오늘 종가 기준 → 내일 시가)
        if pos is None and i < len(idx) - 1:
            cands = [1] + ([-1] if cfg.allow_short and short_ins is not None else [])
            cands.sort(key=lambda s: -(BULL[i] if s > 0 else BEAR[i]))
            for dr in cands:
                if MULT[dr][i] <= 0 or not isinstance(REG[i], str):
                    continue
                state = "국면만"
                if cfg.use_timing:
                    divs = [{"osc": osc, "bias": sg} for (osc, sg), a in DIVA.items() if a[i]]
                    w0 = max(0, i - 9)
                    t = entry_timing(d.iloc[w0: i + 1], dr, True, strat["timing"], strat["divergence"],
                                     pats=PATS.iloc[w0: i + 1], divs=divs)
                    if not t.ok:
                        continue
                    state = t.state
                pending = (dr, i, state)
                break

    if pos is not None:  # 마지막 날 보유분 평가 청산
        close_part(len(idx) - 1, pos["rem"], float(letf[pos["dir"]].iloc[-1]), "기간 종료")
        finish(len(idx) - 1)
        eq[-1] = cash

    dates = idx[start_i:]
    equity = pd.Series(eq, index=dates, name="전략")
    tr = pd.DataFrame(trades)
    bm = benchmarks(d, letf[1], start_i, cfg.equity0, long_ins["ticker"], pair["signal"])
    bm.insert(0, "전략", equity)
    bstats = pd.DataFrame({c: perf_stats(bm[c]) for c in bm}).T
    stats = {**perf_stats(equity), **trade_stats(tr), "노출 비율": float(np.mean([e != 0 for e in expo]))}
    return BTResult(equity, tr, bm, stats, bstats, pd.Series(expo, index=dates))


def benchmarks(d: pd.DataFrame, letf_long: pd.Series, start_i: int, equity0: float, letf_name: str,
               u_name: str) -> pd.DataFrame:
    c = d["Close"].iloc[start_i:]
    l = letf_long.iloc[start_i:]
    sig = (d["Close"] > d["sma_slow"]).shift(1).iloc[start_i:].fillna(False)
    r_filter = l.pct_change().fillna(0) * sig.astype(float)
    return pd.DataFrame({
        f"{u_name} 보유": equity0 * c / c.iloc[0],
        f"{letf_name} 보유": equity0 * l / l.iloc[0],
        f"{letf_name} 200일선 필터": equity0 * (1 + r_filter).cumprod(),
    })


def perf_stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return {}
    r = eq.pct_change().dropna()
    years = len(eq) / TD
    end = max(eq.iloc[-1], 1e-9)
    dd = eq / eq.cummax() - 1
    down = r[r < 0].std()
    return {
        "최종 자산": float(eq.iloc[-1]),
        "총수익률": float(eq.iloc[-1] / eq.iloc[0] - 1),
        "CAGR": float((end / eq.iloc[0]) ** (1 / years) - 1) if years > 0 else float("nan"),
        "MDD": float(dd.min()),
        "샤프": float(r.mean() / r.std() * math.sqrt(TD)) if r.std() > 0 else float("nan"),
        "소르티노": float(r.mean() / down * math.sqrt(TD)) if down and down > 0 else float("nan"),
        "연변동성": float(r.std() * math.sqrt(TD)),
    }


def trade_stats(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"거래 수": 0}
    wins, losses = tr[tr["손익($)"] > 0], tr[tr["손익($)"] <= 0]
    return {
        "거래 수": int(len(tr)),
        "승률": float(len(wins) / len(tr)),
        "평균 R": float(tr["R"].mean()),
        "손익비(PF)": float(wins["손익($)"].sum() / -losses["손익($)"].sum()) if len(losses) and losses["손익($)"].sum() < 0 else float("inf"),
        "평균 보유일": float(tr["보유일"].mean()),
        "익절1 도달": float(tr["익절1"].mean()),
        "익절2 도달": float(tr["익절2"].mean()),
    }


def yearly_returns(df: pd.DataFrame) -> pd.DataFrame:
    """연도별 수익률 (각 열 = 전략/벤치마크)."""
    y = df.groupby(df.index.year).agg(["first", "last"])
    out = pd.DataFrame({c: y[(c, "last")] / y[(c, "first")] - 1 for c in df.columns})
    # 연초값을 전년 말 값으로 (첫 해 제외)
    last = df.groupby(df.index.year).last()
    prev = last.shift(1)
    out = (last / prev - 1).fillna(out)
    out.index.name = "연도"
    return out
