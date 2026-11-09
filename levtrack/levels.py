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
    "support": "지지대 (피보·매물대·스윙)",
}

TP_METHODS = {
    "r": "R 배수",
    "fib": "피보나치 (되돌림·확장)",
    "resistance": "저항대 (피보·매물대·스윙·52주)",
}


def stop_loss(d: pd.DataFrame, entry: float, direction: int, cfg: dict, method: str | None = None,
              levels: pd.DataFrame | None = None) -> float:
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
    elif method == "support":
        # 진입가에서 최소 support_min_atr 이상 떨어진 가장 가까운 지지(저항) 레벨 너머 + 버퍼
        sl = atr_stop
        if levels is not None and not levels.empty:
            gap = cfg.get("support_min_atr", 0.5) * a
            buf = cfg["swing_buffer_atr"] * a
            p = levels["price"]
            cand = p[p < entry - gap] if direction > 0 else p[p > entry + gap]
            if not cand.empty:
                sl = float(cand.max() - buf) if direction > 0 else float(cand.min() + buf)
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


def take_profit_levels(entry: float, stop: float, cfg: dict, method: str = "r",
                       levels: pd.DataFrame | None = None) -> list[tuple[float, str]]:
    """익절 레벨 (가격, 근거). 구조 기반 방식은 최소 tp_min_r 이상 떨어진 레벨부터 가까운 순으로 고르고,
    모자라면 R 배수로 채운다."""
    n = len(cfg["r_multiples"])
    risk = abs(entry - stop)
    direction = 1 if entry > stop else -1
    chosen: list[tuple[float, str]] = []
    if method in ("fib", "resistance") and levels is not None and not levels.empty:
        lv = levels if method == "resistance" else levels[levels["source"] == "fib"]
        min_dist = cfg.get("tp_min_r", 1.0) * risk
        far = lv[(lv["price"] - entry) * direction >= min_dist].copy()
        far["dist"] = (far["price"] - entry) * direction
        last_p = None
        for _, r in far.sort_values("dist").iterrows():
            if last_p is not None and abs(r["price"] - last_p) < 0.25 * risk:  # 너무 붙은 레벨은 하나로
                continue
            chosen.append((float(r["price"]), str(r["label"])))
            last_p = r["price"]
            if len(chosen) == n:
                break
    for r in cfg["r_multiples"]:
        if len(chosen) >= n:
            break
        p = entry + direction * r * risk
        if not chosen or (p - chosen[-1][0]) * direction > 0:
            chosen.append((p, f"{r:g}R"))
    while len(chosen) < n:  # 구조 레벨이 R 배수보다 멀리 있을 때
        base = chosen[-1][0]
        chosen.append((base + direction * risk, "+1R"))
    return chosen


def chandelier(d: pd.DataFrame, direction: int, n: int = 22, mult: float = 3.0) -> float:
    """샹들리에 트레일링 스탑: 롱 = 최고가(n) − m·ATR, 숏 = 최저가(n) + m·ATR."""
    a = float(d["atr"].iloc[-1])
    if direction > 0:
        return float(d["High"].iloc[-n:].max()) - mult * a
    return float(d["Low"].iloc[-n:].min()) + mult * a
