"""LevTrack Streamlit MVP.

실행:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import copy  # noqa: E402

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from levtrack import leverage as lev  # noqa: E402
from levtrack.config import load_pairs, load_strategy  # noqa: E402
from levtrack.data import MarketData  # noqa: E402
from levtrack.indicators import add_indicators  # noqa: E402
from levtrack.levels import SL_METHODS  # noqa: E402
from levtrack.planner import build_plan, sigma_daily_from  # noqa: E402
from levtrack.signals import evaluate  # noqa: E402

st.set_page_config(page_title="LevTrack", page_icon="📈", layout="wide")

PAIRS = load_pairs()
BASE_STRAT = load_strategy()
REGIME_BADGE = {"BULL": "🟢 상승", "NEUTRAL": "🟡 중립", "BEAR": "🔴 하락"}
TF_LABEL = {"1d": "일봉", "1h": "시간봉"}


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("📈 LevTrack")
    mode = st.radio("데이터", ["yahoo", "demo"], format_func={"yahoo": "실시세 (yfinance, 무료)",
                                                            "demo": "데모 (합성 데이터)"}.get)
    if st.button("시세 새로고침"):
        st.cache_resource.clear()
    st.divider()
    equity = st.number_input("계좌 평가금액 (USD)", min_value=100.0, value=10_000.0, step=500.0)
    risk_pct = st.slider("1회 리스크 (계좌 대비 %)", 0.25, 3.0, BASE_STRAT["sizing"]["risk_pct"] * 100, 0.25) / 100
    max_pair = st.slider("페어당 최대 비중 %", 5, 100, int(BASE_STRAT["sizing"]["max_pair_weight"] * 100), 5) / 100
    tf = st.radio("진입 · 손절 기준 봉", ["1d", "1h"], format_func=TF_LABEL.get, horizontal=True,
                  help="국면(추세 판단)은 항상 일봉 기준, 손절·익절 레벨은 선택한 봉 기준")
    sl_method = st.selectbox("손절 방식", list(SL_METHODS), format_func=SL_METHODS.get)
    atr_mult = st.slider("ATR 배수", 1.0, 4.0, float(BASE_STRAT["levels"]["atr_mult"]), 0.25)
    pct_stop = st.slider("고정 % 손절 (기초 기준)", 1.0, 10.0, BASE_STRAT["levels"]["percent"] * 100, 0.5) / 100

strat = copy.deepcopy(BASE_STRAT)
strat["sizing"].update(risk_pct=risk_pct, max_pair_weight=max_pair)
strat["levels"].update(sl_method=sl_method, atr_mult=atr_mult, percent=pct_stop)


@st.cache_resource(ttl=900, show_spinner="시세 불러오는 중…")
def market(mode: str) -> MarketData:
    return MarketData(PAIRS, mode=mode)


md = market(mode)
fx = md.try_last("USDKRW=X") if mode == "yahoo" else 1400.0
if mode == "demo":
    st.warning("데모 모드: 합성 데이터라 실제 가격이 아닙니다. 계산 흐름 확인용입니다.")


def krw(usd: float) -> str:
    return f"≈ ₩{usd * fx:,.0f}" if fx else ""


def ind(ticker: str, interval: str) -> pd.DataFrame:
    return add_indicators(md.ohlc(ticker, interval), strat["indicators"])


def analyze(pair: dict):
    daily = ind(pair["signal"], "1d")
    vix = md.try_last(pair.get("vol_index"))
    return daily, evaluate(daily, strat, vix)


def plan_for(pair: dict, ins: dict, sig, interval: str, **kw):
    d = ind(pair["signal"], interval)
    k = ins["leverage"]
    direction = 1 if k > 0 else -1
    return build_plan(
        d, pair["signal"], ins["ticker"], k, md.last(ins["ticker"], interval), equity, strat,
        regime_mult=sig.multiplier(direction, strat), vol_factor=sig.vol_factor,
        sigma_daily=sigma_daily_from(md.ohlc(pair["signal"], "1d")), expense=ins.get("expense", 0.0),
        **kw,
    )


tab_dash, tab_plan, tab_calc, tab_te = st.tabs(["대시보드", "차트 · 플래너", "환산 계산기", "추적오차"])

# ------------------------------------------------------------------ dashboard
with tab_dash:
    rows, errors = [], []
    for pair in PAIRS:
        try:
            daily, sig = analyze(pair)
            d_ins = next(i for i in pair["instruments"] if (i["leverage"] > 0) == (sig.preferred_direction > 0))
            plan = plan_for(pair, d_ins, sig, "1d")
            c = daily["Close"]
            rows.append({
                "페어": f"{pair['signal']} → {d_ins['ticker']}",
                "기초가": c.iloc[-1],
                "등락": c.iloc[-1] / c.iloc[-2] - 1,
                "국면": REGIME_BADGE[sig.regime],
                "Bull": sig.bull,
                "Bear": sig.bear,
                "추천": d_ins["ticker"] if plan.sizing.shares else "관망",
                "권장 비중": plan.sizing.weight,
                "수량": plan.sizing.shares,
                "ATR%": sig.values["atr_pct"],
                "변동성 백분위": sig.values["rvol_rank"],
                "변동성지수": sig.values["vix"],
                "변동성 계수": sig.vol_factor,
                "52주 고점 대비": sig.values["drawdown"],
                "SMA200 위": "예" if sig.above_sma200 else "아니오",
            })
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pair['signal']}: {e}")
    if rows:
        df = pd.DataFrame(rows).set_index("페어")
        st.dataframe(
            df.style.format({
                "기초가": "{:,.2f}", "등락": "{:+.2%}", "Bull": "{:.0f}", "Bear": "{:.0f}", "권장 비중": "{:.1%}",
                "ATR%": "{:.2%}", "변동성 백분위": "{:.0%}", "변동성지수": "{:.1f}", "변동성 계수": "×{:.2f}",
                "52주 고점 대비": "{:+.1%}",
            }, na_rep="-"),
            width="stretch",
        )
        total = df["권장 비중"].sum()
        cap = strat["sizing"]["max_total_weight"]
        msg = f"권장 비중 합계 {total:.1%} (한도 {cap:.0%})"
        (st.error if total > cap else st.caption)(msg + (" — 한도 초과: 페어별 수량을 줄이세요" if total > cap else ""))
        st.caption("국면·점수는 일봉 기준. 권장 비중 = min(리스크 기준, 변동성 타깃, 페어 상한) × 국면 계수 × 변동성 계수")
    for e in errors:
        st.error(e)

# ------------------------------------------------------------------ chart + planner
with tab_plan:
    c1, c2, c3 = st.columns([1, 1, 1])
    pair = c1.selectbox("페어", PAIRS, format_func=lambda p: f"{p['name']} ({p['signal']})")
    try:
        daily, sig = analyze(pair)
    except Exception as e:  # noqa: BLE001
        st.error(f"데이터 오류: {e}")
        st.stop()
    rec_idx = next(i for i, ins in enumerate(pair["instruments"])
                   if (ins["leverage"] > 0) == (sig.preferred_direction > 0))
    ins = c2.selectbox("매매 종목", pair["instruments"], index=rec_idx,
                       format_func=lambda i: f"{i['ticker']} ({i['leverage']:+g}x)")
    u_now = md.last(pair["signal"], tf)
    u_entry = c3.number_input(f"{pair['signal']} 진입가", value=round(u_now, 2), step=0.5, format="%.2f")
    use_manual = st.checkbox("손절가 직접 입력 (기초 기준)")
    u_stop_manual = None
    if use_manual:
        u_stop_manual = st.number_input(f"{pair['signal']} 손절가", value=round(u_now * (0.97 if ins['leverage'] > 0 else 1.03), 2),
                                        step=0.5, format="%.2f")
    try:
        plan = plan_for(pair, ins, sig, tf, u_entry=u_entry, u_stop_override=u_stop_manual)
    except Exception as e:  # noqa: BLE001
        st.error(f"플랜 계산 오류: {e}")
        st.stop()

    st.markdown(
        f"**국면** {REGIME_BADGE[sig.regime]} · Bull {sig.bull:.0f} / Bear {sig.bear:.0f} · "
        f"국면 계수 ×{plan.sizing.regime_mult:g} · 변동성 계수 ×{sig.vol_factor:g} · 기준 봉 {TF_LABEL[tf]}"
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

    # chart
    n_bars = st.slider("표시 봉 수", 60, 500, 180, 20)
    u_df = ind(pair["signal"], tf).iloc[-n_bars:]
    l_df = md.ohlc(ins["ticker"], tf).iloc[-n_bars:]
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.45, 0.4, 0.15], vertical_spacing=0.03,
                        subplot_titles=(pair["signal"], f"{ins['ticker']} (기초 레벨 환산)", "RSI"))
    for r, df_, name in ((1, u_df, pair["signal"]), (2, l_df, ins["ticker"])):
        fig.add_trace(go.Candlestick(x=df_.index, open=df_.Open, high=df_.High, low=df_.Low, close=df_.Close,
                                     name=name, showlegend=False), row=r, col=1)
    for col, color in (("sma_fast", "#f2a900"), ("sma_slow", "#7b61ff"), ("ema_fast", "#26a69a")):
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df[col], name=col, line=dict(width=1, color=color)), row=1, col=1)
    fig.add_trace(go.Scatter(x=u_df.index, y=u_df["rsi"], name="RSI", line=dict(width=1, color="#888")), row=3, col=1)
    for y in (30, 70):
        fig.add_hline(y=y, line=dict(width=0.5, dash="dot", color="#999"), row=3, col=1)
    lines = [("진입", plan.u_entry, plan.l_entry, "#1f77b4", "solid"),
             ("손절", plan.u_stop, plan.l_stop, "#d62728", "dash")]
    lines += [(f"TP{i}", u, l, "#2ca02c", "dash") for i, (u, l) in enumerate(zip(plan.u_tps, plan.l_tps), 1)]
    lines.append(("트레일", plan.u_trail, plan.l_trail, "#ff7f0e", "dot"))
    for label, u, l, color, dash in lines:
        fig.add_hline(y=u, line=dict(color=color, dash=dash, width=1), annotation_text=f"{label} {u:,.2f}",
                      annotation_position="right", row=1, col=1)
        fig.add_hline(y=l, line=dict(color=color, dash=dash, width=1), annotation_text=f"{label} {l:,.2f}",
                      annotation_position="right", row=2, col=1)
    breaks = [dict(bounds=["sat", "mon"])]
    if tf == "1h":
        breaks.append(dict(bounds=[16, 9.5], pattern="hour"))
    fig.update_xaxes(rangebreaks=breaks, rangeslider_visible=False)
    fig.update_layout(height=820, margin=dict(l=10, r=90, t=30, b=10), legend=dict(orientation="h"))
    st.plotly_chart(fig, width="stretch")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("레벨")
        lt = plan.levels_table()
        st.dataframe(lt.style.format({pair["signal"]: "{:,.2f}", ins["ticker"]: "{:,.2f}", "R": "{:+.0f}R",
                                      f"{pair['signal']} %": "{:+.2%}", f"{ins['ticker']} %": "{:+.2%}",
                                      "손익($)": "{:+,.0f}"}, na_rep="-"), width="stretch")
        st.subheader("메리츠증권 주문표 (수동 입력)")
        st.dataframe(plan.order_sheet(), width="stretch")
        st.caption("레버리지 ETF는 매일 리셋되므로 LETF 기준 손절·익절 가격은 매일 장 시작 전 이 화면에서 다시 확인하세요. "
                   "감시(스탑) 주문이 지원되지 않으면 기초 가격 알림 기준으로 수동 매도합니다.")
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
    a, b, c, d_ = st.columns(4)
    cu0 = a.number_input("기초 진입가", value=500.0, step=1.0)
    cu1 = b.number_input("기초 목표가", value=484.0, step=1.0)
    cl0 = c.number_input("LETF 진입가", value=80.0, step=0.5)
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
