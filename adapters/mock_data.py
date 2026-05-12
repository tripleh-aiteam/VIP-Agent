"""
Realistic mock data for asset / stock / realty agents.
Used as inline fallback when mock FastAPI agents are not running, so reports
look professional even before real-data integration.
"""

import random
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# STOCK — KOSPI realistic data
# ---------------------------------------------------------------------------

KOSPI_HOLDINGS = [
    {"symbol": "005930.KS", "name": "Samsung Electronics", "shares": 12500, "avg_buy": 67500},
    {"symbol": "000660.KS", "name": "SK hynix",            "shares":  3200, "avg_buy": 145000},
    {"symbol": "035420.KS", "name": "NAVER",                "shares":  1800, "avg_buy": 215000},
    {"symbol": "035720.KS", "name": "Kakao",                "shares":  4500, "avg_buy":  62000},
    {"symbol": "207940.KS", "name": "Samsung Biologics",    "shares":   400, "avg_buy": 850000},
    {"symbol": "005380.KS", "name": "Hyundai Motor",        "shares":  2100, "avg_buy": 195000},
    {"symbol": "005490.KS", "name": "POSCO Holdings",       "shares":  1200, "avg_buy": 380000},
    {"symbol": "051910.KS", "name": "LG Chem",              "shares":   800, "avg_buy": 405000},
    {"symbol": "068270.KS", "name": "Celltrion",            "shares":  1500, "avg_buy": 175000},
    {"symbol": "012330.KS", "name": "Hyundai Mobis",        "shares":   900, "avg_buy": 235000},
]


def stock_summary() -> dict:
    """Realistic KOSPI portfolio snapshot."""
    holdings = []
    total_value_krw = 0
    total_cost_krw = 0
    for h in KOSPI_HOLDINGS:
        # Live price ±5% of avg_buy with small random walk
        change_pct = round(random.uniform(-3.5, 4.5), 2)
        price_now = int(h["avg_buy"] * (1 + change_pct / 100))
        value = price_now * h["shares"]
        cost = h["avg_buy"] * h["shares"]
        total_value_krw += value
        total_cost_krw += cost
        holdings.append({
            "symbol": h["symbol"],
            "name": h["name"],
            "shares": h["shares"],
            "avg_buy_krw": h["avg_buy"],
            "price_krw": price_now,
            "change_pct": change_pct,
            "value_krw": value,
            "recommendation": random.choices(["hold", "buy", "sell"], weights=[6, 3, 1])[0],
            "confidence": round(random.uniform(0.65, 0.92), 2),
        })

    pnl_krw = total_value_krw - total_cost_krw
    pnl_pct = round(pnl_krw / total_cost_krw * 100, 2)

    kospi_now = round(random.uniform(2520, 2680), 2)
    kospi_change = round(random.uniform(-1.2, 1.8), 2)

    high_risk = [h for h in holdings if abs(h["change_pct"]) > 3.0]

    return {
        "symbols_analyzed": len(holdings),
        "market_sentiment": random.choices(["bullish", "neutral", "bearish"], weights=[3, 5, 2])[0],
        "risk_score": round(random.uniform(0.25, 0.55), 2),
        "stocks": holdings,
        "portfolio": {
            "total_value_krw": total_value_krw,
            "total_cost_krw":  total_cost_krw,
            "unrealized_pnl_krw": pnl_krw,
            "unrealized_pnl_pct": pnl_pct,
            "holdings_count": len(holdings),
        },
        "market_summary": {
            "index": "KOSPI",
            "value": kospi_now,
            "change_pct": kospi_change,
            "foreign_net_krw": random.randint(-500_000_000_000, 800_000_000_000),
        },
        "high_risk_holdings": [{"symbol": h["symbol"], "name": h["name"], "change_pct": h["change_pct"]} for h in high_risk],
        "summary": f"KOSPI {kospi_now} ({kospi_change:+.2f}%) · Portfolio {total_value_krw/1e9:.1f}B KRW ({pnl_pct:+.2f}%)",
    }


# ---------------------------------------------------------------------------
# ASSET — portfolio with leases, cash flow
# ---------------------------------------------------------------------------

ASSETS = [
    {"name": "Gangnam Office Tower",  "type": "office",     "value_krw": 45_000_000_000, "monthly_income_krw": 180_000_000, "occupancy": 0.94},
    {"name": "Yeouido Plaza",         "type": "office",     "value_krw": 62_000_000_000, "monthly_income_krw": 245_000_000, "occupancy": 0.88},
    {"name": "Pangyo Tech Center",    "type": "office",     "value_krw": 38_000_000_000, "monthly_income_krw": 165_000_000, "occupancy": 0.96},
    {"name": "Jamsil Retail Building","type": "retail",     "value_krw": 28_000_000_000, "monthly_income_krw":  95_000_000, "occupancy": 0.82},
    {"name": "Itaewon Hotel",         "type": "hospitality","value_krw": 35_000_000_000, "monthly_income_krw": 140_000_000, "occupancy": 0.79},
    {"name": "Songdo Logistics Hub",  "type": "industrial", "value_krw": 22_000_000_000, "monthly_income_krw":  78_000_000, "occupancy": 0.91},
    {"name": "Hongdae Apartment Bldg","type": "residential","value_krw": 18_000_000_000, "monthly_income_krw":  62_000_000, "occupancy": 0.87},
    {"name": "Busan Marine Tower",    "type": "office",     "value_krw": 15_500_000_000, "monthly_income_krw":  54_000_000, "occupancy": 0.85},
]


