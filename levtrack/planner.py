"""트레이드 플랜: 기초 레벨 → LETF 레벨 → 수량 → 메리츠증권 수동 주문표."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import leverage as lev
from .levels import chandelier, stop_loss, take_profits
from .sizing import Sizing, size_position, split_shares


@dataclass
class TradePlan:
    signal_ticker: str
    letf_ticker: str
    k: float
    direction: int
    sl_method: str
    u_entry: float
    u_stop: float
    u_tps: list[float]
    u_trail: float
    l_entry: float
    l_stop: float
    l_tps: list[float]
    l_trail: float
    sizing: Sizing
    exit_shares: list[int]
    r_multiples: list[float]
    sigma_daily: float
    gap_pct: float
    gap_u: float
    gap_l: float
    gap_loss: float
    expense: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def gap_r(self) -> float:
        return self.gap_loss / self.sizing.risk_usd if self.sizing.risk_usd else float("nan")

    def levels_table(self) -> pd.DataFrame:
        rows = [("진입", self.u_entry, self.l_entry, 0.0)]
        rows.append(("손절", self.u_stop, self.l_stop, -1.0))
        for i, (u, l, r) in enumerate(zip(self.u_tps, self.l_tps, self.r_multiples), 1):
            rows.append((f"익절{i}", u, l, r))
        rows.append(("트레일링", self.u_trail, self.l_trail, None))
        df = pd.DataFrame(rows, columns=["구분", self.signal_ticker, self.letf_ticker, "R"])
        df[f"{self.signal_ticker} %"] = df[self.signal_ticker] / self.u_entry - 1
        df[f"{self.letf_ticker} %"] = df[self.letf_ticker] / self.l_entry - 1
        df["손익($)"] = (df[self.letf_ticker] - self.l_entry) * self.sizing.shares
        return df.set_index("구분")

    def projection(self, target: str = "손절", days=(1, 5, 10, 20)) -> pd.DataFrame:
        u_target = self.u_stop if target == "손절" else self.u_tps[0]
        return lev.projection_table(
            self.u_entry, u_target, self.l_entry, self.k, self.sigma_daily, days, expense=self.expense
        )

    def order_sheet(self) -> pd.DataFrame:
        """메리츠증권 MTS/HTS 에 직접 입력할 주문표 (API 연동 없음)."""
        t = self.letf_ticker
        q1, q2, q_rest = self.exit_shares
        rows = [
            ("1", "매수", t, self.sizing.shares, f"지정가 {self.l_entry:.2f}", "진입"),
            ("2", "매도", t, self.sizing.shares, f"감시가 {self.l_stop:.2f}",
             f"손절 (기초 {self.signal_ticker} {self.u_stop:.2f} 이탈 시 알림 기준)"),
            ("3", "매도", t, q1, f"지정가 {self.l_tps[0]:.2f}", f"익절1 ({self.r_multiples[0]:g}R)"),
            ("4", "매도", t, q2, f"지정가 {self.l_tps[1]:.2f}", f"익절2 ({self.r_multiples[1]:g}R)"),
            ("5", "매도", t, q_rest, f"감시가 {self.l_trail:.2f}", "잔량 트레일링 — 매일 갱신"),
        ]
        return pd.DataFrame(rows, columns=["#", "구분", "종목", "수량", "가격", "메모"]).set_index("#")


def sigma_daily_from(d: pd.DataFrame, window: int = 20) -> float:
    """일봉 또는 장중봉에서 기초 일간 변동성 추정."""
    c = lev.daily_close(d["Close"])
    s = c.pct_change().dropna().iloc[-window:].std()
    return float(s) if pd.notna(s) else 0.0


def build_plan(
    d: pd.DataFrame,
    signal_ticker: str,
    letf_ticker: str,
    k: float,
    l_price: float,
    equity: float,
    strat: dict,
    regime_mult: float = 1.0,
    vol_factor: float = 1.0,
    u_entry: float | None = None,
    sl_method: str | None = None,
    sigma_daily: float | None = None,
    expense: float = 0.0,
    u_stop_override: float | None = None,
) -> TradePlan:
    """d: 지표가 붙은 기초 OHLC (일봉 또는 시간봉). l_price: LETF 현재가."""
    lv, sz = strat["levels"], strat["sizing"]
    direction = 1 if k > 0 else -1
    u_now = float(d["Close"].iloc[-1])
    u_entry = u_now if u_entry is None else float(u_entry)
    # 현재가가 아닌 가격에 진입할 계획이면 LETF 진입가도 환산
    l_entry = lev.convert_price(u_now, u_entry, l_price, k)
    method = sl_method or lv["sl_method"]
    u_stop = float(u_stop_override) if u_stop_override else stop_loss(d, u_entry, direction, lv, method)
    u_tps = take_profits(u_entry, u_stop, lv["r_multiples"])
    u_trail = chandelier(d, direction, lv["chandelier_n"], lv["chandelier_mult"])

    def conv(u):
        return lev.convert_price(u_entry, u, l_entry, k)

    l_stop = conv(u_stop)
    sigma = sigma_daily if sigma_daily is not None else sigma_daily_from(d)
    sizing = size_position(
        equity, l_entry, l_stop, k, sigma, sz["risk_pct"], sz["target_vol"], sz["max_pair_weight"],
        regime_mult, vol_factor,
    )
    gap_u = u_entry * (1 - direction * sz["gap_pct"])
    gap_l = conv(gap_u)
    notes = []
    if (u_trail - u_entry) * direction > 0:
        notes.append("트레일링 스탑이 진입가보다 유리한 쪽에 있어 아직 의미가 없습니다 (가격이 움직인 뒤 적용).")
    if (gap_u - u_stop) * direction > 0:
        notes.append("갭 시나리오 폭이 손절 폭보다 작습니다.")
    return TradePlan(
        signal_ticker=signal_ticker,
        letf_ticker=letf_ticker,
        k=k,
        direction=direction,
        sl_method=method,
        u_entry=u_entry,
        u_stop=u_stop,
        u_tps=u_tps,
        u_trail=u_trail,
        l_entry=l_entry,
        l_stop=l_stop,
        l_tps=[conv(u) for u in u_tps],
        l_trail=conv(u_trail),
        sizing=sizing,
        exit_shares=split_shares(sizing.shares, lv["tp_fractions"]),
        r_multiples=list(lv["r_multiples"]),
        sigma_daily=sigma,
        gap_pct=sz["gap_pct"],
        gap_u=gap_u,
        gap_l=gap_l,
        gap_loss=sizing.shares * (l_entry - gap_l),
        expense=expense,
        notes=notes,
    )
