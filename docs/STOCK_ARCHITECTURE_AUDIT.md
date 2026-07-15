# 주식 기능 아키텍처 점검 결과

## 결론

이 프로젝트의 주식 기능은 **자체 구현과 외부 Stock Advisor 백엔드를 함께 사용하는 혼합 구조**입니다.

- VIP-Agent 저장소는 주식 관련 화면, 오케스트레이션, 일부 분석·매매 로직, 키움/KIS 직접 연동 코드를 자체 보유합니다.
- 그러나 주식 AI의 일반 질의 응답과 일부 시장 보조 데이터는 별도 Stock Advisor 백엔드를 기준 소스로 사용합니다.
- 종목별 실시간 가격·호가·거래량은 이 프로젝트에 연동된 키움 API를 우선 사용하며, 키움 API를 사용할 수 없을 때만 외부 Stock Advisor 백엔드로 폴백합니다.

## 저장소 및 프로젝트 경계

- 현재 저장소의 원격 저장소는 `https://github.com/tripleh-aiteam/VIP-Agent.git`입니다.
- `.gitmodules` 파일이 없으므로 Git submodule로 별도 주식 프로젝트를 포함하지 않습니다.
- 별도 Stock Advisor 프로젝트의 소스 코드는 이 저장소에 포함되어 있지 않습니다. 서비스 URL을 통해 HTTP API로 연동합니다.

## 외부 Stock Advisor 백엔드 의존성

`apps/orchestrator-api/services/stock_data_tools.py` 및 `apps/orchestrator-api/services/stock_advisor_chat.py`는 `STOCK_BACKEND_URL` 환경 변수를 사용합니다.

환경 변수가 설정되지 않았을 때의 기본 주소는 다음과 같습니다.

```text
https://stock-advisor-agent-9qwi.onrender.com
```

점검 시점의 루트 `.env`에는 `STOCK_BACKEND_URL`이 정의되어 있지 않았습니다. 따라서 별도의 실행 환경 설정이 없다면 코드상 기본 외부 주소를 사용합니다.

### 외부로 위임하는 기능

| 기능 | 외부 API 또는 처리 방식 |
| --- | --- |
| 주식 AI 일반 질의 | `POST /chat/agent`로 질문과 대화 이력을 전달하고 응답을 릴레이합니다. |
| 추천 종목 | `GET /recommendations` |
| 포트폴리오 | `GET /portfolio/positions` |
| 장중 신호 | `GET /intraday/signals`, `GET /intraday/status` |
| 시장 요약·수급·뉴스 | `/market/*`, `/dashboard/investor-flow` 등 |
| 관심종목·알림·지분 변동 | `/watchlist`, `/alerts`, `/ownership/change-events` |

`assistant_agent.py`는 Stock Advisor 앱의 주식 AI와 VIP 화면의 답변을 일치시키기 위해 외부 백엔드를 기준 소스로 취급합니다. 단, 투자 조언·의사결정·전망 성격의 일부 질의는 내부 `decide` 경로가 우선 처리합니다.

### 내부 키움 API 우선 처리 기능

| 기능 | 우선 처리 | 폴백 처리 |
| --- | --- | --- |
| 종목별 실시간 가격 | 프로젝트 내부 키움 REST/실시간 연동 | 외부 Stock Advisor 시장 데이터 |
| 종목별 실시간 호가 | 프로젝트 내부 키움 REST·WebSocket·PC 중계 연동 | 외부 Stock Advisor 시장 데이터 |
| 종목별 거래량 | 프로젝트 내부 키움 시세 연동 | 외부 Stock Advisor 시장 데이터 |

키움 API는 종목별 실시간 시세 데이터의 기본 소스입니다. 키움 자격 증명, 지정 단말기/IP 인증, 토큰 또는 실시간 연결 상태로 인해 내부 조회가 불가능한 경우에만 외부 Stock Advisor 백엔드를 사용합니다.

## 이 저장소의 자체 주식 구현

VIP-Agent 내부에는 다음과 같은 주식 도메인 코드가 존재합니다.

| 영역 | 주요 파일 또는 모듈 |
| --- | --- |
| 키움 REST 연동 및 호가 | `services/kiwoom_rest.py`, `services/ws_orderbook_collector.py`, `services/orderbook_memory.py` |
| KIS 연동 | `services/kis_client.py`, `services/kis_derivatives.py` |
| 모의투자 및 자동매매 | `services/paper_trader.py`, `services/auto_trader.py`, `services/scalp_trader.py`, `services/day_trade.py` |
| 판단·단타 분석·예측 | `services/decision_agent.py`, `services/intraday_setup.py`, `services/intraday_forecast.py` |
| 종목·시장 데이터 보조 기능 | `services/stock_resolver.py`, `services/stock_news.py`, `services/market_investor_flows.py` |
| API 노출 | `routers/paper_desk.py`, `routers/predictions.py`, `routers/chat.py` |

`main.py`는 `paper_desk`, `predictions`, `chat` 라우터를 직접 등록합니다. 또한 키움과 KIS 자격 증명이 설정된 환경에서는 내부 코드가 직접 시장 데이터 및 주문 관련 기능을 수행할 수 있습니다.

## 처리 흐름 요약

```text
VIP UI / 챗봇
        |
        v
VIP Orchestrator API
        |
        +-- 일반 주식 질의·시장 요약·수급·뉴스 --> 외부 Stock Advisor Backend
        |
        +-- 종목별 실시간 가격·호가·거래량 --> 내부 키움 API
        |                                      |
        |                                      +-- 키움 조회 실패 --> 외부 Stock Advisor Backend
        |
        +-- 투자 판단·모의투자·자동매매·일부 분석 --> VIP-Agent 내부 서비스
        |
        +-- 외부 호출 실패 시 --------------------> 내부 주식 엔진 폴백 경로
```

## 운영상 유의 사항

- 외부 Stock Advisor 백엔드의 가용성, 응답 지연, API 변경은 일반 주식 질의와 시장 요약·수급·뉴스 기능에 직접 영향을 줍니다. 종목별 실시간 가격·호가·거래량은 키움 API를 우선 사용합니다.
- `STOCK_BACKEND_URL`과 `STOCK_ASSISTANT_API_KEY`를 배포 환경별로 명시하면 기본 Render 주소 의존성과 인증 구성을 분명히 관리할 수 있습니다.
- 내부 키움/KIS 기능은 각 자격 증명 및 시장 데이터 제공 상태에 의존하므로, 외부 Stock Advisor 기능과 장애·폴백 기준을 분리해 관찰하는 것이 필요합니다.
