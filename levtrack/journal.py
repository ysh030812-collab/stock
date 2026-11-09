"""매매일지: 진입·청산 기록, 성과(R) 계산, 통계, 익절 분할 비교.

거래 1건은 JSON 직렬화 가능한 dict 이다.
"""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime

import pandas as pd

from .leverage import convert_price

EXIT_REASONS = ["익절1", "익절2", "트레일링", "손절", "신호 청산", "수동"]
TP_SPLITS = {
    "균형 33/33/34": [0.33, 0.33],
    "빠른 확보 50/30/20": [0.50, 0.30],
    "추세 추종 25/25/50": [0.25, 0.25],
    "1차 전량 100/0/0": [1.0, 0.0],
}


def _today() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    except Exception:  # noqa: BLE001
        return date.today().isoformat()


def new_trade(plan, *, pair_id: str, fill_price: float, shares: int, entry_date: str | None = None,
              note: str = "", fee: float = 0.0, fx: float | None = None, signal=None, timing=None,
              timeframe: str = "1d") -> dict:
    """트레이드 플랜과 실제 체결로 일지 항목 생성."""
    snap = {}
    if signal is not None:
        snap = {"regime": signal.regime, "bull": signal.bull, "bear": signal.bear, "vol_factor": signal.vol_factor}
    if timing is not None:
        snap["timing"] = timing.state
    return {
        "id": uuid.uuid4().hex[:10],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pair_id": pair_id,
        "signal": plan.signal_ticker,
        "ticker": plan.letf_ticker,
        "k": plan.k,
        "direction": plan.direction,
        "timeframe": timeframe,
        "entry_date": entry_date or _today(),
        "entry_price": float(fill_price),
        "shares": int(shares),
        "entry_fee": float(fee),
        "fx": fx,
        "plan": {
            "u_entry": plan.u_entry, "u_stop": plan.u_stop, "u_tps": list(plan.u_tps),
            "l_entry": plan.l_entry, "l_stop": plan.l_stop, "l_tps": list(plan.l_tps), "l_trail": plan.l_trail,
            "sl_method": plan.sl_method, "tp_method": plan.tp_method,
            "tp_fractions": [plan.exit_shares[0] / max(1, plan.sizing.shares),
                             plan.exit_shares[1] / max(1, plan.sizing.shares)],
            "planned_shares": plan.sizing.shares,
        },
        "snapshot": snap,
        "note": note,
        "exits": [],
    }


def new_trend_trade(*, pair: dict, ticker: str, k: float, mode: str, fill_price: float, shares: int, u_now: float,
                    ref_stop_u: float, exit_rule: str, entry_date: str | None = None, note: str = "", fee: float = 0.0,
                    fx: float | None = None, weight: float | None = None) -> dict:
    """추세 롱숏 전략 거래. 익절·트레일 대신 '신호 청산'(exit_rule)으로 관리.
    ref_stop_u: R 계산용 참고 손절(기초 가격)."""
    direction = 1 if k > 0 else -1
    l_stop = max(0.0, fill_price * (1 + k * (ref_stop_u / u_now - 1)))
    return {
        "id": uuid.uuid4().hex[:10],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": "trend",
        "pair_id": pair["id"], "signal": pair["signal"], "ticker": ticker, "k": k, "direction": direction,
        "timeframe": "1d", "entry_date": entry_date or _today(), "entry_price": float(fill_price),
        "shares": int(shares), "entry_fee": float(fee), "fx": fx,
        "plan": {"u_entry": u_now, "u_stop": ref_stop_u, "u_tps": [], "l_entry": fill_price, "l_stop": l_stop,
                 "l_tps": [], "l_trail": None, "sl_method": "신호", "tp_method": "신호", "tp_fractions": [0.0, 0.0],
                 "planned_shares": int(shares), "exit_rule": exit_rule, "weight": weight},
        "snapshot": {"regime": mode, "timing": mode},
        "note": note, "exits": [],
    }


def add_exit(trade: dict, *, price: float, shares: int, reason: str, exit_date: str | None = None,
             note: str = "", fee: float = 0.0) -> dict:
    rem = remaining_shares(trade)
    if shares <= 0 or shares > rem:
        raise ValueError(f"청산 수량은 1 ~ {rem}주 사이여야 합니다")
    if reason not in EXIT_REASONS:
        raise ValueError(f"청산 사유는 {EXIT_REASONS} 중 하나")
    trade["exits"].append({"date": exit_date or _today(), "price": float(price), "shares": int(shares),
                           "reason": reason, "note": note, "fee": float(fee)})
    return trade


