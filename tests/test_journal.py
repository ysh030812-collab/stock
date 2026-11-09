import base64
import json
import math

import pytest

from levtrack import journal as J
from levtrack.config import load_pairs, load_strategy
from levtrack.data import MarketData
from levtrack.indicators import add_indicators
from levtrack.journal_store import GitHubStore, LocalStore, make_store
from levtrack.planner import build_plan

STRAT = load_strategy()


class P:  # build_plan 결과 흉내 (TQQQ 80 → 손절 72, 익절 88/96)
    signal_ticker, letf_ticker, k, direction = "QQQ", "TQQQ", 3, 1
    u_entry, u_stop, u_tps = 500.0, 486.7, [513.3, 526.7, 540.0]
    l_entry, l_stop, l_tps, l_trail = 80.0, 72.0, [88.0, 96.0, 104.0], 70.0
    sl_method, tp_method = "atr", "r"
    exit_shares = [3, 3, 4]

    class sizing:
        shares = 10


def trade(**kw):
    return J.new_trade(P, pair_id="NDX", fill_price=80.0, shares=10, entry_date="2026-01-02", **kw)


def test_full_cycle_r_and_pnl():
    t = trade()
    assert J.remaining_shares(t) == 10 and not J.is_closed(t)
    J.add_exit(t, price=88, shares=3, reason="익절1", exit_date="2026-01-05")
    J.add_exit(t, price=96, shares=3, reason="익절2", exit_date="2026-01-08")
    J.add_exit(t, price=92, shares=4, reason="트레일링", exit_date="2026-01-12")
    m = J.metrics(t)
    assert m["closed"] and m["final_reason"] == "트레일링"
    assert m["realized"] == pytest.approx(3 * 8 + 3 * 16 + 4 * 12)
    assert m["r"] == pytest.approx(120 / (8 * 10))
    assert m["days"] == 10 and m["tp1_hit"] and m["tp2_hit"]


def test_exit_validation_and_unrealized():
    t = trade()
    with pytest.raises(ValueError):
        J.add_exit(t, price=70, shares=11, reason="손절")
    with pytest.raises(ValueError):
        J.add_exit(t, price=70, shares=1, reason="몰라")
    m = J.metrics(t, current_price=84)
    assert m["unrealized"] == pytest.approx(40) and m["r"] == pytest.approx(0.5)


def test_stats_and_split_comparison():
    win = trade()
    J.add_exit(win, price=88, shares=3, reason="익절1")
    J.add_exit(win, price=96, shares=3, reason="익절2")
    J.add_exit(win, price=100, shares=4, reason="트레일링")
    loss = trade()
    J.add_exit(loss, price=72, shares=10, reason="손절")
    half = trade()
    J.add_exit(half, price=88, shares=3, reason="익절1")
    J.add_exit(half, price=80, shares=7, reason="트레일링")
    s = J.stats([win, loss, half, trade()])  # 마지막은 보유 중 → 제외
    assert s["n"] == 3 and s["win_rate"] == pytest.approx(2 / 3) and s["max_loss_streak"] == 1
    cmp = J.split_comparison([win, loss, half])
    # 1차 전량: win +1R, loss −1R, half +1R → 평균 1/3
    assert cmp.loc["1차 전량 100/0/0", "평균 R"] == pytest.approx(1 / 3)
    # 추세 추종(25/25/50): win = .25*88+.25*96+.5*100 → (96-80)/8 = 2R
    rs = [(0.25 * 88 + 0.25 * 96 + 0.5 * 100 - 80) / 8, -1, (0.25 * 88 + 0.75 * 80 - 80) / 8]
    assert cmp.loc["추세 추종 25/25/50", "평균 R"] == pytest.approx(sum(rs) / 3)
    assert not J.group_stats([win, loss, half], "마지막 청산").empty


def test_tax_and_yearly():
    t = trade()
    J.add_exit(t, price=100, shares=10, reason="수동", exit_date="2026-03-01")
    y = J.realized_by_year([t])
    assert y.loc["2026", "손익($)"] == pytest.approx(200)
    assert J.estimate_tax_krw(5000, 1400) == pytest.approx((7_000_000 - 2_500_000) * 0.22)
    assert J.estimate_tax_krw(1000, 1400) == 0


def test_new_trade_from_real_plan():
    pairs = load_pairs()
    md = MarketData(pairs, mode="demo")
    d = add_indicators(md.ohlc("QQQ"), STRAT["indicators"])
    plan = build_plan(d, "QQQ", "TQQQ", 3, md.last("TQQQ"), 10_000, STRAT)
    t = J.new_trade(plan, pair_id="NDX", fill_price=plan.l_entry, shares=plan.sizing.shares or 1)
    json.dumps(t)  # 직렬화 가능
    assert t["plan"]["l_stop"] < t["entry_price"]
    assert not math.isnan(J.risk_per_share(t))


def test_local_store_roundtrip(tmp_path):
    s = LocalStore(tmp_path / "j.json")
    assert s.load() == []
    s.save([trade()])
    assert s.load()[0]["ticker"] == "TQQQ"
    assert isinstance(make_store({}, tmp_path / "x.json"), LocalStore)


class FakeResp:
    def __init__(self, code, data=None, headers=None):
        self.status_code, self._d, self.text = code, data or {}, json.dumps(data or {})
        self.headers = headers or {}

    def json(self):
        return self._d


