"""
Adapter registry — maps agent type to adapter class.
No hardcoded if-statements in the orchestrator.

Routing priority for stock (most → least preferred):
  1. YAHOO_FINANCE_ENABLED=true in .env → YahooStockAdapter (real-time KOSPI via yfinance)
  2. agent.is_mock=False → RealStockAdapter (user's hosted Render service)
  3. fallback → StockAdapter (mock)

Routing for asset/realty:
  1. UPLOADED_DATA_ENABLED=true → CsvAssetAdapter / CsvRealtyAdapter (read latest user upload)
  2. agent.is_mock=False → RealAssetAdapter / RealRealtyAdapter
  3. fallback → AssetAdapter / RealtyAdapter (inline mock)
"""

import os
from adapters.base_adapter import BaseAdapter
from adapters.asset_adapter import AssetAdapter
from adapters.real_asset_adapter import RealAssetAdapter
from adapters.real_stock_adapter import RealStockAdapter
from adapters.real_realty_adapter import RealRealtyAdapter
from adapters.stock_adapter import StockAdapter
from adapters.realty_adapter import RealtyAdapter
from adapters.yahoo_stock_adapter import YahooStockAdapter

ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "asset": AssetAdapter,
    "stock": StockAdapter,
    "realty": RealtyAdapter,
}

# Real agent adapters — used when agent is not mock
REAL_ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "asset": RealAssetAdapter,
    "stock": RealStockAdapter,
    "realty": RealRealtyAdapter,
}


def _yahoo_enabled() -> bool:
    """Read at call time so .env changes apply without restart."""
    return os.getenv("YAHOO_FINANCE_ENABLED", "false").lower() in ("true", "1", "yes")


def _csv_enabled() -> bool:
    return os.getenv("UPLOADED_DATA_ENABLED", "false").lower() in ("true", "1", "yes")


def get_adapter(agent_type: str, agent_name: str, endpoint_url: str, is_mock: bool = True, **kwargs) -> BaseAdapter:
    """
    Get the best adapter for an agent. Routing:
    - stock + YAHOO_FINANCE_ENABLED → YahooStockAdapter
    - asset/realty + UPLOADED_DATA_ENABLED → CSV-driven adapter (lazy import)
    - is_mock=False → real adapter
    - else → mock adapter
    """
    # Stock: Yahoo Finance live data (highest priority when enabled)
    if agent_type == "stock" and _yahoo_enabled():
        return YahooStockAdapter(agent_name=agent_name, endpoint_url=endpoint_url, **kwargs)

    # Asset/Realty: user-uploaded CSV — only when an actual file exists.
    # If the upload folder is empty, fall through to real backend / mock instead of
    # silently serving mock from inside the CSV adapter.
    if _csv_enabled() and agent_type in ("asset", "realty"):
        try:
            from adapters.csv_data_adapter import CsvAssetAdapter, CsvRealtyAdapter, UPLOADS_DIR
            csv_path = UPLOADS_DIR / agent_type / "latest.csv"
            if csv_path.exists():
                cls = CsvAssetAdapter if agent_type == "asset" else CsvRealtyAdapter
                return cls(agent_name=agent_name, endpoint_url=endpoint_url, **kwargs)
        except Exception:
            pass  # fall through if CSV adapter unavailable

    if not is_mock and agent_type in REAL_ADAPTER_MAP:
        adapter_cls = REAL_ADAPTER_MAP[agent_type]
    else:
        adapter_cls = ADAPTER_MAP.get(agent_type, BaseAdapter)
    return adapter_cls(agent_name=agent_name, endpoint_url=endpoint_url, **kwargs)
