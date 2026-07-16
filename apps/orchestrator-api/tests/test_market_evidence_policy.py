"""Regression tests for live-evidence gating of market-overview questions."""

from services.assistant_agent import _requires_fresh_market_evidence


def test_requires_live_evidence_for_current_market_overviews():
    assert _requires_fresh_market_evidence("오늘 코스피와 코스닥 장 시작 흐름을 정리해줘")
    assert _requires_fresh_market_evidence("코스피 지금 시장 흐름 어때?")
    assert _requires_fresh_market_evidence("Give me the live KOSDAQ market overview")


def test_does_not_force_live_evidence_for_evergreen_or_unrelated_questions():
    assert not _requires_fresh_market_evidence("코스피가 무엇인가요?")
    assert not _requires_fresh_market_evidence("오늘 날씨 알려줘")
    assert not _requires_fresh_market_evidence("삼성전자 장기 투자 전망 알려줘")
