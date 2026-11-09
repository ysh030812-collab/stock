"""백테스트 화면."""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from levtrack.backtest import BTConfig, run_backtest, yearly_returns

ss = st.session_state
COLORS = ["#e8590c", "#868e96", "#1c7ed6", "#2f9e44"]


@st.cache_data(show_spinner="백테스트 계산 중… (처음엔 데이터 내려받기로 시간이 더 걸립니다)", max_entries=20)
def _run(_md, mode: str, pair_json: str, strat_json: str, start: str | None, allow_short: bool, use_timing: bool,
         cost_bps: float, equity0: float):
    pair, strat = json.loads(pair_json), json.loads(strat_json)
    u = _md.ohlc(pair["signal"], "1d", "max")
    bench = None
    if pair.get("benchmark"):
        try:
            bench = _md.ohlc(pair["benchmark"], "1d", "max")["Close"]
        except Exception:  # noqa: BLE001
            bench = None
    vix = None
    if pair.get("vol_index"):
        try:
            vix = _md.ohlc(pair["vol_index"], "1d", "max")["Close"]
        except Exception:  # noqa: BLE001
            vix = None
    cfg = BTConfig(allow_short=allow_short, use_timing=use_timing, equity0=equity0, start=start, cost_bps=cost_bps)
    return run_backtest(u, pair, strat, cfg, bench_close=bench, vix=vix)


def _fmt_stats(df: pd.DataFrame):
    pct = {c: "{:+.1%}" for c in ("총수익률", "CAGR", "MDD") if c in df}
    pct.update({c: "{:.1%}" for c in ("연변동성",) if c in df})
    num = {c: "{:.2f}" for c in ("샤프", "소르티노") if c in df}
    return df.style.format({**pct, **num, "최종 자산": "${:,.0f}"}, na_rep="-")


