"""
Adapter registry — maps agent type to adapter class.
No hardcoded if-statements in the orchestrator.
"""

from adapters.base_adapter import BaseAdapter
from adapters.asset_adapter import AssetAdapter
from adapters.real_asset_adapter import RealAssetAdapter
from adapters.stock_adapter import StockAdapter
from adapters.realty_adapter import RealtyAdapter

ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "asset": AssetAdapter,
    "stock": StockAdapter,
    "realty": RealtyAdapter,
}

# Real agent adapters — used when agent is not mock
REAL_ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "asset": RealAssetAdapter,
}


def get_adapter(agent_type: str, agent_name: str, endpoint_url: str, is_mock: bool = True, **kwargs) -> BaseAdapter:
    """Get the right adapter. Uses real adapter if agent is not mock."""
    if not is_mock and agent_type in REAL_ADAPTER_MAP:
        adapter_cls = REAL_ADAPTER_MAP[agent_type]
    else:
        adapter_cls = ADAPTER_MAP.get(agent_type, BaseAdapter)
    return adapter_cls(agent_name=agent_name, endpoint_url=endpoint_url, **kwargs)
