"""
VIP AI Platform — Event Bus
Redis Pub/Sub for A2A messaging. Falls back to in-memory for local dev.
"""

import json
import threading
from typing import Any, Callable

from services.logger import log

_subscribers: dict[str, list[Callable]] = {}
_redis_client = None
_use_redis = False


def init_event_bus(redis_url: str = "redis://localhost:6379/0"):
    """Try to connect to Redis. Falls back to in-memory if unavailable."""
    global _redis_client, _use_redis
    try:
        import redis
        r = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
        r.ping()
        _redis_client = r
        _use_redis = True
        log.info("event_bus: Redis connected", extra={"action": "event_bus.redis_connected"})
    except Exception as e:
        _use_redis = False
        log.info(f"event_bus: Redis unavailable ({e}), using in-memory bus", extra={"action": "event_bus.memory_fallback"})


def publish(channel: str, message: dict[str, Any]):
    """Publish a message to a channel."""
    payload = json.dumps(message, default=str)

    if _use_redis and _redis_client:
        _redis_client.publish(channel, payload)
        log.info(f"event_bus: published to Redis channel={channel}", extra={"action": "event_bus.publish"})
    else:
        # In-memory fallback
        for handler in _subscribers.get(channel, []):
            try:
                handler(message)
            except Exception as e:
                log.warning(f"event_bus: handler error on {channel}: {e}")

        # Also fire wildcard subscribers
        for handler in _subscribers.get("*", []):
            try:
                handler({"channel": channel, **message})
            except Exception:
                pass

    log.info(f"event_bus: published channel={channel}", extra={"action": "event_bus.publish"})


def subscribe(channel: str, handler: Callable):
    """Subscribe to a channel with a handler function."""
    if channel not in _subscribers:
        _subscribers[channel] = []
    _subscribers[channel].append(handler)

    if _use_redis and _redis_client:
        def _redis_listener():
            pubsub = _redis_client.pubsub()
            pubsub.subscribe(channel)
            for msg in pubsub.listen():
                if msg["type"] == "message":
                    try:
                        data = json.loads(msg["data"])
                        handler(data)
                    except Exception as e:
                        log.warning(f"event_bus: Redis listener error: {e}")

        t = threading.Thread(target=_redis_listener, daemon=True)
        t.start()


def is_redis_connected() -> bool:
    return _use_redis
