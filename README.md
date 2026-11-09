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

## 화면

| 탭 | 내용 |
|---|---|
| 대시보드 | 페어별 국면(일봉), Bull/Bear 점수, 추천 종목(롱/숏 LETF), 권장 비중·수량, 변동성 |
| 차트 · 플래너 | 기초/LETF 2단 차트에 진입·손절·익절·트레일링 레벨, 진입 비율 계산, **메리츠증권 주문표** |
| 환산 계산기 | 기초 목표가 → LETF 가격 (당일 환산 / 보유일·변동성별 감쇠 반영) |
| 추적오차 | LETF 실제 수익률과 k × 기초 수익률의 괴리, 실효 배수 |

## 설정

- `config/pairs.yaml`: 페어 추가 (예: IWM → TNA/TZA)
- `config/strategy.yaml`: 지표 기간, 국면 계수, 손절 방식, 1회 리스크, 비중 한도

## 테스트

```bash
pytest
```

> 이 도구는 계산 보조용이며 투자 자문이 아닙니다. 무료 데이터는 지연되거나 틀릴 수 있습니다.
