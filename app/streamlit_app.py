"""LevTrack Streamlit MVP.

실행:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import copy  # noqa: E402

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from levtrack import leverage as lev  # noqa: E402
from levtrack.analysis import PairAnalysis, analyze_pair, prepare  # noqa: E402
from levtrack.config import load_pairs, load_strategy  # noqa: E402
from levtrack.data import MarketData  # noqa: E402
from levtrack.levels import SL_METHODS, TP_METHODS  # noqa: E402
from levtrack.patterns import (PATTERNS, candle_patterns, find_divergences, recent_divergences,  # noqa: E402
                               recent_patterns)
from levtrack.planner import build_plan, sigma_daily_from  # noqa: E402
from levtrack.structure import fibonacci, volume_profile  # noqa: E402
from home_icon import inject_home_icon  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
st.set_page_config(page_title="LevTrack", page_icon=str(STATIC / "icon-192.png"), layout="wide")
inject_home_icon("LevTrack")

PAIRS = load_pairs()
PAIR_BY_ID = {p["id"]: p for p in PAIRS}
BASE_STRAT = load_strategy()
REGIME_BADGE = {"BULL": "🟢 상승", "NEUTRAL": "🟡 중립", "BEAR": "🔴 하락"}
TF_LABEL = {"1d": "일봉", "1h": "시간봉"}
CAT_LABEL = {"trend": "추세", "momentum": "모멘텀", "structure": "구조", "volume": "거래량", "rs": "상대강도"}


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("📈 LevTrack")
    mode = st.radio("데이터", ["yahoo", "demo"], format_func={"yahoo": "실시세 (yfinance, 무료)",
                                                            "demo": "데모 (합성 데이터)"}.get)
    if st.button("시세 새로고침"):
        st.cache_resource.clear()
        st.cache_data.clear()
    st.divider()
    equity = st.number_input("계좌 평가금액 (USD)", min_value=100.0, value=10_000.0, step=500.0)
    risk_pct = st.slider("1회 리스크 (계좌 대비 %)", 0.25, 3.0, BASE_STRAT["sizing"]["risk_pct"] * 100, 0.25) / 100
    max_pair = st.slider("페어당 최대 비중 %", 5, 100, int(BASE_STRAT["sizing"]["max_pair_weight"] * 100), 5) / 100
    tf = st.radio("진입 · 손절 기준 봉", ["1d", "1h"], format_func=TF_LABEL.get, horizontal=True,
                  help="국면(추세 판단)은 항상 일봉, 진입 타이밍은 항상 시간봉, 손절·익절 레벨은 선택한 봉 기준")
    sl_method = st.selectbox("손절 방식", list(SL_METHODS), format_func=SL_METHODS.get)
    tp_method = st.selectbox("익절 방식", list(TP_METHODS), format_func=TP_METHODS.get)
    atr_mult = st.slider("ATR 배수", 1.0, 4.0, float(BASE_STRAT["levels"]["atr_mult"]), 0.25)
    pct_stop = st.slider("고정 % 손절 (기초 기준)", 1.0, 10.0, BASE_STRAT["levels"]["percent"] * 100, 0.5) / 100

strat = copy.deepcopy(BASE_STRAT)
strat["sizing"].update(risk_pct=risk_pct, max_pair_weight=max_pair)
strat["levels"].update(sl_method=sl_method, tp_method=tp_method, atr_mult=atr_mult, percent=pct_stop)


@st.cache_resource(ttl=900, show_spinner="시세 불러오는 중…")
def market(mode: str) -> MarketData:
    return MarketData(PAIRS, mode=mode)


md = market(mode)


@st.cache_data(ttl=900, show_spinner="분석 중…")
def get_analysis(mode: str, pair_id: str) -> PairAnalysis:
    return analyze_pair(market(mode), PAIR_BY_ID[pair_id], BASE_STRAT)


@st.cache_data(ttl=900, show_spinner=False)
def get_frame(mode: str, pair_id: str, interval: str) -> pd.DataFrame:
    return prepare(market(mode), PAIR_BY_ID[pair_id], interval, BASE_STRAT)


fx = md.try_last("USDKRW=X") if mode == "yahoo" else 1400.0
if mode == "demo":
    st.warning("데모 모드: 합성 데이터라 실제 가격이 아닙니다. 계산 흐름 확인용입니다.")


def krw(usd: float) -> str:
    return f"≈ ₩{usd * fx:,.0f}" if fx else ""


def plan_for(a: PairAnalysis, ins: dict, interval: str, **kw):
    k = ins["leverage"]
    direction = 1 if k > 0 else -1
    d = get_frame(mode, a.pair["id"], interval)
    return build_plan(
        d, a.pair["signal"], ins["ticker"], k, md.last(ins["ticker"], interval), equity, strat,
        regime_mult=a.signal.multiplier(direction, strat), vol_factor=a.signal.vol_factor,
        sigma_daily=sigma_daily_from(a.daily), expense=ins.get("expense", 0.0), **kw,
    )


def div_text(divs) -> str:
    if not divs:
        return "-"
    return ", ".join(f"{'강세' if d['bias'] > 0 else '약세'}({d['osc'].replace('_hist', '').upper()}, {d['bars_ago']}봉 전)"
                     for d in divs)


def pat_text(pats) -> str:
    if not pats:
        return "-"
    icon = {1: "▲", -1: "▼", 0: "·"}
    return ", ".join(f"{icon[p['bias']]}{p['label']}" for p in pats if p["bias"] != 0) or "-"


tab_dash, tab_plan, tab_calc, tab_te = st.tabs(["대시보드", "차트 · 플래너", "환산 계산기", "추적오차"])

# ------------------------------------------------------------------ dashboard
with tab_dash:
    rows, errors = [], []
    for pair in PAIRS:
        try:
            a = get_analysis(mode, pair["id"])
            sig = a.signal
            ins = a.instrument()
            plan = plan_for(a, ins, "1d")
            c = a.daily["Close"]
            rv = sig.values
            rows.append({
                "페어": f"{pair['signal']} → {ins['ticker']}",
                "기초가": c.iloc[-1],
                "등락": c.iloc[-1] / c.iloc[-2] - 1,
                "국면": REGIME_BADGE[sig.regime],
                "Bull": sig.bull,
                "Bear": sig.bear,
                "추천": ins["ticker"] if plan.sizing.shares else "관망",
                "진입 타이밍(시간봉)": a.timing[a.direction].state,
                "권장 비중": plan.sizing.weight,
                "수량": plan.sizing.shares,
                "OBV": "-" if rv["obv_up"] is None else ("상승" if rv["obv_up"] else "하락"),
                "상대강도": "-" if rv["rs_above"] is None else
                ("강함" if rv["rs_above"] else "약함") + f" vs {pair['benchmark']}",
                "다이버전스": div_text(sig.divergences),
                "최근 캔들": pat_text(sig.patterns),
                "변동성 계수": sig.vol_factor,
                "52주 고점 대비": rv["drawdown"],
            })
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pair['signal']}: {e}")
    if rows:
        df = pd.DataFrame(rows).set_index("페어")
        st.dataframe(
            df.style.format({
                "기초가": "{:,.2f}", "등락": "{:+.2%}", "Bull": "{:.0f}", "Bear": "{:.0f}", "권장 비중": "{:.1%}",
                "변동성 계수": "×{:.2f}", "52주 고점 대비": "{:+.1%}",
            }, na_rep="-"),
            width="stretch",
        )
        total = df["권장 비중"].sum()
        cap = strat["sizing"]["max_total_weight"]
        msg = f"권장 비중 합계 {total:.1%} (한도 {cap:.0%})"
        (st.error if total > cap else st.caption)(msg + (" — 한도 초과: 페어별 수량을 줄이세요" if total > cap else ""))
        st.caption("국면·점수·다이버전스·캔들은 일봉 기준, 진입 타이밍은 시간봉 기준. "
                   "권장 비중 = min(리스크 기준, 변동성 타깃, 페어 상한) × 국면 계수 × 변동성 계수")
    for e in errors:
        st.error(e)

# ------------------------------------------------------------------ chart + planner
with tab_plan:
    c1, c2, c3 = st.columns([1, 1, 1])
    pair = c1.selectbox("페어", PAIRS, format_func=lambda p: f"{p['name']} ({p['signal']})")
    try:
        a = get_analysis(mode, pair["id"])
    except Exception as e:  # noqa: BLE001
        st.error(f"데이터 오류: {e}")
        st.stop()
    sig = a.signal
    rec_idx = next(i for i, ins in enumerate(pair["instruments"]) if (ins["leverage"] > 0) == (a.direction > 0))
    ins = c2.selectbox("매매 종목", pair["instruments"], index=rec_idx,
                       format_func=lambda i: f"{i['ticker']} ({i['leverage']:+g}x)")
    direction = 1 if ins["leverage"] > 0 else -1
    u_now = md.last(pair["signal"], tf)
    u_entry = c3.number_input(f"{pair['signal']} 진입가", value=round(u_now, 2), step=0.5, format="%.2f")
    use_manual = st.checkbox("손절가 직접 입력 (기초 기준)")
    u_stop_manual = None
    if use_manual:
        u_stop_manual = st.number_input(f"{pair['signal']} 손절가", value=round(u_now * (0.97 if direction > 0 else 1.03), 2),
                                        step=0.5, format="%.2f")
    try:
        plan = plan_for(a, ins, tf, u_entry=u_entry, u_stop_override=u_stop_manual)
    except Exception as e:  # noqa: BLE001
        st.error(f"플랜 계산 오류: {e}")
        st.stop()

    # ---- 신호 요약
    timing = a.timing[direction]
    box = st.success if timing.ok else (st.error if timing.state.startswith("🚫") else st.warning)
    box(f"**시간봉 진입 타이밍 ({'롱' if direction > 0 else '숏'}): {timing.state}**  \n" + " · ".join(timing.reasons))
    st.markdown(
        f"**일봉 국면** {REGIME_BADGE[sig.regime]} · Bull {sig.bull:.0f} / Bear {sig.bear:.0f} · "
        f"국면 계수 ×{plan.sizing.regime_mult:g} · 변동성 계수 ×{sig.vol_factor:g} · 손절/익절 기준 봉 {TF_LABEL[tf]}"
    )
    m = st.columns(5)
    m[0].metric("수량", f"{plan.sizing.shares}주")
    m[1].metric("비중", f"{plan.sizing.weight:.1%}", f"${plan.sizing.notional:,.0f}", delta_color="off")
    m[2].metric("손절 시 손실", f"${plan.sizing.risk_usd:,.0f}", krw(plan.sizing.risk_usd), delta_color="off")
    m[3].metric(f"갭 {plan.gap_pct:.0%} 시 손실", f"${plan.gap_loss:,.0f}", f"{plan.gap_r:.2f}R", delta_color="off")
    m[4].metric(f"{plan.letf_ticker} 손절폭", f"{plan.l_stop / plan.l_entry - 1:+.1%}")
    if plan.sizing.shares == 0:
        st.info("현재 국면에서는 이 방향 진입을 권장하지 않습니다 (국면 계수 0 또는 계좌 대비 리스크 부족).")
    for n in plan.notes:
        st.caption("ℹ️ " + n)

    with st.expander("신호 근거 (점수 구성 · 다이버전스 · 캔들 패턴)"):
        comp = []
        for cat, w in (("trend", 40), ("momentum", 25), ("structure", 15), ("volume", 10), ("rs", 10)):
            b, r = sig.components.get(f"bull_{cat}"), sig.components.get(f"bear_{cat}")
            comp.append((CAT_LABEL[cat], str(w), "-" if b is None else f"{b:.1f}", "-" if r is None else f"{r:.1f}"))
        comp.append(("다이버전스 보정", "±", f"{sig.values['divergence_adj']:+.0f}", f"{-sig.values['divergence_adj']:+.0f}"))
        st.table(pd.DataFrame(comp, columns=["항목", "배점", "Bull", "Bear"]).set_index("항목"))
        st.caption("배점 합은 100. 거래량 데이터나 기준지수가 없는 항목은 제외하고 100점으로 환산합니다.")
        lower_pats = candle_patterns(a.lower.iloc[-30:])
        cA, cB = st.columns(2)
        cA.markdown("**일봉**  \n다이버전스: " + div_text(sig.divergences) + "  \n캔들: " + pat_text(sig.patterns))
        cB.markdown("**시간봉**  \n다이버전스: " + div_text(recent_divergences(a.lower, strat["divergence"]))
                    + "  \n캔들: " + pat_text(recent_patterns(lower_pats, 3)))

    # ---- chart
    cc = st.columns(4)
    n_bars = cc[0].slider("표시 봉 수", 60, 500, 180, 20)
    show_fib = cc[1].checkbox("피보나치", True)
    show_vp = cc[2].checkbox("매물대", True)
    show_marks = cc[3].checkbox("패턴 · 다이버전스", True)

    u_full = get_frame(mode, pair["id"], tf)
    u_df = u_full.iloc[-n_bars:]
    l_df = md.ohlc(ins["ticker"], tf).iloc[-n_bars:]
    has_rs = "rs" in u_df
    rows_n = 6 if has_rs else 5
    heights = [0.34, 0.22, 0.1, 0.1, 0.12] + ([0.12] if has_rs else [])
    specs = [[{}, {}]] + [[{"colspan": 2, "secondary_y": r == 2}, None] for r in range(1, rows_n)]
    titles = [pair["signal"], "매물대", f"{ins['ticker']} (기초 레벨 환산)", "거래량 · OBV", "RSI", "MACD"]
    if has_rs:
        titles.append(f"상대강도 {pair['signal']}/{pair['benchmark']}")
    fig = make_subplots(rows=rows_n, cols=2, column_widths=[0.88, 0.12], row_heights=heights, specs=specs,
                        shared_xaxes=True, vertical_spacing=0.025, horizontal_spacing=0.005, subplot_titles=titles)
    for r, df_, name in ((1, u_df, pair["signal"]), (2, l_df, ins["ticker"])):
        fig.add_trace(go.Candlestick(x=df_.index, open=df_.Open, high=df_.High, low=df_.Low, close=df_.Close,
                                     name=name, showlegend=False), row=r, col=1)
    for col, color, nm in (("sma_fast", "#f2a900", "SMA50"), ("sma_slow", "#7b61ff", "SMA200"),
                           ("ema_fast", "#26a69a", "EMA20")):
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df[col], name=nm, line=dict(width=1, color=color)), row=1, col=1)

    if "Volume" in u_df and u_df["Volume"].sum() > 0:
        vcol = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(u_df.Close, u_df.Open)]
        fig.add_trace(go.Bar(x=u_df.index, y=u_df.Volume, marker_color=vcol, name="거래량", showlegend=False),
                      row=3, col=1)
        if "obv" in u_df:
            fig.add_trace(go.Scatter(x=u_df.index, y=u_df.obv, name="OBV", line=dict(width=1, color="#1f77b4")),
                          row=3, col=1, secondary_y=True)
            fig.add_trace(go.Scatter(x=u_df.index, y=u_df.obv_ema, name="OBV EMA",
                                     line=dict(width=1, dash="dot", color="#1f77b4")), row=3, col=1, secondary_y=True)
    fig.add_trace(go.Scatter(x=u_df.index, y=u_df["rsi"], name="RSI", line=dict(width=1, color="#888")), row=4, col=1)
    for y in (30, 70):
        fig.add_hline(y=y, line=dict(width=0.5, dash="dot", color="#999"), row=4, col=1)
    hc = ["#26a69a" if v >= 0 else "#ef5350" for v in u_df.macd_hist.fillna(0)]
    fig.add_trace(go.Bar(x=u_df.index, y=u_df.macd_hist, marker_color=hc, name="MACD hist", showlegend=False),
                  row=5, col=1)
    fig.add_trace(go.Scatter(x=u_df.index, y=u_df.macd, name="MACD", line=dict(width=1, color="#1f77b4")), row=5, col=1)
    fig.add_trace(go.Scatter(x=u_df.index, y=u_df.macd_signal, name="Signal", line=dict(width=1, color="#ff7f0e")),
                  row=5, col=1)
    if has_rs:
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df.rs, name="RS", line=dict(width=1, color="#9467bd")), row=6, col=1)
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df.rs_sma, name="RS SMA50",
                                 line=dict(width=1, dash="dot", color="#9467bd")), row=6, col=1)

    # 피보나치
    if show_fib:
        fib = fibonacci(u_full, strat["structure"]["fib_lookback"])
        for r_, p_ in {**fib.retr, **fib.ext}.items():
            fig.add_hline(y=p_, line=dict(color="#b0b0b0", dash="dot", width=0.8), annotation_text=f"fib {r_}",
                          annotation_position="top left", annotation_font_size=9,
                          annotation_font_color="#999", row=1, col=1)
    # 매물대
    vp = volume_profile(u_full, strat["structure"]["vp_bars"], strat["structure"]["vp_bins"],
                        strat["structure"]["value_area"])
    if show_vp and vp:
        in_va = [(vp.val <= c_ <= vp.vah) for c_ in vp.centers]
        fig.add_trace(go.Bar(y=vp.centers, x=vp.volume, orientation="h", showlegend=False, name="매물대",
                             marker_color=["#5c7cfa" if v else "#ced4da" for v in in_va]), row=1, col=2)
        fig.add_hline(y=vp.poc, line=dict(color="#5c7cfa", width=1.2), annotation_text="POC",
                      annotation_position="top left", annotation_font_color="#5c7cfa", row=1, col=1)
    fig.update_yaxes(matches="y", showticklabels=False, row=1, col=2)
    fig.update_xaxes(showticklabels=False, row=1, col=2)

    # 패턴 · 다이버전스 표시
    if show_marks:
        pat = candle_patterns(u_full).loc[u_df.index]
        for name, (label, bias) in PATTERNS.items():
            if bias == 0 or not pat[name].any():
                continue
            hit = u_df[pat[name]]
            y = hit.Low * 0.995 if bias > 0 else hit.High * 1.005
            fig.add_trace(go.Scatter(x=hit.index, y=y, mode="markers", name=label, hovertext=label,
                                     opacity=0.75,
                                     marker=dict(symbol="triangle-up" if bias > 0 else "triangle-down", size=7,
                                                 color="#2ca02c" if bias > 0 else "#d62728")), row=1, col=1)
        dcfg = strat["divergence"]
        for osc, row_ in (("rsi", 4), ("macd_hist", 5)):
            for dv in find_divergences(u_full, osc, dcfg["pivot_left"], dcfg["pivot_right"], n_bars, dcfg["min_gap"]):
                color = "#2ca02c" if dv["bias"] > 0 else "#d62728"
                fig.add_trace(go.Scatter(x=[dv["t1"], dv["t2"]], y=[dv["p1"], dv["p2"]], mode="lines",
                                         line=dict(color=color, width=2), showlegend=False,
                                         hovertext=f"{dv['kind']} divergence ({osc})"), row=1, col=1)
                fig.add_trace(go.Scatter(x=[dv["t1"], dv["t2"]], y=[dv["o1"], dv["o2"]], mode="lines",
                                         line=dict(color=color, width=2), showlegend=False), row=row_, col=1)

    lines = [("진입", plan.u_entry, plan.l_entry, "#1f77b4", "solid"),
             ("손절", plan.u_stop, plan.l_stop, "#d62728", "dash")]
    lines += [(f"TP{i}", u, l, "#2ca02c", "dash") for i, (u, l) in enumerate(zip(plan.u_tps, plan.l_tps), 1)]
    lines.append(("트레일", plan.u_trail, plan.l_trail, "#ff7f0e", "dot"))
    for label, u, l, color, dash in lines:
        fig.add_hline(y=u, line=dict(color=color, dash=dash, width=1), annotation_text=f"{label} {u:,.2f}",
                      annotation_position="top right", annotation_font_color=color, row=1, col=1)
        fig.add_hline(y=l, line=dict(color=color, dash=dash, width=1), annotation_text=f"{label} {l:,.2f}",
                      annotation_position="right", row=2, col=1)
    breaks = [dict(bounds=["sat", "mon"])]
    if tf == "1h":
        breaks.append(dict(bounds=[16, 9.5], pattern="hour"))
    fig.update_xaxes(rangebreaks=breaks, rangeslider_visible=False, col=1)
    for r in range(1, rows_n):  # 마지막 패널에만 날짜 표시
        fig.update_xaxes(showticklabels=False, row=r, col=1)
    fig.update_layout(height=1250, margin=dict(l=10, r=90, t=30, b=10), legend=dict(orientation="h"),
                      bargap=0.05)
    st.plotly_chart(fig, width="stretch")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("레벨")
        lt = plan.levels_table()
        st.dataframe(lt.style.format({pair["signal"]: "{:,.2f}", ins["ticker"]: "{:,.2f}", "R": "{:+.1f}R",
                                      f"{pair['signal']} %": "{:+.2%}", f"{ins['ticker']} %": "{:+.2%}",
                                      "손익($)": "{:+,.0f}"}, na_rep="-"), width="stretch")
        st.subheader("메리츠증권 주문표 (수동 입력)")
        st.dataframe(plan.order_sheet(), width="stretch")
        st.caption("레버리지 ETF는 매일 리셋되므로 LETF 기준 손절·익절 가격은 매일 장 시작 전 이 화면에서 다시 확인하세요. "
                   "감시(스탑) 주문이 지원되지 않으면 기초 가격 알림 기준으로 수동 매도합니다.")
        st.subheader("지지 · 저항 레벨")
        kl = plan.key_levels.copy()
        kl["거리"] = kl["price"] / u_now - 1
        kl["구분"] = ["저항" if p > u_now else "지지" for p in kl["price"]]
        kl[ins["ticker"]] = [lev.convert_price(plan.u_entry, p, plan.l_entry, plan.k) for p in kl["price"]]
        kl = kl.rename(columns={"price": pair["signal"], "label": "레벨"})[["레벨", "구분", pair["signal"], "거리", ins["ticker"]]]
        st.dataframe(kl.style.format({pair["signal"]: "{:,.2f}", "거리": "{:+.2%}", ins["ticker"]: "{:,.2f}"}),
                     width="stretch", hide_index=True)
    with right:
        st.subheader("진입 비율")
        st.table(pd.DataFrame(plan.sizing.as_rows(), columns=["항목", "값"]).set_index("항목"))
        st.subheader("보유 기간별 예상가 (감쇠 반영)")
        target = st.radio("기초가가 도달하는 레벨", ["손절", "익절1"], horizontal=True)
        st.dataframe(plan.projection(target).style.format("{:,.2f}"), width="stretch")
        st.caption(f"일간 변동성 σ = {plan.sigma_daily:.2%} (최근 20일). 같은 기초가라도 오래 보유할수록 LETF 가격이 달라집니다.")

# ------------------------------------------------------------------ calculator
with tab_calc:
    st.subheader("기초 → 레버리지 ETF 가격 환산")
    a_, b_, c_, d_ = st.columns(4)
    cu0 = a_.number_input("기초 진입가", value=500.0, step=1.0)
    cu1 = b_.number_input("기초 목표가", value=484.0, step=1.0)
    cl0 = c_.number_input("LETF 진입가", value=80.0, step=0.5)
    ck = d_.selectbox("배수", [3, 2, -1, -2, -3], index=0)
    e, f, g = st.columns(3)
    csig = e.number_input("기초 일간 변동성 %", value=1.4, step=0.1) / 100
    cdays = f.number_input("보유 일수", value=10, min_value=1, step=1)
    cfee = g.number_input("연 보수 %", value=0.9, step=0.05) / 100
    inst = lev.convert_price(cu0, cu1, cl0, ck)
    proj = lev.project_price(cu0, cu1, cl0, ck, csig, cdays, cfee)
    r1, r2, r3 = st.columns(3)
    r1.metric("기초 변동률", f"{cu1 / cu0 - 1:+.2%}")
    r2.metric("당일 환산가", f"{inst:,.2f}", f"{inst / cl0 - 1:+.2%}")
    r3.metric(f"{cdays}일 보유 예상가", f"{proj:,.2f}", f"{proj / cl0 - 1:+.2%}")
    st.dataframe(lev.projection_table(cu0, cu1, cl0, ck, csig, expense=cfee).style.format("{:,.2f}"),
                 width="stretch")
    st.latex(r"\frac{L_t}{L_0} \approx \left(\frac{U_t}{U_0}\right)^k e^{-\frac{k^2-k}{2}\sigma^2 t}\, e^{-\text{fee}\cdot t}")

# ------------------------------------------------------------------ tracking error
with tab_te:
    st.subheader("추적오차 (LETF 일간수익률 − k × 기초 일간수익률)")
    window = st.slider("창 (일)", 10, 120, 20)
    for pair in PAIRS:
        for ins in pair["instruments"]:
            try:
                te = lev.tracking_error(md.ohlc(pair["signal"], "1d")["Close"], md.ohlc(ins["ticker"], "1d")["Close"],
                                        ins["leverage"], window).dropna().iloc[-250:]
            except Exception as e:  # noqa: BLE001
                st.error(f"{ins['ticker']}: {e}")
                continue
            c1, c2, c3 = st.columns([1, 1, 4])
            c1.metric(f"{pair['signal']}→{ins['ticker']} 누적 괴리 ({window}일)", f"{te['rolling_sum'].iloc[-1]:+.2%}")
            c2.metric("실효 배수", f"{te['implied_k'].iloc[-1]:.2f}x", f"목표 {ins['leverage']:+g}x", delta_color="off")
            c3.line_chart(te["rolling_sum"], height=120)
    st.caption("SOXX 와 SOXL/SOXS 는 추종 지수가 달라 괴리가 상대적으로 큽니다. 실효 배수가 목표에서 많이 벗어나면 신호 기준 종목을 재검토하세요.")
