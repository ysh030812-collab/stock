"""태블릿(터치) 친화 스타일."""
from __future__ import annotations

import streamlit as st

CSS = """
<style>
/* 화면 여백 축소 · 상단 헤더 숨김 (사이드바를 쓰지 않음) */
.block-container { padding-top: 0.8rem !important; padding-left: 1rem !important;
                   padding-right: 1rem !important; max-width: 1400px; }
header[data-testid="stHeader"] { display: none; }
[data-testid="stSidebarCollapsedControl"] { display: none; }

/* 아이콘 주입용 iframe 은 공간 차지 없이 숨김 */
.st-key-lt_icon { position: absolute !important; width: 1px !important; height: 0 !important;
                  overflow: hidden !important; opacity: 0; pointer-events: none; }

/* 터치 타깃 44px 이상 */
.stButton button, [data-testid="stPopover"] button, [data-testid="stDownloadButton"] button {
  min-height: 48px; padding: 0 18px; font-size: 1.02rem; }
[data-testid="stButtonGroup"] button { min-height: 44px; padding: 0 16px; font-size: 1rem; }
[data-testid="stExpander"] summary { min-height: 48px; font-size: 1.05rem; }
[data-baseweb="select"] > div { min-height: 46px; }
[data-testid="stNumberInput"] button { min-width: 44px; }
[data-testid="stCheckbox"] label, [data-testid="stToggle"] label { min-height: 40px; align-items: center; }

/* iOS 입력 시 자동 확대 방지 (16px 이상) */
input, textarea, select { font-size: 16px !important; }

/* 표는 크게 */
[data-testid="stTable"] table { font-size: 1.02rem; }
[data-testid="stTable"] td, [data-testid="stTable"] th { padding: 10px 12px; }

/* 지표 숫자 */
[data-testid="stMetricValue"] { font-size: 1.7rem; }

/* 탭 하이라이트 제거 */
* { -webkit-tap-highlight-color: transparent; }

/* 상태 배지 */
.lt-badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-weight: 600;
            font-size: 0.95rem; margin-right: 6px; }
.lt-bull { background: rgba(34,197,94,.15); color: #15803d; }
.lt-bear { background: rgba(239,68,68,.15); color: #b91c1c; }
.lt-neutral { background: rgba(234,179,8,.18); color: #a16207; }
.lt-sub { color: #6b7280; font-size: 0.92rem; line-height: 1.5; }
@media (prefers-color-scheme: dark) {
  .lt-bull { color: #4ade80; } .lt-bear { color: #f87171; } .lt-neutral { color: #facc15; }
  .lt-sub { color: #9ca3af; }
}
</style>
"""


def apply_style() -> None:
    st.html(CSS)
