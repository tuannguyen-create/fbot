"""Notification review API."""
from datetime import timedelta
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.database import get_db

router = APIRouter()


def _window_start_expr(window: str) -> str:
    if window == "today":
        return "(NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date"
    if window == "7d":
        return "((NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date - INTERVAL '7 days')"
    return "((NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date - INTERVAL '30 days')"


def _event_label(event_type: str | None) -> str:
    labels = {
        "m1_alert_fired": "M1 phát hiện sớm",
        "m1_alert_confirmation": "M1 kết quả 15 phút",
        "m3_cycle_breakout": "M3 breakout",
        "m3_cycle_10d": "M3 còn 10 ngày",
        "m3_cycle_bottom": "M3 vào vùng đáy",
        "m3_daily_digest": "Tổng hợp M3 cuối ngày",
        "m1_replay_digest": "Tổng hợp M1 lịch sử",
        "m3_replay_digest": "Tổng hợp M3 lịch sử",
    }
    return labels.get(event_type or "", "Thông báo hệ thống")


@router.get("/review")
async def review_notifications(
    window: Literal["today", "7d", "30d"] = Query(default="today"),
    limit: int = Query(default=50, ge=1, le=200),
    channel: Literal["telegram", "email"] = Query(default="telegram"),
    pool: asyncpg.Pool = Depends(get_db),
):
    start_expr = _window_start_expr(window)
    telegram_configured = bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_IDS.strip())

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                n.id,
                n.channel,
                n.status,
                n.message_id,
                n.sent_at,
                n.event_type,
                n.preview_text,
                a.id AS alert_id,
                a.ticker AS alert_ticker,
                a.status AS alert_status,
                c.id AS cycle_id,
                c.ticker AS cycle_ticker,
                c.phase AS cycle_phase
            FROM notification_log n
            LEFT JOIN volume_alerts a ON a.id = n.alert_id
            LEFT JOIN cycle_events c ON c.id = n.cycle_id
            WHERE n.channel = $1
              AND (n.sent_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date >= {start_expr}
            ORDER BY n.sent_at DESC
            LIMIT $2
            """,
            channel,
            limit,
        )

        sent_count = await conn.fetchval(
            f"""
            SELECT COUNT(*) FROM notification_log
            WHERE channel=$1
              AND status='sent'
              AND (sent_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date >= {start_expr}
            """,
            channel,
        )
        failed_count = await conn.fetchval(
            f"""
            SELECT COUNT(*) FROM notification_log
            WHERE channel=$1
              AND status='failed'
              AND (sent_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date >= {start_expr}
            """,
            channel,
        )

        drafts = []
        if channel == "telegram":
            alert_rows = await conn.fetch(
                f"""
                SELECT a.id, a.ticker, a.status, a.fired_at, a.confirmed_at, a.ratio_5d, a.ratio_15m
                FROM volume_alerts a
                WHERE a.origin='live'
                  AND DATE(a.bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh') >= {start_expr}
                  AND a.status IN ('fired', 'confirmed', 'cancelled')
                  AND NOT EXISTS (
                        SELECT 1 FROM notification_log n
                        WHERE n.alert_id = a.id AND n.channel='telegram'
                  )
                ORDER BY COALESCE(a.confirmed_at, a.fired_at) DESC
                LIMIT 20
                """
            )
            for row in alert_rows:
                if row["status"] == "fired":
                    title = f"M1 live — {row['ticker']}"
                    preview = f"{row['ticker']} vừa có khối lượng bất thường, đang chờ xác nhận 15 phút."
                    event_type = "m1_alert_fired"
                    ts = row["fired_at"]
                else:
                    label = "xác nhận" if row["status"] == "confirmed" else "không xác nhận"
                    ratio = row["ratio_15m"]
                    ratio_str = f"{ratio:.2f}x" if ratio is not None else "N/A"
                    title = f"M1 kết quả — {row['ticker']}"
                    preview = f"{row['ticker']} đã {label} sau 15 phút. Tỷ lệ 15p: {ratio_str}."
                    event_type = "m1_alert_confirmation"
                    ts = row["confirmed_at"] or row["fired_at"]
                drafts.append({
                    "id": f"draft-alert-{row['id']}-{event_type}",
                    "source": "draft",
                    "channel": channel,
                    "status": "not_sent",
                    "sent_at": ts.isoformat() if ts else None,
                    "event_type": event_type,
                    "title": title,
                    "preview_text": preview,
                    "alert_id": row["id"],
                    "cycle_id": None,
                    "ticker": row["ticker"],
                    "link": f"/alerts/{row['id']}",
                })

            cycle_rows = await conn.fetch(
                f"""
                SELECT c.id, c.ticker, c.phase, c.created_at, c.breakout_date
                FROM cycle_events c
                WHERE c.origin='live'
                  AND c.breakout_date >= {start_expr}
                  AND NOT EXISTS (
                        SELECT 1 FROM notification_log n
                        WHERE n.cycle_id = c.id AND n.channel='telegram'
                  )
                ORDER BY c.created_at DESC
                LIMIT 10
                """
            )
            for row in cycle_rows:
                drafts.append({
                    "id": f"draft-cycle-{row['id']}",
                    "source": "draft",
                    "channel": channel,
                    "status": "not_sent",
                    "sent_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "event_type": "m3_cycle_breakout",
                    "title": f"M3 breakout — {row['ticker']}",
                    "preview_text": f"{row['ticker']} có breakout daily ngày {row['breakout_date']}.",
                    "alert_id": None,
                    "cycle_id": row["id"],
                    "ticker": row["ticker"],
                    "link": f"/cycles/{row['id']}",
                })

            replay_rows = await conn.fetch(
                f"""
                SELECT id, module, mode, created_count, started_at, finished_at
                FROM replay_runs
                WHERE notify_mode='digest'
                  AND status='done'
                  AND (COALESCE(finished_at, started_at) AT TIME ZONE 'Asia/Ho_Chi_Minh')::date >= {start_expr}
                ORDER BY COALESCE(finished_at, started_at) DESC
                LIMIT 10
                """
            )
            for row in replay_rows:
                finished_at = row["finished_at"] or row["started_at"]
                event_type = f"{row['module']}_replay_digest"
                drafts.append({
                    "id": f"draft-replay-{row['id']}",
                    "source": "draft",
                    "channel": channel,
                    "status": "not_sent",
                    "sent_at": finished_at.isoformat() if finished_at else None,
                    "event_type": event_type,
                    "title": _event_label(event_type),
                    "preview_text": f"Run {row['mode']} đã tạo {row['created_count']} kết quả mới.",
                    "alert_id": None,
                    "cycle_id": None,
                    "ticker": None,
                    "link": None,
                })

    items = [
        {
            "id": str(r["id"]),
            "source": "log",
            "channel": r["channel"],
            "status": r["status"],
            "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
            "event_type": r["event_type"],
            "title": (
                f"{r['alert_ticker']} — {_event_label(r['event_type'])}" if r["alert_ticker"] else
                f"{r['cycle_ticker']} — {_event_label(r['event_type'])}" if r["cycle_ticker"] else
                _event_label(r["event_type"])
            ),
            "preview_text": r["preview_text"],
            "alert_id": r["alert_id"],
            "cycle_id": r["cycle_id"],
            "ticker": r["alert_ticker"] or r["cycle_ticker"],
            "link": f"/alerts/{r['alert_id']}" if r["alert_id"] else f"/cycles/{r['cycle_id']}" if r["cycle_id"] else None,
            "message_id": r["message_id"],
        }
        for r in rows
    ]
    items.extend(drafts)
    items.sort(key=lambda x: x["sent_at"] or "", reverse=True)

    return {
        "success": True,
        "data": {
            "window": window,
            "channel": channel,
            "telegram_configured": telegram_configured,
            "sent_count": sent_count or 0,
            "failed_count": failed_count or 0,
            "draft_count": len(drafts),
            "items": items[:limit],
        },
    }
