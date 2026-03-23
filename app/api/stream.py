"""SSE (Server-Sent Events) endpoint for real-time alerts."""
import asyncio
import json
import logging
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# Global queue — M1/M3 push here, broadcaster fans out to subscribers
alert_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

# Per-client subscriber queues
_subscribers: list[asyncio.Queue] = []


async def broadcaster():
    """Fan-out events from alert_queue to all per-client subscriber queues."""
    while True:
        try:
            event = await alert_queue.get()
            dead = []
            for q in list(_subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass
        except Exception as e:
            logger.error(f"Broadcaster error: {e}")


async def _event_generator():
    """Generate SSE stream per client. Each client gets its own queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.append(q)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                data = json.dumps(event["data"], default=str)
                yield f"event: {event['type']}\ndata: {data}\n\n"
            except asyncio.TimeoutError:
                yield "event: heartbeat\ndata: {}\n\n"
    except asyncio.CancelledError:
        logger.debug("SSE client disconnected")
    finally:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


@router.get("/alerts", summary="Real-time alert stream (SSE)")
async def stream_alerts():
    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
