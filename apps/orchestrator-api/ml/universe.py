"""Stock universe for the ML pipeline.

STARTER_KR = a curated ~40-name set across the sectors our Breaking engine
already tracks (방산/조선/반도체/2차전지/자동차/원전/IT/바이오/에너지/금융/항공),
so the model bake-off starts where our news coverage is strongest. Use --all to
pull the full KRX listing instead.
"""
from __future__ import annotations

# (code, name) — real KRX codes, mirrors breaking_report.SECTOR_UNIVERSE
STARTER_KR: list[tuple[str, str]] = [
    # 방산
    ("012450", "한화에어로스페이스"), ("047810", "한국항공우주"), ("079550", "LIG넥스원"),
    ("064350", "현대로템"), ("272210", "한화시스템"),
    # 조선
    ("329180", "HD현대중공업"), ("009540", "HD한국조선해양"), ("010140", "삼성중공업"),
    ("042660", "한화오션"),
    # 반도체
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("042700", "한미반도체"),
    # 2차전지
    ("373220", "LG에너지솔루션"), ("006400", "삼성SDI"), ("005490", "POSCO홀딩스"),
    ("247540", "에코프로비엠"),
    # 자동차
    ("005380", "현대차"), ("000270", "기아"), ("012330", "현대모비스"),
    # 원전
    ("034020", "두산에너빌리티"), ("052690", "한전기술"), ("015760", "한국전력"),
    # 인터넷/IT
    ("035420", "NAVER"), ("035720", "카카오"), ("018260", "삼성SDS"),
    # 바이오
    ("207940", "삼성바이오로직스"), ("068270", "셀트리온"),
    # 에너지/정유
    ("010950", "S-OIL"), ("078930", "GS"), ("096770", "SK이노베이션"),
    # 금융
    ("105560", "KB금융"), ("055550", "신한지주"), ("138040", "메리츠금융지주"),
    # 항공/여행
    ("003490", "대한항공"), ("272450", "진에어"), ("039130", "하나투어"),
    # 지수 ETF
    ("069500", "KODEX 200"),
]


def get_universe(mode: str = "starter") -> list[tuple[str, str]]:
    if mode == "all":
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        code_col = next((c for c in ("Code", "Symbol") if c in df.columns), df.columns[0])
        name_col = next((c for c in ("Name", "Korean Name") if c in df.columns), None)
        out = []
        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            if code.isdigit():
                out.append((code, str(row[name_col]).strip() if name_col else ""))
        return out
    return STARTER_KR
