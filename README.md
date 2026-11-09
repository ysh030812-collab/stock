# LevTrack — 3배 레버리지 ETF 단타 도우미

기초지수(QQQ · SOXX · SPY)의 **일봉 RSI(2) 단기 되돌림** 신호로 3배 ETF(TQQQ/SQQQ · SOXL/SOXS · UPRO/SPXU)를
**1~5거래일** 단타하는 개인용 웹앱입니다. 화면은 **⚡ 단타 · 📒 일지 · 🧪 백테스트** 세 개뿐입니다.

## 단타 규칙

기초지수 일봉 **종가**로 판단하고 **다음 거래일**에 실행 (한국 아침 확인 → 밤 미국장 개장 직후 주문).

| 신호 | 진입 | 청산 | 종목 · 비중 |
|---|---|---|---|
| 🟢 눌림 매수 | 종가 > 200일선 · RSI(2) < 10 | RSI(2) > 70 또는 5거래일 | 3배 롱 |
| 🟡 과매도 롱 | 종가 < 200일선 · RSI(2) < 10 | RSI(2) > 70 또는 5거래일 | 3배 롱 (절반) |
| 🔴 반등 숏 | 200일선 아래 · 50일선 < 200일선 · RSI(2) > 90 | RSI(2) < 30 / 5거래일 / 200일선 회복 | 3배 숏 |

- 비중 = min(100%, 목표 변동성 40% ÷ (3 × 최근 20일 변동성))
- 화면에 **진입가 · 익절가(RSI 역산) · 시간 청산일 · 손절(숏) · 비상 손절(선택)** 과 3배 ETF 환산가, MTS 주문 방식(지정가/감시가/종가 알림)을 보여줍니다.
- **자동 점검**: 규칙을 '했다면'의 최근 3년 성과가 마이너스면 신호를 쉬고 현금.

## 근거와 한계 (실제 데이터 검증)

- 1999년 이후 9개 시장(QQQ·SPY·SOXX·다우·러셀·DAX·닛케이·FTSE·항셍) 중 **8곳에서 현금보다 나았고**, '눌림 매수'가 가장 강했습니다.
  (합성 3배 ETF, 차입비용·보수·현금이자, 편도 0.1% 비용, 다음 날 시가 체결 · 1999~ SPY 연 11.0% / MDD −25% / 승률 66%, QQQ 연 6.2% / −43%)
- **1999년 이전엔 모든 시장에서 손해**였습니다(나스닥 1972~98 −100%). 일간 되돌림이 2000년 무렵부터 나타난 시기 의존적 효과라는
  외부 분석과도 일치합니다. 자동 점검을 켜면 그 구간 손실을 대부분 막습니다(나스닥 1972~98: −23%/년 → +5%/년).
- 미국 주식 수익은 대부분 **밤사이**에 나왔고 장중 수익은 0 근처 — 장중 스캘핑보다 며칠 보유가 유리합니다.
- 데이트레이더 대다수가 손실을 본다는 연구(대만 80%+, 브라질 97%)가 있습니다. **📒 일지로 내 실제 성적(30~50건 후 평균 R)을 확인**하세요.
- 과거 성과가 미래를 보장하지 않습니다. 이 앱은 투자 자문이 아닙니다.

## 실행

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app/streamlit_app.py # 브라우저에서 http://localhost:8501
```

- 왼쪽 메뉴의 **데이터**에서 `실시세 (yfinance)`를 고르면 Yahoo Finance 무료 시세를 받습니다 (인터넷 필요, 15분 캐시).
- `데모`는 오프라인에서 화면과 계산 흐름을 확인하는 합성 데이터입니다. 실제 가격이 아닙니다.

## 태블릿에서 쓰기 (Streamlit Community Cloud, 무료)

1. [share.streamlit.io](https://share.streamlit.io) 에 GitHub 계정으로 로그인 → **Create app**
2. Repository `ysh030812-collab/stock`, Branch `claude/bold-hopper-02xh9x`, Main file `app/streamlit_app.py` → **Deploy**
3. 배포가 끝나면 태블릿에서 `https://<앱이름>.streamlit.app` 열기
4. 홈 화면에 추가
   - **iPad (Safari)**: 공유 버튼 → *홈 화면에 추가*
   - **안드로이드 (Chrome)**: ⋮ 메뉴 → *홈 화면에 추가*
   - 앱 화면이 다 뜬 뒤(대시보드 표가 보인 뒤) 추가해야 LevTrack 아이콘이 잡힙니다.

