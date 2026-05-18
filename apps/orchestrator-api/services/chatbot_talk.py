"""
Chatbot Talk service — natural-language Q&A engine for the @triple-h/chatbot module.

Two-tier classification for natural-language understanding:

  1. FAST PATH: keyword + fuzzy match against the agent's intent list
     Handles obvious cases like "open reports" → nav_reports.

  2. LLM PATH: send the user query + agent context to Claude Haiku.
     The LLM either:
       a) Picks a matching intent (returns intent_name + entities)
       b) Answers the question directly using live data from the agent's
          knowledge sources (free-form natural-language reply)
       c) Falls back to friendly "I don't know" response.

This gives users TRULY natural language — they can phrase the same request
infinite ways and get a sensible answer, instead of having to memorize
specific keywords.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from services.logger import log


# ---------------------------------------------------------------------------
# Agent registry — each consuming agent provides:
#   - intents: list of dicts with name/description/examples/action
#   - knowledge_fetch: callable that returns a snapshot dict of live data
#   - personality: tone + greetings
#
# For now this is hardcoded for the VIP agent; later it'll read from a
# config file (apps/admin-dashboard/chatbot.config.ts equivalent on backend).
# ---------------------------------------------------------------------------

def _vip_intent_list() -> list[dict[str, Any]]:
    """The intents VIP supports — used by the LLM classifier."""
    return [
        # Navigation
        {"name": "nav_reports", "description": "Open the reports page",
         "examples": ["open reports", "show me reports", "go to reports", "I want to see reports"]},
        {"name": "nav_twins", "description": "Open the twins page",
         "examples": ["open twins", "show me my twins", "go to twins page"]},
        {"name": "nav_messages", "description": "Open the Messages hub — full conversation archive with each twin. Browse and reply to DMs.",
         "examples": ["open messages", "show me messages", "go to messages",
                      "open the message hub", "see DMs", "show my inbox",
                      "메시지 페이지", "메시지 열어", "받은 메시지"]},
        {"name": "nav_agents", "description": "Open the agents listing page",
         "examples": ["open agents", "show all agents", "list of agents"]},
        {"name": "nav_asset_agent", "description": "Open the asset agent",
         "examples": ["open asset agent", "go to asset agent", "show asset"]},
        {"name": "nav_stock_agent", "description": "Open the stock agent",
         "examples": ["open stock agent", "go to stock agent"]},
        {"name": "nav_realty_agent", "description": "Open the real estate agent",
         "examples": ["open realty", "go to real estate agent"]},
        {"name": "nav_meetings", "description": "Open the meetings page",
         "examples": ["open meetings", "meeting room"]},
        {"name": "nav_judgement", "description": "Open the judgement / approvals page",
         "examples": ["open approvals", "open judgement page", "review approvals"]},
        {"name": "nav_dashboard", "description": "Go to the main dashboard / home",
         "examples": ["go home", "back to dashboard"]},

        # Data queries — answered via knowledge sources, not navigation
        {"name": "query_daily_briefing", "description": "What's today's situation? Get a status briefing on all twins, tasks, alerts.",
         "examples": ["what's today's situation", "give me today's briefing",
                      "what happened today", "morning briefing", "how is everything",
                      "오늘 상황 알려줘", "오늘 어떻게 됐어"]},
        {"name": "query_weekly_report", "description": "Get this week's company performance — tasks completed, progress.",
         "examples": ["weekly report", "this week's performance", "how did we do this week",
                      "주간 보고", "이번 주 어떻게 됐어"]},
        {"name": "query_stock", "description": "Information about the stock portfolio — current prices, holdings, gains/losses, KOSPI.",
         "examples": ["what is my stock status", "give me info about my stock",
                      "how is my portfolio doing", "stock report", "kospi today",
                      "what's the market doing", "tell me about my stocks",
                      "주식 상황", "내 주식 어때"]},
        {"name": "query_asset", "description": "Information about real-estate assets — properties, rental income, occupancy, yield.",
         "examples": ["asset status", "give me info about my assets", "how are my properties",
                      "tell me my asset portfolio", "what's the occupancy rate",
                      "자산 상태", "내 자산 어때"]},
        {"name": "query_realty", "description": "Real estate market data — listings, vacancy, market trends.",
         "examples": ["real estate status", "realty market", "how is the property market",
                      "부동산 상황"]},
        {"name": "query_twins", "description": "Information about the digital twins — count, modes, current activity.",
         "examples": ["how are my twins", "show me twin status",
                      "list my twins", "twins overview", "트윈 상태"]},
        {"name": "query_approvals", "description": "Pending approvals or judgement decisions awaiting human review.",
         "examples": ["pending approvals", "what needs review", "any decisions waiting", "승인 대기"]},
        {"name": "query_absences", "description": "Workers who haven't logged in recently.",
         "examples": ["who's absent", "missing workers", "결근자"]},

        # Triggers
        {"name": "trigger_daily_report", "description": "Generate a fresh daily report right now.",
         "examples": ["generate daily report", "make a daily report",
                      "create today's report", "데일리 리포트 생성"]},
        {"name": "trigger_weekly_report", "description": "Generate a fresh weekly report right now.",
         "examples": ["generate weekly report", "create weekly report"]},
        {"name": "broadcast", "description": "Send a message to all workers.",
         "examples": ["broadcast a message", "tell everyone", "send to all", "공지"],
         "requires_confirmation": True},
        {"name": "send_twin_message", "description": "Send a personal message to a specific twin/employee.",
         "examples": ["send message to {name}", "tell {name} that ...",
                      "text {name} ...", "{name}에게 메시지 보내"],
         "requires_confirmation": True},

        # Help
        {"name": "help", "description": "Explain what the assistant can do.",
         "examples": ["what can you do", "help", "how can you help"]},

        # === External portals — open the actual deployed agent web UIs ===
        {"name": "nav_asset_portal", "description": "Open the EXTERNAL Asset Agent web portal in a new tab (the deployed asset-agent.onrender.com app, not the agents listing page). Use when user says 'asset agent portal', 'asset agent website'.",
         "examples": ["open asset agent portal", "asset agent website", "open the asset portal", "go to the asset agent app", "자산 에이전트 포털"]},
        {"name": "nav_stock_portal", "description": "Open the EXTERNAL Stock Agent web portal in a new tab.",
         "examples": ["open stock agent portal", "stock agent website", "주식 에이전트 포털"]},

        # === UI control intents — chatbot drives the host UI ===
        {"name": "ui_go_back", "description": "Go back to the previous page (browser back button). Use when user says 'close X menu/page', 'go back', 'previous page'.",
         "examples": ["go back", "previous page", "close this menu", "close the twins menu", "close the reports page", "back to previous", "이전 페이지", "뒤로 가기", "뒤로"]},
        {"name": "ui_refresh", "description": "Refresh / reload the current page",
         "examples": ["refresh", "reload", "refresh the page", "reload page", "새로고침"]},
        {"name": "ui_scroll_top", "description": "Scroll to the top of the current page",
         "examples": ["scroll up", "go to top", "scroll to top", "맨 위로"]},
        {"name": "ui_scroll_bottom", "description": "Scroll to the bottom of the current page",
         "examples": ["scroll down", "go to bottom", "scroll to bottom", "맨 아래로"]},
        {"name": "ui_close_chatbot", "description": "Close or minimize the chatbot panel",
         "examples": ["close chatbot", "hide chatbot", "minimize chatbot", "챗봇 닫아", "챗봇 숨겨"]},
        {"name": "ui_clear_chat", "description": "Clear the chat history in the chatbot panel",
         "examples": ["clear chat", "clear history", "delete messages", "reset chat", "대화 지워"]},
        {"name": "ui_stop_speaking", "description": "Stop the chatbot's current speech",
         "examples": ["stop speaking", "be quiet", "stop", "shut up", "그만 말해", "조용히"]},
    ]


def _triple_h_realty_knowledge_base() -> dict[str, Any]:
    """Knowledge base for the Triple H Real Estate customer-facing chatbot
    (`@부동산에이전트챗봇` on KakaoTalk).

    Used by chatbot_reply_service when handling inbound customer messages
    from the Kakao channel — NOT the VIP boss-facing platform.

    EDIT THE LISTS BELOW to add real property data, rental terms, contract
    info, etc. The LLM uses this content to ground its replies; without it
    the bot returns generic "I don't understand" fallbacks.

    Pattern for each list item: the more SPECIFIC and SEARCHABLE the text,
    the better the LLM grounds its answer. Include unit numbers, neighborhoods,
    prices, and dates whenever possible.
    """
    return {
        "purpose":
            "Triple H(트리플에이치) 부동산 AI 상담 챗봇입니다. 고객의 매물 문의, "
            "임대/매매 상담, 계약 관련 안내, 부동산 시장 정보 등에 답변합니다. "
            "복잡한 상담이나 계약 협상이 필요한 경우 담당자에게 연결해 드립니다.",

        # ── 매물 정보 (Property Listings) ─────────────────────────────────
        # Replace these placeholders with your real listings. Format keeps
        # it human-readable AND searchable by the LLM. One entry per unit.
        "listings": [
            {
                "unit": "A-303",
                "type": "오피스텔 임대",
                "location": "강남구 역삼동",
                "size": "전용 26㎡ (약 8평)",
                "rent": "월세 120만원",
                "deposit": "보증금 1,000만원",
                "maintenance": "관리비 12만원 (전기·수도 별도)",
                "features": "남향, 풀옵션 (냉장고/세탁기/에어컨/침대), 역삼역 도보 3분",
                "available_from": "즉시 입주 가능",
                "tour": "방문 예약 가능 — 평일 10:00-18:00, 토요일 10:00-15:00",
            },
            {
                "unit": "B-201",
                "type": "아파트 임대",
                "location": "서초구 반포동",
                "size": "전용 84㎡ (32평형)",
                "rent": "월세 180만원",
                "deposit": "보증금 2,000만원",
                "maintenance": "관리비 25만원 (장기수선충당금 별도)",
                "features": "남동향, 풀옵션 + 발코니 확장, 반포역 도보 5분, 단지 내 헬스장",
                "available_from": "2026년 6월 1일 입주 가능",
                "tour": "방문 예약 필수 — 1주일 전 사전 문의",
            },
            {
                "unit": "C-Tower 1505호",
                "type": "오피스텔 매매",
                "location": "성동구 성수동",
                "size": "전용 33㎡ (12평)",
                "price": "매매가 4억 8,000만원",
                "maintenance": "관리비 약 15만원",
                "features": "한강뷰 일부, 풀옵션, 성수역 도보 7분, 주차 1대",
                "available_from": "잔금 후 즉시 입주",
                "tour": "매매 계약 전 현장 확인 필수",
            },
            {
                "unit": "D-105",
                "type": "원룸 임대 (신축)",
                "location": "송파구 잠실동",
                "size": "전용 17㎡ (5평)",
                "rent": "월세 75만원",
                "deposit": "보증금 500만원",
                "maintenance": "관리비 8만원 (인터넷·수도 포함)",
                "features": "신축 1년차, 풀옵션 (냉장고/세탁기/인덕션/책상), 잠실새내역 도보 4분, CCTV·도어락",
                "available_from": "즉시 입주 가능",
                "tour": "방문 예약 가능 — 평일·토요일 10:00-18:00",
            },
            {
                "unit": "E-702",
                "type": "투룸 임대",
                "location": "강남구 논현동",
                "size": "전용 49㎡ (15평)",
                "rent": "월세 150만원",
                "deposit": "보증금 1,500만원",
                "maintenance": "관리비 15만원",
                "features": "남서향, 분리형 투룸, 풀옵션 + 빌트인 에어컨 2대, 논현역 도보 6분, 반려동물 가능 (소형견)",
                "available_from": "2026년 6월 15일 입주 가능",
                "tour": "방문 예약 — 1주일 전 사전 문의",
            },
            {
                "unit": "F-301",
                "type": "아파트 전세",
                "location": "서초구 방배동",
                "size": "전용 59㎡ (24평형)",
                "deposit": "전세 보증금 4억 5,000만원",
                "maintenance": "관리비 18만원",
                "features": "남향, 리모델링 완료, 방배역 도보 8분, 학세권 (서이초·서운중)",
                "available_from": "2026년 7월 1일 입주 가능",
                "tour": "방문 예약 가능 — 평일 13:00-18:00",
            },
            {
                "unit": "G-Tower 808호",
                "type": "오피스 임대",
                "location": "강남구 삼성동",
                "size": "전용 66㎡ (20평)",
                "rent": "월세 280만원",
                "deposit": "보증금 3,000만원",
                "maintenance": "관리비 35만원 (공용 전기·청소비 포함)",
                "features": "삼성역 도보 3분, 24시간 출입 가능, 회의실·라운지 공용, 주차 2대",
                "available_from": "즉시 입주 가능",
                "tour": "방문 예약 — 평일 10:00-17:00",
            },
            {
                "unit": "H-1102",
                "type": "주상복합 매매",
                "location": "송파구 문정동",
                "size": "전용 72㎡ (28평형)",
                "price": "매매가 8억 2,000만원",
                "maintenance": "관리비 22만원",
                "features": "남동향, 풀옵션, 문정역 도보 5분, 헬스장·골프연습장·도서관 단지 내, 학세권",
                "available_from": "잔금 후 즉시 입주",
                "tour": "매매 계약 전 현장 확인 필수, 평일 10:00-18:00",
            },
        ],

        # ── 기본 임대 조건 (Standard Rental Terms) ────────────────────────
        "rental_terms": {
            "default_lease_period": "기본 임대 기간은 2년이며, 1년 단기 임대도 협의 가능합니다.",
            "deposit_policy": "보증금은 임대 시작 전 전액 납부, 계약 만료 후 30일 이내 반환됩니다.",
            "payment_schedule": "월세는 매월 1일 자동이체 권장. 카드 결제 시 수수료 별도 발생.",
            "renewal": "임대 갱신은 만료 1개월 전 통보. 시세 변동에 따라 임대료 조정 가능 (통상 5% 이내).",
            "early_termination": "중도 해지 시 잔여 기간 1개월분 위약금 발생. 새 임차인 매칭 시 면제 가능.",
        },

        # ── 계약 정보 (Contract Info) ────────────────────────────────────
        "contract_info": {
            "required_docs": [
                "신분증 (주민등록증 또는 운전면허증) 사본",
                "재직증명서 또는 사업자등록증",
                "최근 3개월 통장 사본 (월세 납부 능력 증빙)",
                "보증인 동의서 (보증금 1,000만원 이상의 경우)",
            ],
            "contract_process": (
                "1) 매물 방문 및 확인  "
                "2) 가계약금 입금 (보증금의 10%)  "
                "3) 본 계약서 작성 및 잔금 납부  "
                "4) 입주일 키 수령 및 시설 점검  "
                "5) 입주 후 1주일 내 누락된 설비 신고 가능"
            ),
            "fees": (
                "공인중개사 수수료: 임대인·임차인 각 0.4% (월세 환산 기준). "
                "계약서 작성 비용 5만원, 등기 비용은 매매 시 별도 안내."
            ),
        },

        # ── 자주 묻는 질문 (FAQ) ─────────────────────────────────────────
        "faq": [
            {"q": "방문 예약은 어떻게 하나요?",
             "a": "이 채널에 방문 희망 매물 번호(예: B-201호)와 가능한 날짜·시간을 알려주시면 담당자가 예약 확인 메시지를 보내드립니다. 평일 10:00-18:00, 토요일 10:00-15:00 가능합니다."},

            {"q": "전세 매물도 있나요?",
             "a": "현재 전세 매물은 제한적으로 보유하고 있습니다. 희망 지역과 예산을 알려주시면 시장에 나온 전세 매물을 별도로 안내해 드리겠습니다."},

            {"q": "외국인도 임대 가능한가요?",
             "a": "네, 가능합니다. 외국인 등록증 또는 여권, 한국 내 체류 자격 증빙(비자), 재직증명서 또는 학생증이 필요합니다. 보증보험 가입을 권장드립니다."},

            {"q": "반려동물 동반 가능한 매물이 있나요?",
             "a": "매물에 따라 다릅니다. 반려동물 동반 가능 매물을 별도로 안내해 드릴 수 있으니, 동물 종류와 크기를 알려주세요."},

            {"q": "월세를 더 낮출 수 있나요?",
             "a": "임대료 협의는 가능하지만, 보증금 조정과 연계되는 경우가 많습니다. 구체적인 협의는 담당자가 직접 연락드리겠습니다."},

            {"q": "공실 매물 알림을 받고 싶어요",
             "a": "선호 지역, 예산, 면적, 입주 희망 시기를 알려주시면 매물 등록 시 우선 안내해 드리겠습니다."},

            {"q": "Triple H는 어떤 회사인가요?",
             "a": "트리플에이치(Triple H)는 서울 강남·서초·성동·송파 지역 중심의 부동산 중개 회사입니다. 오피스텔·아파트·상가 임대 및 매매를 전문으로 합니다."},

            {"q": "예산이 적은데 가능한 매물이 있나요?",
             "a": "네, 예산에 맞는 매물을 추천드릴 수 있습니다. 희망 월세(예: 50만원 이하 / 80만원 이하) 또는 보증금 한도를 알려주시면 해당 가격대 매물을 안내해 드리겠습니다."},

            {"q": "신용대출 또는 전세대출도 도와주시나요?",
             "a": "직접 대출은 진행하지 않지만, 신뢰할 수 있는 협력 은행·대출상담사를 연결해 드립니다. 필요하시면 상담 요청해 주세요."},

            {"q": "수수료는 얼마인가요?",
             "a": "공인중개사 법정 수수료를 따릅니다. 임대는 임대인·임차인 각 0.4% (월세 환산 기준), 매매는 거래 금액에 따라 0.4~0.9% 범위입니다. 정확한 금액은 매물별로 안내드립니다."},

            {"q": "차량 등록 가능한 매물인가요?",
             "a": "매물마다 다릅니다. 주차 가능 여부와 추가 비용(주차장 임대료, 등록비)은 담당자가 확인 후 안내드리겠습니다. 차종을 알려주시면 더 정확하게 답변드릴 수 있습니다."},

            {"q": "단기 임대 (1-3개월)도 가능한가요?",
             "a": "일부 풀옵션 오피스텔에 한해 단기 임대가 가능합니다. 다만 단기 임대료는 일반 월세보다 20-30% 높을 수 있습니다. 희망 기간을 알려주시면 가능한 매물을 안내드리겠습니다."},

            {"q": "이 매물 사진을 받을 수 있나요?",
             "a": "네, 가능합니다. 관심 있으신 매물 번호(예: B-201호)를 알려주시면 사진과 평면도를 보내드리겠습니다."},
        ],

        # ── 회사 정보 (Company Info) ─────────────────────────────────────
        "company": {
            "name": "트리플에이치 주식회사 (Triple H Co., Ltd.)",
            "business_no": "215-86-81254",
            "specialty": "오피스텔 / 아파트 임대 및 매매, 부동산 중개",
            "service_areas": ["강남구", "서초구", "성동구", "송파구"],
            "channel": "@부동산에이전트챗봇 (카카오톡)",
            "operating_hours": "평일 10:00-18:00, 토요일 10:00-15:00 (일요일·공휴일 휴무)",
        },

        # ── 응대 가이드 (Reply Guidelines for the LLM) ────────────────────
        "reply_style": (
            "당신은 트리플에이치 부동산의 친절한 상담 매니저입니다. "
            "전문성 있으면서도 따뜻한 친구처럼 자연스럽게 대화합니다. 기계적이거나 "
            "딱딱한 답변은 피하고, 실제 상담사가 카카오톡으로 채팅하듯 답변합니다.\n\n"
            "■ 언어 (가장 중요)\n"
            "• 고객이 한국어로 쓰면 → 한국어로 답변 (존댓말, 친근하게)\n"
            "• 고객이 영어로 쓰면 → 영어로 답변 (warm, friendly English)\n"
            "• 고객이 섞어 쓰면 → 주된 언어를 따라가되 핵심 단어는 양쪽 다 표기\n\n"
            "■ 톤 & 매너\n"
            "• 한국어: 너무 격식 차리지 않고 친근하게 (예: '네, 안내해드릴게요 🙂')\n"
            "• 이모지 1-2개 자연스럽게 사용 (🏠 😊 🙂 ✨ 👍 등) — 과하지 않게\n"
            "• 고객 마음에 공감 먼저 ('아 그러시군요!', '좋은 선택이세요', '걱정 마세요')\n"
            "• 답변 길이: 카카오톡 채팅 호흡으로 1-3문장 (너무 길면 답답함)\n"
            "• 자연스러운 한국어 — 'A이지만 B' 같은 딱딱한 문어체 대신 'A인데 B예요' 같은 구어체\n\n"
            "■ 답변 원칙\n"
            "• KB에 매물 정보가 있으면 정확히 인용 (가격·평형·위치)\n"
            "• 모르는 정보는 추측하지 않기 → '담당자가 정확히 확인해서 안내드릴게요'\n"
            "• 계약·법적 문의는 담당자 연결 권장\n"
            "• 고객이 막연한 질문 시 → 한두 가지 구체적 옵션 제안 (예: '강남쪽 보시나요, 아니면 송파쪽?')\n"
            "• 항상 다음 질문이나 행동을 자연스럽게 유도 (대화가 끊기지 않게)\n\n"
            "■ 예시\n"
            "고객: '월세 매물 있어요?'\n"
            "좋은 답변: '네, 다양하게 보유하고 있어요 🏠 혹시 어느 지역 보시나요? 강남·서초·성동·송파 중에서요. 그리고 희망 평형대도 알려주시면 바로 추천드릴게요!'\n"
            "나쁜 답변 (피해야 함): '월세 매물 문의 감사합니다. 지역과 평형을 알려주시면 매물을 안내해 드리겠습니다.'\n\n"
            "고객: 'B-201호 보증금이 얼마예요?'\n"
            "좋은 답변: 'B-201호는 보증금 2,000만원이에요! 서초구 반포동 32평 아파트로 월세 180만원이고, 6월 1일부터 입주 가능합니다 🙂 방문 보시려면 1주일 전에 미리 말씀해 주세요.'\n"
        ),
    }


def _vip_knowledge_base() -> dict[str, Any]:
    """
    Static knowledge about VIP — UI structure, features, FAQ.
    Used to answer questions like "what is Twins menu", "where is reports",
    "what does this agent do", "how do I add a twin".
    """
    return {
        "purpose":
            "VIP Agent is the boss/CEO command center for the multi-agent platform. The boss "
            "supervises digital twins (one per employee) and three domain agents (Asset for "
            "real estate, Stock for financial portfolio, Realty for property market). The "
            "platform automatically generates daily and weekly reports, runs overnight handoffs "
            "where twins work while their owners sleep, and escalates anything that needs human review.",
        "menus": [
            {"name": "Dashboard",     "path": "/",              "description": "Home — overview cards: today's situation, alerts, agent health, latest reports, quick actions."},
            {"name": "Twins",         "path": "/twins",         "description": "List of all digital twins (one per worker). Click a twin to see its activity, knowledge, mode (shadow/active/handoff), and recent tasks."},
            {"name": "Messages",      "path": "/messages",      "description": "Central communication hub. Full conversation archive with each worker's twin — left pane lists threads, right pane shows the selected thread with a composer. Boss can browse history + send replies. The chatbot can also send quick messages, but this is the searchable view."},
            {"name": "Control Room",  "path": "/control-room",  "description": "Real-time live view of all agents and twins working — operations dashboard with running tasks."},
            {"name": "Task Board",    "path": "/task-board",    "description": "Kanban board of all tasks across the platform — pending, in progress, blocked, completed."},
            {"name": "Agents",        "path": "/agents",        "description": "List of registered domain agents (Asset, Stock, Realty, etc.). Status, endpoint URL, last health check."},
            {"name": "Workflows",     "path": "/workflows",     "description": "Schedules and cron jobs — daily report at 8 AM, weekly Friday 6:30 PM. Edit timing here."},
            {"name": "Reports",       "path": "/reports",       "description": "All generated daily/weekly reports. Click to read, download as DOCX, or compose a new one."},
            {"name": "Judgement",     "path": "/judgement",     "description": "Decision queue — items needing human approval. The boss reviews, approves, or escalates."},
            {"name": "A2A Monitor",   "path": "/a2a",           "description": "Agent-to-Agent communication monitor — see messages flowing between agents in real time."},
            {"name": "Channels",      "path": "/channels",      "description": "Communication channels (Telegram bot, email, webhooks). Register and configure each."},
            {"name": "AI Glass",      "path": "/ai-glass",      "description": "Smart-glasses integration page — for hands-free field work via AR glasses."},
            {"name": "Meetings",      "path": "/meetings",      "description": "Multi-twin meeting rooms. Create a meeting, invite multiple twins, run a discussion."},
            {"name": "Meeting Notes", "path": "/meeting-notes", "description": "Real-world meeting recordings: bilingual KR/EN transcription, summary, action items extracted automatically."},
            {"name": "Settings",      "path": "/settings",      "description": "Platform settings — user accounts, API keys, channel config, system preferences."},
        ],
        "features": [
            {"name": "Daily Briefing", "description": "Auto-generated every morning at 8 AM KST. Summarizes overnight twin activity, completed tasks, alerts.",
             "how_to": "Visible at top of Dashboard — or ask Chatbot 'what's today's situation'."},
            {"name": "Twin Handoff", "description": "Workers submit overnight tasks before bed; twins execute autonomously; boss reviews in the morning.",
             "how_to": "Workers do it from the Twin Portal (port 3010). Boss reviews at /handoff page."},
            {"name": "Twin Modes", "description": "Each twin has a mode: shadow (passive learning), active (working), handoff (preparing morning report). Auto-switches by Korean working hours.",
             "how_to": "Visible on each twin's detail page. Manual override available."},
            {"name": "Voice Chatbot", "description": "Always-on voice assistant in bottom-right corner. Speak naturally — no exact keywords needed.",
             "how_to": "Just speak: 'Hey Chatbot, asset status' or type in the panel."},
            {"name": "Telegram Reports", "description": "Daily and weekly reports auto-pushed to Telegram with executive summaries.",
             "how_to": "Connect bot in Channels page; reports send automatically once scheduled."},
        ],
        "faq": [
            {"q": "How many twins do I have?",
             "a": "Currently 11 twins. Ask 'show my twins' for the full list with their modes and status."},
            {"q": "How do I add a new twin?",
             "a": "Twins are created when a worker registers via the Twin Portal (port 3010). They auto-link to their owner via email."},
            {"q": "Where do my daily reports come from?",
             "a": "Auto-generated by the scheduler at 8 AM KST. Sources: twin handoffs from previous evening + asset/stock/realty agent summaries."},
            {"q": "What's the difference between Twins page and Agents page?",
             "a": "Twins are per-employee AI assistants (one per worker). Agents are domain specialists (Asset, Stock, Realty). Different purposes."},
        ],
        "context":
            "Tech stack: Next.js admin-dashboard (port 3020) + twin-portal (3010), FastAPI orchestrator (8000), "
            "Postgres on Supabase, Redis pub/sub, multi-provider LLM client. Scheduler runs 7+ cron jobs.",
    }


def _vip_knowledge_snapshot(db: Session) -> dict[str, str]:
    """Live data snapshot — gives the LLM real numbers to answer with."""
    out: dict[str, str] = {}

    # Asset / stock / realty live summaries via adapters
    try:
        from db.models import CoreAgent
        from adapters import get_adapter
        for domain in ("asset", "stock", "realty"):
            agent = db.query(CoreAgent).filter(CoreAgent.type == domain, CoreAgent.status == "active").first()
            if not agent:
                continue
            try:
                adapter = get_adapter(agent.type, agent.name, agent.endpoint_url or "", agent.is_mock)
                if hasattr(adapter, "fetch_summary"):
                    summary = (adapter.fetch_summary() or {}).get("summary", "")
                    if summary:
                        out[f"{domain}_summary"] = summary[:300]
            except Exception:
                pass
    except Exception:
        pass

    # Twin counts
    try:
        from db.models import DigitalTwin
        twins = db.query(DigitalTwin).all()
        active = sum(1 for t in twins if (t.mode or "") == "active")
        shadow = sum(1 for t in twins if (t.mode or "") == "shadow")
        working = sum(1 for t in twins if (t.status or "") == "working")
        out["twins_summary"] = f"{len(twins)} twins total · {active} active · {shadow} shadow · {working} working"
    except Exception:
        pass

    # Pending approvals
    try:
        from db.models import JudgementCase
        pending = db.query(JudgementCase).filter(JudgementCase.decision == "human_review_required").count()
        out["pending_approvals"] = f"{pending} cases awaiting human review"
    except Exception:
        pass

    return out


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def handle_talk(
    db: Session, query: str, language: str, agent_id: str,
    intents: Optional[list[dict]] = None,
    knowledge_base: Optional[dict] = None,
    history: Optional[list[dict]] = None,
    current_path: Optional[str] = None,
    confirmed: bool = False,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Process a natural-language query and return a TalkResponse-shaped dict.

    `intents` and `knowledge_base` are typically sent by the agent's frontend
    config — the backend uses these directly. If not provided, falls back to
    a hardcoded VIP default (legacy behavior, only used during VIP's own
    transition to the config-driven approach).

    SELF-IMPROVE pillar — every call:
      1. Detects user corrections of the previous reply
      2. Augments intents with auto-learned examples for fast-path matching
      3. Logs the interaction for later analysis
      4. When LLM matches a new phrasing, adds it to auto-examples (Phase 1)
    """
    import time as _t
    _start = _t.time()

    if not query or not query.strip():
        return _make_response("I didn't catch a question.", language)

    query = query.strip()

    # === SELF-IMPROVE Phase 1.3 — correction detection ===
    from services.chatbot_self_improve import (
        detect_correction, record_correction, register_auto_example,
        load_auto_examples, log_interaction,
    )
    if detect_correction(query) and history:
        # Find the previous assistant turn to get the wrong intent
        last_assistant = next((t for t in reversed(history) if t.get("role") == "assistant"), None)
        last_user = None
        for t in reversed(history[:-1] if history else []):
            if t.get("role") == "user":
                last_user = t
                break
        if last_assistant:
            try:
                record_correction(
                    db,
                    agent_id=agent_id,
                    user_id=user_id,
                    original_query=(last_user or {}).get("text", "") or query,
                    wrong_intent=last_assistant.get("intent"),
                    correction_text=query,
                )
            except Exception:
                pass

    # Detect language
    if language == "auto":
        hangul = sum(1 for c in query if 0xAC00 <= ord(c) <= 0xD7A3)
        lang = "ko" if hangul > len(query) / 4 else "en"
    else:
        lang = "ko" if language == "ko" else "en"

    # Use whatever the agent's config sent in. Fall back to VIP's hardcoded
    # defaults only if the caller didn't provide them (legacy support).
    if intents is None:
        intents = _vip_intent_list() if agent_id == "vip" else []
    if knowledge_base is None:
        knowledge_base = _vip_knowledge_base() if agent_id == "vip" else {}
    snapshot = _vip_knowledge_snapshot(db) if agent_id == "vip" else {}
    kb = knowledge_base or {}

    # === SELF-IMPROVE Phase 1.5 — augment intents with auto-learned examples ===
    # When the LLM has previously classified a new phrasing into intent X,
    # that phrasing is stored in chatbot_auto_examples and merged here so the
    # fast keyword path catches it next time (no LLM call needed).
    try:
        auto_map = load_auto_examples(db, agent_id)
        if auto_map:
            for it in intents:
                extra = auto_map.get(it.get("name", ""), [])
                if extra:
                    it["examples"] = list(it.get("examples", [])) + extra
    except Exception:
        pass

    # === SELF-IMPROVE Phase 2.1 — capture length preference signals ===
    try:
        from services.chatbot_self_improve import maybe_apply_length_pref, find_canned_reply
        maybe_apply_length_pref(db, agent_id, query)
    except Exception:
        pass

    # === SELF-IMPROVE Phase 2.3 — auto-FAQ: skip LLM if this exact query has a
    # successful reply repeated 3+ times before. Returns the last canned answer.
    try:
        cached = find_canned_reply(db, agent_id, query, threshold=3)
        if cached:
            log.info(f"chatbot.talk: auto-FAQ hit for '{query[:40]}'", extra={"action": "chatbot.talk.faq"})
            return _make_response(cached, lang, intent="auto_faq", source="keyword")
    except Exception:
        pass

    # ---- FOLLOW-UP RESOLUTION ---- short references like "close it", "again", "더"
    # need the previous turn for context. Resolve before any other classification.
    followup = _resolve_followup(query, lang, history or [], current_path)
    if followup:
        log.info(f"chatbot.talk: follow-up resolved → {followup['intent']}", extra={"action": "chatbot.talk.followup"})
        intent_name = followup["intent"]
        reply, action = _execute_intent(db, intent_name, query, lang, snapshot)
        return _make_response(
            reply or followup.get("reply", ""), lang,
            intent=intent_name, action=action, source="keyword",
        )

    # ---- WORKFLOW DETECTION ---- compound requests like "do X then do Y" ----
    from services.chatbot_action import looks_like_workflow, plan_workflow, execute_step_plan
    if looks_like_workflow(query):
        log.info("chatbot.talk: detected workflow signal", extra={"action": "chatbot.talk.workflow_detect"})
        plan = plan_workflow(query, lang, intents, agent_id)
        if plan:
            log.info(f"chatbot.talk: executing {len(plan)}-step plan", extra={"action": "chatbot.talk.workflow_run"})
            final_reply, process_log, last_action = execute_step_plan(
                db, plan, lang, agent_id, intents, snapshot
            )
            return _make_response(
                final_reply, lang,
                intent="workflow",
                action=last_action,
                source="workflow",
                steps=process_log,
                ack_reply=("바로 진행하겠습니다." if lang == "ko"
                           else "Got it — running these steps now."),
            )

    # ---- HELP-INTENT DETECTION ---- skip Tier-1 keyword match for "what is X / where is Y / how do I Z"
    # questions about the agent's UI/structure, so the LLM can answer from the knowledge base instead
    # of getting hijacked by a data-query keyword.
    if _looks_like_help_question(query):
        log.info("chatbot.talk routing to LLM (help question)", extra={"action": "chatbot.talk.help_route"})
    else:
        # ---- TIER 1: FAST PATH ---- keyword/fuzzy match for obvious cases ----
        fast_match = _fast_match(query, intents)
        if fast_match:
            intent_name = fast_match["name"]
            log.info(f"chatbot.talk fast-path: {intent_name}", extra={"action": "chatbot.talk.fast", "intent": intent_name})

            # Confirmation gate: when intent is risky AND user hasn't yet confirmed,
            # return a preview without actually executing the side-effect.
            if not confirmed and fast_match.get("requires_confirmation"):
                preview = _make_confirmation_preview(intent_name, query, lang)
                if preview:
                    return preview

            reply, action = _execute_intent(db, intent_name, query, lang, snapshot)
            return _make_response(reply, lang, intent=intent_name, action=action, source="keyword")

    # ---- TIER 2: LLM PATH ---- ask Claude to classify or answer naturally ----
    # Pass length preference so Phase 2 can constrain reply length per user
    try:
        from services.chatbot_self_improve import get_length_pref as _glp
        _len_pref = _glp(db, agent_id)
    except Exception:
        _len_pref = "normal"
    llm_result = _llm_classify_or_answer(query, lang, intents, snapshot, agent_id, kb, history, current_path, length_pref=_len_pref)
    if llm_result is None:
        return _make_response(_fallback_reply(lang), lang, source="fallback")

    if llm_result.get("intent") and llm_result["intent"] != "free_answer":
        intent_name = llm_result["intent"]
        log.info(f"chatbot.talk llm-classified: {intent_name}", extra={"action": "chatbot.talk.llm", "intent": intent_name})

        # Confirmation gate (same as fast path) — find intent definition by name
        intent_def = next((it for it in intents if it.get("name") == intent_name), None)
        if not confirmed and intent_def and intent_def.get("requires_confirmation"):
            preview = _make_confirmation_preview(intent_name, query, lang, llm_extracted=llm_result.get("entities"))
            if preview:
                return preview

        reply, action = _execute_intent(db, intent_name, query, lang, snapshot, llm_extracted=llm_result.get("entities"))
        # If executor returned an empty reply, use the LLM's own reply
        if not reply and llm_result.get("answer"):
            reply = llm_result["answer"]
        return _make_response(reply, lang, intent=intent_name, action=action, source="llm")

    # Free-form answer (no intent matched — LLM didn't pick an intent)
    answer = llm_result.get("answer") or ""

    # If the answer is a "I can't do that" style refusal AND the user clearly
    # asked for an ACTION (not a question), try generating JS as a last resort.
    if _looks_like_action_request(query) and _looks_like_refusal(answer):
        log.info("chatbot.talk: action-request fallback → generating script", extra={"action": "chatbot.talk.script_gen"})
        script_action = _generate_script_action(query, lang)
        if script_action:
            return _make_response(
                script_action.get("explanation") or ("I'll do that for you. Click Run to confirm." if lang == "en"
                                                    else "이렇게 처리하겠습니다. 실행을 눌러 확인하세요."),
                lang,
                intent="generated_script",
                action=script_action,
                source="llm",
                requires_confirmation=True,
                confirm_text=("Run this script?" if lang == "en" else "이 스크립트를 실행할까요?"),
            )

    return _make_response(answer or _fallback_reply(lang), lang, source="llm")


