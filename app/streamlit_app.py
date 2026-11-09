"""LevTrack — 태블릿용 Streamlit 앱.

실행:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import copy  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from home_icon import inject_home_icon  # noqa: E402
from levtrack import leverage as lev  # noqa: E402
from levtrack.analysis import PairAnalysis, analyze_pair, prepare  # noqa: E402
from levtrack.config import load_pairs, load_strategy  # noqa: E402
from levtrack.data import MarketData  # noqa: E402
from levtrack.levels import SL_METHODS, TP_METHODS  # noqa: E402
from levtrack.patterns import candle_patterns, recent_divergences, recent_patterns  # noqa: E402
from levtrack.planner import build_plan, sigma_daily_from  # noqa: E402
from ui_chart import OVERLAYS, PANELS, PERIODS, build_chart  # noqa: E402
from ui_style import apply_style  # noqa: E402
import ui_journal as uj  # noqa: E402
import ui_settings as us  # noqa: E402
import ui_backtest as ubt  # noqa: E402
import ui_trend as ut  # noqa: E402
from levtrack import trend as TR  # noqa: E402
from levtrack.journal import TP_SPLITS  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
st.set_page_config(page_title="LevTrack", page_icon=str(STATIC / "icon-192.png"), layout="wide",
                   initial_sidebar_state="collapsed")

APP_VERSION = "2026.11.09 추세 롱숏 TP·SL"
MIN_STREAMLIT = (1, 67)
_st_ver = tuple(int(x) for x in st.__version__.split(".")[:2] if x.isdigit())
if _st_ver < MIN_STREAMLIT:
    st.error(
        f"이 앱은 Streamlit {'.'.join(map(str, MIN_STREAMLIT))} 이상이 필요하지만 현재 {st.__version__} 입니다.  \n"
        "Streamlit Community Cloud 라면 앱 화면 오른쪽 아래 **Manage app → ⋮ → Reboot app** 을 눌러 "
        "requirements.txt 를 다시 설치하세요."
    )
    st.caption(f"LevTrack {APP_VERSION}")
    st.stop()
inject_home_icon("LevTrack")
apply_style()

PAIRS = load_pairs()
PAIR_BY_ID = {p["id"]: p for p in PAIRS}
BASE_STRAT = load_strategy()
REGIME = {"BULL": ("🟢 상승", "lt-bull"), "NEUTRAL": ("🟡 중립", "lt-neutral"), "BEAR": ("🔴 하락", "lt-bear")}
TF_LABEL = {"1d": "일봉", "1h": "시간봉"}
CAT_LABEL = {"trend": "추세", "momentum": "모멘텀", "structure": "구조", "volume": "거래량", "rs": "상대강도"}
VIEWS = ["📊 대시보드", "📋 플래너", "📒 일지", "🧪 백테스트", "🧮 계산기", "📉 추적오차"]

ss = st.session_state
ss.setdefault("view", VIEWS[0])
ss.setdefault("pair_id", PAIRS[0]["id"])


def go_plan(pair_id: str, ticker: str) -> None:
    ss["view"] = VIEWS[1]
    ss["pair_id"] = pair_id
    ss[f"ins_{pair_id}"] = ticker


def refresh() -> None:
    st.cache_resource.clear()
    st.cache_data.clear()


# ------------------------------------------------------------------ 상단 바 (내비게이션 · 설정)
# 설정값은 일지 저장소(GitHub journal-data 브랜치의 journal/settings.json)에 자동 저장된다 → 다시 열어도 유지
with st.container(horizontal=True, vertical_alignment="center", gap="small"):
    st.markdown("### 📈 LevTrack")
    st.segmented_control("화면", VIEWS, key="view", required=True, label_visibility="collapsed")
    st.space("stretch")
    st.button("새로고침", icon="🔄", on_click=refresh)
    _strat_name = {"trend": "추세 롱숏", "swing": "스윙"}[ss.get("strategy", us.pick("strategy", "trend", ["trend", "swing"]))]
    eq_label = f"{_strat_name} · ${ss.get('eq', us.pick('eq', 10_000.0)):,.0f}"
    with st.popover(f"설정 · {eq_label}", icon="⚙️"):
        # 기본값은 저장된 설정(us.pick). 바뀌면 아래 save_if_changed 가 저장 → 다시 열어도 유지
        mode = st.segmented_control("데이터", ["yahoo", "demo"], key="mode", required=True,
                                    default=us.pick("mode", "yahoo", ["yahoo", "demo"]),
                                    format_func={"yahoo": "실시세 (무료)", "demo": "데모"}.get)
        strategy = st.segmented_control("전략", ["trend", "swing"], key="strategy", required=True,
                                        default=us.pick("strategy", "trend", ["trend", "swing"]),
                                        format_func={"trend": "📈 추세 롱숏 (기본)", "swing": "🧪 스윙 (실험용)"}.get,
                                        help="추세 롱숏: 실제 데이터 백테스트로 고른 3배 롱/숏 규칙. 스윙: 이전 규칙(검증상 우위 없음)")
        equity = st.number_input("계좌 평가금액 (USD)", min_value=100.0, value=max(100.0, us.pick("eq", 10_000.0)),
                                 step=500.0, key="eq")
        tl_vt = st.slider("추세 롱숏 목표 변동성 (%)", 20, 60, min(60, max(20, us.pick("tl_vt", 40))), 5, key="tl_vt",
                          help="비중 = 목표 변동성 ÷ (3 × 최근 20일 변동성), 최대 100%. 높일수록 공격적")
        swing_box = st.expander("🧪 스윙 전략(실험용) 설정", expanded=strategy == "swing")
        with swing_box:
            risk_ui = st.slider("1회 리스크 (계좌 대비 %)", 0.25, 3.0,
                                min(3.0, max(0.25, us.pick("risk", BASE_STRAT["sizing"]["risk_pct"] * 100))), 0.25, key="risk")
            maxw_ui = st.slider("페어당 최대 비중 (%)", 5, 100,
                                min(100, max(5, us.pick("maxw", int(BASE_STRAT["sizing"]["max_pair_weight"] * 100)))), 5,
                                key="maxw")
            tvol_ui = st.slider("목표 연변동성 (%)", 10, 80, min(80, max(10, us.pick("tvol", int(BASE_STRAT["sizing"]["target_vol"] * 100)))),
                                5, key="tvol",
                                help="레버리지 ETF 포지션이 계좌에 주는 변동성 한도. 높이면 변동성 큰 종목(SOXL 등)도 더 많이 삽니다.")
            neutral_mult = st.slider("중립 국면 비중 계수", 0.0, 1.0,
                                     min(1.0, max(0.0, us.pick("neu", float(BASE_STRAT["regime_multiplier"]["NEUTRAL"]["long"])))),
                                     0.25, key="neu", help="일봉 국면이 중립일 때 비중에 곱하는 값 (1이면 줄이지 않음)")
            use_vf = st.toggle("변동성 높을 때 비중 줄이기", value=us.pick("vf", True), key="vf",
                               help="끄면 VIX·실현변동성이 높아도 비중을 줄이지 않습니다.")
            sl_keys, tp_keys, split_keys = list(SL_METHODS), list(TP_METHODS), list(TP_SPLITS)
            sl_method = st.selectbox("손절 방식", sl_keys, format_func=SL_METHODS.get, key="sl",
                                     index=sl_keys.index(us.pick("sl", BASE_STRAT["levels"]["sl_method"], sl_keys)))
            tp_method = st.selectbox("익절 방식", tp_keys, format_func=TP_METHODS.get, key="tp",
                                     index=tp_keys.index(us.pick("tp", BASE_STRAT["levels"].get("tp_method", "r"), tp_keys)))
            tp_split = st.selectbox("익절 분할 (익절1 / 익절2 / 트레일링 잔량)", split_keys, key="split",
                                    index=split_keys.index(us.pick("split", split_keys[0], split_keys)),
                                    help="익절1·익절2 에서 몇 %씩 팔지. 매매일지 → 통계의 '익절 분할 비교'로 내게 맞는 비율을 확인하세요.")
            atr_mult = st.slider("ATR 배수 (ATR 손절)", 1.0, 4.0,
                                 min(4.0, max(1.0, us.pick("atr", float(BASE_STRAT["levels"]["atr_mult"])))), 0.25, key="atr")
            pct_ui = st.slider("고정 % 손절 (기초 기준)", 1.0, 10.0,
                               min(10.0, max(1.0, us.pick("pct", BASE_STRAT["levels"]["percent"] * 100))), 0.5, key="pct")
        us.save_if_changed({"strategy": strategy, "tl_vt": tl_vt, "mode": mode, "eq": equity, "risk": risk_ui, "maxw": maxw_ui, "tvol": tvol_ui,
                            "neu": neutral_mult, "vf": use_vf, "sl": sl_method, "tp": tp_method, "split": tp_split,
                            "atr": atr_mult, "pct": pct_ui})
        risk_pct, max_pair, target_vol, pct_stop = risk_ui / 100, maxw_ui / 100, tvol_ui / 100, pct_ui / 100
        st.caption(us.status_text())
        st.caption(f"LevTrack {APP_VERSION} · Streamlit {st.__version__}")

strat = copy.deepcopy(BASE_STRAT)
strat["sizing"].update(risk_pct=risk_pct, max_pair_weight=max_pair, target_vol=target_vol)
strat["regime_multiplier"]["NEUTRAL"] = {"long": neutral_mult, "short": neutral_mult}
strat["levels"].update(sl_method=sl_method, tp_method=tp_method, atr_mult=atr_mult, percent=pct_stop,
                       tp_fractions=TP_SPLITS[tp_split])
strat["trend_ls"] = {**strat.get("trend_ls", {}), "vt": tl_vt / 100}


@st.cache_resource(ttl=900, show_spinner="시세 불러오는 중…")
def market(mode: str) -> MarketData:
    return MarketData(PAIRS, mode=mode)


@st.cache_data(ttl=900, show_spinner="분석 중…")
def get_analysis(mode: str, pair_id: str) -> PairAnalysis:
    return analyze_pair(market(mode), PAIR_BY_ID[pair_id], BASE_STRAT)


@st.cache_data(ttl=900, show_spinner=False)
def get_frame(mode: str, pair_id: str, interval: str) -> pd.DataFrame:
    return prepare(market(mode), PAIR_BY_ID[pair_id], interval, BASE_STRAT)


md = market(mode)
fx = md.try_last("USDKRW=X") if mode == "yahoo" else 1400.0
if mode == "demo":
    st.warning("데모 모드: 합성 데이터라 실제 가격이 아닙니다.", icon="🧪")


def krw(usd: float) -> str:
    return f"≈ ₩{usd * fx:,.0f}" if fx else ""


def plan_for(a: PairAnalysis, ins: dict, interval: str, **kw):
    k = ins["leverage"]
    direction = 1 if k > 0 else -1
    d = get_frame(mode, a.pair["id"], interval)
    return build_plan(
        d, a.pair["signal"], ins["ticker"], k, md.last(ins["ticker"], interval), equity, strat,
        regime_mult=a.signal.multiplier(direction, strat), vol_factor=a.signal.vol_factor if use_vf else 1.0,
        sigma_daily=sigma_daily_from(a.daily), expense=ins.get("expense", 0.0), **kw,
    )


def badge(text: str, cls: str) -> str:
    return f'<span class="lt-badge {cls}">{text}</span>'


def timing_cls(t) -> str:
    return "lt-bull" if t.ok else ("lt-bear" if t.state.startswith("🚫") else "lt-neutral")


def div_text(divs) -> str:
    if not divs:
        return "없음"
    return ", ".join(f"{'강세' if d['bias'] > 0 else '약세'}({d['osc'].replace('_hist', '').upper()} {d['bars_ago']}봉 전)"
                     for d in divs)


def pat_text(pats) -> str:
    icon = {1: "▲", -1: "▼"}
    txt = ", ".join(f"{icon[p['bias']]}{p['label']}" for p in pats if p["bias"] != 0)
    return txt or "없음"


# ================================================================== 대시보드
def view_dashboard() -> None:
    if strategy == "trend":
        return ut.dashboard(md, PAIRS, TR.params(strat), equity, go_plan)
    total, cap = 0.0, strat["sizing"]["max_total_weight"]
    with st.container(horizontal=True, gap="small"):
        for pair in PAIRS:
            with st.container(border=True, width=360):
                try:
                    a = get_analysis(mode, pair["id"])
                except Exception as e:  # noqa: BLE001
                    st.error(f"{pair['signal']}: {e}")
                    continue
                sig, ins = a.signal, a.instrument()
                plan = plan_for(a, ins, "1d")
                total += plan.sizing.weight
                t = a.timing[a.direction]
                c = a.daily["Close"]
                rl, rc = REGIME[sig.regime]
                st.markdown(f"#### {pair['name']} · {pair['signal']} → {ins['ticker']}")
                st.markdown(badge(rl, rc) + badge(t.state, timing_cls(t)), unsafe_allow_html=True)
                with st.container(horizontal=True):
                    st.metric(pair["signal"], f"{c.iloc[-1]:,.2f}", f"{c.iloc[-1] / c.iloc[-2] - 1:+.2%}", width=140)
                    if t.ok and plan.sizing.shares:
                        lbl, val = "지금 진입", f"{plan.sizing.shares}주"
                    elif t.blocked or not plan.sizing.shares:
                        lbl, val = "권장", "관망"
                    else:
                        lbl, val = "대기 (진입 시)", f"{plan.sizing.shares}주"
                    st.metric(lbl, val, f"{plan.sizing.weight:.1%} · {ins['ticker']}", delta_color="off",
                              delta_arrow="off", width=140)
                v = sig.values
                lines = [
                    f"Bull <b>{sig.bull:.0f}</b> / Bear <b>{sig.bear:.0f}</b> · 변동성 계수 ×{sig.vol_factor:.2f}",
                    f"OBV {'-' if v['obv_up'] is None else ('상승' if v['obv_up'] else '하락')}"
                    + ("" if v["rs_above"] is None else
                       f" · 상대강도 {'강함' if v['rs_above'] else '약함'}(vs {pair['benchmark']})"),
                    f"다이버전스 {div_text(sig.divergences)}",
                    f"캔들 {pat_text(sig.patterns)} · 52주 고점 대비 {v['drawdown']:+.1%}",
                ]
                st.markdown('<div class="lt-sub">' + "<br>".join(lines) + "</div>", unsafe_allow_html=True)
                st.button("플랜 보기 →", key=f"go_{pair['id']}", type="primary", width="stretch",
                          on_click=go_plan, args=(pair["id"], ins["ticker"]))
    msg = f"권장 비중 합계 {total:.1%} (한도 {cap:.0%})"
    (st.error if total > cap else st.caption)(msg + (" — 한도 초과: 페어별 수량을 줄이세요" if total > cap else ""))
    st.caption("국면·점수·다이버전스·캔들은 일봉, 진입 타이밍은 시간봉 기준")


# ================================================================== 플래너
def _save_trend_trade(t: dict) -> None:
    uj.trades().append(t)
    uj.save(f"{t['ticker']} {t['shares']}주 진입 기록")


def view_planner() -> None:
    if strategy == "trend":
        return ut.planner(md, PAIRS, TR.params(strat), equity, fx, uj.trades(), _save_trend_trade)
    with st.container(horizontal=True, gap="medium"):
        pid = st.segmented_control("페어", [p["id"] for p in PAIRS], key="pair_id", required=True,
                                   format_func=lambda i: f"{PAIR_BY_ID[i]['name']} {PAIR_BY_ID[i]['signal']}",
                                   persist_state="session")
        pair = PAIR_BY_ID[pid]
        try:
            a = get_analysis(mode, pid)
        except Exception as e:  # noqa: BLE001
            st.error(f"데이터 오류: {e}")
            return
        tickers = [i["ticker"] for i in pair["instruments"]]
        ss.setdefault(f"ins_{pid}", a.instrument()["ticker"])
        ins_t = st.segmented_control(
            "매매 종목", tickers, key=f"ins_{pid}", required=True, persist_state="session",
            format_func=lambda t: f"{t} ({next(i for i in pair['instruments'] if i['ticker'] == t)['leverage']:+g}x)")
        tf = st.segmented_control("손절·익절 기준 봉", ["1d", "1h"], key="tf", required=True,
                                  default=us.pick("tf", "1d", ["1d", "1h"]), format_func=TF_LABEL.get,
                                  persist_state="session")
        us.save_if_changed({"tf": tf})
    ins = next(i for i in pair["instruments"] if i["ticker"] == ins_t)
    direction = 1 if ins["leverage"] > 0 else -1
    sig = a.signal
    u_now = md.last(pair["signal"], tf)

    with st.expander("✏️ 진입가 · 손절가 직접 입력 (비워두면 현재가 · 자동 계산)"):
        with st.container(horizontal=True):
            u_entry = st.number_input(f"{pair['signal']} 진입가", value=None, step=0.5, format="%.2f",
                                      placeholder=f"현재가 {u_now:,.2f}", key=f"entry_{pid}", width=260)
            u_stop = st.number_input(f"{pair['signal']} 손절가", value=None, step=0.5, format="%.2f",
                                     placeholder="자동", key=f"stop_{pid}", width=260)
    timing = a.timing[direction]
    # 눌림 대기·과열이면 '지금 가격'이 아니라 기다릴 가격(시간봉 EMA20)으로 지정가 매수 계획을 만든다
    use_wait = (not timing.ok) and (not timing.blocked) and timing.wait_price is not None and not u_entry
    entry = u_entry if u_entry else (timing.wait_price if use_wait else u_now)
    if u_stop and (u_stop - entry) * direction >= 0:
        st.warning(f"손절가가 {'진입가보다 낮아야' if direction > 0 else '진입가보다 높아야'} 합니다. 자동 계산값을 씁니다.")
        u_stop = None
    try:
        plan = plan_for(a, ins, tf, u_entry=entry, u_stop_override=u_stop)
    except Exception as e:  # noqa: BLE001
        st.error(f"플랜 계산 오류: {e}")
        return

    box = st.success if timing.ok else (st.error if timing.blocked else st.warning)
    extra = (f"  \n👉 **{pair['signal']} {timing.wait_price:,.2f}** (시간봉 EMA20)까지 "
             f"{'눌리면' if direction > 0 else '반등하면'} 진입 — 아래는 그 가격 기준 계획입니다." if use_wait else "")
    box(f"**시간봉 진입 타이밍 ({'롱' if direction > 0 else '숏'}) — {timing.state}**  \n" + " · ".join(timing.reasons)
        + extra)
    rl, rc = REGIME[sig.regime]
    st.markdown(
        badge(f"일봉 {rl}", rc)
        + f'<span class="lt-sub">Bull {sig.bull:.0f} / Bear {sig.bear:.0f} · 국면 계수 ×{plan.sizing.regime_mult:g}'
        f" · 변동성 계수 ×{sig.vol_factor:g} · {SL_METHODS[plan.sl_method]} 손절 · {TP_METHODS[plan.tp_method]} 익절</span>",
        unsafe_allow_html=True)

    with st.container(horizontal=True, gap="small"):
        st.metric("수량", f"{plan.sizing.shares}주", f"${plan.sizing.notional:,.0f}", delta_color="off",
                  delta_arrow="off", border=True, width=140)
        st.metric("비중", f"{plan.sizing.weight:.1%}", plan.sizing.binding, delta_color="off", delta_arrow="off",
                  border=True, width=140)
        st.metric("손절 시 손실", f"${plan.sizing.risk_usd:,.0f}", krw(plan.sizing.risk_usd), delta_color="off",
                  delta_arrow="off", border=True, width=140)
        st.metric(f"갭 {plan.gap_pct:.0%} 시 손실", f"${plan.gap_loss:,.0f}", f"{plan.gap_r:.2f}R",
                  delta_color="off", delta_arrow="off", border=True, width=140)
        st.metric(f"{plan.letf_ticker} 손절폭", f"{plan.l_stop / plan.l_entry - 1:+.1%}",
                  f"기초 {plan.u_stop / plan.u_entry - 1:+.1%}", delta_color="off", delta_arrow="off", border=True,
                  width=140)
    why = plan.sizing.bottleneck()
    st.caption("🧮 " + plan.sizing.explain() + (f"  \n가장 크게 줄인 요인: **{why}** — ⚙️ 설정에서 조절할 수 있습니다." if why else ""))
    if plan.sizing.shares == 0:
        st.info("현재 국면에서는 이 방향 진입을 권장하지 않습니다 (국면 계수 0 또는 계좌 대비 리스크 부족).")
    for n in plan.notes:
        st.caption("ℹ️ " + n)

    reset_note = "LETF 기준 가격은 매일 리셋되므로 장 시작 전 다시 확인하세요. 감시 주문이 안 되면 기초 가격 기준으로 수동 매도합니다."
    if timing.blocked and not u_entry:
        st.info("🚫 **진입 금지** — 일봉 국면이 이 방향을 허용하지 않아 주문표를 만들지 않습니다. "
                "직접 판단으로 들어가려면 위의 '진입가 직접 입력'을 쓰세요.")
    elif timing.ok or u_entry:
        st.markdown("##### 📝 메리츠증권 주문표 — " + ("직접 입력한 진입가" if u_entry and not timing.ok else "지금 진입"))
        st.table(plan.order_sheet(), hide_index=True)
        st.caption(reset_note)
        uj.entry_form(plan, pair_id=pid, signal=sig, timing=timing, timeframe=tf, fx=fx)
    elif use_wait:
        st.markdown("##### 📝 눌림 대기 주문표 — 지정가 매수 예약")
        st.table(plan.order_sheet(), hide_index=True)
        st.caption(f"1번 매수 지정가({plan.l_entry:,.2f})는 {pair['signal']}가 {timing.wait_price:,.2f}까지 "
                   f"{'내려올' if direction > 0 else '올라올'} 때 체결되도록 맞춘 값입니다. 체결되면 2~5번을 거세요. "
                   "지정가는 보통 당일만 유효하고 EMA20 도 매일 움직이니 매일 갱신하세요. " + reset_note)
        uj.entry_form(plan, pair_id=pid, signal=sig, timing=timing, timeframe=tf, fx=fx)
    else:
        st.info("⏳ 아직 진입 신호가 없습니다. 반등 신호(✅)가 뜨면 이 화면에 진입 주문표가 나옵니다.")
        with st.expander("참고: 지금 가격에 들어간다면"):
            st.table(plan.order_sheet(), hide_index=True)
            st.caption(reset_note)

    # ---- 차트
    periods = PERIODS[tf]
    with st.container(horizontal=True, gap="medium", vertical_alignment="bottom"):
        per = st.segmented_control("기간", list(periods), key=f"period_{tf}", required=True,
                                   default=list(periods)[1], persist_state="session")
        overlays = st.pills("겹쳐 보기", OVERLAYS, selection_mode="multi", key="ov", default=OVERLAYS,
                            persist_state="session")
        panels = st.pills("보조 지표", PANELS, selection_mode="multi", key="pn", default=["거래량·OBV", "RSI"],
                          persist_state="session")
        interactive = st.toggle("차트 확대·이동", key="zoom", persist_state="session",
                                help="끄면 화면 스크롤이 편하고, 켜면 두 손가락 확대와 드래그 이동이 됩니다.")
    fig, config = build_chart(get_frame(mode, pid, tf), md.ohlc(ins["ticker"], tf), plan, pair, ins, tf,
                              periods[per], overlays, panels, interactive, strat)
    st.plotly_chart(fig, width="stretch", config=config, key=f"chart_{pid}_{tf}")

    # ---- 상세
    with st.expander("📏 레벨 (기초 · LETF · 손익)", expanded=True):
        lt = plan.levels_table()
        st.table(lt.style.format({pair["signal"]: "{:,.2f}", ins["ticker"]: "{:,.2f}", "R": "{:+.1f}R",
                                  f"{pair['signal']} %": "{:+.2%}", f"{ins['ticker']} %": "{:+.2%}",
                                  "손익($)": "{:+,.0f}"}, na_rep="-"))
    with st.expander("⚖️ 진입 비율 계산"):
        st.table(pd.DataFrame(plan.sizing.as_rows(), columns=["항목", "값"]), hide_index=True)
    with st.expander("🧱 지지 · 저항 레벨"):
        kl = plan.key_levels.copy()
        kl["거리"] = kl["price"] / u_now - 1
        kl["구분"] = ["저항" if p > u_now else "지지" for p in kl["price"]]
        kl[ins["ticker"]] = [lev.convert_price(plan.u_entry, p, plan.l_entry, plan.k) for p in kl["price"]]
        kl = kl.rename(columns={"price": pair["signal"], "label": "레벨"})[
            ["레벨", "구분", pair["signal"], "거리", ins["ticker"]]]
        st.table(kl.style.format({pair["signal"]: "{:,.2f}", "거리": "{:+.2%}", ins["ticker"]: "{:,.2f}"}),
                 hide_index=True)
    with st.expander("⏳ 보유 기간별 예상가 (감쇠 반영)"):
        target = st.segmented_control("기초가가 도달하는 레벨", ["손절", "익절1"], key="proj_target", required=True,
                                      default="손절")
        st.table(plan.projection(target).style.format("{:,.2f}"))
        st.caption(f"일간 변동성 σ = {plan.sigma_daily:.2%} (최근 20일)")
    with st.expander("🔎 신호 근거 (점수 · 다이버전스 · 캔들)"):
        comp = []
        for cat, w in (("trend", 40), ("momentum", 25), ("structure", 15), ("volume", 10), ("rs", 10)):
            b, r = sig.components.get(f"bull_{cat}"), sig.components.get(f"bear_{cat}")
            comp.append((CAT_LABEL[cat], str(w), "-" if b is None else f"{b:.1f}", "-" if r is None else f"{r:.1f}"))
        adj = sig.values["divergence_adj"]
        comp.append(("다이버전스 보정", "±", f"{adj:+.0f}", f"{-adj:+.0f}"))
        st.table(pd.DataFrame(comp, columns=["항목", "배점", "Bull", "Bear"]), hide_index=True)
        lower_pats = recent_patterns(candle_patterns(a.lower.iloc[-30:]), 3)
        st.markdown(
            f"**일봉** 다이버전스: {div_text(sig.divergences)} · 캔들: {pat_text(sig.patterns)}  \n"
            f"**시간봉** 다이버전스: {div_text(recent_divergences(a.lower, strat['divergence']))}"
            f" · 캔들: {pat_text(lower_pats)}")


# ================================================================== 계산기
def view_calc() -> None:
    st.markdown("#### 기초 → 레버리지 ETF 가격 환산")
    with st.container(horizontal=True, gap="medium"):
        cu0 = st.number_input("기초 진입가", value=500.0, step=1.0, width=200)
        cu1 = st.number_input("기초 목표가", value=484.0, step=1.0, width=200)
        cl0 = st.number_input("LETF 진입가", value=80.0, step=0.5, width=200)
        ck = st.segmented_control("배수", [3, 2, -2, -3], default=3, required=True, format_func=lambda k: f"{k:+d}x")
    with st.container(horizontal=True, gap="medium"):
        csig = st.number_input("기초 일간 변동성 %", value=1.4, step=0.1, width=200) / 100
        cdays = st.number_input("보유 일수", value=10, min_value=1, step=1, width=200)
        cfee = st.number_input("연 보수 %", value=0.9, step=0.05, width=200) / 100
    inst = lev.convert_price(cu0, cu1, cl0, ck)
    proj = lev.project_price(cu0, cu1, cl0, ck, csig, cdays, cfee)
    with st.container(horizontal=True, gap="small"):
        st.metric("기초 변동률", f"{cu1 / cu0 - 1:+.2%}", border=True, width=200)
        st.metric("당일 환산가", f"{inst:,.2f}", f"{inst / cl0 - 1:+.2%}", border=True, width=200)
        st.metric(f"{cdays}일 보유 예상가", f"{proj:,.2f}", f"{proj / cl0 - 1:+.2%}", border=True, width=200)
    st.table(lev.projection_table(cu0, cu1, cl0, ck, csig, expense=cfee).style.format("{:,.2f}"))
    st.latex(r"\frac{L_t}{L_0} \approx \left(\frac{U_t}{U_0}\right)^k e^{-\frac{k^2-k}{2}\sigma^2 t}\, e^{-\text{fee}\cdot t}")


# ================================================================== 추적오차
def view_tracking() -> None:
    st.markdown("#### 추적오차 (LETF 일간수익률 − k × 기초 일간수익률)")
    window = st.segmented_control("창", [10, 20, 60, 120], default=20, required=True, key="te_win",
                                  format_func=lambda w: f"{w}일")
    for pair in PAIRS:
        for ins in pair["instruments"]:
            try:
                te = lev.tracking_error(md.ohlc(pair["signal"], "1d")["Close"], md.ohlc(ins["ticker"], "1d")["Close"],
                                        ins["leverage"], window).dropna().iloc[-250:]
            except Exception as e:  # noqa: BLE001
                st.error(f"{ins['ticker']}: {e}")
                continue
            with st.container(border=True):
                with st.container(horizontal=True, gap="small"):
                    st.metric(f"{pair['signal']}→{ins['ticker']} 누적 괴리 ({window}일)",
                              f"{te['rolling_sum'].iloc[-1]:+.2%}", width=220)
                    st.metric("실효 배수", f"{te['implied_k'].iloc[-1]:.2f}x", f"목표 {ins['leverage']:+g}x",
                              delta_color="off", delta_arrow="off", width=140)
                st.line_chart(te["rolling_sum"], height=120)
    st.caption("SOXX 와 SOXL/SOXS 는 추종 지수가 달라 괴리가 상대적으로 큽니다.")


{VIEWS[0]: view_dashboard, VIEWS[1]: view_planner,
 VIEWS[2]: lambda: uj.view(md, fx, lambda pid, tf: get_frame(mode, pid, tf), strat),
 VIEWS[3]: lambda: ubt.view(md, mode, PAIRS, strat, equity, strategy),
 VIEWS[4]: view_calc, VIEWS[5]: view_tracking}[ss["view"]]()
st.caption(f"LevTrack {APP_VERSION} · Streamlit {st.__version__}")
