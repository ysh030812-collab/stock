"""설정 영구 저장: 매매일지와 같은 저장소(GitHub journal-data 브랜치의 journal/settings.json)에 보관.

앱을 다시 열거나 업데이트돼도, 다른 기기에서 열어도 같은 설정으로 시작한다.
세션 시작 때 한 번 읽어 위젯 기본값으로 쓰고, 값이 바뀌면 저장한다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import streamlit as st

from levtrack.journal_store import clean_token, make_store

LOCAL_PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"
REMOTE_PATH = "journal/settings.json"
ss = st.session_state


def _secrets() -> dict:
    try:
        return dict(st.secrets.get("journal", {}))
    except Exception:  # noqa: BLE001
        return {}


@st.cache_resource
def _store(token_key: str):
    return make_store(_secrets(), LOCAL_PATH, path=REMOTE_PATH)


def store():
    tok = clean_token(_secrets().get("github_token"))
    return _store(hashlib.sha256(tok.encode()).hexdigest()[:16] if tok else "")


def initial() -> dict:
    """세션 시작 때 저장된 설정을 한 번 읽는다 (이후 같은 세션에서는 고정 → 위젯이 흔들리지 않음)."""
    if "settings_init" not in ss:
        try:
            data = store().load()
            data = data if isinstance(data, dict) else {}
            ss["settings_err"] = None
        except Exception as e:  # noqa: BLE001
            data, ss["settings_err"] = {}, str(e)
        ss["settings_init"] = data
        ss["settings_saved"] = dict(data)
    return ss["settings_init"]


def save_if_changed(current: dict) -> None:
    merged = {**ss.get("settings_saved", {}), **current}
    if merged == ss.get("settings_saved"):
        return
    try:
        store().save(merged)
        ss["settings_saved"] = merged
        ss["settings_err"] = None
    except Exception as e:  # noqa: BLE001
        ss["settings_err"] = str(e)


def pick(key: str, default, choices=None):
    """저장값을 위젯 기본값으로 (형식·범위가 맞지 않으면 기본값)."""
    v = initial().get(key, default)
    if choices is not None:
        return v if v in choices else default
    if isinstance(default, bool):
        return v if isinstance(v, bool) else default
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


def status_text() -> str:
    if ss.get("settings_err"):
        return f"⚠️ 설정 저장/불러오기 실패: {ss['settings_err']}"
    s = store()
    where = "GitHub (일지와 같은 곳)" if s.kind == "github" else "서버 파일 (Cloud 재시작 시 사라질 수 있음)"
    return f"설정은 바꿀 때마다 자동 저장됩니다 · 저장 위치: {where}"