# ---------------------------------------------------------------------------
# Tier 1 — fast keyword + fuzzy
# ---------------------------------------------------------------------------

def _resolve_followup(
    query: str, lang: str, history: list[dict], current_path: Optional[str]
) -> Optional[dict]:
    """
    Resolve short follow-up phrases ("close it", "again", "더", "그것 말고")
    by referring to the previous assistant turn. Returns a dict with the
    resolved intent + optional canned reply, or None if no follow-up signal.
    """
    q = query.lower().strip()
    if len(q) > 60:
        return None  # follow-ups are usually short

    # Find the most-recent assistant turn — what did we just do?
    last_assistant_intent: Optional[str] = None
    for turn in reversed(history):
        if turn.get("role") == "assistant" and turn.get("intent"):
            last_assistant_intent = turn["intent"]
            break

    # Pattern: "close it" / "close that" / "close the menu" / "close X" right after
    # a navigation intent → user wants to go back to where they were.
    close_patterns = [
        r"\bclose\s+(it|this|that|the\s+(menu|page|tab))\b",
        r"\b(go\s+back|back|previous|cancel)\b",
        r"^닫아$|^닫아줘$|^뒤로$|^이전$|^취소$",
    ]
    if any(re.search(p, q) for p in close_patterns):
        # If the previous intent was a navigation, "close it" = go back.
        if last_assistant_intent and last_assistant_intent.startswith("nav_"):
            return {"intent": "ui_go_back"}
        # If the user is currently NOT on the homepage and said "close it", go back.
        if current_path and current_path not in ("/", ""):
            return {"intent": "ui_go_back"}

    # Pattern: "again" / "한번 더" → re-trigger the previous intent if it's a trigger
    again_patterns = [r"\b(again|once more|do it again|repeat)\b", r"한\s*번\s*더|다시"]
    if any(re.search(p, q) for p in again_patterns):
        if last_assistant_intent and last_assistant_intent.startswith("trigger_"):
            return {"intent": last_assistant_intent}

    return None


