"""
VIP AI Platform — Event Bus
Redis Pub/Sub for A2A messaging. Falls back to in-memory for local dev.
Always fires local subscribers (triggers, notifications) regardless of Redis state.
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


def _fire_local_subscribers(channel: str, message: dict[str, Any]):
    """Fire all local subscribers for a channel. Always runs regardless of Redis."""
    for handler in _subscribers.get(channel, []):
        try:
            handler(message)
        except Exception as e:
            log.warning(f"event_bus: handler error on {channel}: {e}")

    for handler in _subscribers.get("*", []):
        try:
            handler({"channel": channel, **message})
        except Exception:
            pass


def publish(channel: str, message: dict[str, Any]):
    """Publish a message. Always fires local subscribers + optionally publishes to Redis."""
    # Always fire local subscribers (triggers, notifications)
    _fire_local_subscribers(channel, message)

    # Also publish to Redis if connected (for cross-process communication)
    if _use_redis and _redis_client:
        try:
            payload = json.dumps(message, default=str)
            _redis_client.publish(channel, payload)
        except Exception as e:
            log.warning(f"event_bus: Redis publish failed: {e}")

    log.info(f"event_bus: published channel={channel}", extra={"action": "event_bus.publish"})


def subscribe(channel: str, handler: Callable):
    """Subscribe to a channel with a handler function."""
    if channel not in _subscribers:
        _subscribers[channel] = []
    _subscribers[channel].append(handler)

    # If Redis is connected, also listen for messages from other processes
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