def asset_summary() -> dict:
    """Realistic asset portfolio snapshot."""
    total_value = sum(a["value_krw"] for a in ASSETS)
    total_income = sum(a["monthly_income_krw"] for a in ASSETS)
    avg_occ = sum(a["occupancy"] for a in ASSETS) / len(ASSETS)
    annual_yield = (total_income * 12 / total_value) * 100

    contracts_active = random.randint(95, 110)
    contracts_expiring_30d = random.randint(3, 9)
    contracts_overdue = random.randint(0, 3)

    cash_balance = random.randint(2_500_000_000, 8_500_000_000)

    return {
        # Aliases used by /reports/compose/auto-daily — keep both shapes
        "portfolio": {
            "total_properties": len(ASSETS),
            "vacancy_rate": round((1 - avg_occ) * 100, 2),
            "total_value_krw": total_value,
        },
        "contracts": {
            "total": contracts_active,
            "active": contracts_active,
            "expiring_within_30d": contracts_expiring_30d,
            "overdue_payment": contracts_overdue,
        },
        "cash": {"total_balance": cash_balance, "currency": "KRW"},
        "risk_level": random.choices(["low", "medium", "high"], weights=[5, 4, 1])[0],
        # Original keys (kept for voice + UI usage)
        "total_assets": len(ASSETS),
        "portfolio_value_krw": total_value,
        "monthly_income_krw": total_income,
        "annual_yield_pct": round(annual_yield, 2),
        "average_occupancy_pct": round(avg_occ * 100, 1),
        "by_type": {
            t: sum(1 for a in ASSETS if a["type"] == t)
            for t in {a["type"] for a in ASSETS}
        },
        "top_performers": sorted(ASSETS, key=lambda a: a["occupancy"], reverse=True)[:3],
        "needs_attention": [a for a in ASSETS if a["occupancy"] < 0.85],
        "summary": f"Portfolio {total_value/1e9:.1f}B KRW · Yield {annual_yield:.2f}% · Avg occupancy {avg_occ*100:.1f}%",
    }


# ---------------------------------------------------------------------------
# REALTY — listings + market trends
# ---------------------------------------------------------------------------

REALTY_LISTINGS = [
    {"address": "Gangnam-gu, Yeoksam-dong",       "type": "office",  "price_krw": 12_500_000_000, "size_sqm": 850, "vacancy": 0.06},
    {"address": "Seocho-gu, Banpo-dong",          "type": "retail",  "price_krw":  8_200_000_000, "size_sqm": 420, "vacancy": 0.12},
    {"address": "Mapo-gu, Hongdae-ro",            "type": "mixed",   "price_krw":  6_800_000_000, "size_sqm": 380, "vacancy": 0.09},
    {"address": "Jung-gu, Myeongdong",            "type": "retail",  "price_krw": 15_000_000_000, "size_sqm": 320, "vacancy": 0.18},
    {"address": "Yongsan-gu, Itaewon",            "type": "hotel",   "price_krw": 22_000_000_000, "size_sqm": 1200, "vacancy": 0.15},
    {"address": "Songpa-gu, Jamsil",              "type": "office",  "price_krw":  9_400_000_000, "size_sqm": 620, "vacancy": 0.07},
    {"address": "Yeongdeungpo-gu, Yeouido",       "type": "office",  "price_krw": 18_500_000_000, "size_sqm": 950, "vacancy": 0.05},
    {"address": "Gangseo-gu, Magok",              "type": "industrial","price_krw":  5_200_000_000, "size_sqm": 720, "vacancy": 0.11},
]


def realty_summary() -> dict:
    """Realistic real-estate market snapshot."""
    total_value = sum(l["price_krw"] for l in REALTY_LISTINGS)
    avg_vacancy = sum(l["vacancy"] for l in REALTY_LISTINGS) / len(REALTY_LISTINGS)
    yields = [round(random.uniform(0.038, 0.072), 4) for _ in REALTY_LISTINGS]
    avg_yield = sum(yields) / len(yields) * 100

    market_trend = random.choices(["up", "stable", "down"], weights=[3, 5, 2])[0]
    return {
        # Aliases used by /reports/compose/auto-daily
        "total_listings": len(REALTY_LISTINGS),
        "avg_vacancy_pct": round(avg_vacancy * 100, 2),
        "avg_yield_pct": round(avg_yield, 2),
        "market_trend": market_trend,
        # Original keys
        "listings_count": len(REALTY_LISTINGS),
        "market_value_krw": total_value,
        "average_vacancy_pct": round(avg_vacancy * 100, 2),
        "average_yield_pct": round(avg_yield, 2),
        "by_type": {
            t: sum(1 for l in REALTY_LISTINGS if l["type"] == t)
            for t in {l["type"] for l in REALTY_LISTINGS}
        },
        "listings": [
            {**l, "yield_pct": round(yields[i] * 100, 2)}
            for i, l in enumerate(REALTY_LISTINGS)
        ],
        "high_vacancy_listings": [l for l in REALTY_LISTINGS if l["vacancy"] > 0.15],
        "trend_30d": {
            "price_change_pct":  round(random.uniform(-1.5, 2.8), 2),
            "vacancy_change_pp": round(random.uniform(-1.2, 1.5), 2),
        },
        "summary": f"{len(REALTY_LISTINGS)} listings · Avg vacancy {avg_vacancy*100:.2f}% · Avg yield {avg_yield:.2f}%",
    }


# ---------------------------------------------------------------------------
# Dispatcher — returns mock data by agent type
# ---------------------------------------------------------------------------

def get_mock_summary(agent_type: str) -> dict:
    """Return realistic mock data for an agent type."""
    if agent_type == "stock":
        return stock_summary()
    if agent_type == "asset":
        return asset_summary()
    if agent_type == "realty":
        return realty_summary()
    return {"summary": f"No mock data for agent type {agent_type}"}