def _looks_like_action_request(query: str) -> bool:
    """
    Detect imperatives — user asking for an ACTION rather than asking a question.
    Used to decide whether to fall back to LLM-generated JS when no intent matches.
    """
    q = query.lower().strip()
    # Common action verbs in English
    action_verbs = [
        "open", "close", "show", "hide", "make", "set", "change", "remove",
        "delete", "click", "press", "tap", "scroll", "fill", "type",
        "highlight", "color", "resize", "move", "drag", "send", "submit",
        "refresh", "reload", "go back", "navigate", "expand", "collapse",
        "create", "add", "insert", "update", "switch", "toggle", "enable",
        "disable", "start", "stop", "pause", "play", "increase", "decrease",
        "zoom", "focus", "select", "copy", "paste", "save",
    ]
    # Korean action verbs (rough)
    ko_verbs = ["열어", "닫아", "보여", "숨겨", "크게", "작게", "바꿔", "변경",
                "지워", "삭제", "클릭", "눌러", "스크롤", "채워", "입력",
                "강조", "색", "크기", "보내", "전송", "새로고침", "확대"]
    if any(re.search(rf"\b{v}\b", q) for v in action_verbs):
        return True
    if any(v in q for v in ko_verbs):
        return True
    return False


def _looks_like_refusal(text: str) -> bool:
    """Did the LLM refuse / say it can't do something?"""
    t = (text or "").lower()
    refusal_patterns = [
        "i can't", "i cannot", "i can not", "i'm not able", "i'm unable",
        "i don't have", "i don't know how", "i lack",
        "sorry, i", "afraid i can't", "not sure how to",
        "you can use", "you'll need to", "you have to",
        "use your browser", "browser's zoom", "developer tools",
        "tap or click", "manually", "you would need to",
        "not possible", "unable to",
        "할 수 없", "할 수 없습니다", "어렵습니다", "모르겠습니다",
        "직접 ", "수동으로",
    ]
    return any(p in t for p in refusal_patterns)


