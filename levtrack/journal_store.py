"""매매일지 저장소.

- LocalStore : 서버의 JSON 파일 (내 PC 실행용). Streamlit Cloud 에서는 재시작 시 사라질 수 있음.
- GitHubStore: GitHub 저장소의 별도 브랜치에 JSON 파일로 저장 (무료, 영구).
  앱이 배포된 브랜치와 다른 브랜치를 써야 저장할 때마다 앱이 재배포되지 않는다.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

API = "https://api.github.com"


class StoreError(RuntimeError):
    pass


class LocalStore:
    kind = "local"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def label(self) -> str:
        return f"서버 파일 ({self.path})"

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, trades: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(trades, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


class GitHubStore:
    kind = "github"

    def __init__(self, token: str, repo: str, branch: str = "journal-data", path: str = "journal/trades.json",
                 session=None):
        import requests

        self.repo, self.branch, self.path = repo, branch, path
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})
        self._sha: str | None = None

    @property
    def label(self) -> str:
        return f"GitHub {self.repo} · {self.branch} 브랜치"

    def _url(self, p: str) -> str:
        return f"{API}/repos/{self.repo}/{p}"

    def _check(self, r, what: str):
        if r.status_code >= 400:
            raise StoreError(f"GitHub {what} 실패 ({r.status_code}): {r.text[:200]}")
        return r

    def _ensure_branch(self) -> None:
        r = self.s.get(self._url(f"git/ref/heads/{self.branch}"))
        if r.status_code == 200:
            return
        if r.status_code != 404:
            self._check(r, "브랜치 확인")
        default = self._check(self.s.get(self._url("")), "저장소 조회").json()["default_branch"]
        sha = self._check(self.s.get(self._url(f"git/ref/heads/{default}")), "기본 브랜치 조회").json()["object"]["sha"]
        self._check(self.s.post(self._url("git/refs"), json={"ref": f"refs/heads/{self.branch}", "sha": sha}),
                    "브랜치 생성")

    def load(self) -> list[dict]:
        r = self.s.get(self._url(f"contents/{self.path}"), params={"ref": self.branch})
        if r.status_code == 404:
            self._sha = None
            return []
        data = self._check(r, "일지 읽기").json()
        self._sha = data["sha"]
        return json.loads(base64.b64decode(data["content"]).decode("utf-8"))

    def save(self, trades: list[dict]) -> None:
        self._ensure_branch()
        if self._sha is None:
            self.load()
        body = {
            "message": f"journal: {len(trades)} trades",
            "content": base64.b64encode(json.dumps(trades, ensure_ascii=False, indent=1).encode("utf-8")).decode(),
            "branch": self.branch,
        }
        if self._sha:
            body["sha"] = self._sha
        r = self.s.put(self._url(f"contents/{self.path}"), json=body)
        if r.status_code == 409:  # 다른 곳에서 먼저 저장됨 → 최신 sha 로 한 번 재시도
            self.load()
            body["sha"] = self._sha
            r = self.s.put(self._url(f"contents/{self.path}"), json=body)
        self._sha = self._check(r, "일지 저장").json()["content"]["sha"]


def make_store(secrets: dict | None, local_path: str | Path):
    """st.secrets['journal'] 에 github_token 이 있으면 GitHubStore, 없으면 LocalStore."""
    cfg = dict(secrets or {})
    token = cfg.get("github_token")
    if token:
        return GitHubStore(token, cfg.get("repo", "ysh030812-collab/stock"), cfg.get("branch", "journal-data"),
                           cfg.get("path", "journal/trades.json"))
    return LocalStore(local_path)
