"""⚡ 단타 화면 — 오늘 신호 · 진입/익절/손절 가격 · 주문표 · 차트 · 일지 기록."""
from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from levtrack import dant as D
from levtrack import journal as J
from levtrack.leverage import convert_price

ss = st.session_state
MODE_CLS = {"DIP": "lt-bull", "BOUNCE": "lt-neutral", "SHORT": "lt-bear", "CASH": "lt-sub"}
MODE_BG = {"DIP": "rgba(34,197,94,0.15)", "BOUNCE": "rgba(234,179,8,0.20)", "SHORT": "rgba(239,68,68,0.15)"}


def _ins(pair: dict, side: int) -> dict | None:
    return next((i for i in pair["instruments"] if i["leverage"] == (3 if side > 0 else -3)), None)


def _badge(text: str, cls: str) -> str:
    return f'<span class="lt-badge {cls}">{text}</span>'


def _gate_text(td: dict, p: dict) -> str:
    perf = "" if td["gate_perf"] is None else f" · 최근 3년 {td['gate_perf']:+.0%}"
    if not p["gate"]:
        return f"자동 점검 꺼짐{perf}"
    return ("✅ 규칙 작동 중" if td["gate_on"] else "⏸️ 쉬는 중 (최근 3년 성과 마이너스)") + perf


