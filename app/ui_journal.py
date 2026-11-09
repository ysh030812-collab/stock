"""매매일지 화면 · 플래너의 '일지에 기록' 폼."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from levtrack import journal as J
import hashlib

from levtrack.journal_store import clean_token, make_store, token_problem

LOCAL_PATH = Path(__file__).resolve().parent.parent / "data" / "journal.json"
ss = st.session_state


# ------------------------------------------------------------------ 저장소
def _secrets() -> dict:
    try:
        return dict(st.secrets.get("journal", {}))
    except Exception:  # noqa: BLE001  secrets.toml 이 없으면 예외
        return {}


@st.cache_resource
def _store(token_key: str):  # 토큰이 바뀌면 새 저장소 객체를 만든다
    return make_store(_secrets(), LOCAL_PATH)


def store():
    tok = clean_token(_secrets().get("github_token"))
    return _store(hashlib.sha256(tok.encode()).hexdigest()[:16] if tok else "")


def trades() -> list[dict]:
    if "journal" not in ss:
        try:
            ss["journal"] = store().load()
            ss["journal_err"] = None
        except Exception as e:  # noqa: BLE001
            ss["journal"], ss["journal_err"] = [], str(e)
    return ss["journal"]


def save(msg: str) -> bool:
    try:
        store().save(ss["journal"])
    except Exception as e:  # noqa: BLE001
        st.error(f"일지 저장 실패: {e}")
        return False
    st.toast(msg, icon="📒")
    return True


def reload() -> None:
    ss.pop("journal", None)
    _store.clear()


# ------------------------------------------------------------------ 플래너: 진입 기록
def entry_form(plan, *, pair_id: str, signal, timing, timeframe: str, fx) -> None:
    with st.expander("📒 체결했으면 매매일지에 기록"):
        with st.form(f"entry_{pair_id}_{plan.letf_ticker}", clear_on_submit=True, border=False):
            with st.container(horizontal=True, gap="medium"):
                price = st.number_input(f"{plan.letf_ticker} 체결가", value=round(plan.l_entry, 2), step=0.01,
                                        format="%.2f", width=180)
                shares = st.number_input("수량", value=max(1, plan.sizing.shares), min_value=1, step=1, width=150)
                d = st.date_input("체결일", value=date.today(), width=170)
                fee = st.number_input("수수료 ($)", value=0.0, min_value=0.0, step=0.1, width=150)
            note = st.text_input("진입 근거 메모", placeholder="예: 시간봉 눌림 반등, POC 지지")
            if st.form_submit_button("일지에 저장", type="primary", width="stretch"):
                t = J.new_trade(plan, pair_id=pair_id, fill_price=price, shares=int(shares),
                                entry_date=d.isoformat(), note=note, fee=fee, fx=fx, signal=signal,
                                timing=timing, timeframe=timeframe)
                trades().append(t)
                save(f"{plan.letf_ticker} {int(shares)}주 진입 기록")
        st.caption("계획(손절·익절가)과 그때의 국면·타이밍이 함께 저장되어, 나중에 어떤 조건에서 잘 맞았는지 볼 수 있습니다.")


# ------------------------------------------------------------------ 일지 화면
def _fmt(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    fmt = {"진입가": "{:,.2f}", "평균 청산가": "{:,.2f}", "손익($)": "{:+,.0f}", "R": "{:+.2f}R",
           "수익률": "{:+.1%}"}
    return df.style.format({k: v for k, v in fmt.items() if k in df}, na_rep="-")


def _exit_box(t: dict, cur: float | None, lv: dict | None = None) -> None:
    rem = J.remaining_shares(t)
    p = t["plan"]
    with st.expander("청산 기록"):
        trend_trade = t.get("strategy") in ("trend", "dant")
        reasons = ["신호 청산", "손절", "수동"] if trend_trade else [r for r in J.EXIT_REASONS if r != "신호 청산"]
        reason = st.segmented_control("사유", reasons, key=f"rsn_{t['id']}", required=True, default=reasons[0])
        f1, f2 = p.get("tp_fractions", [0.33, 0.33])
        tp_today = {name: l for name, _, l in lv["tps"]} if lv else {}
        l_tps = p.get("l_tps") or [cur or t["entry_price"]] * 2
        sug_price = {"익절1": tp_today.get("익절1", l_tps[0]), "익절2": tp_today.get("익절2", l_tps[1]),
                     "신호 청산": cur or t["entry_price"],
                     "손절": lv["l_stop"] if lv else p["l_stop"],
                     "트레일링": (lv["l_trail"] if lv and lv["l_trail"] else None) or cur or p.get("l_trail") or t["entry_price"],
                     "수동": cur or t["entry_price"]}[reason]
        sug_qty = {"익절1": round(t["shares"] * f1), "익절2": round(t["shares"] * f2)}.get(reason, rem)
        sug_qty = min(rem, max(1, sug_qty))
        with st.container(horizontal=True, gap="medium"):
            price = st.number_input("체결가", value=round(float(sug_price), 2), step=0.01, format="%.2f",
                                    key=f"px_{t['id']}_{reason}", width=170)
            qty = st.number_input("수량", value=sug_qty, min_value=1, max_value=rem, step=1,
                                  key=f"q_{t['id']}_{reason}", width=140)
            d = st.date_input("청산일", value=date.today(), key=f"d_{t['id']}", width=170)
            fee = st.number_input("수수료 ($)", value=0.0, min_value=0.0, step=0.1, key=f"f_{t['id']}", width=140)
        note = st.text_input("메모", key=f"n_{t['id']}", placeholder="예: 트레일 이탈, 실적 발표 전 정리")
        if st.button("청산 저장", key=f"save_{t['id']}", type="primary", width="stretch"):
            J.add_exit(t, price=price, shares=int(qty), reason=reason, exit_date=d.isoformat(), note=note, fee=fee)
            if save(f"{t['ticker']} {int(qty)}주 {reason} 기록"):
                st.rerun()


def _delete_box(t: dict) -> None:
    with st.expander("기록 삭제"):
        sure = st.checkbox("이 거래 기록을 삭제합니다", key=f"del_ok_{t['id']}")
        if st.button("삭제", key=f"del_{t['id']}", disabled=not sure):
            ss["journal"] = [x for x in trades() if x["id"] != t["id"]]
            if save(f"{t['ticker']} 기록 삭제"):
                st.rerun()


def _levels_for(t: dict, md, frame_fn, strat) -> tuple[dict | None, bool]:
    """오늘 기준 손절·트레일·익절 가격 계산 (데이터를 못 받으면 None)."""
    from levtrack.levels import chandelier

    if t.get("strategy") in ("trend", "dant"):
        return None, False
    try:
        u_now, l_now = md.last(t["signal"]), md.last(t["ticker"])
        d = frame_fn(t["pair_id"], t.get("timeframe", "1d"))
        # 진입 이후 구간만 써서 진입 전 고점이 트레일에 섞이지 않게 함
        since = d[d.index >= pd.Timestamp(t["entry_date"])]
        ch = None
        if len(since) >= 1:
            lvcfg = strat["levels"]
            n = min(lvcfg["chandelier_n"], len(since))
            ch = chandelier(since.assign(atr=d["atr"].reindex(since.index)), t["direction"], n, lvcfg["chandelier_mult"])
        return J.today_levels(t, u_now=u_now, l_now=l_now, chandelier_u=ch)
    except Exception:  # noqa: BLE001
        return None, False


def _trend_status(t: dict, md, strat) -> None:
    """신호 청산형 거래(단타 · 추세): 오늘 신호가 이 거래를 계속 보유하라는지 확인하고 익절·손절 가격 표시."""
    from levtrack.config import load_pairs

    try:
        u = md.ohlc(t["signal"], "1d")
        if t.get("strategy") == "dant":
            from levtrack import dant as D

            td = D.today(u, D.params(strat))
            notes = [f"RSI(2) {td['rsi2']:.0f}", f"{td['days_in']}거래일째" if td["mode"] != "CASH" else "신호 종료"]
        else:
            from levtrack import trend as TR

            td = TR.today(u, TR.params(strat))
            notes = td["triggers"]
    except Exception as e:  # noqa: BLE001
        st.caption(f"신호 확인 실패: {e}")
        return
    want = td["side"]
    if want == t["direction"]:
        st.success(f"계속 보유 — 오늘 신호 {td['label']} (목표 비중 {td['weight']:.0%})", icon="✅")
        if t.get("strategy") == "dant":
            try:
                from ui_dant import _fmt, level_table

                pair = next(x for x in load_pairs() if x["id"] == t["pair_id"])
                st.table(_fmt(level_table(md, pair, td)), hide_index=True)
            except Exception as e:  # noqa: BLE001
                st.caption(f"익절·손절 계산 실패: {e}")
    else:
        st.error(f"**청산 신호** — 오늘 신호 {td['label']}. 다음 거래일에 {t['ticker']} 를 매도하세요.", icon="🔔")
    st.caption(" · ".join(notes))


def _today_table(t: dict, lv: dict) -> None:
    rows = [("손절 감시가", lv["stop_u"], lv["l_stop"]),
            ("트레일 감시가" + ("" if lv["trail_active"] else " (아직 손절 아래)"), lv["trail_u"], lv["l_trail"]),
            ("➡️ 적용 감시가", lv["eff_u"], lv["l_eff"])]
    rows += [(f"{name} 지정가", u, l) for name, u, l in lv["tps"]]
    df = pd.DataFrame(rows, columns=["오늘 주문", f"{t['signal']} (알림 기준)", f"{t['ticker']} (MTS 입력)"])
    st.table(df.style.format({df.columns[1]: "{:,.2f}", df.columns[2]: "{:,.2f}"}, na_rep="-"), hide_index=True)
    upd = t.get("trail_updated")
    st.caption("레버리지 ETF 는 매일 리셋되어 같은 기초 가격이라도 LETF 가격이 달라집니다. 장 시작 전 MTS 의 감시가·지정가를 "
               "이 표대로 고치세요. **감시가는 올리기만** 합니다(숏 ETF 도 LETF 가격 기준으로는 올리기만)."
               + (f" 트레일 마지막 상향: {upd}" if upd else ""))


def view(md, fx, frame_fn=None, strat=None) -> None:
    ts = trades()
    s = store()
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown("#### 📒 매매일지")
        st.space("stretch")
        st.button("다시 불러오기", icon="🔄", on_click=reload)
    if ss.get("journal_err"):
        st.error(f"일지를 불러오지 못했습니다: {ss['journal_err']}")
        st.caption("Secrets 를 고친 뒤 위의 **다시 불러오기**를 누르세요. 그래도 같으면 Manage app → Reboot app.")
    problem = token_problem(clean_token(_secrets().get("github_token")))
    if problem:
        st.warning(problem, icon="🔑")
    if s.kind == "local":
        st.warning("일지가 **서버 파일**에 저장되고 있습니다. Streamlit Cloud 에서는 앱이 재시작되면 지워질 수 있으니 "
                   "아래 **백업**을 받아 두거나, README 의 'GitHub 저장 설정'을 해 주세요.", icon="⚠️")
    else:
        st.caption(f"저장 위치: {s.label} · 토큰 {s.fingerprint}")
    if s.kind == "github" and (ss.get("journal_err") or st.toggle("GitHub 연결 테스트", key="gh_diag")):
        if ss.get("journal_err"):
            st.markdown("**GitHub 연결 테스트**")
        try:
            for ok, msg in s.diagnose():
                (st.success if ok else st.error)(msg, icon="✅" if ok else "❌")
        except Exception as e:  # noqa: BLE001
            st.error(f"연결 테스트 실패: {e}")

    prices = {}
    for tk in {t["ticker"] for t in ts if not J.is_closed(t)}:
        try:
            prices[tk] = md.last(tk)
        except Exception:  # noqa: BLE001
            pass

    tab = st.segmented_control("보기", ["보유 중", "청산 완료", "통계", "백업"], key="jr_tab", required=True,
                               default="보유 중", label_visibility="collapsed", persist_state="session")
    if tab == "보유 중":
        opens = [t for t in ts if not J.is_closed(t)]
        if not opens:
            st.info("보유 중인 거래가 없습니다. 플래너에서 주문표 아래 **매매일지에 기록**으로 진입을 남기세요.")
        dirty = False
        for t in reversed(opens):
            cur = prices.get(t["ticker"])
            lv, changed = _levels_for(t, md, frame_fn, strat) if frame_fn and strat else (None, False)
            dirty |= changed
            m = J.metrics(t, cur)
            p = t["plan"]
            with st.container(border=True):
                side = "롱" if t["direction"] > 0 else "숏"
                st.markdown(f"**{t['ticker']}** ({side} {t['signal']}) · {m['remaining']}/{t['shares']}주 보유 · "
                            f"진입 {t['entry_price']:,.2f} ({t['entry_date']}) · {m['days']}일째")
                with st.container(horizontal=True, gap="small"):
                    st.metric("현재가", "-" if cur is None else f"{cur:,.2f}", border=True, width=140)
                    st.metric("평가 손익", f"${m['pnl']:+,.0f}", f"{m['return_pct']:+.1%}", border=True, width=140)
                    st.metric("R", "-" if pd.isna(m["r"]) else f"{m['r']:+.2f}R", border=True, width=140)
                    stop_px = lv["l_eff"] if lv else p["l_stop"]
                    st.metric("감시가 체결 시", f"${(stop_px - t['entry_price']) * m['remaining']:+,.0f}",
                              "트레일 수익 확정" if lv and lv["trail_active"] and stop_px > t["entry_price"] else None,
                              delta_color="off", delta_arrow="off", border=True, width=150)
                if lv:
                    _today_table(t, lv)
                if t.get("strategy") in ("trend", "dant"):
                    _trend_status(t, md, strat)
                    st.markdown(f'<div class="lt-sub">청산 규칙: {p.get("exit_rule", "-")}'
                                + (f"<br>메모: {t['note']}" if t.get("note") else "") + "</div>", unsafe_allow_html=True)
                else:
                    st.markdown(
                        f'<div class="lt-sub">진입 시 계획(LETF): 손절 {p["l_stop"]:,.2f} · 익절1 {p["l_tps"][0]:,.2f} · '
                        f'익절2 {p["l_tps"][1]:,.2f}'
                        + (f"<br>메모: {t['note']}" if t.get("note") else "") + "</div>", unsafe_allow_html=True)
                if t["exits"]:
                    st.table(pd.DataFrame(t["exits"])[["date", "reason", "shares", "price"]]
                             .rename(columns={"date": "날짜", "reason": "사유", "shares": "수량", "price": "가격"})
                             .style.format({"가격": "{:,.2f}"}), hide_index=True)
                _exit_box(t, cur, lv)
                _delete_box(t)
        if dirty:
            save("트레일링 스탑 상향 저장")

    elif tab == "청산 완료":
        closed = [t for t in ts if J.is_closed(t)]
        if not closed:
            st.info("청산 완료된 거래가 없습니다.")
        else:
            df = J.to_frame(closed).sort_values("진입일", ascending=False)
            cols = ["진입일", "종목", "방향", "수량", "진입가", "평균 청산가", "손익($)", "R", "수익률", "보유일", "마지막 청산"]
            st.table(_fmt(df[cols]), hide_index=True)
            pick = st.selectbox("상세 보기", [t["id"] for t in closed], format_func=lambda i: next(
                f"{t['entry_date']} {t['ticker']} ({J.metrics(t)['r']:+.2f}R)" for t in closed if t["id"] == i))
            t = next(x for x in closed if x["id"] == pick)
            st.json({k: t[k] for k in ("snapshot", "plan", "exits", "note")}, expanded=False)
            _delete_box(t)

    elif tab == "통계":
        stt = J.stats(ts)
        if stt["n"] == 0:
            st.info("청산 완료된 거래가 쌓이면 통계가 나옵니다.")
            return
        with st.container(horizontal=True, gap="small"):
            st.metric("거래 수", stt["n"], border=True, width=130)
            st.metric("승률", f"{stt['win_rate']:.0%}", border=True, width=130)
            st.metric("평균 R", f"{stt['avg_r']:+.2f}R", border=True, width=130)
            st.metric("손익 합계", f"${stt['total_pnl']:+,.0f}", f"≈ ₩{stt['total_pnl'] * (fx or 0):,.0f}" if fx else None,
                      delta_color="off", delta_arrow="off", border=True, width=160)
            pf = stt["profit_factor"]
            st.metric("손익비(PF)", "∞" if pf == float("inf") else f"{pf:.2f}", border=True, width=130)
            st.metric("최대 연속 손실", f"{stt['max_loss_streak']}회", border=True, width=140)
            st.metric("익절1 / 익절2 도달", f"{stt['tp1_rate']:.0%} / {stt['tp2_rate']:.0%}", border=True, width=170)

        closed_df = J.to_frame([t for t in ts if J.is_closed(t)]).sort_values("진입일")
        st.markdown("##### 누적 손익")
        st.line_chart(closed_df.set_index("진입일")["손익($)"].cumsum(), height=200)

        cmp = J.split_comparison(ts)
        if not cmp.empty and cmp["거래 수"].max() > 0:
            st.markdown("##### 익절 분할 비교 (스윙 거래 기준)")
            st.table(cmp.style.format({"평균 R": "{:+.2f}R", "승률": "{:.0%}"}))
            st.caption("내 거래의 익절1·익절2 도달 여부와 마지막 청산가로 '다른 분할 비율이었다면'을 다시 계산한 값입니다. "
                   "거래가 20~30건 이상 쌓여야 믿을 만합니다.")

        for key in ("국면", "타이밍", "마지막 청산", "손절 방식", "익절 방식", "종목"):
            g = J.group_stats(ts, key)
            if not g.empty:
                with st.expander(f"{key}별 성과"):
                    st.table(g.style.format({"승률": "{:.0%}", "평균 R": "{:+.2f}R", "손익 합계($)": "{:+,.0f}"}))

        st.markdown("##### 연도별 실현 손익 · 양도세 추정")
        y = J.realized_by_year(ts)
        if fx:
            y["원화 환산"] = y["손익($)"] * fx
            y["세금 추정"] = [J.estimate_tax_krw(v, fx) for v in y["손익($)"]]
        st.table(y.style.format({"손익($)": "{:+,.0f}", "원화 환산": "₩{:,.0f}", "세금 추정": "₩{:,.0f}"}))
        st.caption("해외주식 양도소득세: 연 250만 원 공제 후 22% 단순 추정. 실제 세액은 체결일 환율·다른 해외주식 손익과 합산해 계산됩니다.")

    else:  # 백업
        st.download_button("JSON 백업 받기", json.dumps(ts, ensure_ascii=False, indent=1),
                           file_name="levtrack_journal.json", mime="application/json", icon="⬇️")
        st.download_button("CSV (엑셀용) 받기", J.to_frame(ts).to_csv(index=False).encode("utf-8-sig"),
                           file_name="levtrack_journal.csv", mime="text/csv", icon="⬇️")
        up = st.file_uploader("JSON 백업 불러오기 (같은 거래는 덮어씀)", type=["json"])
        if up is not None and st.button("불러오기", type="primary"):
            incoming = json.loads(up.getvalue().decode("utf-8"))
            by_id = {t["id"]: t for t in ts}
            by_id.update({t["id"]: t for t in incoming})
            ss["journal"] = sorted(by_id.values(), key=lambda t: t["entry_date"])
            if save(f"{len(incoming)}건 불러옴"):
                st.rerun()
