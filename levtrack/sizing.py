"""포지션 사이징 (진입 비율)."""
from __future__ import annotations

import math
from dataclasses import dataclass


def risk_based_shares(equity: float, risk_pct: float, entry: float, stop: float) -> int:
    per_share = abs(entry - stop)
    if per_share <= 0:
        return 0
    return int(math.floor(equity * risk_pct / per_share))


def vol_target_weight(target_vol: float, k: float, sigma_daily: float, periods: int = 252) -> float:
    """목표 연변동성 / (|k| · 기초 연변동성)."""
    ann = abs(k) * sigma_daily * math.sqrt(periods)
    return float("inf") if ann <= 0 else target_vol / ann


def kelly_fraction(win_rate: float, payoff: float) -> float:
    """켈리 비율 w − (1−w)/b (참고용, 음수면 0)."""
    if payoff <= 0:
        return 0.0
    return max(0.0, win_rate - (1 - win_rate) / payoff)


@dataclass
class Sizing:
    shares: int
    weight: float
    notional: float
    risk_usd: float
    w_risk: float
    w_vol: float
    w_cap: float
    regime_mult: float
    vol_factor: float
    binding: str  # 가장 작은 제약

    @property
    def base_weight(self) -> float:
        return min(self.w_risk, self.w_vol, self.w_cap)

    def explain(self) -> str:
        """수량이 정해진 과정을 한 줄로."""
        return (f"리스크 기준 {self.w_risk:.1%} · 변동성 타깃 {self.w_vol:.1%} · 페어 상한 {self.w_cap:.0%} "
                f"→ 가장 작은 값({self.binding}) {self.base_weight:.1%} × 국면 {self.regime_mult:g} "
                f"× 변동성 {self.vol_factor:.2f} = {self.base_weight * self.regime_mult * self.vol_factor:.1%}")

    def bottleneck(self) -> str | None:
        """비중을 가장 크게 깎은 요인 (리스크 기준 대비)."""
        cuts = []
        if self.binding != "리스크 기준" and self.w_risk > 0:
            cuts.append((self.base_weight / self.w_risk, f"{self.binding} 제한 ({self.w_risk:.1%} → {self.base_weight:.1%})"))
        if self.regime_mult < 1:
            cuts.append((self.regime_mult, f"국면 계수 ×{self.regime_mult:g}"))
        if self.vol_factor < 1:
            cuts.append((self.vol_factor, f"변동성 계수 ×{self.vol_factor:.2f}"))
        return min(cuts)[1] if cuts else None

    def as_rows(self) -> list[tuple[str, str]]:
        return [
            ("리스크 기준 비중", f"{self.w_risk:.1%}"),
            ("변동성 타깃 비중", f"{self.w_vol:.1%}"),
            ("페어 상한", f"{self.w_cap:.1%}"),
            ("적용 제약", self.binding),
            ("국면 계수", f"×{self.regime_mult:g}"),
            ("변동성 계수", f"×{self.vol_factor:g}"),
            ("최종 비중", f"{self.weight:.1%}"),
            ("수량", f"{self.shares}주"),
            ("매수 금액", f"${self.notional:,.0f}"),
            ("손절 시 손실", f"${self.risk_usd:,.0f}"),
        ]


def size_position(
    equity: float,
    entry: float,
    stop: float,
    k: float,
    sigma_daily: float,
    risk_pct: float,
    target_vol: float,
    max_pair_weight: float,
    regime_mult: float = 1.0,
    vol_factor: float = 1.0,
) -> Sizing:
    """최종 비중 = min(리스크 기준, 변동성 타깃, 페어 상한) × 국면 계수 × 변동성 계수."""
    w_risk = risk_based_shares(equity, risk_pct, entry, stop) * entry / equity
    w_vol = vol_target_weight(target_vol, k, sigma_daily)
    caps = {"리스크 기준": w_risk, "변동성 타깃": w_vol, "페어 상한": max_pair_weight}
    binding = min(caps, key=caps.get)
    w = caps[binding] * regime_mult * vol_factor
    shares = int(math.floor(w * equity / entry)) if entry > 0 else 0
    notional = shares * entry
    return Sizing(
        shares=shares,
        weight=notional / equity,
        notional=notional,
        risk_usd=shares * abs(entry - stop),
        w_risk=w_risk,
        w_vol=w_vol,
        w_cap=max_pair_weight,
        regime_mult=regime_mult,
        vol_factor=vol_factor,
        binding=binding,
    )


def split_shares(total: int, fractions) -> list[int]:
    """분할 비율대로 정수 수량 배분 (나머지는 마지막 몫)."""
    out = [int(math.floor(total * f)) for f in fractions]
    out.append(total - sum(out))
    return out


def split_entries(prices, fractions, stop: float, total_shares: int) -> dict:
    """분할 매수 계획: 단계별 수량, 평균단가, 전체 손절 손실."""
    if abs(sum(fractions) - 1) > 1e-6:
        raise ValueError("분할 비율의 합은 1 이어야 합니다")
    qty = split_shares(total_shares, fractions[:-1])
    filled = [(p, q) for p, q in zip(prices, qty) if q > 0]
    n = sum(q for _, q in filled)
    avg = sum(p * q for p, q in filled) / n if n else 0.0
    return {
        "tranches": [{"price": p, "shares": q} for p, q in zip(prices, qty)],
        "avg_price": avg,
        "shares": n,
        "risk_usd": n * abs(avg - stop),
    }