def view(md, mode: str, pairs: list[dict], strat: dict, equity: float) -> None:
    st.markdown("#### 🧪 백테스트")
    by_id = {p["id"]: p for p in pairs}
    with st.container(horizontal=True, gap="medium", vertical_alignment="bottom"):
        pid = st.segmented_control("페어", list(by_id), key="bt_pair", required=True, default=pairs[0]["id"],
                                   format_func=lambda i: f"{by_id[i]['name']} {by_id[i]['signal']}",
                                   persist_state="session")
        side = st.segmented_control("방향", ["롱+숏", "롱만"], key="bt_side", required=True, default="롱+숏",
                                    persist_state="session")
        entry = st.segmented_control("진입 규칙", ["국면+타이밍", "국면만"], key="bt_entry", required=True,
                                     default="국면+타이밍", persist_state="session",
                                     help="국면+타이밍: 앱과 같은 진입 타이밍 규칙(일봉 적용). 국면만: 국면이 허용하면 바로 진입")
    pair = by_id[pid]
    try:
        first = md.ohlc(pair["signal"], "1d", "max").index[0]
    except Exception as e:  # noqa: BLE001
        st.error(f"데이터를 받지 못했습니다: {e}")
        return
    years = list(range(first.year + 1, pd.Timestamp.today().year))
    with st.container(horizontal=True, gap="medium", vertical_alignment="bottom"):
        start_y = st.selectbox("시작 연도", years, index=0, key=f"bt_start_{pid}", width=150,
                               help="지표 준비(200일)에 앞 기간을 쓰므로 데이터 시작 다음 해부터 고를 수 있습니다")
        cost = st.number_input("편도 비용 (bp)", value=10.0, min_value=0.0, step=5.0, key="bt_cost", width=150,
                               help="수수료 + 슬리피지. 10bp = 0.1%")
        run = st.button("▶ 백테스트 실행", type="primary", key="bt_run")

    params = (mode, pid, f"{start_y}-01-01", side == "롱+숏", entry == "국면+타이밍", float(cost), float(equity),
              json.dumps(strat, sort_keys=True, ensure_ascii=False))
    if run:
        ss["bt_params"] = params
    if ss.get("bt_params") != params:
        st.info("조건을 고른 뒤 **백테스트 실행**을 누르세요. 손절·익절·분할·리스크는 ⚙️ 설정값 그대로 씁니다.")
        return
    try:
        r = _run(md, mode, json.dumps(pair, ensure_ascii=False), params[-1], params[2], params[3], params[4],
                 params[5], params[6])
    except Exception as e:  # noqa: BLE001
        st.error(f"백테스트 실패: {e}")
        return

    s = r.stats
    avg_expo = float(r.exposure[r.exposure != 0].abs().mean()) if (r.exposure != 0).any() else 0.0
    st.caption(f"{r.equity.index[0].date()} ~ {r.equity.index[-1].date()} · 초기 ${equity:,.0f} · "
               f"레버리지 ETF 는 기초지수로 합성(보수 반영) · 신호 다음 날 시가 진입")
    with st.container(horizontal=True, gap="small"):
        for label, val in [("CAGR", f"{s.get('CAGR', 0):+.1%}"), ("MDD", f"{s.get('MDD', 0):.1%}"),
                           ("샤프", f"{s.get('샤프', float('nan')):.2f}"), ("거래 수", f"{s.get('거래 수', 0)}"),
                           ("승률", f"{s.get('승률', float('nan')):.0%}"), ("평균 R", f"{s.get('평균 R', float('nan')):+.2f}R"),
                           ("손익비", f"{s.get('손익비(PF)', float('nan')):.2f}"),
                           ("보유 기간 비율", f"{s.get('노출 비율', 0):.0%}"), ("평균 실효 노출", f"{avg_expo:.0%}")]:
            st.metric(label, val, border=True, width=125)
    if avg_expo < 0.3:
        st.caption(f"ℹ️ 보유 중일 때 평균 실효 노출이 계좌의 {avg_expo:.0%} 수준이라 수익률·낙폭 모두 작게 나옵니다. "
                   "⚙️ 설정의 1회 리스크·목표 변동성·중립 계수·변동성 감액이 비중을 정합니다.")

    st.markdown("##### 비교")
    st.table(_fmt_stats(r.bench_stats[["최종 자산", "총수익률", "CAGR", "MDD", "샤프", "소르티노", "연변동성"]]))

    fig = go.Figure()
    for c, color in zip(r.benchmarks.columns, COLORS):
        fig.add_trace(go.Scatter(x=r.benchmarks.index, y=r.benchmarks[c], name=c,
                                 line=dict(width=2.2 if c == "전략" else 1.2, color=color)))
    fig.update_yaxes(type="log", title=None, fixedrange=True)
    fig.update_xaxes(fixedrange=True)
    fig.update_layout(height=380, margin=dict(l=8, r=8, t=30, b=8), title="자산 곡선 (로그)", dragmode=False,
                      legend=dict(orientation="h", y=-0.12), hovermode="x unified")
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="bt_eq")

    dd = r.benchmarks / r.benchmarks.cummax() - 1
    fig2 = go.Figure()
    for c, color in zip(dd.columns, COLORS):
        fig2.add_trace(go.Scatter(x=dd.index, y=dd[c], name=c, line=dict(width=2 if c == "전략" else 1, color=color),
                                  fill="tozeroy" if c == "전략" else None))
    fig2.update_yaxes(tickformat=".0%", fixedrange=True)
    fig2.update_xaxes(fixedrange=True)
    fig2.update_layout(height=260, margin=dict(l=8, r=8, t=30, b=8), title="낙폭", dragmode=False,
                       showlegend=False, hovermode="x unified")
    st.plotly_chart(fig2, width="stretch", config={"displayModeBar": False}, key="bt_dd")

    with st.expander("📅 연도별 수익률"):
        st.table(yearly_returns(r.benchmarks).style.format("{:+.1%}", na_rep="-"))
    tr = r.trades
    with st.expander(f"🧾 거래 내역 ({len(tr)}건)"):
        if tr.empty:
            st.info("거래가 없습니다.")
        else:
            reason = tr.groupby("청산 사유").agg(건수=("R", "size"), 평균R=("R", "mean"))
            st.table(reason.style.format({"평균R": "{:+.2f}R"}))
            show = tr.sort_values("진입일", ascending=False).head(100).copy()
            for c in ("진입일", "청산일"):
                show[c] = show[c].dt.date
            st.table(show[["진입일", "청산일", "방향", "종목", "R", "수익률", "보유일", "청산 사유", "국면"]]
                     .style.format({"R": "{:+.2f}R", "수익률": "{:+.1%}"}), hide_index=True)
            st.download_button("거래 내역 CSV", tr.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"backtest_{pair['signal']}.csv", mime="text/csv", icon="⬇️")
    with st.expander("⚠️ 가정과 한계"):
        st.markdown(
            "- 레버리지 ETF 가격은 기초지수 일간 수익률 × 배수 − 보수로 **합성**합니다. 실제 ETF 와 추적오차·차입비용 차이가 있습니다. "
            "(SOXL/SOXS 는 실제로 SOXX 와 다른 반도체 지수를 추종)\n"
            "- 실제 앱의 진입 타이밍은 **시간봉**이지만, 무료 시간봉은 최근 2년뿐이라 백테스트는 같은 규칙을 **일봉**에 적용합니다.\n"
            "- 신호는 그날 종가로 판단하고 **다음 날 시가**에 진입. 손절·익절은 기초 가격 기준, 갭이면 시가 체결, "
            "같은 날 손절과 익절이 모두 가능하면 손절을 먼저 처리(보수적).\n"
            "- 현금 이자·세금·환율은 반영하지 않습니다. 과거 성과가 미래를 보장하지 않습니다.\n"
            "- 설정을 바꿔 가며 가장 좋은 값을 고르면 **과최적화**되기 쉽습니다. 여러 페어·기간에서 고르게 괜찮은 설정을 고르세요."
        )
