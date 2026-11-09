"""손절 · 익절 · 트레일링 레벨 (기초자산 가격 기준).

direction: +1 = 기초 상승 베팅 (TQQQ 매수), -1 = 기초 하락 베팅 (SQQQ 매수)
"""
from __future__ import annotations

import pandas as pd

SL_METHODS = {
    "atr": "ATR 배수",
    "swing": "직전 스윙 고/저점",
    "percent": "고정 %",
    "bollinger": "볼린저밴드",
}


def stop_loss(d: pd.DataFrame, entry: float, direction: int, cfg: dict, method: str | None = None) -> float:
    method = method or cfg["sl_method"]
    last = d.iloc[-1]
    a = float(last["atr"])
    atr_stop = entry - direction * cfg["atr_mult"] * a

    if method == "atr":
        sl = atr_stop
    elif method == "percent":
        sl = entry * (1 - direction * cfg["percent"])
    elif method == "swing":
        n = cfg["swing_lookback"]
        buf = cfg["swing_buffer_atr"] * a
        if direction > 0:
            sl = float(d["Low"].iloc[-n:].min()) - buf
        else:
            sl = float(d["High"].iloc[-n:].max()) + buf
    elif method == "bollinger":
        sl = float(last["bb_lower"] if direction > 0 else last["bb_upper"])
    else:
        raise ValueError(f"알 수 없는 손절 방식: {method}")

    # 손절이 진입가의 반대편(또는 같은 가격)에 있으면 의미가 없으므로 ATR 방식으로 대체
    if (sl - entry) * direction >= 0:
        sl = atr_stop
    return float(sl)


def take_profits(entry: float, stop: float, r_multiples) -> list[float]:
    risk = abs(entry - stop)
    direction = 1 if entry > stop else -1
    return [entry + direction * r * risk for r in r_multiples]


def chandelier(d: pd.DataFrame, direction: int, n: int = 22, mult: float = 3.0) -> float:
    """샹들리에 트레일링 스탑: 롱 = 최고가(n) − m·ATR, 숏 = 최저가(n) + m·ATR."""
    a = float(d["atr"].iloc[-1])
    if direction > 0:
        return float(d["High"].iloc[-n:].max()) - mult * a
    return float(d["Low"].iloc[-n:].min()) + mult * a
