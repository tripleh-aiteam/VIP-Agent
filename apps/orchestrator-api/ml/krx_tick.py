"""KRX cash-equity quotation-price units for KOSPI and KOSDAQ.

Source: Korea Exchange, *Guide to Trading in the Korean Stock Market*,
Tick Size table (checked 2026-07-15):
https://global.krx.co.kr/contents/GLB/01/0109/0109000000/guide_to_trading_in_the_korean_stock_market.pdf

This applies to ordinary shares. ETFs, ETNs and ELWs have a fixed KRW 5
tick and must call these helpers with ``instrument_type="ETF"`` (or ETN/ELW).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Literal

Market = Literal["KOSPI", "KOSDAQ"]

# The ML universe is predominantly KOSPI. Keep the exceptional KOSDAQ names
# explicit until exchange metadata is available in the securities master table.
KOSDAQ_TICKERS = frozenset({"042700", "247540"})
FIXED_FIVE_WON_INSTRUMENTS = frozenset({"ETF", "ETN", "ELW"})


def market_for_ticker(ticker: str | None) -> Market:
    """Return the exchange for a tracked equity; KOSPI is the safe universe default."""
    return "KOSDAQ" if str(ticker or "").zfill(6) in KOSDAQ_TICKERS else "KOSPI"


def tick_size(price: int | float | Decimal, market: Market = "KOSPI",
              instrument_type: str = "STOCK") -> int:
    """Return the valid KRX price increment for the supplied price band."""
    if instrument_type.upper() in FIXED_FIVE_WON_INSTRUMENTS:
        return 5
    value = Decimal(str(price))
    if value < 1_000:
        return 1
    if value < 5_000:
        return 5
    if value < 10_000:
        return 10
    if value < 50_000:
        return 50
    if value < 100_000:
        return 100
    if market == "KOSDAQ":
        return 100
    if value < 500_000:
        return 500
    return 1_000


def _round(price: int | float | Decimal, market: Market, instrument_type: str,
           mode: str) -> int:
    """Round a positive price to its own KRX band, including band-boundary values."""
    value = Decimal(str(price))
    if value <= 0:
        raise ValueError("KRX quotation prices must be positive")
    unit = Decimal(tick_size(value, market, instrument_type))
    quotient = value / unit
    rounding = {"nearest": ROUND_HALF_UP, "floor": ROUND_FLOOR, "ceil": ROUND_CEILING}[mode]
    return int(quotient.to_integral_value(rounding=rounding) * unit)


def round_to_tick(price: int | float | Decimal, market: Market = "KOSPI",
                  instrument_type: str = "STOCK") -> int:
    """Round a model estimate to the nearest executable KRX quote price."""
    return _round(price, market, instrument_type, "nearest")


def floor_to_tick(price: int | float | Decimal, market: Market = "KOSPI",
                  instrument_type: str = "STOCK") -> int:
    """Round down; suitable for buy limits and downside/stop levels."""
    return _round(price, market, instrument_type, "floor")


def ceil_to_tick(price: int | float | Decimal, market: Market = "KOSPI",
                 instrument_type: str = "STOCK") -> int:
    """Round up; suitable for sell limits and upside target levels."""
    return _round(price, market, instrument_type, "ceil")
