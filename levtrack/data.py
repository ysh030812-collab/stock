"""시세 데이터: yfinance (무료) + CSV 캐시, 오프라인 데모용 합성 데이터."""
from __future__ import annotations

import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from .leverage import synthetic_letf_ohlc

COLS = ["Open", "High", "Low", "Close", "Volume"]
# yfinance 시간봉은 최근 730일까지만 제공
PERIODS = {"1d": "10y", "1h": "720d"}
HOURS = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]

# 데모용 대략적인 일간 변동성
DEMO_VOL = {"QQQ": 0.014, "SOXX": 0.022, "SPY": 0.011}
DEMO_START = {"QQQ": 180.0, "SOXX": 150.0, "SPY": 260.0}


class DataError(RuntimeError):
    pass


class MarketData:
    """mode='yahoo' : yfinance 실시세 (사용자 PC 에서 인터넷 필요)
    mode='demo'  : 합성 데이터 (오프라인 테스트용, 실제 가격 아님)
    """

    def __init__(self, pairs: list[dict], mode: str = "yahoo", cache_dir: str | Path = ".cache",
                 max_age_min: float = 15):
        self.mode = mode
        self.cache_dir = Path(cache_dir)
        self.max_age = max_age_min * 60
        self._mem: dict[tuple[str, str], pd.DataFrame] = {}
        self._letf_map = {
            ins["ticker"]: (p["signal"], ins["leverage"], ins.get("expense", 0.0))
            for p in pairs for ins in p["instruments"]
        }

    # ---- public -------------------------------------------------------
    def ohlc(self, ticker: str, interval: str = "1d", period: str | None = None) -> pd.DataFrame:
        """period: None 이면 기본(일봉 10년, 시간봉 720일), 'max' 면 전체 기간 (백테스트용)."""
        key = (ticker, interval, period)
        if key not in self._mem:
            if self.mode == "demo":
                df = self._demo(ticker, interval)
            else:
                df = self._yahoo_cached(ticker, interval, period)
            if df is None or df.empty:
                raise DataError(f"{ticker} ({interval}) 데이터를 받지 못했습니다")
            self._mem[key] = df
        return self._mem[key]

    def last(self, ticker: str, interval: str = "1d") -> float:
        return float(self.ohlc(ticker, interval)["Close"].iloc[-1])

    def try_last(self, ticker: str | None) -> float | None:
        if not ticker:
            return None
        try:
            return self.last(ticker)
        except Exception:
            return None

    # ---- yahoo --------------------------------------------------------
    def _yahoo_cached(self, ticker: str, interval: str, period: str | None = None) -> pd.DataFrame:
        suffix = f"_{period}" if period else ""
        path = self.cache_dir / f"{ticker.replace('^', '_').replace('=', '_')}_{interval}{suffix}.csv"
        if path.exists() and time.time() - path.stat().st_mtime < self.max_age:
            return pd.read_csv(path, index_col=0, parse_dates=True)
        df = fetch_yahoo(ticker, interval, period)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(path)
        return df

    # ---- demo ---------------------------------------------------------
    def _demo(self, ticker: str, interval: str) -> pd.DataFrame:
        if ticker in self._letf_map:
            sig, k, fee = self._letf_map[ticker]
            base = self._demo(sig, interval)
            out = synthetic_letf_ohlc(base, k, fee, start=50.0)
            # 데모 LETF 현재가를 보기 좋은 수준(≈50)으로 맞춤 (비율 기반이라 결과에 영향 없음)
            num = out.select_dtypes("number").columns.drop("Volume", errors="ignore")
            out[num] = out[num] * (50.0 / out["Close"].iloc[-1])
            return out.round(4)
        if ticker.startswith("^"):  # 변동성지수 근사: 기초 실현변동성 기반
            src = self._demo("QQQ", "1d")
            v = np.log(src["Close"]).diff().rolling(20).std() * np.sqrt(252) * 100 * 1.1
            v = v.dropna()
            return pd.DataFrame({c: v for c in ["Open", "High", "Low", "Close"]}).assign(Volume=0)
        hourly = demo_hourly(ticker)
        if interval == "1h":
            return hourly[hourly.index >= hourly.index[-1] - pd.Timedelta(days=720)]
        return to_daily(hourly)


def fetch_yahoo(ticker: str, interval: str = "1d", period: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    df = yf.Ticker(ticker).history(period=period or PERIODS.get(interval, "10y"), interval=interval,
                                   auto_adjust=False, actions=False)
    if df is None or df.empty:
        raise DataError(f"yfinance 에서 {ticker} 데이터가 비어 있습니다")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    if interval == "1d":
        idx = idx.normalize()
    df.index = idx
    df.index.name = "Date"
    return df[[c for c in COLS if c in df.columns]].dropna(subset=["Close"])


def to_daily(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(df.index.normalize())
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                        "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    out.index.name = "Date"
    return out


def demo_hourly(ticker: str, years: int = 10) -> pd.DataFrame:
    """국면 전환(상승/횡보/하락)이 있는 합성 시간봉. 실제 가격과 무관."""
    rng = np.random.default_rng(zlib.crc32(ticker.encode()))
    end = pd.Timestamp.today().normalize()
    days = pd.bdate_range(end=end, periods=252 * years)
    n_bars = len(days) * len(HOURS)
    vol_d = DEMO_VOL.get(ticker, 0.015)
    # 국면: 평균 60거래일 지속, 일간 드리프트
    drifts = np.array([0.0009, 0.0, -0.0013])
    regime_day = np.empty(len(days), dtype=int)
    r = 0
    for i in range(len(days)):
        if rng.random() < 1 / 60:
            r = rng.choice(3, p=[0.45, 0.35, 0.2])
        regime_day[i] = r
    vol_scale = np.where(regime_day == 2, 1.8, 1.0)
    per_bar = len(HOURS)
    mu = np.repeat(drifts[regime_day] / per_bar, per_bar)
    sig = np.repeat(vol_d * vol_scale / np.sqrt(per_bar), per_bar)
    rets = mu + sig * rng.standard_normal(n_bars)
    close = DEMO_START.get(ticker, 100.0) * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.standard_normal((2, n_bars))) * sig * 0.6
    high = np.maximum(open_, close) * (1 + wick[0])
    low = np.minimum(open_, close) * (1 - wick[1])
    idx = pd.DatetimeIndex([pd.Timestamp(f"{d.date()} {h}") for d in days for h in HOURS], name="Date")
    vol = rng.integers(1_000_000, 5_000_000, n_bars)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
                        index=idx).round(2)
