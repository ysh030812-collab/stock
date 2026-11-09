"""추세 롱숏 전략 화면 (대시보드 카드 · 플래너 · 차트)."""
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from levtrack import journal as J
from levtrack import trend as TR
from levtrack.leverage import convert_price

ss = st.session_state
MODE_CLS = {"TREND": "lt-bull", "BOUNCE": "lt-neutral", "SHORT": "lt-bear", "CASH": "lt-sub"}
MODE_COLOR = {"TREND": "rgba(34,197,94,0.12)", "BOUNCE": "rgba(234,179,8,0.18)", "SHORT": "rgba(239,68,68,0.15)"}


def _ins(pair: dict, side: int) -> dict | None:
    want = 3 if side > 0 else -3
    return next((i for i in pair["instruments"] if i["leverage"] == want), None)


def _state(md, pair: dict, p: dict) -> dict:
    u = md.ohlc(pair["signal"], "1d")
    return TR.today(u, p)


def _held(trades: list[dict], pair_id: str) -> dict[str, int]:
    """일지의 보유 중 수량 (종목별)."""
    out: dict[str, int] = {}
    for t in trades:
        if t["pair_id"] == pair_id and not J.is_closed(t):
            out[t["ticker"]] = out.get(t["ticker"], 0) + J.remaining_shares(t)
    return out


def level_table(md, pair: dict, td: dict, u: pd.DataFrame, p: dict) -> pd.DataFrame:
    """현재 상태의 익절·손절(또는 현금일 때 진입) 가격 — 기초 가격과 3배 ETF 환산가, MTS 주문 방식."""
    lv = TR.levels(u, td, p)
    rows = []
    for r in lv["rows"]:
        name = r["구분"]
        if "진입" in name:
            side = -1 if "숏" in name else 1
        else:
            side = td["side"] or 1
        ins = _ins(pair, side)
        l_now = md.last(ins["ticker"]) if ins else None
        lp = convert_price(td["close"], r["가격"], l_now, ins["leverage"]) if (ins and r["가격"]) else None
        if "진입" in name:
            how = "종가 확인 → 다음 날 매수"
        elif r["가격"] is None:
            how = "-" if name == "익절" else "종가 확인 → 다음 날 매도"
        elif r["기준"] == "종가":
            how = "종가 알림 → 다음 날 매도"
        elif lp is not None and l_now is not None and lp > l_now:
            how = "지정가 매도"
        else:
            how = "감시가(스탑) 매도"
        rows.append({"구분": name, pair["signal"]: r["가격"], "3배 ETF": ins["ticker"] if ins else "-",
                     "ETF 환산가": lp, "주문": how, "설명": r["설명"]})
    return pd.DataFrame(rows)


def _fmt_levels(df: pd.DataFrame):
    num = {c: "{:,.2f}" for c in (df.columns[1], "ETF 환산가") if c in df}
    return df.style.format(num, na_rep="-")


def _badge(text: str, cls: str) -> str:
    return f'<span class="lt-badge {cls}">{text}</span>'