class FakeGitHub:
    """GitHub contents/refs API 흉내."""

    def __init__(self):
        self.headers, self.branches, self.files, self.n = {}, {"main": "abc"}, {}, 0

    def get(self, url, params=None):
        if url.endswith("/"):  # 실제 GitHub 처럼 끝 슬래시는 404
            return FakeResp(404, {"message": "Not Found"})
        if url.endswith("/repos/o/r"):
            return FakeResp(200, {"default_branch": "main"})
        if "/git/ref/heads/" in url:
            b = url.rsplit("/", 1)[1]
            return FakeResp(200, {"object": {"sha": self.branches[b]}}) if b in self.branches else FakeResp(404)
        if "/contents/" in url:
            key = (params["ref"], url.split("/contents/")[1])
            if key not in self.files:
                return FakeResp(404)
            content, sha = self.files[key]
            return FakeResp(200, {"content": base64.b64encode(content).decode(), "sha": sha})
        raise AssertionError(url)

    def post(self, url, json=None):
        self.branches[json["ref"].split("/")[-1]] = json["sha"]
        return FakeResp(201, {})

    def put(self, url, json=None):
        key = (json["branch"], url.split("/contents/")[1])
        if key in self.files and json.get("sha") != self.files[key][1]:
            return FakeResp(409, {"message": "conflict"})
        self.n += 1
        self.files[key] = (base64.b64decode(json["content"]), f"sha{self.n}")
        return FakeResp(200, {"content": {"sha": f"sha{self.n}"}})


def test_github_store_creates_branch_and_roundtrips():
    gh = FakeGitHub()
    s = GitHubStore("tok", "o/r", "journal-data", "journal/trades.json", session=gh)
    assert s.load() == []
    s.save([trade()])
    assert "journal-data" in gh.branches
    s2 = GitHubStore("tok", "o/r", "journal-data", "journal/trades.json", session=gh)
    assert s2.load()[0]["ticker"] == "TQQQ"
    # 다른 기기에서 먼저 저장해도 (sha 충돌) 최신으로 재시도
    s2.save([trade(), trade()])
    s.save([trade()] * 3)
    assert len(GitHubStore("tok", "o/r", session=gh).load()) == 3
    assert gh.headers["Authorization"] == "Bearer tok"


def test_token_cleanup_and_checks():
    from levtrack.journal_store import StoreError, clean_token, token_problem

    raw = '  "github_pat_' + "A" * 85 + '\n" '
    assert clean_token(raw) == "github_pat_" + "A" * 85
    assert clean_token("Bearer github_pat_x") == "github_pat_x"
    assert token_problem(clean_token(raw)) is None
    assert "시작하지" in token_problem("levtrack-journal")
    assert "짧습니다" in token_problem("github_pat_abc")
    assert isinstance(make_store({"github_token": raw}, "x.json"), GitHubStore)

    class Bad(FakeGitHub):
        def get(self, url, params=None):
            return FakeResp(401, {"message": "Bad credentials"})

    s = GitHubStore("tok", "o/r", session=Bad())
    with pytest.raises(StoreError, match="토큰 값이 틀렸거나"):
        s.load()


def test_diagnose_reports_each_step():
    class GH(FakeGitHub):
        def __init__(self, user_code=200):
            super().__init__()
            self.user_code = user_code

        def get(self, url, params=None):
            if url.endswith("/user"):
                return FakeResp(self.user_code, {"login": "me"})
            return super().get(url, params)

    token = "github_pat_" + "B" * 82
    ok = GitHubStore(token, "o/r", session=GH()).diagnose()
    assert [x[0] for x in ok] == [True, True, True, True] and "me" in ok[0][1]
    bad = GitHubStore(token, "o/r", session=GH(401)).diagnose()
    assert bad[0][0] is False and "…BBBB (93자)" in bad[0][1]


def test_diagnose_flags_missing_contents_permission():
    class NoContents(FakeGitHub):
        def get(self, url, params=None):
            if url.endswith("/user"):
                return FakeResp(200, {"login": "me"})
            if "/git/ref/" in url or "/contents/" in url:
                return FakeResp(404, {"message": "Not Found"})
            return super().get(url, params)

    res = GitHubStore("github_pat_" + "C" * 82, "o/r", session=NoContents()).diagnose()
    assert [x[0] for x in res] == [True, True, False] and "Contents" in res[2][1]
    from levtrack.journal_store import StoreError
    with pytest.raises(StoreError, match="Contents"):
        GitHubStore("t", "o/r", session=NoContents()).save([])


def test_smart_quotes_and_odd_chars():
    from levtrack.journal_store import clean_token, token_problem

    assert clean_token("“ghp_" + "D" * 36 + "”") == "ghp_" + "D" * 36
    assert "둥근 따옴표" in token_problem("ghp_ab“cd")


def test_diagnose_classic_token_without_repo_scope():
    class Classic(FakeGitHub):
        def get(self, url, params=None):
            if url.endswith("/user"):
                return FakeResp(200, {"login": "me"}, {"X-OAuth-Scopes": "public_repo, read:user"})
            if url.endswith("/repos/o/r"):
                return FakeResp(404, {"message": "Not Found"})
            return super().get(url, params)

    res = GitHubStore("ghp_" + "E" * 36, "o/r", session=Classic()).diagnose()
    assert res[0][0] and "public_repo" in res[0][1]
    assert res[1][0] is False and "'repo' 권한이 없습니다" in res[1][1]
