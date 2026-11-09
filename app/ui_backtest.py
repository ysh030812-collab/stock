"""🧪 단타 백테스트 화면."""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from levtrack import dant as D
from levtrack.backtest import yearly_returns

ss = st.session_state
COLORS = ["#e8590c", "#868e96", "#1c7ed6"]


@st.cache_data(show_spinner="단타 백테스트 계산 중… (처음엔 데이터 내려받기로 시간이 더 걸립니다)", max_entries=20)
def _run(_md, mode: str, pair_json: str, strat_json: str, start: str, cost_bps: float, equity0: float):
    pair, strat = json.loads(pair_json), json.loads(strat_json)
    u = _md.ohlc(pair["signal"], "1d", "max")
    rf = None
    if mode != "demo":
        try:
            rf = (_md.ohlc("^IRX", "1d", "max")["Close"] / 100).clip(lower=0)  # 미국 3개월 국채 금리
        except Exception:  # noqa: BLE001
            rf = None
    return D.backtest(u, pair, strat, equity0, start, cost_bps, rf=rf)


def view(md, mode: str, pairs: list[dict], strat: dict, equity: float) -> None:
    st.markdown("#### 🧪 단타 백테스트")
    by_id = {p["id"]: p for p in pairs}
    with st.container(horizontal=True, gap="medium", vertical_alignment="bottom"):
        pid = st.segmented_control("페어", list(by_id), key="bt_pair", required=True, default=pairs[0]["id"],
                                   format_func=lambda i: f"{by_id[i]['name']} {by_id[i]['signal']}",
                                   persist_state="session")
        pair = by_id[pid]
        try:
            first = md.ohlc(pair["signal"], "1d", "max").index[0]
        except Exception as e:  # noqa: BLE001
            st.error(f"데이터를 받지 못했습니다: {e}")
            return
        years = list(range(first.year + 1, pd.Timestamp.today().year))
        start_y = st.selectbox("시작 연도", years, index=0, key=f"bt_start_{pid}", width=150)
        cost = st.number_input("편도 비용 (bp)", value=10.0, min_value=0.0, step=5.0, key="bt_cost", width=150,
                               help="수수료 + 슬리피지. 10bp = 0.1%")
        run = st.button("▶ 백테스트 실행", type="primary", key="bt_run")
    st.caption("신호 · 비중 · 자동 점검은 ⚙️ 설정값을 그대로 씁니다.")
    params = (mode, pid, f"{start_y}-01-01", float(cost), float(equity), json.dumps(strat, sort_keys=True, ensure_ascii=False))
    if run:
        ss["bt_params"] = params
    if ss.get("bt_params") != params:
        st.info("조건을 고른 뒤 **백테스트 실행**을 누르세요.")
        return
    try:
        r = _run(md, mode, json.dumps(pair, ensure_ascii=False), params[5], params[2], params[3], params[4])
    except Exception as e:  # noqa: BLE001
        st.error(f"백테스트 실패: {e}")
        return

    s = r.stats
    st.caption(f"{r.equity.index[0].date()} ~ {r.equity.index[-1].date()} · 초기 ${equity:,.0f} · 합성 3배 ETF "
               "(3배 × 일간수익 − 차입비용 − 보수, 현금은 국채 금리) · 신호 다음 날 시가 체결")
    with st.container(horizontal=True, gap="small"):
        for label, val in [("CAGR", f"{s.get('CAGR', 0):+.1%}"), ("MDD", f"{s.get('MDD', 0):.1%}"),
                           ("샤프", f"{s.get('샤프', float('nan')):.2f}"), ("거래 수", f"{s.get('거래 수', 0)}"),
                           ("승률", f"{s.get('승률', float('nan')):.0%}"), ("평균 보유", f"{s.get('평균 보유일', 0):.0f}일"),
                           ("보유 기간 비율", f"{s.get('노출 비율', 0):.0%}")]:
            st.metric(label, val, border=True, width=125)
    if not r.trades.empty:
        by = r.trades.groupby("모드")["계좌 기여"]
        st.table(pd.DataFrame({"횟수": by.size(), "승률": by.apply(lambda x: (x > 0).mean()),
                               "평균 기여": by.mean(), "합산 기여": by.apply(lambda x: (1 + x).prod() - 1)})
                 .style.format({"승률": "{:.0%}", "평균 기여": "{:+.2%}", "합산 기여": "{:+.1%}"}))

    st.markdown("##### 비교")
    cols = ["최종 자산", "총수익률", "CAGR", "MDD", "샤프", "소르티노", "연변동성"]
    st.table(r.bench_stats[[c for c in cols if c in r.bench_stats]].style.format(
        {"최종 자산": "${:,.0f}", "총수익률": "{:+.1%}", "CAGR": "{:+.1%}", "MDD": "{:.1%}", "샤프": "{:.2f}",
         "소르티노": "{:.2f}", "연변동성": "{:.1%}"}, na_rep="-"))

    fig = go.Figure()
    for c, color in zip(r.benchmarks.columns, COLORS):
        fig.add_trace(go.Scatter(x=r.benchmarks.index, y=r.benchmarks[c], name=c,
                                 line=dict(width=2.2 if c == "전략" else 1.2, color=color)))
    fig.update_yaxes(type="log", fixedrange=True)
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
    fig2.update_layout(height=260, margin=dict(l=8, r=8, t=30, b=8), title="낙폭", dragmode=False, showlegend=False,
                       hovermode="x unified")
    st.plotly_chart(fig2, width="stretch", config={"displayModeBar": False}, key="bt_dd")

    with st.expander("📅 연도별 수익률"):
        st.table(yearly_returns(r.benchmarks).style.format("{:+.1%}", na_rep="-"))
    tr = r.trades
    with st.expander(f"🧾 거래 내역 ({len(tr)}건)"):
        if tr.empty:
            st.info("거래가 없습니다.")
        else:
            show = tr.sort_values("진입일", ascending=False).head(100).copy()
            for c in ("진입일", "청산일"):
                show[c] = show[c].dt.date
            st.table(show[["진입일", "청산일", "모드", "종목", "계좌 기여", "보유일"]].style.format({"계좌 기여": "{:+.2%}"}),
                     hide_index=True)
            st.download_button("거래 내역 CSV", tr.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"dant_{pair['signal']}.csv", mime="text/csv", icon="⬇️")
    with st.expander("⚠️ 가정과 한계"):
        st.markdown(
            "- 3배 ETF 가격은 기초지수로 합성합니다 (실제 ETF 와 추적오차·차입 조건이 다를 수 있음).\n"
            "- 그날 종가로 신호를 확인하고 **다음 날 시가**에 체결, 목표 비중과 10%p 이상 차이 날 때만 조정합니다.\n"
            "- 익절은 종가 기준 RSI 로 판단합니다 (앱이 보여주는 익절가로 장중 지정가를 걸면 결과가 조금 다를 수 있음).\n"
            "- 세금·환율 제외. 단기 되돌림은 1999년 이전엔 통하지 않았던 **시기 의존적** 효과입니다 — 자동 점검을 끄면 그 위험이 커집니다.\n"
            "- 과거 성과가 미래를 보장하지 않습니다."
        )
