"""
model3_federation.bus.event_bus
---------------------------------
Redis Pub/Sub wrapper for the federation event bus.

Publishes FederatedEvent objects as JSON to the channel
  sentinel:federation:events

Subscribers receive those JSON messages, deserialise them back to
FederatedEvent, and call a handler coroutine.

Graceful degradation: if Redis is unavailable the bus falls back to an
in-process asyncio.Queue so the adapters and correlation engine still
work in environments where Redis is not running (e.g. bare local dev
without docker-compose). A warning is logged on every fallback publish.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

from model3_federation.schemas.models import FederatedEvent

logger = logging.getLogger("sentinel.federation.bus")

_CHANNEL = "sentinel:federation:events"


class FederationEventBus:
    """
    Redis Pub/Sub event bus for the Model 3 federation layer.

    Usage:
        bus = FederationEventBus(redis_url="redis://localhost:6379")
        await bus.publish(event)            # from adapter callbacks
        await bus.subscribe(handler)        # from correlation engine
    """

    def __init__(self, redis_url: str = "redis://localhost:6379") -> None:
        self._redis_url = redis_url
        # In-process fallback queue — used when Redis is unavailable
        self._fallback_queue: asyncio.Queue[FederatedEvent] = asyncio.Queue(maxsize=2000)
        self._redis_available: Optional[bool] = None  # None = not yet probed

    # ── Internal helpers ────────────────────────────────────────

    async def _get_redis(self):
        """
        Return an aioredis client, or None if unavailable.
        We import aioredis lazily so the app still boots if the library
        isn't installed (it's optional for bare local dev).
        """
        try:
            import aioredis  # type: ignore
            client = aioredis.from_url(self._redis_url, decode_responses=True)
            await client.ping()
            return client
        except Exception:
            return None

    # ── Public API ──────────────────────────────────────────────

    async def publish(self, event: FederatedEvent) -> None:
        """
        Serialise event to JSON and publish to Redis channel.
        Falls back to in-process queue if Redis is unavailable.
        """
        payload = event.model_dump_json()

        # Try Redis first
        client = await self._get_redis()
        if client is not None:
            try:
                await client.publish(_CHANNEL, payload)
                await client.aclose()
                return
            except Exception as exc:
                logger.warning("Redis publish failed, using fallback queue: %s", exc)
            finally:
                try:
                    await client.aclose()
                except Exception:
                    pass

        # Fallback: in-process queue (no Redis needed)
        logger.debug("Bus fallback: enqueuing event %s", event.id)
        try:
            self._fallback_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Fallback queue full — dropping oldest event to make room.")
            try:
                self._fallback_queue.get_nowait()
                self._fallback_queue.put_nowait(event)
            except Exception:
                pass

    async def subscribe(
        self,
        handler: Callable[[FederatedEvent], Awaitable[None]],
    ) -> None:
        """
        Subscribe to the federation channel and call handler() per event.
        Runs indefinitely; designed to be started as an asyncio background task.
        Falls back to draining the in-process queue if Redis is unavailable.
        """
        client = await self._get_redis()

        if client is not None:
            logger.info("Bus subscriber connected to Redis at %s", self._redis_url)
            try:
                pubsub = client.pubsub()
                await pubsub.subscribe(_CHANNEL)
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                        event = FederatedEvent(**data)
                        await handler(event)
                    except Exception as exc:
                        logger.error("Failed to deserialise bus message: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Redis subscriber crashed: %s — switching to fallback.", exc)
            finally:
                try:
                    await client.aclose()
                except Exception:
                    pass

        # Fallback: drain in-process queue
        logger.warning(
            "Bus running in fallback (in-process) mode — Redis not available at %s.",
            self._redis_url,
        )
        while True:
            try:
                event = await asyncio.wait_for(self._fallback_queue.get(), timeout=1.0)
                await handler(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Fallback queue handler error: %s", exc)
