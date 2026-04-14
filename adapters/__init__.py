"""
Adapter registry — maps agent type to adapter class.
No hardcoded if-statements in the orchestrator.
"""

from adapters.base_adapter import BaseAdapter
from adapters.asset_adapter import AssetAdapter
from adapters.stock_adapter import StockAdapter
from adapters.realty_adapter import RealtyAdapter

ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "asset": AssetAdapter,
    "stock": StockAdapter,
    "realty": RealtyAdapter,
}


def get_adapter(agent_type: str, agent_name: str, endpoint_url: str, **kwargs) -> BaseAdapter:
    """Get the right adapter for an agent type. Falls back to BaseAdapter for unknown types."""
    adapter_cls = ADAPTER_MAP.get(agent_type, BaseAdapter)
    return adapter_cls(agent_name=agent_name, endpoint_url=endpoint_url, **kwargs)
