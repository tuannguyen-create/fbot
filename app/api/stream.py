"""SSE (Server-Sent Events) endpoint for real-time alerts."""
import asyncio
import json
import logging
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# Global queue — M1/M3 push here, SSE clients consume
alert_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)


async def _event_generator():
    """Generate SSE stream. Yields events from queue, heartbeat every 30s."""
    try:
        while True:
            try:
                event = await asyncio.wait_for(alert_queue.get(), timeout=30.0)
                data = json.dumps(event["data"], default=str)
                yield f"event: {event['type']}\ndata: {data}\n\n"
            except asyncio.TimeoutError:
                yield "event: heartbeat\ndata: {}\n\n"
    except asyncio.CancelledError:
        logger.debug("SSE client disconnected")


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