### 태블릿 조작

- 상단 버튼: **⚡ 단타 · 📒 일지 · 🧪 백테스트**
- **⚙️ 설정**(데이터, 단타 계좌 금액, 목표 변동성, 신호 3종 켜기/끄기, 자동 점검)은 바꿀 때마다 일지와 같은 곳
  (GitHub `journal-data` 브랜치의 `journal/settings.json`)에 자동 저장되어, 다시 열거나 다른 기기에서도 그대로입니다.
- ⚡ 단타: 위쪽 카드 3개로 오늘 상태를 보고, **자세히**로 주문표 · 익절/손절 가격 · 차트를 봅니다. 체결했으면 **일지에 기록**.
- 📒 일지: 보유 카드가 매일 **계속 보유 / 🔔 청산 신호**와 익절가를 알려줍니다.

아이콘은 `app/static/icon-*.png` 이고 `python tools/make_icon.py` 로 다시 만들 수 있습니다. 아이콘을 바꾸면 `app/home_icon.py` 의 `ICON_VERSION` 을 올리고, 홈 화면 아이콘을 지웠다가 다시 추가하세요.

## 매매일지 저장 (GitHub, 무료)

Streamlit Cloud 는 서버 파일이 재시작 때 지워지므로, 일지는 이 저장소의 **`journal-data` 브랜치**에 저장하는 것을 권장합니다
(앱이 배포된 브랜치와 달라서 저장해도 앱이 재배포되지 않습니다).

1. GitHub → Settings → Developer settings → **Fine-grained personal access tokens** → *Generate new token*
   - Repository access: **Only select repositories** → `ysh030812-collab/stock`
   - Permissions → Repository permissions → **Contents: Read and write**
2. share.streamlit.io → 앱 ⋮ → **Settings → Secrets** 에 붙여넣고 저장
   ```toml
   [journal]
   github_token = "github_pat_여기에_토큰"
   repo = "ysh030812-collab/stock"
   branch = "journal-data"
   ```
3. 앱의 📒 일지 화면 상단이 "저장 위치: GitHub …" 로 바뀌면 완료. 첫 저장 때 `journal-data` 브랜치가 자동으로 만들어집니다.

설정 전에는 서버 파일에 저장되니, 일지 → **백업**에서 JSON 을 받아 두세요 (같은 화면에서 다시 불러올 수 있음).
내 PC 에서 실행할 때는 `data/journal.json` 에 저장됩니다.

## 화면

| 탭 | 내용 |
|---|---|
| ⚡ 단타 | 페어별 오늘 신호(🟢 눌림 매수 · 🟡 과매도 롱 · 🔴 반등 숏 · 💤 대기), 다음 거래일 주문표(일지 보유와 비교), 진입가 / 익절가 / 시간 청산일 / 손절, 3배 ETF 환산가, 신호 차트, 내 단타 성적 |
| 📒 일지 | 진입·청산 기록, 보유 중 포지션의 오늘 신호 상태와 익절가, 통계(승률·평균 R·손익비), 연도별 손익·양도세 추정, 백업 |
| 🧪 백테스트 | 단타 규칙을 현재 설정으로 전체 기간 검증 — 기초 1배 · 3배 보유와 비교, 신호별 기여, 자산 곡선 · 낙폭 · 연도별 수익률 · 거래 내역 |

## 설정

- `config/pairs.yaml`: 페어 추가 (예: IWM → TNA/TZA)
- 단타 규칙 기본값: `levtrack/dant.py` 의 `DEFAULTS` (RSI 기준값, 보유일, 비중 등)

이전에 만들었던 추세 롱숏 · 스윙 · 레버리지 비교 등의 분석 코드는 `levtrack/` 에 남아 있지만 앱 화면에서는 뺐습니다
(검증 결과와 연구 기록은 [PLAN.md](PLAN.md) 와 git 기록 참고).

## 테스트

```bash
pytest
```

> 이 도구는 계산 보조용이며 투자 자문이 아닙니다. 무료 데이터는 지연되거나 틀릴 수 있습니다.