# ------------------------------------------------------------------ 대시보드
def dashboard(md, pairs: list[dict], p: dict, equity: float, go_plan) -> None:
    st.caption("기본 전략: **추세 롱숏** — 기초지수 일봉 종가로 판단하고 다음 거래일에 실행합니다.")
    with st.container(horizontal=True, gap="small"):
        for pair in pairs:
            with st.container(border=True, width=360):
                try:
                    td = _state(md, pair, p)
                except Exception as e:  # noqa: BLE001
                    st.error(f"{pair['signal']}: {e}")
                    continue
                ins = _ins(pair, td["side"]) if td["side"] else None
                st.markdown(f"#### {pair['name']} · {pair['signal']}")
                badges = _badge(td["label"], MODE_CLS[td["mode"]])
                if td["changed"]:
                    badges += _badge("🔔 오늘 신호 변경", "lt-neutral")
                st.markdown(badges, unsafe_allow_html=True)
                with st.container(horizontal=True):
                    st.metric(pair["signal"], f"{td['close']:,.2f}", f"200일선 대비 {td['dist']:+.1%}",
                              delta_color="off", delta_arrow="off", width=140)
                    if ins:
                        px = md.last(ins["ticker"])
                        n = int(equity * td["weight"] // px) if px > 0 else 0
                        st.metric(f"목표 {ins['ticker']}", f"{n}주", f"비중 {td['weight']:.0%}", delta_color="off",
                                  delta_arrow="off", width=140)
                    else:
                        st.metric("목표", "현금", "보유 없음", delta_color="off", delta_arrow="off", width=140)
                try:
                    lt = level_table(md, pair, td, md.ohlc(pair["signal"], "1d"), p)
                    lines = [f"<b>{r['구분']}</b> {r[pair['signal']]:,.2f}"
                             + (f" ({r['3배 ETF']} {r['ETF 환산가']:,.2f})" if pd.notna(r["ETF 환산가"]) else "")
                             if pd.notna(r[pair['signal']]) else f"<b>{r['구분']}</b> {r['설명'][:24]}"
                             for _, r in lt.iterrows() if "선택" not in r["구분"]]
                except Exception:  # noqa: BLE001
                    lines = []
                st.markdown('<div class="lt-sub">' + "<br>".join(lines)
                            + f"<br>RSI(2) {td['rsi2']:.0f} · 20일 변동성 {td['rv']:.0%}</div>", unsafe_allow_html=True)
                st.button("주문 보기 →", key=f"tgo_{pair['id']}", type="primary", width="stretch",
                          on_click=go_plan, args=(pair["id"], ins["ticker"] if ins else pair["instruments"][0]["ticker"]))


# ------------------------------------------------------------------ 플래너
def planner(md, pairs: list[dict], p: dict, equity: float, fx, trades: list[dict], save_fn, strat: dict | None = None) -> None:
    by_id = {x["id"]: x for x in pairs}
    pid = st.segmented_control("페어", list(by_id), key="pair_id", required=True,
                               format_func=lambda i: f"{by_id[i]['name']} {by_id[i]['signal']}", persist_state="session")
    pair = by_id[pid]
    try:
        u = md.ohlc(pair["signal"], "1d")
        sig = TR.signals(u, p)
        td = TR.today(u, p)
    except Exception as e:  # noqa: BLE001
        st.error(f"데이터 오류: {e}")
        return

    box = {"TREND": st.success, "BOUNCE": st.warning, "SHORT": st.error, "CASH": st.info}[td["mode"]]
    head = f"**{td['date'].date()} 종가 기준 신호: {td['label']}**"
    if td["changed"]:
        head += f"  (어제 {TR.MODES[td['prev_mode']]} → 오늘 변경 🔔)"
    elif td["mode"] != "CASH":
        head += f"  ({td['days_in']}거래일째)"
    box(head + "  \n" + " · ".join(td["triggers"]))

    held = _held(trades, pid)
    ins = _ins(pair, td["side"]) if td["side"] else None
    target_n, px = 0, None
    if ins:
        px = md.last(ins["ticker"])
        target_n = int(equity * td["weight"] // px) if px > 0 else 0

    with st.container(horizontal=True, gap="small"):
        st.metric("목표 종목", ins["ticker"] if ins else "현금", f"{ins['leverage']:+d}x" if ins else None,
                  delta_color="off", delta_arrow="off", border=True, width=140)
        st.metric("목표 비중", f"{td['weight']:.0%}", f"변동성 {td['rv']:.0%}", delta_color="off", delta_arrow="off",
                  border=True, width=140)
        st.metric("목표 수량", f"{target_n}주" if ins else "-", f"${target_n * (px or 0):,.0f}" if ins else None,
                  delta_color="off", delta_arrow="off", border=True, width=140)
        st.metric("일지 보유", ", ".join(f"{k} {v}주" for k, v in held.items()) or "없음", border=True, width=170)

    # 주문표: 일지 보유 → 목표
    rows = []
    for tk, q in held.items():
        if not ins or tk != ins["ticker"]:
            rows.append(("매도", tk, q, "다음 거래일 시가 부근 (시장가/지정가)", "목표에 없는 종목 정리"))
    if ins:
        have = held.get(ins["ticker"], 0)
        diff = target_n - have
        band = p["rebalance_band"]
        if have == 0 and target_n > 0:
            rows.append(("매수", ins["ticker"], target_n, f"현재가 {px:,.2f} 부근", f"{td['label']} 진입 (비중 {td['weight']:.0%})"))
        elif have and abs(diff) * px / max(equity, 1) > band:
            rows.append(("매수" if diff > 0 else "매도", ins["ticker"], abs(diff), f"현재가 {px:,.2f} 부근",
                         f"비중 조정 (차이 {abs(diff) * px / equity:.0%} > {band:.0%})"))
    st.markdown("##### 📝 다음 거래일 주문")
    if rows:
        st.table(pd.DataFrame(rows, columns=["구분", "종목", "수량", "가격", "메모"]), hide_index=True)
    else:
        st.info("주문할 것이 없습니다 — " + ("목표대로 보유 중입니다." if ins else "현금 유지."))
    st.caption("신호는 종가로 확정되므로, 한국 시간 아침에 확인하고 그날 밤 미국장 개장 직후 실행하면 됩니다. "
               "비중 차이가 작으면(10%p 이하) 조정하지 않습니다.")

    st.markdown("##### 🎯 " + ("익절 · 손절 (오늘 기준)" if td["mode"] != "CASH" else "다음 진입 가격"))
    try:
        st.table(_fmt_levels(level_table(md, pair, td, u, p)), hide_index=True)
    except Exception as e:  # noqa: BLE001
        st.caption(f"가격 계산 실패: {e}")
    st.caption("모든 가격은 전략 규칙을 오늘 기준 가격으로 바꾼 값이라 **매일 다시 확인**하세요. "
               "'종가' 기준 항목은 장중에 잠깐 닿아도 청산하지 않습니다 — 종가로 확인 후 다음 날 실행. "
               "익절(RSI) 가격은 장중 지정가로 걸어도 됩니다. 비상 손절은 규칙에 없는 선택 사항입니다.")

    if ins and target_n > 0:
        _record_form(pair, ins, td, px, target_n, fx, save_fn)

    _chart(sig, md, pair, p)

    if strat is not None:
        with st.expander("🔬 참고 지표 (규칙에는 쓰이지 않음)", expanded=False):
            try:
                indicator_panel(md, pair, strat)
            except Exception as e:  # noqa: BLE001
                st.caption(f"지표 계산 실패: {e}")

    with st.expander("📖 전략 규칙"):
        st.markdown(
            f"- **📈 추세 롱**: 종가 > 200일선×{1 + p['buf']:.2f} 진입, 종가 < 200일선×{1 - p['buf']:.2f} 청산 → 3배 롱\n"
            f"- **📉 반등 숏**: 200일선 아래 · 50일선 < 200일선 · RSI({p['rsi_n']}) > {p['short_entry']} 진입, "
            f"RSI < {p['short_exit']} 또는 {p['short_hold']}거래일 또는 하락 조건 해제 시 청산 → 3배 숏\n"
            f"- **🔁 과매도 롱**: 200일선 아래 · RSI({p['rsi_n']}) < {p['bounce_entry']} 진입, "
            f"RSI > {p['bounce_exit']} 또는 {p['bounce_hold']}거래일 청산 → 3배 롱 (비중 ×{p['bounce_scale']})\n"
            f"- **비중** = min(100%, 목표 변동성 {p['vt']:.0%} ÷ (3 × 최근 20일 변동성))\n"
            "- 1999년 이후 실제 데이터(합성 3배 ETF) 백테스트로 고른 규칙입니다. 🧪 백테스트에서 직접 확인하세요."
        )


def indicator_panel(md, pair: dict, strat: dict) -> None:
    """참고 지표 (매매 규칙에는 쓰이지 않음)."""
    from levtrack.analysis import prepare
    from levtrack.indicators import adx, rsi
    from levtrack.signals import evaluate

    d = prepare(md, pair, "1d", strat)
    sig = evaluate(d, strat, md.try_last(pair.get("vol_index")))
    last = d.iloc[-1]
    a = adx(d).iloc[-1]
    c = float(last["Close"])
    v = sig.values
    vix = v.get("vix")
    bbp = (c - last["bb_lower"]) / (last["bb_upper"] - last["bb_lower"])
    up, dn, mid = "🟢 강세", "🔴 약세", "⚪ 중립"
    rows = [
        ("200일선 거리", f"{c / last['sma_slow'] - 1:+.1%}", up if c > last["sma_slow"] else dn, "규칙의 기준선"),
        ("50일선 vs 200일선", "골든크로스" if last["sma_fast"] > last["sma_slow"] else "데드크로스",
         up if last["sma_fast"] > last["sma_slow"] else dn, "중기 추세"),
        ("MACD", f"{last['macd']:,.2f} / 시그널 {last['macd_signal']:,.2f}",
         up if last["macd"] > last["macd_signal"] else dn, "히스토그램 " + ("양(+)" if last["macd_hist"] > 0 else "음(−)")),
        ("RSI(14)", f"{last['rsi']:.0f}", up if last["rsi"] > 55 else (dn if last["rsi"] < 45 else mid),
         "70↑ 과매수 · 30↓ 과매도"),
        ("RSI(2)", f"{float(rsi(d['Close'], 2).iloc[-1]):.0f}", mid, "규칙에 사용 (90↑ 반등 숏, 10↓ 과매도 롱)"),
        ("ADX(14)", f"{a['adx']:.0f} (+DI {a['pdi']:.0f} / −DI {a['mdi']:.0f})",
         (up if a["pdi"] > a["mdi"] else dn) if a["adx"] > 20 else mid, "20↑ 추세 뚜렷, 20↓ 횡보"),
        ("OBV", "50일 평균 위" if v.get("obv_up") else "50일 평균 아래",
         up if v.get("obv_up") else dn, "거래량 흐름"),
        ("볼린저 %B", f"{bbp:.2f}", up if bbp > 0.8 else (dn if bbp < 0.2 else mid), "1↑ 상단 돌파 · 0↓ 하단 이탈"),
        ("변동성지수", "-" if vix is None else f"{vix:.1f}",
         mid if vix is None else (up if vix < 20 else (dn if vix > 30 else mid)), pair.get("vol_index", "")),
        ("20일 변동성 순위", "-" if v.get("rvol_rank") is None else f"{v['rvol_rank']:.0%}", mid, "1년 중 백분위"),
        ("52주 고점 대비", f"{v['drawdown']:+.1%}", mid, ""),
    ]
    if v.get("rs_above") is not None:
        rows.append(("상대강도", f"vs {pair['benchmark']}", up if v["rs_above"] else dn, "50일 평균 대비"))
    divs = ", ".join(f"{'강세' if x['bias'] > 0 else '약세'}({x['osc'].replace('_hist', '').upper()} {x['bars_ago']}봉 전)"
                     for x in sig.divergences) or "없음"
    pats = ", ".join(("▲" if p_["bias"] > 0 else "▼") + p_["label"] for p_ in sig.patterns if p_["bias"]) or "없음"
    rows += [("다이버전스", divs, mid, "최근 10봉"), ("캔들 패턴", pats, mid, "최근 3봉"),
             ("종합 점수", f"Bull {sig.bull:.0f} / Bear {sig.bear:.0f}",
              up if sig.bull >= 65 else (dn if sig.bear >= 65 else mid), "추세·모멘텀·구조·거래량·상대강도")]
    bull = sum(r[2] == up for r in rows)
    bear = sum(r[2] == dn for r in rows)
    st.markdown(f"강세 {bull}개 · 약세 {bear}개 · 중립 {len(rows) - bull - bear}개")
    st.table(pd.DataFrame(rows, columns=["지표", "값", "상태", "설명"]), hide_index=True)
    st.caption("⚠️ 참고용입니다. 1999년 이후 실제 데이터로 이 지표들(MACD·ADX·골든크로스·RSI14·OBV·VIX·상대강도·모멘텀·볼린저 등 19개)을 "
               "규칙에 하나씩 넣어 봤지만 세 지수·두 기간에서 일관되게 좋아지는 것이 없었고, 청산을 앞당기는 지표는 성과를 낮췄습니다. "
               "그래서 매매 규칙은 200일선 · RSI(2) · 변동성만 씁니다.")


def _record_form(pair, ins, td, px, n, fx, save_fn) -> None:
    exit_rule = {
        "TREND": f"{pair['signal']} 종가 < 200일선×(1−버퍼) 이면 청산",
        "BOUNCE": "RSI(2) > 70 또는 5거래일째 청산",
        "SHORT": "RSI(2) < 30, 5거래일째, 또는 하락 조건 해제 시 청산",
    }[td["mode"]]
    daily = td["rv"] / math.sqrt(252)
    ref = {"TREND": td["dn_band"], "BOUNCE": td["close"] * (1 - 2 * daily),
           "SHORT": td["close"] * (1 + 2 * daily)}[td["mode"]]
    with st.expander("📒 체결했으면 매매일지에 기록"):
        with st.form(f"tr_entry_{pair['id']}_{ins['ticker']}", clear_on_submit=True, border=False):
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
                save_fn(t)
        st.caption(f"청산 규칙이 함께 저장되고, 일지의 보유 카드가 매일 '계속 보유 / 청산 신호'를 알려줍니다. "
                   f"R 계산용 참고 손절: {pair['signal']} {ref:,.2f}")


def _chart(sig: pd.DataFrame, md, pair: dict, p: dict) -> None:
    per = st.segmented_control("기간", ["6개월", "1년", "3년"], key="tr_per", required=True, default="1년",
                               persist_state="session")
    n = {"6개월": 126, "1년": 252, "3년": 756}[per]
    s = sig.iloc[-n:]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04,
                        subplot_titles=(f"{pair['signal']} · 200일선 밴드 · 상태", f"RSI({p['rsi_n']})"))
    u = md.ohlc(pair["signal"], "1d").loc[s.index]
    fig.add_trace(go.Candlestick(x=u.index, open=u.Open, high=u.High, low=u.Low, close=u.Close, showlegend=False,
                                 increasing_line_color="#26a69a", decreasing_line_color="#ef5350"), row=1, col=1)
    for col, color, dash, name in (("sma", "#7b61ff", "solid", "200일선"), ("up_band", "#7b61ff", "dot", "+버퍼"),
                                   ("dn_band", "#7b61ff", "dot", "−버퍼"), ("sma50", "#f2a900", "solid", "50일선")):
        fig.add_trace(go.Scatter(x=s.index, y=s[col], name=name, line=dict(color=color, dash=dash, width=1.2)),
                      row=1, col=1)
    fig.add_trace(go.Scatter(x=s.index, y=s["rsi2"], name="RSI", line=dict(color="#888", width=1)), row=2, col=1)
    for y, c in ((p["short_entry"], "#d62728"), (p["bounce_entry"], "#2ca02c")):
        fig.add_hline(y=y, line=dict(color=c, dash="dot", width=0.8), row=2, col=1)
    # 상태 구간 음영
    m = s["mode"]
    start = None
    for i in range(len(m) + 1):
        cur = m.iloc[i] if i < len(m) else None
        prev = m.iloc[i - 1] if i > 0 else None
        if cur != prev:
            if prev in MODE_COLOR and start is not None:
                fig.add_vrect(x0=m.index[start], x1=m.index[i - 1], fillcolor=MODE_COLOR[prev], line_width=0,
                              layer="below", row=1, col=1)
            start = i
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], rangeslider_visible=False, fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    fig.update_layout(height=560, margin=dict(l=8, r=8, t=30, b=8), dragmode=False, hovermode="x",
                      legend=dict(orientation="h", y=-0.08))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=f"tr_chart_{pair['id']}")
    st.caption("배경색: 초록 = 추세 롱, 노랑 = 과매도 롱, 빨강 = 반등 숏 (그날 종가 기준 다음 날 보유 상태)")