def today_levels(trade: dict, *, u_now: float, l_now: float, chandelier_u: float | None = None) -> tuple[dict, bool]:
    """보유 중 거래의 '오늘' 주문 가격.

    - 레버리지 ETF 는 매일 리셋되므로, 계획한 기초 가격(손절·익절)을 오늘의 기초가·LETF 가로 다시 환산한다.
    - 트레일링 스탑(기초 기준 샹들리에)은 유리한 쪽으로만 움직이게 고정(래칫)해 trade['trail_u'] 에 저장한다.
    반환: (레벨 dict, 트레일이 갱신됐는지)
    """
    d, k, p = trade["direction"], trade["k"], trade["plan"]
    changed = False
    if chandelier_u is not None and not math.isnan(chandelier_u):
        prev = trade.get("trail_u")
        if prev is None or (chandelier_u - prev) * d > 0:
            trade["trail_u"] = float(chandelier_u)
            trade["trail_updated"] = _today()
            changed = True
    trail_u = trade.get("trail_u")
    stop_u = p["u_stop"]
    trail_active = trail_u is not None and (trail_u - stop_u) * d > 0
    eff_u = trail_u if trail_active else stop_u

    def conv(u):
        return convert_price(u_now, u, l_now, k)

    hit = {e["reason"] for e in trade["exits"]}
    tps = [(f"익절{i}", u, conv(u)) for i, u in enumerate(p["u_tps"][:2], 1) if f"익절{i}" not in hit]
    return {
        "u_now": u_now, "l_now": l_now,
        "stop_u": stop_u, "l_stop": conv(stop_u),
        "trail_u": trail_u, "l_trail": conv(trail_u) if trail_u is not None else None,
        "trail_active": trail_active, "eff_u": eff_u, "l_eff": conv(eff_u),
        "tps": tps,
    }, changed


def remaining_shares(trade: dict) -> int:
    return trade["shares"] - sum(e["shares"] for e in trade["exits"])


def is_closed(trade: dict) -> bool:
    return remaining_shares(trade) == 0


def risk_per_share(trade: dict) -> float:
    r = trade["entry_price"] - trade["plan"]["l_stop"]
    return r if r > 0 else float("nan")


def metrics(trade: dict, current_price: float | None = None) -> dict:
    """실현/평가 손익, R, 보유일."""
    entry, n = trade["entry_price"], trade["shares"]
    fees = trade.get("entry_fee", 0.0) + sum(e.get("fee", 0.0) for e in trade["exits"])
    realized = sum((e["price"] - entry) * e["shares"] for e in trade["exits"]) - fees
    rem = remaining_shares(trade)
    unreal = (current_price - entry) * rem if (current_price is not None and rem) else 0.0
    risk = risk_per_share(trade) * n
    last_date = trade["exits"][-1]["date"] if trade["exits"] else None
    end = last_date if rem == 0 and last_date else _today()
    days = (pd.Timestamp(end) - pd.Timestamp(trade["entry_date"])).days
    exited = sum(e["shares"] for e in trade["exits"])
    return {
        "remaining": rem,
        "closed": rem == 0,
        "realized": realized,
        "unrealized": unreal,
        "pnl": realized + unreal,
        "risk_usd": risk,
        "r": (realized + unreal) / risk if risk and not math.isnan(risk) else float("nan"),
        "return_pct": (realized + unreal) / (entry * n) if entry * n else 0.0,
        "avg_exit": sum(e["price"] * e["shares"] for e in trade["exits"]) / exited if exited else None,
        "days": days,
        "final_reason": trade["exits"][-1]["reason"] if trade["exits"] else None,
        "tp1_hit": any(e["reason"] == "익절1" for e in trade["exits"]),
        "tp2_hit": any(e["reason"] == "익절2" for e in trade["exits"]),
    }


def to_frame(trades: list[dict], prices: dict | None = None) -> pd.DataFrame:
    rows = []
    for t in trades:
        m = metrics(t, (prices or {}).get(t["ticker"]))
        rows.append({
            "id": t["id"], "진입일": t["entry_date"], "종목": t["ticker"],
            "방향": "롱" if t["direction"] > 0 else "숏", "수량": t["shares"], "잔량": m["remaining"],
            "진입가": t["entry_price"], "평균 청산가": m["avg_exit"], "손익($)": m["pnl"], "R": m["r"],
            "수익률": m["return_pct"], "보유일": m["days"], "상태": "청산" if m["closed"] else "보유",
            "마지막 청산": m["final_reason"], "국면": t.get("snapshot", {}).get("regime"),
            "타이밍": t.get("snapshot", {}).get("timing"), "손절 방식": t["plan"]["sl_method"],
            "익절 방식": t["plan"]["tp_method"], "메모": t.get("note", ""),
        })
    return pd.DataFrame(rows)


def stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if is_closed(t)]
    ms = [metrics(t) for t in closed]
    rs = [m["r"] for m in ms if not math.isnan(m["r"])]
    pnl = [m["pnl"] for m in ms]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    streak = cur = 0
    for p in pnl:
        cur = cur + 1 if p <= 0 else 0
        streak = max(streak, cur)
    return {
        "n": len(closed),
        "win_rate": len(wins) / len(closed) if closed else float("nan"),
        "avg_r": sum(rs) / len(rs) if rs else float("nan"),
        "total_pnl": sum(pnl),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float("inf") if wins else float("nan"),
        "max_loss_streak": streak,
        "tp1_rate": sum(m["tp1_hit"] for m in ms) / len(ms) if ms else float("nan"),
        "tp2_rate": sum(m["tp2_hit"] for m in ms) / len(ms) if ms else float("nan"),
    }


def group_stats(trades: list[dict], key: str) -> pd.DataFrame:
    """국면·타이밍·손절 방식 등 기준별 성과."""
    df = to_frame([t for t in trades if is_closed(t)])
    if df.empty or key not in df:
        return pd.DataFrame()
    g = df.groupby(df[key].fillna("-"))
    return pd.DataFrame({"거래 수": g.size(), "승률": g["손익($)"].apply(lambda s: (s > 0).mean()),
                         "평균 R": g["R"].mean(), "손익 합계($)": g["손익($)"].sum()})


def split_comparison(trades: list[dict], splits: dict[str, list[float]] | None = None) -> pd.DataFrame:
    """내 청산 기록으로 '익절 분할 비율을 다르게 했다면' 평균 R 을 다시 계산.

    각 거래에서 익절1·익절2 도달 여부와 마지막 잔량 청산가(트레일링/손절/수동)를 쓰고,
    익절 가격은 계획한 LETF 익절가를 쓴다. 익절2 를 찍었으면 익절1 도 찍은 것으로 본다.
    """
    splits = splits or TP_SPLITS
    rows = []
    sample = []
    for t in trades:
        if not is_closed(t) or len(t["plan"].get("l_tps") or []) < 2:  # 추세 롱숏 거래는 익절 분할이 없음
            continue
        risk = risk_per_share(t)
        if math.isnan(risk):
            continue
        m = metrics(t)
        tail = [e for e in t["exits"] if e["reason"] not in ("익절1", "익절2")]
        final = tail[-1]["price"] if tail else t["exits"][-1]["price"]
        tps = t["plan"]["l_tps"]
        sample.append((t["entry_price"], risk, m["tp1_hit"] or m["tp2_hit"], m["tp2_hit"], tps[0], tps[1], final))
    for name, (f1, f2) in splits.items():
        rs = []
        for entry, risk, h1, h2, tp1, tp2, final in sample:
            f3 = 1 - f1 - f2
            if h2:
                px = f1 * tp1 + f2 * tp2 + f3 * final
            elif h1:
                px = f1 * tp1 + (f2 + f3) * final
            else:
                px = final
            rs.append((px - entry) / risk)
        rows.append({"분할": name, "거래 수": len(rs), "평균 R": sum(rs) / len(rs) if rs else float("nan"),
                     "승률": sum(r > 0 for r in rs) / len(rs) if rs else float("nan")})
    return pd.DataFrame(rows).set_index("분할")


def realized_by_year(trades: list[dict]) -> pd.DataFrame:
    """연도별 실현 손익 (청산일 기준, USD)."""
    rows = []
    for t in trades:
        entry = t["entry_price"]
        for e in t["exits"]:
            rows.append({"연도": e["date"][:4], "손익($)": (e["price"] - entry) * e["shares"] - e.get("fee", 0.0)})
        if t["exits"] and t.get("entry_fee"):
            rows.append({"연도": t["exits"][0]["date"][:4], "손익($)": -t["entry_fee"]})
    if not rows:
        return pd.DataFrame(columns=["손익($)"])
    return pd.DataFrame(rows).groupby("연도")[["손익($)"]].sum()


def estimate_tax_krw(realized_usd: float, fx: float, deduction: float = 2_500_000, rate: float = 0.22) -> float:
    """해외주식 양도소득세 추정 (연 250만 원 공제 후 22%). 실제 세액은 체결일 환율 등으로 달라짐."""
    return max(0.0, realized_usd * fx - deduction) * rate
