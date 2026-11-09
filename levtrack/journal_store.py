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
        self.fingerprint = f"{token[:15]}…{token[-4:]} ({len(token)}자)" if len(token) > 20 else f"({len(token)}자)"
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
            hint = {
                401: "토큰 값이 틀렸거나 만료·삭제됨 → GitHub 에서 토큰을 새로 만들어 Secrets 의 github_token 을 교체하세요",
                403: "토큰 권한 부족 → 토큰의 Repository permissions 에서 Contents 를 'Read and write' 로 설정하세요",
                404: (f"저장소 {self.repo} 를 찾을 수 없음 → 토큰의 Repository access 에 이 저장소가 선택됐는지, "
                      "Secrets 의 repo 값이 맞는지 확인하세요") if what == "저장소 조회" else
                     ("저장소 내용(Contents) 권한 없음 → 토큰의 Permissions 에 Contents 를 추가하고 'Read and write' 로 "
                      "설정하세요 (Classic 토큰이면 repo 범위 체크)"),
            }.get(r.status_code, r.text[:200])
            raise StoreError(f"GitHub {what} 실패 ({r.status_code}): {hint}")
        return r

    def diagnose(self) -> list[tuple[bool, str]]:
        """연결 테스트: 토큰 인증 → 저장소 접근 → 쓰기 권한 순으로 확인."""
        out = []
        r = self.s.get(f"{API}/user")
        if r.status_code != 200:
            out.append((False, f"토큰 인증 실패 ({r.status_code}) — 토큰 값이 틀렸거나 만료/삭제됨. 사용 중인 토큰: {self.fingerprint}"))
            return out
        out.append((True, f"토큰 인증 성공 — GitHub 계정 {r.json().get('login')}"))
        r = self.s.get(self._url(""))
        if r.status_code != 200:
            out.append((False, f"저장소 {self.repo} 접근 실패 ({r.status_code}) — 토큰의 Repository access 에 이 저장소를 선택하세요"))
            return out
        out.append((True, f"저장소 {self.repo} 접근 가능"))
        default = r.json().get("default_branch", "main")
        r = self.s.get(self._url(f"git/ref/heads/{default}"))
        if r.status_code != 200:
            out.append((False, f"Contents 권한 없음 ({r.status_code}) — 토큰 Permissions 에 Contents 를 추가하고 "
                               "'Read and write' 로 바꾸세요 (Classic 토큰이면 repo 범위 체크)"))
            return out
        out.append((True, "Contents 읽기 권한 확인"))
        # 쓰기 권한: 기본 브랜치에 빈 ref 생성 없이 확인하는 공식 방법이 없어, 일지 브랜치 확인/생성으로 대신함
        try:
            self._ensure_branch()
            out.append((True, f"쓰기 권한 확인 — {self.branch} 브랜치 준비됨"))
        except StoreError as e:
            out.append((False, f"쓰기 권한 없음 — 토큰의 Contents 권한을 'Read and write' 로 바꾸세요 ({e})"))
        return out

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


def clean_token(token) -> str:
    """붙여넣을 때 섞이기 쉬운 공백·줄바꿈·따옴표·'Bearer ' 접두어 제거."""
    t = "".join(str(token or "").split())
    t = t.strip("'\"“”‘’「」『』")
    if t.lower().startswith("bearer"):
        t = t[6:]
    return t


def token_problem(token: str) -> str | None:
    """명백히 잘못된 토큰 형식이면 안내 문구."""
    if not token:
        return None
    if not token.startswith(("github_pat_", "ghp_")):
        return "github_token 이 'github_pat_' 로 시작하지 않습니다. 토큰 이름이 아니라 생성 직후 보이는 긴 값을 넣어야 합니다."
    odd = sorted({c for c in token if not (c.isascii() and (c.isalnum() or c == "_"))})
    if odd:
        return (f"github_token 안에 토큰에 쓰이지 않는 문자 {' '.join(odd)} 가 있습니다. iPad 의 둥근 따옴표(“ ”) 자동 변환일 수 "
                "있으니 Secrets 에서 따옴표를 지우고 키보드의 \" 로 다시 입력하세요.")
    if token.startswith("github_pat_") and len(token) < 80:
        return f"github_token 길이가 {len(token)}자로 짧습니다 (보통 90자 이상). 복사할 때 잘렸을 수 있습니다."
    return None


def make_store(secrets: dict | None, local_path: str | Path):
    """st.secrets['journal'] 에 github_token 이 있으면 GitHubStore, 없으면 LocalStore."""
    cfg = dict(secrets or {})
    token = clean_token(cfg.get("github_token"))
    if token:
        return GitHubStore(token, cfg.get("repo", "ysh030812-collab/stock"), cfg.get("branch", "journal-data"),
                           cfg.get("path", "journal/trades.json"))
    return LocalStore(local_path)
