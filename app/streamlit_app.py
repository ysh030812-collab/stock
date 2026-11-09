"""LevTrack — 3배 레버리지 ETF 단타 도우미 (태블릿용 Streamlit 앱).

실행:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

import ui_backtest as ubt  # noqa: E402
import ui_dant as ud  # noqa: E402
import ui_journal as uj  # noqa: E402
import ui_settings as us  # noqa: E402
from home_icon import inject_home_icon  # noqa: E402
from levtrack import dant as D  # noqa: E402
from levtrack.config import load_pairs, load_strategy  # noqa: E402
from levtrack.data import MarketData  # noqa: E402
from ui_style import apply_style  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
st.set_page_config(page_title="LevTrack", page_icon=str(STATIC / "icon-192.png"), layout="wide",
                   initial_sidebar_state="collapsed")

APP_VERSION = "2026.11.09 단타 전용"
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
BASE_STRAT = load_strategy()
VIEWS = ["⚡ 단타", "📒 일지", "🧪 백테스트"]

ss = st.session_state
if ss.get("view") not in VIEWS:  # 이전 버전 화면 이름이 남아 있으면 초기화
    ss["view"] = VIEWS[0]


def refresh() -> None:
    st.cache_resource.clear()
    st.cache_data.clear()


# ------------------------------------------------------------------ 상단 바 · 설정 (설정은 일지 저장소에 자동 저장)
with st.container(horizontal=True, vertical_alignment="center", gap="small"):
    st.markdown("### ⚡ LevTrack 단타")
    st.segmented_control("화면", VIEWS, key="view", required=True, label_visibility="collapsed")
    st.space("stretch")
    st.button("새로고침", icon="🔄", on_click=refresh)
    with st.popover(f"설정 · ${ss.get('eq', us.pick('eq', 10_000.0)):,.0f}", icon="⚙️"):
        mode = st.segmented_control("데이터", ["yahoo", "demo"], key="mode", required=True,
                                    default=us.pick("mode", "yahoo", ["yahoo", "demo"]),
                                    format_func={"yahoo": "실시세 (무료)", "demo": "데모"}.get)
        equity = st.number_input("단타 계좌 금액 (USD)", min_value=100.0, value=max(100.0, us.pick("eq", 10_000.0)),
                                 step=500.0, key="eq")
        vt = st.slider("목표 변동성 (%)", 20, 60, min(60, max(20, us.pick("d_vt", 40))), 5, key="d_vt",
                       help="비중 = 목표 변동성 ÷ (3 × 최근 20일 변동성), 최대 100%. 높일수록 한 번에 더 많이 삽니다.")
        use_dip = st.toggle("🟢 눌림 매수 (200일선 위 과매도)", value=us.pick("d_dip", True), key="d_dip")
        use_bounce = st.toggle("🟡 과매도 롱 (200일선 아래, 절반)", value=us.pick("d_bounce", True), key="d_bounce")
        use_short = st.toggle("🔴 반등 숏 (하락장 과열)", value=us.pick("d_short", True), key="d_short")
        gate = st.toggle("자동 점검 (최근 3년 성과가 마이너스면 쉬기)", value=us.pick("d_gate", True), key="d_gate",
                         help="단기 되돌림은 1999년 이전엔 크게 손해였습니다. 효과가 사라지면 자동으로 멈춥니다.")
        us.save_if_changed({"mode": mode, "eq": equity, "d_vt": vt, "d_dip": use_dip, "d_bounce": use_bounce,
                            "d_short": use_short, "d_gate": gate})
        st.caption(us.status_text())
        st.caption(f"LevTrack {APP_VERSION} · Streamlit {st.__version__}")

strat = dict(BASE_STRAT)
strat["dant"] = {"vt": vt / 100, "use_dip": use_dip, "use_bounce": use_bounce, "use_short": use_short, "gate": gate}
P = D.params(strat)


@st.cache_resource(ttl=900, show_spinner="시세 불러오는 중…")
def market(mode: str) -> MarketData:
    return MarketData(PAIRS, mode=mode)


md = market(mode)
fx = md.try_last("USDKRW=X") if mode == "yahoo" else 1400.0
if mode == "demo":
    st.warning("데모 모드: 합성 데이터라 실제 가격이 아닙니다.", icon="🧪")


def _save_trade(t: dict) -> None:
    uj.trades().append(t)
    uj.save(f"{t['ticker']} {t['shares']}주 진입 기록")


def view_dant() -> None:
    ud.cards(md, PAIRS, P)
    st.divider()
    ud.detail(md, PAIRS, P, equity, fx, uj.trades(), _save_trade)
    ud.my_record(uj.trades())


{VIEWS[0]: view_dant,
 VIEWS[1]: lambda: uj.view(md, fx, None, strat),
 VIEWS[2]: lambda: ubt.view(md, mode, PAIRS, strat, equity)}[ss["view"]]()
st.caption(f"LevTrack {APP_VERSION} · Streamlit {st.__version__}")