def _held(trades: list[dict], pair_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in trades:
        if t["pair_id"] == pair_id and not J.is_closed(t):
            out[t["ticker"]] = out.get(t["ticker"], 0) + J.remaining_shares(t)
    return out


def level_table(md, pair: dict, td: dict) -> pd.DataFrame:
    rows = []
    for r in td["levels"]:
        name = r["구분"]
        side = -1 if "숏" in name else (td["side"] or 1)
        ins = _ins(pair, side)
        l_now = md.last(ins["ticker"]) if ins else None
        lp = convert_price(td["close"], r["가격"], l_now, ins["leverage"]) if (ins and r["가격"]) else None
        if "진입" in name:
            how = "종가 확인 → 다음 날 매수"
        elif r["가격"] is None:
            how = "종가 확인 → 다음 날 매도"
        elif r["기준"] == "종가":
            how = "종가 알림 → 다음 날 매도"
        elif lp is not None and l_now is not None and lp > l_now:
            how = "지정가 매도"
        else:
            how = "감시가(스탑) 매도"
        rows.append({"구분": name, pair["signal"]: r["가격"], "3배 ETF": ins["ticker"] if ins else "-",
                     "ETF 가격": lp, "주문": how, "설명": r["설명"]})
    return pd.DataFrame(rows)


def _fmt(df: pd.DataFrame):
    return df.style.format({c: "{:,.2f}" for c in df.columns if c not in ("구분", "3배 ETF", "주문", "설명")}, na_rep="-")


# ------------------------------------------------------------------ 카드 목록
def cards(md, pairs: list[dict], p: dict) -> None:
    with st.container(horizontal=True, gap="small"):
        for pair in pairs:
            with st.container(border=True, width=360):
                try:
                    td = D.today(md.ohlc(pair["signal"], "1d"), p)
                except Exception as e:  # noqa: BLE001
                    st.error(f"{pair['signal']}: {e}")
                    continue
                st.markdown(f"#### {pair['name']} · {pair['signal']}")
                b = _badge(td["label"], MODE_CLS[td["mode"]])
                if td["new"]:
                    b += _badge("🔔 새 신호 — 다음 날 진입", "lt-neutral")
                elif td["exit"]:
                    b += _badge("🔔 청산 — 다음 날 매도", "lt-neutral")
                elif td["mode"] != "CASH":
                    b += _badge(f"{td['days_in']}일째 보유", "lt-sub")
                st.markdown(b, unsafe_allow_html=True)
                with st.container(horizontal=True):
                    st.metric("RSI(2)", f"{td['rsi2']:.0f}", "200일선 " + ("위" if td["above"] else "아래"),
                              delta_color="off", delta_arrow="off", width=140)
                    ins = _ins(pair, td["side"]) if td["side"] else None
                    st.metric("목표", f"{ins['ticker']} {td['weight']:.0%}" if ins else "현금", delta_color="off", width=150)
                lines = []
                for r in td["levels"]:
                    if "선택" in r["구분"]:
                        continue
                    lines.append(f"<b>{r['구분']}</b> " + (f"{r['가격']:,.2f}" if r["가격"] else r["설명"][:28]))
                if td["gated"]:
                    lines.append(f"⏸️ {td['gated']} 신호가 있었지만 쉬는 중")
                lines.append(_gate_text(td, p))
                st.markdown('<div class="lt-sub">' + "<br>".join(lines) + "</div>", unsafe_allow_html=True)
                st.button("자세히 →", key=f"dsel_{pair['id']}", type="primary", width="stretch",
                          on_click=lambda pid=pair["id"]: ss.update(dant_pair=pid))


# ------------------------------------------------------------------ 상세
def detail(md, pairs: list[dict], p: dict, equity: float, fx, trades: list[dict], save_fn) -> None:
    by_id = {x["id"]: x for x in pairs}
    ss.setdefault("dant_pair", pairs[0]["id"])
    pid = st.segmented_control("페어", list(by_id), key="dant_pair", required=True,
                               format_func=lambda i: f"{by_id[i]['name']} {by_id[i]['signal']}")
    pair = by_id[pid]
    try:
        u = md.ohlc(pair["signal"], "1d")
        td = D.today(u, p)
        sig = D.signals(u, p)
    except Exception as e:  # noqa: BLE001
        st.error(f"데이터 오류: {e}")
        return

    box = {"DIP": st.success, "BOUNCE": st.warning, "SHORT": st.error, "CASH": st.info}[td["mode"]]
    head = f"**{td['date'].date()} 종가 기준: {td['label']}**"
    if td["new"]:
        head += " — 🔔 새 신호, 다음 거래일 시가 부근 진입"
    elif td["exit"]:
        head += f" — 🔔 {D.MODES[td['prev_mode']]} 종료, 다음 거래일 매도"
    elif td["mode"] != "CASH":
        head += f" — {td['days_in']}거래일째 (최대 {p['hold']}일)"
    extra = f"  \n⏸️ {td['gated']} 신호가 있었지만 자동 점검으로 쉬고 있습니다." if td["gated"] else ""
    box(head + f"  \nRSI(2) {td['rsi2']:.0f} · 200일선 {'위' if td['above'] else '아래'} "
        f"({td['close'] / td['sma'] - 1:+.1%}) · {_gate_text(td, p)}" + extra)

    held = _held(trades, pid)
    ins = _ins(pair, td["side"]) if td["side"] else None
    px = md.last(ins["ticker"]) if ins else None
    target_n = int(equity * td["weight"] // px) if (ins and px) else 0
    with st.container(horizontal=True, gap="small"):
        st.metric("목표 종목", ins["ticker"] if ins else "현금", f"{ins['leverage']:+d}x" if ins else None,
                  delta_color="off", delta_arrow="off", border=True, width=140)
        st.metric("비중", f"{td['weight']:.0%}", f"변동성 {td['rv']:.0%}", delta_color="off", delta_arrow="off",
                  border=True, width=140)
        st.metric("목표 수량", f"{target_n}주" if ins else "-", f"${target_n * (px or 0):,.0f}" if ins else None,
                  delta_color="off", delta_arrow="off", border=True, width=140)
        st.metric("일지 보유", ", ".join(f"{k} {v}주" for k, v in held.items()) or "없음", border=True, width=170)

    rows = []
    for tk, q in held.items():
        if not ins or tk != ins["ticker"]:
            rows.append(("매도", tk, q, "다음 거래일 시가 부근", "신호 종료 → 정리"))
    if ins and held.get(ins["ticker"], 0) == 0 and target_n > 0:
        rows.append(("매수", ins["ticker"], target_n, f"현재가 {px:,.2f} 부근", f"{td['label']} 진입"))
    st.markdown("##### 📝 다음 거래일 주문")
    if rows:
        st.table(pd.DataFrame(rows, columns=["구분", "종목", "수량", "가격", "메모"]), hide_index=True)
    else:
        st.info("주문할 것이 없습니다 — " + ("신호대로 보유 중입니다." if ins else "신호를 기다립니다."))

    st.markdown("##### 🎯 " + ("익절 · 손절 · 시간 청산" if td["mode"] != "CASH" else "다음 진입 가격"))
    st.table(_fmt(level_table(md, pair, td)), hide_index=True)
    st.caption("모든 가격은 오늘 기준으로 규칙을 환산한 값이라 매일 다시 확인하세요. '종가' 항목은 종가로 판단해 다음 날 실행하고, "
               "익절가는 장중 지정가로 걸어 둘 수 있습니다. 지정가는 보통 당일만 유효합니다.")

    if ins and target_n > 0 and held.get(ins["ticker"], 0) == 0:
        _record_form(pair, ins, td, px, target_n, fx, save_fn, p)

    _chart(sig, u, pair, p)

    with st.expander("📖 규칙과 근거"):
        st.markdown(
            f"- **🟢 눌림 매수**: 종가 > 200일선 · RSI(2) < {p['long_entry']} → 3배 롱. RSI(2) > {p['long_exit']} 또는 "
            f"{p['hold']}거래일째 청산\n"
            f"- **🟡 과매도 롱**: 종가 < 200일선 · RSI(2) < {p['long_entry']} → 3배 롱 절반 비중, 청산은 같음\n"
            f"- **🔴 반등 숏**: 200일선 아래 · 50일선 < 200일선 · RSI(2) > {p['short_entry']} → 3배 숏. "
            f"RSI(2) < {p['short_exit']} · {p['hold']}거래일째 · 200일선 회복 시 청산\n"
            f"- **비중** = min(100%, 목표 변동성 {p['vt']:.0%} ÷ (3 × 최근 20일 변동성))\n"
            "- **근거**: 1999년 이후 9개 시장(QQQ·SPY·SOXX·다우·러셀·DAX·닛케이·FTSE·항셍) 중 8곳에서 현금보다 나았음. "
            "'눌림 매수'가 가장 강함. 단, **1999년 이전엔 모든 시장에서 손해**(나스닥 1972~98 −100%)라 "
            "**자동 점검**(최근 3년 가상 성과가 마이너스면 쉬기)을 기본으로 켭니다 — 1999년 이전 구간 손실을 대부분 막음.\n"
            "- 미국 주식 수익은 대부분 **밤사이**에 나왔고 장중 수익은 0 근처 → 장중 매매보다 며칠 보유가 유리.\n"
            "- 과거 성과가 미래를 보장하지 않습니다. 일지로 내 실제 성적을 확인하세요."
        )


def _record_form(pair, ins, td, px, n, fx, save_fn, p) -> None:
    exit_rule = {"DIP": f"RSI(2) > {p['long_exit']} 또는 {p['hold']}거래일째 청산",
                 "BOUNCE": f"RSI(2) > {p['long_exit']} 또는 {p['hold']}거래일째 청산",
                 "SHORT": f"RSI(2) < {p['short_exit']}, {p['hold']}거래일째, 200일선 회복 시 청산"}[td["mode"]]
    ref = td["close"] * (1 - 2 * td["sigma_daily"] if td["side"] > 0 else 1 + 2 * td["sigma_daily"])
    with st.expander("📒 체결했으면 매매일지에 기록"):
        with st.form(f"d_entry_{pair['id']}_{ins['ticker']}", clear_on_submit=True, border=False):
            with st.container(horizontal=True, gap="medium"):
                price = st.number_input(f"{ins['ticker']} 체결가", value=round(px, 2), step=0.01, format="%.2f", width=180)
                shares = st.number_input("수량", value=max(1, n), min_value=1, step=1, width=150)
                d = st.date_input("체결일", value=date.today(), width=170)
                fee = st.number_input("수수료 ($)", value=0.0, min_value=0.0, step=0.1, width=150)
            note = st.text_input("메모", placeholder=f"{td['label']} 신호")
            if st.form_submit_button("일지에 저장", type="primary", width="stretch"):
                t = J.new_trend_trade(pair=pair, ticker=ins["ticker"], k=ins["leverage"], mode=td["label"],
                                      fill_price=price, shares=int(shares), u_now=td["close"], ref_stop_u=ref,
                                      exit_rule=exit_rule, entry_date=d.isoformat(), note=note, fee=fee, fx=fx,
                                      weight=td["weight"])
                t["strategy"] = "dant"
                save_fn(t)
        st.caption("일지의 보유 카드가 매일 '계속 보유 / 청산 신호'와 익절가를 알려줍니다.")


def _chart(sig: pd.DataFrame, u: pd.DataFrame, pair: dict, p: dict) -> None:
    per = st.segmented_control("기간", ["3개월", "6개월", "1년"], key="d_per", required=True, default="6개월",
                               persist_state="session")
    n = {"3개월": 63, "6개월": 126, "1년": 252}[per]
    s = sig.iloc[-n:]
    o = u.loc[s.index]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.04,
                        subplot_titles=(f"{pair['signal']} · 200일선 · 50일선", f"RSI({p['rsi_n']})"))
    fig.add_trace(go.Candlestick(x=o.index, open=o.Open, high=o.High, low=o.Low, close=o.Close, showlegend=False,
                                 increasing_line_color="#26a69a", decreasing_line_color="#ef5350"), row=1, col=1)
    fig.add_trace(go.Scatter(x=s.index, y=s["sma"], name="200일선", line=dict(color="#7b61ff", width=1.4)), row=1, col=1)
    fig.add_trace(go.Scatter(x=s.index, y=s["sma50"], name="50일선", line=dict(color="#f2a900", width=1.2)), row=1, col=1)
    ent = s[(s["mode"] != "CASH") & (s["days_in"] == 1)]
    for m, sym, col in (("DIP", "triangle-up", "#2ca02c"), ("BOUNCE", "triangle-up", "#d4a017"), ("SHORT", "triangle-down", "#d62728")):
        e = ent[ent["mode"] == m]
        if len(e):
            y = o.loc[e.index, "Low"] * 0.99 if m != "SHORT" else o.loc[e.index, "High"] * 1.01
            fig.add_trace(go.Scatter(x=e.index, y=y, mode="markers", name=D.MODES[m],
                                     marker=dict(symbol=sym, size=11, color=col)), row=1, col=1)
    fig.add_trace(go.Scatter(x=s.index, y=s["rsi2"], name="RSI(2)", line=dict(color="#888", width=1)), row=2, col=1)
    for y, c in ((p["long_entry"], "#2ca02c"), (p["long_exit"], "#999"), (p["short_entry"], "#d62728"), (p["short_exit"], "#999")):
        fig.add_hline(y=y, line=dict(color=c, dash="dot", width=0.8), row=2, col=1)
    m = s["mode"]
    start = None
    for i in range(len(m) + 1):
        cur = m.iloc[i] if i < len(m) else None
        prev = m.iloc[i - 1] if i > 0 else None
        if cur != prev:
            if prev in MODE_BG and start is not None:
                fig.add_vrect(x0=m.index[start], x1=m.index[i - 1], fillcolor=MODE_BG[prev], line_width=0, layer="below",
                              row=1, col=1)
            start = i
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], rangeslider_visible=False, fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    fig.update_layout(height=560, margin=dict(l=8, r=8, t=30, b=8), dragmode=False, hovermode="x",
                      legend=dict(orientation="h", y=-0.08))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=f"d_chart_{pair['id']}")
    st.caption("▲ 진입 신호 (초록 눌림 매수 · 노랑 과매도 롱) ▼ 반등 숏 · 배경 = 보유 구간 · RSI 점선: 10 / 70 / 90 / 30")


def my_record(trades: list[dict]) -> None:
    mine = [t for t in trades if t.get("strategy") == "dant"]
    closed = [t for t in mine if J.is_closed(t)]
    with st.expander(f"📊 내 단타 성적 (기록 {len(mine)}건 · 청산 {len(closed)}건)"):
        if not closed:
            st.caption("청산된 단타 기록이 쌓이면 여기에 승률·평균 수익이 나옵니다. 30~50건 이후의 숫자로 판단하세요.")
            return
        s = J.stats(closed)
        with st.container(horizontal=True, gap="small"):
            st.metric("거래 수", s["n"], border=True, width=120)
            st.metric("승률", f"{s['win_rate']:.0%}", border=True, width=120)
            st.metric("평균 R", f"{s['avg_r']:+.2f}R", border=True, width=120)
            st.metric("손익 합계", f"${s['total_pnl']:+,.0f}", border=True, width=150)
        st.caption("평균 R 이 플러스면 계속, 마이너스면 비중을 줄이거나 멈추세요. (백테스트 기준 승률 약 60~66%)")