def _generate_script_action(query: str, lang: str) -> Optional[dict]:
    """
    Last-resort fallback: ask the LLM to write a small JavaScript snippet that
    does what the user asked. Returns a `script` action that the FRONTEND will
    show to the user with a confirmation prompt before executing.
    """
    try:
        from services.llm_client import chat_completion_sync
    except Exception:
        return None

    if lang == "ko":
        system = (
            "당신은 웹 페이지 자동화 도우미입니다. 사용자의 요청을 수행할 작은 JavaScript "
            "스니펫을 작성합니다. 코드는 메인 윈도우 컨텍스트에서 실행됩니다.\n\n"
            "JSON 형식으로만 응답:\n"
            '{ "code": "<JavaScript code>", "explanation": "<TTS용 한 줄 설명 (한국어)>" }\n\n'
            "규칙:\n"
            "- 짧고 안전한 코드 (한 줄~다섯 줄)\n"
            "- document/window/console만 사용\n"
            "- 위험한 작업 금지 (페이지 삭제, 외부 요청, eval 등)\n"
            "- 작업 불가능하면 빈 응답 (빈 JSON 객체)\n"
        )
    else:
        system = (
            "You are a web-page automation helper. Write a small JavaScript snippet "
            "that performs the user's request. The code runs in the main window context.\n\n"
            "Respond ONLY with JSON:\n"
            '{ "code": "<JavaScript code>", "explanation": "<one-line TTS-friendly description>" }\n\n'
            "Rules:\n"
            "- Short, safe code (1–5 lines)\n"
            "- Use only document/window/console\n"
            "- No dangerous operations (page deletion, fetch to external URLs, eval, etc.)\n"
            "- Return empty JSON object if the request is impossible\n"
            "- Prefer specific selectors that are likely to exist (class names, semantic HTML)\n"
            "- Do NOT include backticks, markdown code fences, or explanations outside JSON\n"
        )

    try:
        raw = chat_completion_sync(
            system_prompt=system,
            messages=[{"role": "user", "content": query}],
            max_tokens=400,
            temperature=0.2,
            model="gpt-4o-mini",
        )
    except Exception as e:
        log.warning(f"chatbot.talk script-gen LLM error: {e}")
        return None

    raw = (raw or "").strip()
    if not raw or raw.startswith("[LLM unavailable]"):
        return None

    # Strip markdown code fences if LLM ignored instructions
    raw = re.sub(r"^```(json|javascript)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    parsed = _try_extract_json(raw)
    if not isinstance(parsed, dict):
        return None
    code = (parsed.get("code") or "").strip()
    if not code or len(code) > 2000:  # sanity bound
        return None

    # Quick safety filter — block obviously dangerous patterns
    dangerous = ["eval(", "Function(", "import(", "fetch(", "XMLHttpRequest",
                 "WebSocket(", "indexedDB", "localStorage.clear", "sessionStorage.clear",
                 "document.cookie", "window.open", ".innerHTML = ", "outerHTML"]
    if any(d in code for d in dangerous):
        log.warning(f"chatbot.talk: blocked dangerous script pattern: {code[:100]}")
        return None

    return {
        "type": "script",
        "code": code,
        "explanation": parsed.get("explanation") or "",
    }


def _looks_like_help_question(query: str) -> bool:
    """
    Detect questions about the agent's UI/structure (vs data queries).
    These should bypass keyword fast-path so the LLM can answer from the
    knowledge base.

    Examples that match:
      "what is twins menu", "where is the reports page", "how do I add a twin",
      "what does this agent do", "explain the dashboard",
      "트윈 메뉴 뭐야", "어디에 보고서가 있어"
    """
    q = query.lower().strip()
    en_patterns = [
        r"\bwhat\s+is\s+",
        r"\bwhat'?s\s+",
        r"\bwhere\s+is\s+",
        r"\bwhere\s+can\s+",
        r"\bhow\s+do\s+i\s+",
        r"\bhow\s+can\s+i\s+",
        r"\bexplain\s+",
        r"\bwhat\s+does\s+(this|the)\s+",
        r"\btell\s+me\s+what\s+",
        r"\bsidebar",
        r"\bmenu\b",
        r"\bpage\b.*\?",
    ]
    ko_patterns = [
        r"뭐(야|에요|입니까|예요)?",
        r"무엇",
        r"어디",
        r"어떻게",
        r"메뉴",
        r"페이지",
        r"설명",
    ]
    if any(re.search(p, q) for p in en_patterns):
        return True
    if any(re.search(p, q) for p in ko_patterns):
        return True
    return False


def _fast_match(query: str, intents: list[dict]) -> Optional[dict]:
    """
    Attempt to match by exact substring of any example phrase, OR fuzzy match
    on a single-word example (typo tolerance for things like 'Assest' → 'asset').
    """
    from difflib import SequenceMatcher
    q = query.lower().strip()
    for intent in intents:
        examples = (intent.get("examples") or [])
        for ex in examples:
            ex_lower = ex.lower()
            # Skip examples with placeholders for fast path
            if "{" in ex_lower:
                continue
            if ex_lower in q:
                return intent
            # Single-word fuzzy match (avoid false matches on multi-word phrases)
            if " " not in ex_lower and len(ex_lower) >= 5:
                # Check if any word in query is similar to the example word
                for word in re.findall(r"[a-z']+", q):
                    if abs(len(word) - len(ex_lower)) > 2:
                        continue
                    if SequenceMatcher(None, ex_lower, word).ratio() >= 0.86:
                        return intent
    return None


# ---------------------------------------------------------------------------
# Tier 2 — LLM classifier + answerer
# ---------------------------------------------------------------------------

def _llm_classify_or_answer(
    query: str, lang: str, intents: list[dict], snapshot: dict[str, str], agent_id: str,
    kb: Optional[dict] = None,
    history: Optional[list[dict]] = None,
    current_path: Optional[str] = None,
    length_pref: str = "normal",
) -> Optional[dict]:
    """
    Ask the LLM to either:
      - Pick the matching intent (returns intent name + entities)
      - Or answer from the knowledge base (UI/menu/feature questions)
      - Or answer the question naturally using the live data snapshot
      - Or admit it doesn't know
    Returns dict with keys: intent, entities, answer
    """
    try:
        from services.llm_client import chat_completion_sync
    except Exception:
        return None

    # Build the intent menu
    intent_menu = "\n".join(
        f"- {it['name']}: {it['description']}"
        for it in intents
    )
    live_block = "\n".join(f"- {k}: {v}" for k, v in snapshot.items()) or "(no live data available)"

    # Build the knowledge-base block (UI/menus/features/FAQ)
    kb = kb or {}
    kb_lines: list[str] = []
    if kb.get("purpose"):
        kb_lines.append(f"## What this agent is\n{kb['purpose']}")
    if kb.get("menus"):
        kb_lines.append("\n## Menus / pages in this agent's sidebar")
        for m in kb["menus"]:
            kb_lines.append(f"- **{m['name']}** ({m['path']}): {m['description']}")
    if kb.get("features"):
        kb_lines.append("\n## Features")
        for f in kb["features"]:
            line = f"- **{f['name']}**: {f['description']}"
            if f.get("how_to"):
                line += f" — _How to: {f['how_to']}_"
            kb_lines.append(line)
    if kb.get("faq"):
        kb_lines.append("\n## FAQ")
        for entry in kb["faq"]:
            kb_lines.append(f"- Q: {entry['q']}\n  A: {entry['a']}")
    if kb.get("context"):
        kb_lines.append(f"\n## Additional context\n{kb['context']}")
    kb_block = "\n".join(kb_lines) if kb_lines else "(no static knowledge base)"

    # Recent conversation history — for pronoun resolution and follow-ups
    history_lines: list[str] = []
    if history:
        for turn in history[-6:]:  # last 6 turns
            role = turn.get("role", "?")
            text = (turn.get("text") or "")[:200]
            intent = turn.get("intent")
            tag = f" [intent={intent}]" if intent else ""
            history_lines.append(f"  {role}: {text}{tag}")
    history_block = "\n".join(history_lines) if history_lines else "  (no recent turns)"
    path_line = f"\n\nCurrent page user is on: {current_path}" if current_path else ""

    # === SELF-IMPROVE Phase 2.1 — length cap from preference ===
    _len_cap_en = {"terse": 30, "normal": 80, "detailed": 150}.get(length_pref, 80)
    _len_cap_ko = {"terse": 25, "normal": 60, "detailed": 120}.get(length_pref, 60)

    if lang == "ko":
        system = (
            f"당신은 '{agent_id}' 에이전트의 음성 비서입니다. 보스의 자연어 질문을 처리합니다.\n"
            f"답변은 짧고({_len_cap_ko}단어 이하), 음성으로 읽힐 친근한 한국어로. 마크다운/목록 금지.\n\n"
            "사용 가능한 인텐트 (사용자가 데이터를 요청할 때):\n" + intent_menu + "\n\n"
            "에이전트 자체에 대한 지식 (메뉴, 기능, FAQ — UI/구조 질문에 사용):\n" + kb_block + "\n\n"
            "현재 라이브 데이터 (실시간 숫자):\n" + live_block + "\n\n"
            "최근 대화 (대명사/'그것'/'다시' 등 해석에 사용):\n" + history_block + path_line + "\n\n"
            "다음 JSON 형식으로만 응답하세요 — 다른 텍스트 금지:\n"
            '{ "intent": "<인텐트명 OR free_answer>", "entities": { "key": "value" }, "answer": "<TTS용 응답>" }\n\n'
            "규칙:\n"
            "- 'X 메뉴는 뭐야?' 같은 UI/구조 질문 → intent='free_answer', 위의 메뉴/기능/FAQ에서 답.\n"
            "- 데이터 요청 → 가장 적합한 데이터 인텐트.\n"
            "- 짧은 후속 질문 ('닫아', '다시', '그것 말고') → 위의 최근 대화를 참고해 무엇을 가리키는지 파악 후 적절한 인텐트 선택.\n"
            "- 모르면 intent='free_answer', 짧게 모른다고. 데이터 추측 금지.\n"
            "- send_twin_message: entities에 {\"target\":\"<이름>\", \"message\":\"<내용>\"} 추출.\n"
        )
    else:
        system = (
            f"You are the voice assistant for the '{agent_id}' agent. Process the boss's natural-language question.\n"
            f"Reply short (under {_len_cap_en} words), spoken-friendly English. No markdown, no bullet lists.\n\n"
            "Available intents (use these when user asks for DATA or an ACTION):\n" + intent_menu + "\n\n"
            "Knowledge base about this agent itself (menus, features, FAQ — use for UI/structure questions):\n" + kb_block + "\n\n"
            "Current live data snapshot (real-time numbers):\n" + live_block + "\n\n"
            "Recent conversation (use to resolve pronouns 'it'/'that'/'again' and follow-ups):\n" + history_block + path_line + "\n\n"
            "Respond ONLY with this JSON shape — no other text:\n"
            '{ "intent": "<one of the intent names OR free_answer>", "entities": { "key": "value" }, "answer": "<TTS reply>" }\n\n'
            "Rules:\n"
            "- 'what is X menu?' / 'where is Y?' / 'how do I Z?' → intent='free_answer', use menus/features/FAQ above.\n"
            "- DATA requests (status, current numbers) → pick the matching data intent.\n"
            "- Short follow-ups ('close it', 'do it again', 'do the same for stocks') → look at the RECENT CONVERSATION above to figure out what 'it'/'that'/'same' refers to, then pick the right intent. Example: if previous turn was nav_reports and user says 'close it', they mean go back from reports — pick ui_go_back, NOT ui_close_chatbot.\n"
            "- Live data answers it but no intent fits → intent='free_answer' with the answer.\n"
            "- If you don't know, intent='free_answer' and briefly admit. Never guess data.\n"
            "- For send_twin_message: extract {\"target\":\"<name>\", \"message\":\"<body>\"} into entities.\n"
        )

    try:
        raw = chat_completion_sync(
            system_prompt=system,
            messages=[{"role": "user", "content": query}],
            max_tokens=400,
            temperature=0.3,
            model="gpt-4o-mini",
        )
    except Exception as e:
        log.warning(f"chatbot.talk llm error: {e}")
        return None

    # Parse JSON from the response (may have surrounding text)
    raw = (raw or "").strip()
    if not raw or raw.startswith("[LLM unavailable]"):
        return None
    parsed = _try_extract_json(raw)
    if not isinstance(parsed, dict):
        # Fallback: treat entire response as a free-form answer
        return {"intent": "free_answer", "entities": {}, "answer": raw[:500]}
    # Normalize fields
    if "intent" not in parsed:
        parsed["intent"] = "free_answer"
    if "answer" not in parsed:
        parsed["answer"] = ""
    if "entities" not in parsed or not isinstance(parsed["entities"], dict):
        parsed["entities"] = {}
    return parsed


def _try_extract_json(text: str) -> Any:
    """Extract first JSON object from a string, even if surrounded by other text."""
    try:
        return json.loads(text)
    except Exception:
        pass
    # Find the first balanced { ... } block
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


# ---------------------------------------------------------------------------
# Intent execution — routes to existing voice_intents handlers
# ---------------------------------------------------------------------------

def _execute_intent(
    db: Session,
    intent_name: str,
    query: str,
    lang: str,
    snapshot: dict[str, str],
    llm_extracted: Optional[dict] = None,
) -> tuple[str, Optional[dict]]:
    """
    Execute an intent and return (reply_text, optional_action_dict).
    Reuses the existing voice_intents handlers for VIP-specific behavior.
    """
    # External agent portals — open in new tab via `external: true`
    if intent_name == "nav_asset_portal":
        url = os.getenv("REAL_ASSET_AGENT_URL", "https://asset-agent-s4tw.onrender.com")
        return (("자산 에이전트 포털을 새 탭에서 엽니다." if lang == "ko"
                 else "Opening the Asset Agent portal in a new tab."),
                {"type": "navigate", "to": url, "external": True})
    if intent_name == "nav_stock_portal":
        url = os.getenv("REAL_STOCK_AGENT_URL", "https://stock-advisor-agent-9qwi.onrender.com")
        return (("주식 에이전트 포털을 새 탭에서 엽니다." if lang == "ko"
                 else "Opening the Stock Agent portal in a new tab."),
                {"type": "navigate", "to": url, "external": True})

    # Per-agent navigation: navigate to /agents AND highlight the specific card.
    # Handled below NAV_MAP because it adds an extra `highlight` field to the action.
    AGENT_HIGHLIGHT = {
        "nav_asset_agent":   ("Asset Agent",       "Opening the Asset Agent — scrolling to it now.",         "자산 에이전트로 이동해서 강조합니다."),
        "nav_stock_agent":   ("Stock",             "Opening the Stock Agent.",                                "주식 에이전트로 이동해서 강조합니다."),
        "nav_realty_agent":  ("Real Estate Agent", "Opening the Real Estate Agent.",                          "부동산 에이전트로 이동해서 강조합니다."),
    }
    if intent_name in AGENT_HIGHLIGHT:
        text, en_msg, ko_msg = AGENT_HIGHLIGHT[intent_name]
        return (
            (ko_msg if lang == "ko" else en_msg),
            {"type": "navigate", "to": "/agents", "highlight": text},
        )

    NAV_MAP = {
        "nav_reports":       ("/reports",          "Sure, opening the reports page.",        "네, 리포트 페이지를 엽니다."),
        "nav_twins":         ("/twins",            "Of course, opening the twins page.",     "네, 트윈 페이지를 엽니다."),
        "nav_messages":      ("/messages",         "Sure, opening Messages.",                 "네, 메시지 페이지를 엽니다."),
        "nav_agents":        ("/agents",           "On it — opening the agents page.",       "네, 에이전트 페이지를 엽니다."),
        "nav_meetings":      ("/meetings",         "Opening the meetings page.",             "네, 미팅 페이지를 엽니다."),
        "nav_judgement":     ("/judgement",        "Opening the judgement page.",            "네, 승인 페이지를 엽니다."),
        "nav_dashboard":     ("/",                 "Taking you back to the main dashboard.", "네, 대시보드로 이동합니다."),
    }
    if intent_name in NAV_MAP:
        path, en_msg, ko_msg = NAV_MAP[intent_name]
        return (ko_msg if lang == "ko" else en_msg, {"type": "navigate", "to": path})

    # Data queries — call the existing handlers from voice_intents
    if intent_name in ("query_daily_briefing", "query_weekly_report",
                       "query_stock", "query_asset", "query_realty",
                       "query_twins", "query_approvals", "query_absences"):
        return (_handle_data_query(db, intent_name, lang, snapshot), None)

    if intent_name == "trigger_daily_report":
        msg = ("데일리 리포트를 생성합니다. 잠시 후 리포트 페이지에서 확인하세요."
               if lang == "ko" else
               "I'll trigger a fresh daily report. Check the reports page in a moment.")
        return (msg, {"type": "trigger", "endpoint": "/reports/compose/auto-daily", "method": "POST"})

    if intent_name == "trigger_weekly_report":
        msg = "주간 리포트를 생성합니다." if lang == "ko" else "I'll trigger a fresh weekly report."
        return (msg, {"type": "trigger", "endpoint": "/reports/compose/weekly", "method": "POST"})

    if intent_name == "broadcast":
        # Broadcast needs a message body — extract from query or LLM entities
        msg_body = (llm_extracted or {}).get("message", "")
        if not msg_body:
            return ("어떤 메시지를 전송할까요?" if lang == "ko" else "What message should I broadcast?", None)
        return _broadcast(db, msg_body, lang)

    if intent_name == "send_twin_message":
        target = (llm_extracted or {}).get("target", "")
        body = (llm_extracted or {}).get("message", "")
        return _send_twin_message(db, target, body, lang)

    # === UI commands — return ui_command actions for the frontend to execute ===
    UI_CMD_MAP = {
        "ui_go_back":        ("go_back",        "Going back.",                "이전 페이지로 이동합니다."),
        "ui_refresh":        ("refresh",        "Refreshing the page.",       "페이지를 새로고침합니다."),
        "ui_scroll_top":     ("scroll_top",     "Scrolling to the top.",      "맨 위로 이동합니다."),
        "ui_scroll_bottom":  ("scroll_bottom",  "Scrolling to the bottom.",   "맨 아래로 이동합니다."),
        "ui_close_chatbot":  ("close_chatbot",  "Closing the chatbot. Click the icon if you need me again.", "챗봇을 닫습니다. 필요하시면 아이콘을 다시 눌러주세요."),
        "ui_clear_chat":     ("clear_chat",     "Chat cleared.",              "대화를 지웠습니다."),
        "ui_stop_speaking":  ("stop_speaking",  "OK, I'll be quiet.",         "네, 조용히 하겠습니다."),
    }
    if intent_name in UI_CMD_MAP:
        cmd, en_msg, ko_msg = UI_CMD_MAP[intent_name]
        return (ko_msg if lang == "ko" else en_msg, {"type": "ui_command", "command": cmd})

    if intent_name == "help":
        msg = ("저는 오늘 상황, 주간 리포트, 자산/주식/부동산 정보, 트윈 상태, 승인 대기, 페이지 열기, 메시지 전송 등을 도와드릴 수 있습니다."
               if lang == "ko" else
               "I can give you today's briefing, weekly reports, asset/stock/realty info, twin status, "
               "pending approvals, open any page, or send messages. Just ask naturally.")
        return (msg, None)

    return ("", None)


def _handle_data_query(db: Session, intent_name: str, lang: str, snapshot: dict[str, str]) -> str:
    """Map a query_* intent to its existing handler in voice_intents."""
    from services.voice_intents import (
        handle_daily_briefing, handle_weekly_report, handle_domain_situation,
        handle_twin_summary, handle_pending_approvals, handle_worker_absences,
    )
    try:
        if intent_name == "query_daily_briefing":
            return handle_daily_briefing(db, lang)
        if intent_name == "query_weekly_report":
            return handle_weekly_report(db, lang)
        if intent_name == "query_stock":
            return handle_domain_situation(db, lang, "stock")
        if intent_name == "query_asset":
            return handle_domain_situation(db, lang, "asset")
        if intent_name == "query_realty":
            return handle_domain_situation(db, lang, "realty")
        if intent_name == "query_twins":
            return handle_twin_summary(db, lang)
        if intent_name == "query_approvals":
            return handle_pending_approvals(db, lang)
        if intent_name == "query_absences":
            return handle_worker_absences(db, lang)
    except Exception as e:
        log.warning(f"chatbot.talk data query failed: {e}")
        return _fallback_reply(lang)
    return _fallback_reply(lang)


def _broadcast(db: Session, message: str, lang: str) -> tuple[str, None]:
    from db.models import DirectMessage, DigitalTwin
    from services.twin_notifications import notify
    twins = db.query(DigitalTwin).all()
    for t in twins:
        db.add(DirectMessage(twin_id=t.id, sender_type="boss", content=message))
        try:
            notify(db, t.id, "boss_message", "Message from Boss", message)
        except Exception:
            pass
    db.commit()
    msg = (f"{len(twins)}명의 워커에게 전송 완료했습니다." if lang == "ko"
           else f"Broadcast sent to {len(twins)} workers.")
    return (msg, None)


def _send_twin_message(db: Session, target: str, body: str, lang: str) -> tuple[str, None]:
    from db.models import DirectMessage, DigitalTwin
    from services.twin_notifications import notify
    if not target:
        msg = ("받는 사람을 못 들었습니다. '다브론벡에게 메시지 보내'처럼 말해주세요."
               if lang == "ko" else
               "I didn't catch the recipient. Try 'send a message to Davronbek: ...'.")
        return (msg, None)
    target_lower = target.lower()
    NOISE = {"twin", "agent", "the", "to", "for", "please"}
    target_words = [w for w in re.split(r"\s+", target_lower) if w and w not in NOISE]
    twins = db.query(DigitalTwin).all()
    twin = next((t for t in twins if target_lower in t.name.lower()), None)
    if not twin and target_words:
        for t in twins:
            if any(w in t.name.lower() for w in target_words):
                twin = t
                break
    if not twin:
        names = ", ".join(t.name for t in twins[:5])
        msg = (f"{target}이라는 트윈을 찾을 수 없습니다. 사용 가능: {names}"
               if lang == "ko" else
               f"I couldn't find a twin named {target}. Available: {names}")
        return (msg, None)
    if not body:
        body = "Boss wants to see you" if lang == "en" else "보스가 보고 싶어합니다"
    db.add(DirectMessage(twin_id=twin.id, sender_type="boss", content=body))
    try:
        notify(db, twin.id, "boss_message", "Message from Boss", body)
    except Exception:
        pass
    db.commit()
    msg = (f"완료. {twin.name}에게 메시지 전달했습니다: \"{body[:80]}\""
           if lang == "ko" else
           f"All done. Your message has been delivered to {twin.name}: \"{body[:80]}\"")
    return (msg, None)


def _make_confirmation_preview(
    intent_name: str, query: str, lang: str, llm_extracted: Optional[dict] = None,
) -> Optional[dict]:
    """
    Build a confirmation-preview response for a risky intent. The frontend
    shows this with Run/Cancel buttons. If the user clicks Run, frontend re-issues
    the same query with `confirmed=true`, bypassing this gate the second time.
    """
    if intent_name == "broadcast":
        # Extract message body from query (e.g. "broadcast: hi everyone")
        m = re.search(r"(?:broadcast|tell everyone|send to all|공지|모두에게)[:\s]+(.+)", query, re.I)
        body = (m.group(1).strip(" .!?") if m else "").strip()
        if not body:
            return None  # fall through to handler which asks for the message
        from db.base import SessionLocal
        from db.models import DigitalTwin
        db = SessionLocal()
        try:
            n = db.query(DigitalTwin).count()
        finally:
            db.close()
        confirm = (
            f"\"{body}\" 메시지를 모든 워커 {n}명에게 보낼까요?"
            if lang == "ko" else
            f"Send \"{body}\" to all {n} workers?"
        )
        ack = ("이 메시지를 보낼까요? 확인 후 실행됩니다."
               if lang == "ko" else
               "About to broadcast — confirm to send.")
        reply = (f"📢 전체 공지 미리보기:\n\"{body}\"\n받는 사람: 워커 {n}명"
                 if lang == "ko" else
                 f"📢 Broadcast preview:\n\"{body}\"\nRecipients: {n} workers")
        return _make_response(
            reply, lang,
            intent=intent_name, source="keyword",
            requires_confirmation=True,
            confirm_text=confirm,
            ack_reply=ack,
        )

    if intent_name == "send_twin_message":
        # Reuse the parser to extract target + body
        target, body = _parse_twin_message_back_compat(query)
        if llm_extracted:
            target = target or llm_extracted.get("target", "")
            body = body or llm_extracted.get("message", "")
        if not target:
            return None
        if not body:
            body = "Boss wants to see you" if lang == "en" else "보스가 보고 싶어합니다"
        confirm = (
            f"{target}에게 \"{body[:80]}\" 메시지를 보낼까요?"
            if lang == "ko" else
            f"Send \"{body[:80]}\" to {target}?"
        )
        ack = ("메시지를 보낼까요? 확인 후 전송됩니다."
               if lang == "ko" else
               "About to send — confirm to deliver.")
        reply = (f"💬 메시지 미리보기:\n받는 사람: {target}\n내용: \"{body[:200]}\""
                 if lang == "ko" else
                 f"💬 Message preview:\nTo: {target}\nBody: \"{body[:200]}\"")
        return _make_response(
            reply, lang,
            intent=intent_name, source="keyword",
            requires_confirmation=True,
            confirm_text=confirm,
            ack_reply=ack,
        )

    return None


def _parse_twin_message_back_compat(text: str) -> tuple[str, str]:
    """Reuse voice_intents._parse_twin_message — kept here as thin wrapper."""
    try:
        from services.voice_intents import _parse_twin_message
        return _parse_twin_message(text)
    except Exception:
        return ("", "")


def _fallback_reply(lang: str) -> str:
    return ("죄송합니다, 잘 이해하지 못했습니다. 다시 말씀해 주시겠어요?"
            if lang == "ko" else
            "I'm not sure how to help with that. Could you rephrase?")


def _make_response(reply: str, lang: str, *, intent: str | None = None,
                   action: dict | None = None, source: str | None = None,
                   steps: list[dict] | None = None,
                   ack_reply: str | None = None,
                   requires_confirmation: bool = False,
                   confirm_text: str | None = None) -> dict[str, Any]:
    return {
        "reply": reply or "",
        "language": "ko" if lang == "ko" else "en",
        "intent": intent,
        "action": action,
        "source": source,
        "steps": steps,
        "ackReply": ack_reply,
        "requiresConfirmation": requires_confirmation,
        "confirmText": confirm_text,
    }
