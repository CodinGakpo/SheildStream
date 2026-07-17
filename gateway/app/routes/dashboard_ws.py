"""Real-time WebSocket dashboard fan-out — Week 9 Part B.

Redis Pub/Sub (already used for policy invalidation in Part A) is repurposed
here as the fan-out bus across gateway replicas: if three dashboard clients
are spread across two replicas behind a load balancer, a replica can only
push to the WebSocket clients physically connected to *it*. The Week 8 alert
consumer's `redis.publish("dashboard:alerts", ...)` and this week's per-second
`dashboard:metrics` snapshot (see consumers/alerts/worker.py) reach every
subscribed replica, regardless of which replica a given client is attached to.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

logger = logging.getLogger("shieldstream.dashboard_ws")

router = APIRouter()

_connected_clients: set[WebSocket] = set()

CHANNELS = ("dashboard:alerts", "dashboard:metrics")


@router.websocket("/ws/dashboard")
async def dashboard_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    _connected_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # no client->server messages expected —
            # this just keeps the coroutine alive so a disconnect raises here
    except WebSocketDisconnect:
        pass
    finally:
        _connected_clients.discard(websocket)


async def fanout_loop(redis: Redis) -> None:
    """One subscriber task per gateway process, started at lifespan startup.
    A send failure to one client must never take down the broadcast for
    everyone else — each send is wrapped individually, and only the client
    that actually failed is dropped."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(*CHANNELS)
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        dead = []
        for client in _connected_clients:
            try:
                await client.send_text(message["data"])
            except Exception:
                dead.append(client)  # vanished between iteration and send
        for client in dead:
            _connected_clients.discard(client)
