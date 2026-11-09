"""다중 시간대 진입 타이밍: 일봉 국면이 허용한 방향으로, 하위 봉(시간봉)에서 진입 시점을 판단."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .patterns import PATTERNS, candle_patterns, recent_divergences

BULL_TRIGGERS = ("bull_engulfing", "hammer", "morning_star")
BEAR_TRIGGERS = ("bear_engulfing", "shooting_star", "evening_star")


@dataclass
class Timing:
    state: str
    ok: bool
    direction: int
    reasons: list = field(default_factory=list)
    trend_agree: bool = False
    wait_price: float | None = None  # 기다릴 눌림 가격 (기초, 시간봉 EMA20). 대기·과열일 때만

    @property
    def blocked(self) -> bool:
        return self.state.startswith("🚫")

    @property
    def label(self) -> str:
        return self.state


def entry_timing(h: pd.DataFrame, direction: int, allowed: bool, cfg: dict, div_cfg: dict,
                 pats: pd.DataFrame | None = None, divs: list | None = None) -> Timing:
    """h: 지표가 붙은 하위 봉 (시간봉 권장). direction: +1 롱 / -1 숏. allowed: 일봉 국면 계수 > 0.

    상태:
      🚫 진입 금지      — 일봉 국면이 이 방향을 허용하지 않음
      ⚠️ 과열          — RSI 극단 또는 EMA20 에서 ATR×n 이상 이탈 (추격 금지)
      ✅ 돌파 진입      — 직전 20봉 고가(저가) 돌파 + 거래량 증가
      ✅ 눌림 반등 진입  — 최근 눌림(EMA20 터치 또는 RSI 되돌림) 후 반전 트리거
      ⏳ 눌림 대기      — 시간봉 추세는 일치, 트리거 없음
      ⏳ 시간봉 추세 불일치
    """
    d = direction
    last, prev = h.iloc[-1], h.iloc[-2]
    reasons: list[str] = []
    side = "롱" if d > 0 else "숏"

    trend_agree = bool((last["ema_fast"] > last["ema_slow"]) if d > 0 else (last["ema_fast"] < last["ema_slow"]))
    reasons.append(f"시간봉 EMA20/50 {'일치' if trend_agree else '불일치'}")
    if not allowed:
        return Timing("🚫 진입 금지", False, d, [f"일봉 국면이 {side}을 허용하지 않음"] + reasons, trend_agree)

    rsi_now = float(last["rsi"])
    stretch = (float(last["Close"]) - float(last["ema_fast"])) * d / float(last["atr"])
    overheated = (rsi_now > cfg["overheat_rsi"]) if d > 0 else (rsi_now < 100 - cfg["overheat_rsi"])
    if overheated or stretch > cfg["overheat_atr"]:
        reasons.append(f"RSI {rsi_now:.0f}, EMA20 대비 {stretch:.1f} ATR 이탈")
        return Timing("⚠️ 과열 — 추격 금지", False, d, reasons, trend_agree, float(last["ema_fast"]))

    vol_ratio = last.get("vol_ratio")
    vol_ok = vol_ratio is None or pd.isna(vol_ratio) or vol_ratio >= cfg["breakout_vol"]
    level = last["dc_high"] if d > 0 else last["dc_low"]
    if pd.notna(level) and (last["Close"] - level) * d > 0 and vol_ok:
        vr = "" if vol_ratio is None or pd.isna(vol_ratio) else f", 거래량 {vol_ratio:.1f}배"
        reasons.append(f"직전 20봉 {'고가' if d > 0 else '저가'} {level:,.2f} 돌파{vr}")
        return Timing("✅ 돌파 진입", True, d, reasons, trend_agree)

    n = cfg["pullback_lookback"]
    win = h.iloc[-n:]
    if d > 0:
        touched = (win["Low"] <= win["ema_fast"]).any() or (win["rsi"] < cfg["rsi_pullback"]).any()
    else:
        touched = (win["High"] >= win["ema_fast"]).any() or (win["rsi"] > 100 - cfg["rsi_pullback"]).any()

    triggers = []
    # pats/divs 를 미리 계산해 넘기면(백테스트) 재계산하지 않는다
    pats = candle_patterns(h.iloc[-30:]).iloc[-2:] if pats is None else pats.iloc[-2:]
    for name in BULL_TRIGGERS if d > 0 else BEAR_TRIGGERS:
        if pats[name].any():
            triggers.append(PATTERNS[name][0])
    if (prev["macd_hist"] <= 0 < last["macd_hist"]) if d > 0 else (prev["macd_hist"] >= 0 > last["macd_hist"]):
        triggers.append("MACD 히스토그램 전환")
    if (prev["Close"] - prev["ema_fast"]) * d <= 0 < (last["Close"] - last["ema_fast"]) * d:
        triggers.append("EMA20 회복" if d > 0 else "EMA20 이탈")
    divs = [dv for dv in (recent_divergences(h, div_cfg) if divs is None else divs) if dv["bias"] == d]
    if divs:
        triggers.append(f"{'강세' if d > 0 else '약세'} 다이버전스({divs[0]['osc'].upper()})")

    if touched and triggers and (trend_agree or divs):
        reasons.append("최근 눌림 확인")
        reasons += triggers
        return Timing("✅ 눌림 반등 진입", True, d, reasons, trend_agree)
    if trend_agree:
        reasons.append("눌림 확인됨, 반전 트리거 대기" if touched else "EMA20 부근 눌림 대기")
        reasons += triggers
        ema = float(last["ema_fast"])
        wait = ema if (float(last["Close"]) - ema) * d > 0 else None  # 아직 EMA20 위(숏은 아래)면 거기까지 기다림
        return Timing("⏳ 눌림 대기", False, d, reasons, trend_agree, wait)
    reasons += triggers
    return Timing("⏳ 시간봉 추세 불일치", False, d, reasons, trend_agree)
