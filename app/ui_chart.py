"""플래너 차트 (Plotly). 기본은 터치 스크롤을 방해하지 않도록 확대·이동을 끈다."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from levtrack.patterns import PATTERNS, candle_patterns, find_divergences
from levtrack.structure import fibonacci, volume_profile

PANELS = ["거래량·OBV", "RSI", "MACD", "상대강도"]
OVERLAYS = ["피보나치", "매물대", "패턴·다이버전스"]
PERIODS = {
    "1d": {"3개월": 63, "6개월": 126, "1년": 252},
    "1h": {"1주": 35, "2주": 70, "1개월": 147},
}

GREEN, RED, BLUE, ORANGE = "#26a69a", "#ef5350", "#1f77b4", "#ff7f0e"


def build_chart(u_full: pd.DataFrame, l_df: pd.DataFrame, plan, pair: dict, ins: dict, tf: str, n_bars: int,
                overlays: list[str], panels: list[str], interactive: bool, strat: dict) -> tuple[go.Figure, dict]:
    u_df = u_full.iloc[-n_bars:]
    # LETF 를 기초 날짜(봉)에 정확히 맞춤 → 위아래 패널의 같은 x 위치 = 같은 시점
    l_df = l_df.reindex(u_df.index)
    has_vol = "Volume" in u_df and u_df["Volume"].sum() > 0
    panels = [p for p in panels if (p != "상대강도" or "rs" in u_df) and (p != "거래량·OBV" or has_vol)]
    show_vp = "매물대" in overlays
    vp = volume_profile(u_full, strat["structure"]["vp_bars"], strat["structure"]["vp_bins"],
                        strat["structure"]["value_area"]) if show_vp else None

    heights = [380, 250] + [130] * len(panels)
    n_rows = len(heights)
    # 모든 패널을 한 열로 → x축 폭이 같아 날짜가 세로로 정확히 맞는다 (매물대는 가격 패널 위에 겹쳐 그림)
    specs = [[{}]] + [[{"secondary_y": p == "거래량·OBV"}] for p in ["LETF"] + panels]
    titles = [pair["signal"] + (" · 매물대" if vp else ""), f"{ins['ticker']} (기초 레벨 환산)"]
    titles += [f"상대강도 {pair['signal']}/{pair.get('benchmark', '')}" if p == "상대강도" else p for p in panels]
    fig = make_subplots(rows=n_rows, cols=1, row_heights=heights, specs=specs, shared_xaxes=True,
                        vertical_spacing=0.035, subplot_titles=titles)

    for r, df_, name in ((1, u_df, pair["signal"]), (2, l_df, ins["ticker"])):
        fig.add_trace(go.Candlestick(x=df_.index, open=df_.Open, high=df_.High, low=df_.Low, close=df_.Close,
                                     name=name, showlegend=False, increasing_line_color=GREEN,
                                     decreasing_line_color=RED), row=r, col=1)
    for col, color, nm in (("sma_fast", "#f2a900", "SMA50"), ("sma_slow", "#7b61ff", "SMA200"),
                           ("ema_fast", GREEN, "EMA20")):
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df[col], name=nm, line=dict(width=1.2, color=color)), row=1, col=1)

    row_of = {p: 3 + i for i, p in enumerate(panels)}
    if "거래량·OBV" in row_of:
        r = row_of["거래량·OBV"]
        vcol = [GREEN if c >= o else RED for c, o in zip(u_df.Close, u_df.Open)]
        fig.add_trace(go.Bar(x=u_df.index, y=u_df.Volume, marker_color=vcol, name="거래량", showlegend=False),
                      row=r, col=1)
        if "obv" in u_df:
            fig.add_trace(go.Scatter(x=u_df.index, y=u_df.obv, name="OBV", line=dict(width=1.2, color=BLUE)),
                          row=r, col=1, secondary_y=True)
            fig.add_trace(go.Scatter(x=u_df.index, y=u_df.obv_ema, name="OBV EMA",
                                     line=dict(width=1, dash="dot", color=BLUE)), row=r, col=1, secondary_y=True)
    if "RSI" in row_of:
        r = row_of["RSI"]
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df.rsi, name="RSI", line=dict(width=1.2, color="#888")),
                      row=r, col=1)
        for y in (30, 70):
            fig.add_hline(y=y, line=dict(width=0.6, dash="dot", color="#999"), row=r, col=1)
    if "MACD" in row_of:
        r = row_of["MACD"]
        hc = [GREEN if v >= 0 else RED for v in u_df.macd_hist.fillna(0)]
        fig.add_trace(go.Bar(x=u_df.index, y=u_df.macd_hist, marker_color=hc, name="MACD hist", showlegend=False),
                      row=r, col=1)
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df.macd, name="MACD", line=dict(width=1.2, color=BLUE)),
                      row=r, col=1)
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df.macd_signal, name="Signal",
                                 line=dict(width=1.2, color=ORANGE)), row=r, col=1)
    if "상대강도" in row_of:
        r = row_of["상대강도"]
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df.rs, name="RS", line=dict(width=1.2, color="#9467bd")),
                      row=r, col=1)
        fig.add_trace(go.Scatter(x=u_df.index, y=u_df.rs_sma, name="RS SMA50",
                                 line=dict(width=1, dash="dot", color="#9467bd")), row=r, col=1)

    if "피보나치" in overlays:
        fib = fibonacci(u_full, strat["structure"]["fib_lookback"])
        for k, p in {**fib.retr, **fib.ext}.items():
            fig.add_hline(y=p, line=dict(color="#b0b0b0", dash="dot", width=0.8), annotation_text=f"fib {k}",
                          annotation_position="top left", annotation_font_size=10,
                          annotation_font_color="#999", row=1, col=1)
    if vp:
        fig.add_hline(y=vp.poc, line=dict(color="#5c7cfa", width=1.2), annotation_text="POC",
                      annotation_position="top left", annotation_font_color="#5c7cfa", row=1, col=1)

    if "패턴·다이버전스" in overlays:
        pat = candle_patterns(u_full).loc[u_df.index]
        for name, (label, bias) in PATTERNS.items():
            if bias == 0 or not pat[name].any():
                continue
            hit = u_df[pat[name]]
            y = hit.Low * 0.995 if bias > 0 else hit.High * 1.005
            fig.add_trace(go.Scatter(x=hit.index, y=y, mode="markers", name=label, hovertext=label, opacity=0.8,
                                     marker=dict(symbol="triangle-up" if bias > 0 else "triangle-down", size=9,
                                                 color="#2ca02c" if bias > 0 else "#d62728")), row=1, col=1)
        dcfg = strat["divergence"]
        for osc, panel in (("rsi", "RSI"), ("macd_hist", "MACD")):
            for dv in find_divergences(u_full, osc, dcfg["pivot_left"], dcfg["pivot_right"], n_bars, dcfg["min_gap"]):
                color = "#2ca02c" if dv["bias"] > 0 else "#d62728"
                fig.add_trace(go.Scatter(x=[dv["t1"], dv["t2"]], y=[dv["p1"], dv["p2"]], mode="lines",
                                         line=dict(color=color, width=2.5), showlegend=False,
                                         hovertext=f"{'강세' if dv['bias'] > 0 else '약세'} 다이버전스 ({osc})"),
                              row=1, col=1)
                if panel in row_of:
                    fig.add_trace(go.Scatter(x=[dv["t1"], dv["t2"]], y=[dv["o1"], dv["o2"]], mode="lines",
                                             line=dict(color=color, width=2.5), showlegend=False),
                                  row=row_of[panel], col=1)

    lines = [("진입", plan.u_entry, plan.l_entry, BLUE, "solid"), ("손절", plan.u_stop, plan.l_stop, "#d62728", "dash")]
    lines += [(f"TP{i}", u, l, "#2ca02c", "dash") for i, (u, l) in enumerate(zip(plan.u_tps, plan.l_tps), 1)]
    lines.append(("트레일", plan.u_trail, plan.l_trail, ORANGE, "dot"))
    for label, u, l, color, dash in lines:
        fig.add_hline(y=u, line=dict(color=color, dash=dash, width=1.2), annotation_text=f"{label} {u:,.2f}",
                      annotation_position="top right", annotation_font_color=color, annotation_font_size=12,
                      row=1, col=1)
        fig.add_hline(y=l, line=dict(color=color, dash=dash, width=1.2), annotation_text=f"{label} {l:,.2f}",
                      annotation_position="top right", annotation_font_color=color, annotation_font_size=12,
                      row=2, col=1)

    breaks = [dict(bounds=["sat", "mon"])]
    if tf == "1h":
        breaks.append(dict(bounds=[16, 9.5], pattern="hour"))
    fig.update_xaxes(rangebreaks=breaks, rangeslider_visible=False, col=1)
    for r in range(1, n_rows):
        fig.update_xaxes(showticklabels=False, row=r, col=1)
    fig.update_xaxes(fixedrange=not interactive)
    fig.update_yaxes(fixedrange=not interactive)
    fig.update_layout(height=sum(heights) + 60, margin=dict(l=8, r=8, t=28, b=8), showlegend=False,
                      dragmode="pan" if interactive else False, hovermode="x", bargap=0.05,
                      font=dict(size=13))
    if vp:
        # 매물대: 가격 패널 오른쪽 가장자리에서 왼쪽으로 자라는 반투명 막대 (별도 x축을 겹쳐 사용)
        in_va = [(vp.val <= c <= vp.vah) for c in vp.centers]
        fig.add_trace(go.Bar(y=vp.centers, x=vp.volume, orientation="h", showlegend=False, name="매물대",
                             hoverinfo="skip", xaxis="x99", yaxis="y", opacity=0.35,
                             marker_color=["#5c7cfa" if v else "#adb5bd" for v in in_va]))
        fig.update_layout(xaxis99=dict(overlaying="x", side="top", range=[float(vp.volume.max()) / 0.22, 0],
                                       visible=False, fixedrange=True, showgrid=False))
    config = {"displayModeBar": interactive, "scrollZoom": interactive, "displaylogo": False,
              "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d", "toImage"]}
    return fig, config
