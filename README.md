# LevTrack

기초지수(QQQ, SOXX, SPY)로 국면과 손절·익절을 계산하고, 레버리지 ETF(TQQQ/SQQQ, SOXL/SOXS, UPRO/SPXU)의 진입가·수량·주문가로 바꿔 주는 개인용 기술적 분석 웹앱입니다. 기획 내용은 [PLAN.md](PLAN.md)에 있습니다.

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

- 상단 버튼으로 화면 전환: **대시보드 · 플래너 · 계산기 · 추적오차**
- **⚙️ 설정**(계좌, 리스크, 손절·익절 방식, 데이터)은 주소(URL)에 저장됩니다. 값을 맞춘 뒤 홈 화면에 추가하면 다음에도 그 설정으로 열립니다.
- 수량이 너무 적게 나오면 플래너의 🧮 줄에서 어떤 제한이 비중을 줄였는지 확인하세요. 설정에서 **목표 연변동성**, **중립 국면 비중 계수**, **변동성 높을 때 비중 줄이기**를 조절할 수 있습니다.
- 대시보드 카드의 **플랜 보기**를 누르면 해당 페어·추천 종목의 플래너로 바로 이동합니다.
- 차트는 기본적으로 확대·이동이 꺼져 있어 손가락으로 화면을 편하게 스크롤할 수 있습니다. 확대가 필요하면 **차트 확대·이동**을 켜세요. 보조 지표와 겹쳐 보기는 버튼으로 켜고 끕니다.

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
| 대시보드 | 페어별 국면(일봉), Bull/Bear 점수, 추천 종목(롱/숏 LETF), 권장 비중·수량, 변동성 |
| 차트 · 플래너 | 기초/LETF 2단 차트에 진입·손절·익절·트레일링 레벨, 진입 비율 계산, **메리츠증권 주문표** |
| 환산 계산기 | 기초 목표가 → LETF 가격 (당일 환산 / 보유일·변동성별 감쇠 반영) |
| 추적오차 | LETF 실제 수익률과 k × 기초 수익률의 괴리, 실효 배수 |
| 매매일지 | 진입·분할 청산 기록, 보유 포지션 평가손익·R, 통계(승률·평균 R·손익비), 익절 분할 비교, 조건별 성과, 연도별 손익·양도세 추정, 백업 |

## 반영된 기술적 분석

| 구분 | 내용 | 사용처 |
|---|---|---|
| 추세 | SMA50/200, SMA50 기울기, EMA20/50 | 방향 점수 (일봉) |
| 모멘텀 | RSI(14), MACD(12,26,9), ROC(20) | 방향 점수 |
| 구조 | 52주 범위 위치, 돈치안(20) | 방향 점수, 돌파 판단 |
| 거래량 | OBV, 상승봉 거래량 비중, 거래량 비율 | 방향 점수, 돌파 확인 |
| 상대강도 | QQQ/SPY, SOXX/QQQ | 방향 점수 |
| 다이버전스 | RSI · MACD 히스토그램 정규 다이버전스 | 점수 보정, 시간봉 트리거 |
| 캔들 패턴 | 장악형, 망치형/유성형, 샛별형/석별형, 도지, 인사이드바 | 시간봉 진입 트리거 |
| 피보나치 · 매물대 · 스윙 | 되돌림/확장, POC/VAH/VAL/HVN, 피벗 고저점 | 지지대 손절, 피보나치/저항대 익절 |
| 변동성 | ATR, 실현변동성 백분위, VIX/VXN | 손절폭, 비중 감액 |
| 다중 시간대 | 일봉 국면 + 시간봉 진입 타이밍 | 대시보드 · 플래너 |

자세한 규칙은 [PLAN.md](PLAN.md) 4.3 ~ 4.4를 참고하세요.

## 설정

- `config/pairs.yaml`: 페어 추가 (예: IWM → TNA/TZA)
- `config/strategy.yaml`: 지표 기간, 국면 계수, 손절 방식, 1회 리스크, 비중 한도

## 테스트

```bash
pytest
```

> 이 도구는 계산 보조용이며 투자 자문이 아닙니다. 무료 데이터는 지연되거나 틀릴 수 있습니다.
