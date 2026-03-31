"""Notification service via Resend email and Telegram."""
import asyncio
from collections import Counter
import html
import logging
import re
from datetime import datetime
from typing import Optional

from app.config import settings
from app.utils.timezone import format_ict, format_time_ict, to_ict
from app.utils.trading_hours import slot_to_time_str

logger = logging.getLogger(__name__)

_pool = None
_resend = None
_redis = None

def _preview_text(text: str) -> str:
    """Strip HTML tags/entities so UI can render a readable one-line preview."""
    plain = re.sub(r"<[^>]+>", "", text or "")
    plain = html.unescape(plain)
    return re.sub(r"\s+", " ", plain).strip()


async def _log_notification(
    *,
    channel: str,
    status: str,
    alert_id: int | None = None,
    cycle_id: int | None = None,
    message_id: str | None = None,
    event_type: str | None = None,
    preview_text: str | None = None,
) -> None:
    if _pool is None:
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO notification_log
                (alert_id, cycle_id, channel, message_id, status, event_type, preview_text)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            alert_id,
            cycle_id,
            channel,
            message_id,
            status,
            event_type,
            preview_text,
        )


def should_send_m1_fired_telegram(alert: dict) -> bool:
    ratio = float(alert.get("ratio_5d") or 0)
    volume = int(alert.get("volume") or 0)
    normal_ok = ratio >= settings.M1_TELEGRAM_FIRED_MIN_RATIO and volume >= settings.M1_TELEGRAM_FIRED_MIN_VOLUME
    extreme_ok = ratio >= settings.M1_TELEGRAM_EXTREME_RATIO and volume >= settings.M1_TELEGRAM_EXTREME_VOLUME
    return normal_ok or extreme_ok


def should_send_m1_confirmation_telegram(alert: dict) -> bool:
    if alert.get("status") != "confirmed":
        return False
    ratio = float(alert.get("ratio_15m") or 0)
    volume = int(alert.get("volume") or 0)
    return ratio >= settings.M1_TELEGRAM_CONFIRM_MIN_RATIO and volume >= settings.M1_TELEGRAM_CONFIRM_MIN_VOLUME


def _redis_hash_value(payload: dict, key: str, default: str = "") -> str:
    if key in payload:
        value = payload[key]
    else:
        value = payload.get(key.encode(), default)
    if isinstance(value, bytes):
        return value.decode()
    return str(value) if value is not None else default


async def _record_m1_progress(
    *,
    ticker: str,
    event_kind: str,
    event_ts: datetime,
    ratio: float,
    volume: int,
) -> None:
    if _redis is None:
        return

    trade_date = to_ict(event_ts).strftime("%Y%m%d")
    key = f"notif:m1:{event_kind}:ticker:{ticker}:{trade_date}"
    await _redis.hset(key, "ratio", str(ratio))
    await _redis.hset(key, "volume", str(volume))
    await _redis.hset(key, "ts", event_ts.isoformat())
    await _redis.expire(key, 60 * 60 * 36)


async def _should_send_m1_progressive(
    *,
    ticker: str,
    event_kind: str,
    event_ts: datetime,
    ratio: float,
    volume: int,
    ratio_multiplier: float,
    volume_multiplier: float,
) -> bool:
    if _redis is None:
        return True

    trade_date = to_ict(event_ts).strftime("%Y%m%d")
    key = f"notif:m1:{event_kind}:ticker:{ticker}:{trade_date}"
    payload = await _redis.hgetall(key)
    if not payload:
        await _record_m1_progress(
            ticker=ticker,
            event_kind=event_kind,
            event_ts=event_ts,
            ratio=ratio,
            volume=volume,
        )
        return True

    prev_ratio = float(_redis_hash_value(payload, "ratio", "0") or 0)
    prev_volume = int(float(_redis_hash_value(payload, "volume", "0") or 0))
    stronger = ratio >= prev_ratio * ratio_multiplier or volume >= prev_volume * volume_multiplier
    if stronger:
        await _record_m1_progress(
            ticker=ticker,
            event_kind=event_kind,
            event_ts=event_ts,
            ratio=ratio,
            volume=volume,
        )
    return stronger


def inject_deps(pool, redis=None):
    global _pool, _resend, _redis
    _pool = pool
    _redis = redis
    try:
        import resend as resend_module
        resend_module.api_key = settings.RESEND_API_KEY
        _resend = resend_module
        logger.info("Resend initialized")
    except ImportError:
        logger.warning("resend package not installed — emails disabled")


async def _send_telegram(
    text: str,
    alert_id: int | None = None,
    cycle_id: int | None = None,
    event_type: str | None = None,
) -> None:
    """Send HTML-formatted message to all configured Telegram chat IDs."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return
    chat_ids = [c.strip() for c in settings.TELEGRAM_CHAT_IDS.split(",") if c.strip()]
    if not chat_ids:
        return

    import httpx

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        for chat_id in chat_ids:
            try:
                response = await client.post(url, json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                response.raise_for_status()
                body = response.json()
                message_id = str(body.get("result", {}).get("message_id", ""))
                await _log_notification(
                    alert_id=alert_id,
                    cycle_id=cycle_id,
                    channel="telegram",
                    message_id=message_id,
                    status="sent",
                    event_type=event_type,
                    preview_text=_preview_text(text),
                )
            except Exception as e:
                logger.error(f"Telegram send failed → {chat_id}: {e}")
                await _log_notification(
                    alert_id=alert_id,
                    cycle_id=cycle_id,
                    channel="telegram",
                    status="failed",
                    event_type=event_type,
                    preview_text=_preview_text(text),
                )


def _format_number(n: Optional[int]) -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n/1_000:.0f}K"
    return f"{n:,}"


def _render_volume_alert_html(alert: dict) -> str:
    ticker = alert["ticker"]
    slot = alert["slot"]
    time_str = slot_to_time_str(slot)
    fired_ict = format_ict(alert["fired_at"], "%d/%m/%Y %H:%M")
    volume_str = _format_number(alert["volume"])
    baseline_str = _format_number(alert["baseline_5d"])
    ratio = f"{alert['ratio_5d']:.2f}" if alert["ratio_5d"] else "N/A"
    bu_pct = f"{alert['bu_pct']:.1f}%" if alert["bu_pct"] is not None else "N/A"
    foreign_net_str = _format_number(alert["foreign_net"])
    magic_label = "✅ Đúng (Magic Window)" if alert["in_magic_window"] else "Không"
    status_label = {
        "confirmed": "✅ Đã xác nhận (15 phút)",
        "cancelled": "❌ Không xác nhận",
        "fired": "⏳ Chờ xác nhận",
        "expired": "🕓 Hết phiên, không đủ 15 phút",
    }.get(alert["status"], alert["status"])

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; color: #222; padding: 20px; max-width: 600px;">
  <h2 style="color:#e74c3c; border-bottom: 2px solid #e74c3c; padding-bottom: 8px;">
    ⚠️ Cảnh báo Khối lượng — {ticker}
  </h2>
  <table style="width:100%; border-collapse: collapse; margin-top: 12px;">
    <tr style="background:#f9f9f9;">
      <td style="padding:8px; font-weight:bold; width:45%;">Thời điểm</td>
      <td style="padding:8px;"><b>{time_str} ICT</b> ({fired_ict})</td>
    </tr>
    <tr>
      <td style="padding:8px; font-weight:bold;">Khối lượng</td>
      <td style="padding:8px; font-size:1.1em;"><b>{volume_str}</b></td>
    </tr>
    <tr style="background:#f9f9f9;">
      <td style="padding:8px; font-weight:bold;">Baseline (5 ngày)</td>
      <td style="padding:8px;">{baseline_str}</td>
    </tr>
    <tr>
      <td style="padding:8px; font-weight:bold;">Tỷ lệ KL/Baseline</td>
      <td style="padding:8px; color:#e74c3c; font-size:1.2em;"><b>{ratio}x</b></td>
    </tr>
    <tr style="background:#f9f9f9;">
      <td style="padding:8px; font-weight:bold;">BU% (Bên mua)</td>
      <td style="padding:8px;">{bu_pct}</td>
    </tr>
    <tr>
      <td style="padding:8px; font-weight:bold;">Foreign Net</td>
      <td style="padding:8px;">{foreign_net_str}</td>
    </tr>
    <tr style="background:#f9f9f9;">
      <td style="padding:8px; font-weight:bold;">Magic Window</td>
      <td style="padding:8px;">{magic_label}</td>
    </tr>
    <tr>
      <td style="padding:8px; font-weight:bold;">Trạng thái</td>
      <td style="padding:8px;">{status_label}</td>
    </tr>
  </table>
  <p style="color:#888; font-size:0.85em; margin-top: 16px;">
    fbot — Hệ thống cảnh báo chứng khoán tự động
  </p>
</body>
</html>
"""


def _render_cycle_breakout_html(cycle: dict) -> str:
    ticker = cycle["ticker"]
    breakout_date = cycle["breakout_date"].strftime("%d/%m/%Y") if hasattr(cycle["breakout_date"], "strftime") else str(cycle["breakout_date"])
    peak_vol = _format_number(cycle["peak_volume"])
    price = f"{cycle['breakout_price']:,.0f}đ" if cycle["breakout_price"] else "N/A"
    est_days = cycle["estimated_dist_days"] or 20
    game_type = cycle.get("game_type") or "—"
    phase_reason = cycle.get("phase_reason") or ""
    zone_low = f"{cycle['breakout_zone_low']:,.0f}đ" if cycle.get("breakout_zone_low") else "N/A"

    rw_start = cycle.get("rewatch_window_start")
    rw_end   = cycle.get("rewatch_window_end")
    rw_start_str = rw_start.strftime("%d/%m/%Y") if hasattr(rw_start, "strftime") else str(rw_start or "—")
    rw_end_str   = rw_end.strftime("%d/%m/%Y")   if hasattr(rw_end,   "strftime") else str(rw_end or "—")

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; color: #222; padding: 20px; max-width: 600px;">
  <h2 style="color:#f39c12; border-bottom: 2px solid #f39c12; padding-bottom: 8px;">
    📈 Breakout phát hiện — {ticker}
  </h2>
  <table style="width:100%; border-collapse: collapse; margin-top: 12px;">
    <tr style="background:#f9f9f9;">
      <td style="padding:8px; font-weight:bold; width:50%;">Ngày Breakout</td>
      <td style="padding:8px;"><b>{breakout_date}</b></td>
    </tr>
    <tr>
      <td style="padding:8px; font-weight:bold;">Giá Breakout</td>
      <td style="padding:8px;">{price}</td>
    </tr>
    <tr style="background:#f9f9f9;">
      <td style="padding:8px; font-weight:bold;">Volume đỉnh</td>
      <td style="padding:8px;">{peak_vol}</td>
    </tr>
    <tr>
      <td style="padding:8px; font-weight:bold;">Loại game</td>
      <td style="padding:8px;">{game_type}</td>
    </tr>
    <tr style="background:#f9f9f9;">
      <td style="padding:8px; font-weight:bold;">Tín hiệu</td>
      <td style="padding:8px;">{phase_reason}</td>
    </tr>
    <tr>
      <td style="padding:8px; font-weight:bold;">Dự kiến phân phối</td>
      <td style="padding:8px;">{est_days} ngày giao dịch</td>
    </tr>
    <tr style="background:#f9f9f9;">
      <td style="padding:8px; font-weight:bold;">Cửa sổ quan sát</td>
      <td style="padding:8px; color:#27ae60;"><b>{rw_start_str} → {rw_end_str}</b></td>
    </tr>
    <tr>
      <td style="padding:8px; font-weight:bold;">Ngưỡng vô hiệu hóa</td>
      <td style="padding:8px; color:#e74c3c;">&lt; {zone_low}</td>
    </tr>
  </table>
  <p style="color:#666; margin-top:12px;">
    ⚠️ fbot sẽ cảnh báo khi tiến gần cửa sổ quan sát và khi xuất hiện dấu hiệu tạo đáy.
  </p>
  <p style="color:#888; font-size:0.85em;">fbot — Hệ thống cảnh báo chứng khoán tự động</p>
</body>
</html>
"""


def _render_cycle_10day_html(cycle: dict) -> str:
    ticker = cycle["ticker"]
    days_rem = cycle["days_remaining"] or 10
    rw_start = cycle.get("rewatch_window_start")
    rw_start_str = rw_start.strftime("%d/%m/%Y") if hasattr(rw_start, "strftime") else str(rw_start or "—")

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; color: #222; padding: 20px; max-width: 600px;">
  <h2 style="color:#3498db;">⏰ Sắp vào cửa sổ quan sát — {ticker}</h2>
  <p>Còn khoảng <b>{days_rem} ngày giao dịch</b> trước khi mở cửa sổ quan sát.</p>
  <p>Cửa sổ quan sát dự kiến mở: <b style="font-size:1.2em;">{rw_start_str}</b></p>
  <p style="color:#666;">
    Hãy chuẩn bị theo dõi {ticker} trong {days_rem} ngày tới.<br>
    fbot sẽ thông báo khi phát hiện dấu hiệu tạo đáy/tích lũy.
  </p>
  <p style="color:#888; font-size:0.85em;">fbot — Hệ thống cảnh báo chứng khoán tự động</p>
</body>
</html>
"""


def _render_cycle_bottom_html(cycle: dict) -> str:
    ticker = cycle["ticker"]
    elapsed = cycle["trading_days_elapsed"] or 0

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; color: #222; padding: 20px; max-width: 600px;">
  <h2 style="color:#27ae60;">🟢 Vào vùng đáy — {ticker}</h2>
  <p>
    {ticker} đã có <b>3 phiên liên tiếp khối lượng thấp (&lt; 50% MA20)</b><br>
    sau {elapsed} ngày phân phối.
  </p>
  <p style="color:#e74c3c; font-weight:bold;">
    ⚡ Đây có thể là thời điểm tích lũy. Theo dõi kỹ volume phiên tiếp theo.
  </p>
  <p style="color:#888; font-size:0.85em;">fbot — Hệ thống cảnh báo chứng khoán tự động</p>
</body>
</html>
"""


async def _send_email(
    subject: str,
    html: str,
    alert_id: int = None,
    cycle_id: int = None,
    event_type: str | None = None,
    preview_text: str | None = None,
):
    if _resend is None:
        logger.warning(f"Email skipped (Resend not configured): {subject}")
        return None
    try:
        import asyncio
        params = {
            "from": settings.RESEND_FROM,
            "to": settings.RESEND_RECIPIENTS,
            "subject": subject,
            "html": html,
        }
        # Run sync SDK call in thread executor to avoid blocking the event loop
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _resend.Emails.send(params)
        )
        message_id = result.get("id")

        # Log to DB
        await _log_notification(
            alert_id=alert_id,
            cycle_id=cycle_id,
            channel="email",
            message_id=message_id,
            status="sent",
            event_type=event_type,
            preview_text=preview_text or subject,
        )
        if alert_id:
            async with _pool.acquire() as conn:
                await conn.execute(
                    "UPDATE volume_alerts SET email_sent=TRUE WHERE id=$1", alert_id
                )
        logger.info(f"Email sent: {subject} → {settings.RESEND_RECIPIENTS}")
        return message_id
    except Exception as e:
        logger.error(f"Resend failed: {e}")
        await _log_notification(
            alert_id=alert_id,
            cycle_id=cycle_id,
            channel="email",
            status="failed",
            event_type=event_type,
            preview_text=preview_text or subject,
        )
        return None


async def send_volume_alert_email(alert_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM volume_alerts WHERE id=$1", alert_id)
    if not row:
        return
    alert = dict(row)
    if alert.get("status") == "expired":
        return
    ticker = alert["ticker"]
    slot = alert["slot"]
    time_str = slot_to_time_str(slot)
    ratio = f"{alert['ratio_5d']:.2f}" if alert["ratio_5d"] else "?"
    magic_flag = " ⚡" if alert["in_magic_window"] else ""
    subject = f"🔥 [{ticker}] KL bất thường — {time_str} ICT | {ratio}x baseline{magic_flag}"
    html = _render_volume_alert_html(alert)

    vol_str = _format_number(alert["volume"])
    bu_str = f"{alert['bu_pct']:.0f}%" if alert["bu_pct"] is not None else "N/A"
    magic_line = "⚡ Magic window ✅\n" if alert["in_magic_window"] else ""
    app_link = f"{settings.FRONTEND_URL}/alerts/{alert_id}"
    tg_text = (
        f"🔥 <b>{ticker}</b> — KL bất thường\n"
        f"📊 <b>{time_str} ICT</b> | <b>{ratio}x</b> baseline\n"
        f"💰 Vol: {vol_str} | BU%: {bu_str}\n"
        f"{magic_line}"
        f"<a href='{app_link}'>Xem alert →</a>"
    )
    send_tasks = [
        _send_email(
            subject,
            html,
            alert_id=alert_id,
            event_type="m1_alert_fired",
            preview_text=_preview_text(tg_text),
        )
    ]
    if should_send_m1_fired_telegram(alert):
        fired_at = alert.get("fired_at") or alert.get("bar_time")
        if isinstance(fired_at, str):
            fired_at = datetime.fromisoformat(fired_at.replace("Z", "+00:00"))
        if fired_at and await _should_send_m1_progressive(
            ticker=ticker,
            event_kind="fired",
            event_ts=fired_at,
            ratio=float(alert.get("ratio_5d") or 0),
            volume=int(alert.get("volume") or 0),
            ratio_multiplier=settings.M1_TELEGRAM_FIRED_REPEAT_RATIO_MULTIPLIER,
            volume_multiplier=settings.M1_TELEGRAM_FIRED_REPEAT_VOLUME_MULTIPLIER,
        ):
            send_tasks.append(_send_telegram(tg_text, alert_id=alert_id, event_type="m1_alert_fired"))
        else:
            logger.info("M1 fired Telegram suppressed by progression rule: %s alert_id=%s", ticker, alert_id)
    else:
        logger.info("M1 fired Telegram suppressed by policy: %s alert_id=%s", ticker, alert_id)
    await asyncio.gather(
        *send_tasks,
    )


async def send_volume_alert_confirmation(alert_id: int):
    """Telegram follow-up when the 15-minute M1 confirmation settles."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM volume_alerts WHERE id=$1", alert_id)
    if not row:
        return

    alert = dict(row)
    status = alert.get("status")
    if status != "confirmed":
        logger.info("M1 confirmation Telegram skipped: %s alert_id=%s status=%s", alert.get("ticker"), alert_id, status)
        return
    if not should_send_m1_confirmation_telegram(alert):
        logger.info("M1 confirmation Telegram suppressed by policy: %s alert_id=%s", alert.get("ticker"), alert_id)
        return

    ticker = alert["ticker"]
    slot = alert["slot"]
    time_str = slot_to_time_str(slot)
    ratio = alert.get("ratio_15m")
    ratio_str = f"{ratio:.2f}x" if ratio is not None else "N/A"
    features = alert.get("features") or {}
    if isinstance(features, str):
        try:
            features = json.loads(features)
        except Exception:
            features = {}
    confirm_window_minutes = features.get("confirm_window_minutes")
    confirm_window_target = features.get("confirm_window_target_minutes") or 15
    window_label = (
        f"{confirm_window_minutes}/{confirm_window_target}p"
        if confirm_window_minutes and confirm_window_minutes < confirm_window_target
        else "15p"
    )
    icon = "✅" if status == "confirmed" else "⚪"
    label = (
        f"Xác nhận M1 {window_label}" if status == "confirmed"
        else f"Không xác nhận M1 {window_label}"
    )
    app_link = f"{settings.FRONTEND_URL}/alerts/{alert_id}"

    text = (
        f"{icon} <b>{ticker}</b> — {label}\n"
        f"⏱ Alert gốc: <b>{time_str} ICT</b>\n"
        f"📊 Tỷ lệ {window_label}: <b>{ratio_str}</b>\n"
        f"<a href='{app_link}'>Xem alert →</a>"
    )
    confirmed_at = alert.get("confirmed_at") or alert.get("bar_time") or alert.get("fired_at")
    if isinstance(confirmed_at, str):
        confirmed_at = datetime.fromisoformat(confirmed_at.replace("Z", "+00:00"))
    if confirmed_at and not await _should_send_m1_progressive(
        ticker=ticker,
        event_kind="confirm",
        event_ts=confirmed_at,
        ratio=float(alert.get("ratio_15m") or 0),
        volume=int(alert.get("volume") or 0),
        ratio_multiplier=settings.M1_TELEGRAM_CONFIRM_REPEAT_RATIO_MULTIPLIER,
        volume_multiplier=settings.M1_TELEGRAM_CONFIRM_REPEAT_VOLUME_MULTIPLIER,
    ):
        logger.info("M1 confirmation Telegram suppressed by progression rule: %s alert_id=%s", ticker, alert_id)
        return
    await _send_telegram(text, alert_id=alert_id, event_type="m1_alert_confirmation")


async def send_cycle_breakout_email(cycle_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM cycle_events WHERE id=$1", cycle_id)
    if not row:
        return
    cycle = dict(row)
    ticker = cycle["ticker"]
    subject = f"📈 [{ticker}] Breakout phát hiện — Phân phối {cycle['estimated_dist_days']} ngày"
    html = _render_cycle_breakout_html(cycle)

    price = f"{cycle['breakout_price']:,.0f}đ" if cycle.get("breakout_price") else "N/A"
    game = cycle.get("game_type") or "—"
    reason = cycle.get("phase_reason") or ""
    rw_start = cycle.get("rewatch_window_start")
    rw_end   = cycle.get("rewatch_window_end")
    rw_s = rw_start.strftime("%d/%m") if hasattr(rw_start, "strftime") else str(rw_start or "—")
    rw_e = rw_end.strftime("%d/%m/%Y") if hasattr(rw_end, "strftime") else str(rw_end or "—")
    zone_low = cycle.get("breakout_zone_low")
    zone_str = f"{zone_low:,.0f}đ" if zone_low else "N/A"
    app_link = f"{settings.FRONTEND_URL}/cycles/{cycle_id}"
    tg_text = (
        f"📈 <b>{ticker}</b> — Breakout phát hiện\n"
        f"🎯 Game: {game}\n"
        f"💰 Giá: {price} | {reason}\n"
        f"📅 Cửa sổ: {rw_s} → {rw_e}\n"
        f"⚠️ Vô hiệu &lt; {zone_str}\n"
        f"<a href='{app_link}'>Xem cycle →</a>"
    )
    await asyncio.gather(
        _send_email(
            subject,
            html,
            cycle_id=cycle_id,
            event_type="m3_cycle_breakout",
            preview_text=_preview_text(tg_text),
        ),
        _send_telegram(tg_text, cycle_id=cycle_id, event_type="m3_cycle_breakout"),
    )


async def send_cycle_10day_warning_email(cycle_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM cycle_events WHERE id=$1", cycle_id)
    if not row:
        return
    cycle = dict(row)
    ticker = cycle["ticker"]
    days_rem = cycle["days_remaining"] or 10
    subject = f"⏰ [{ticker}] Còn {days_rem} ngày đến cửa sổ quan sát"
    html = _render_cycle_10day_html(cycle)

    rw_start = cycle.get("rewatch_window_start")
    rw_s = rw_start.strftime("%d/%m/%Y") if hasattr(rw_start, "strftime") else str(rw_start or "—")
    app_link = f"{settings.FRONTEND_URL}/cycles/{cycle_id}"
    tg_text = (
        f"⏰ <b>{ticker}</b> — Sắp vào cửa sổ quan sát\n"
        f"Còn ~{days_rem} ngày GD\n"
        f"Cửa sổ mở: {rw_s}\n"
        f"<a href='{app_link}'>Theo dõi →</a>"
    )
    await asyncio.gather(
        _send_email(
            subject,
            html,
            cycle_id=cycle_id,
            event_type="m3_cycle_10d",
            preview_text=_preview_text(tg_text),
        ),
        _send_telegram(tg_text, cycle_id=cycle_id, event_type="m3_cycle_10d"),
    )


async def send_cycle_bottom_email(cycle_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM cycle_events WHERE id=$1", cycle_id)
    if not row:
        return
    cycle = dict(row)
    ticker = cycle["ticker"]
    elapsed = cycle.get("trading_days_elapsed") or 0
    subject = f"🟢 [{ticker}] Tín hiệu tạo đáy — KL thấp 3 phiên liên tiếp"
    html = _render_cycle_bottom_html(cycle)

    app_link = f"{settings.FRONTEND_URL}/cycles/{cycle_id}"
    tg_text = (
        f"🟢 <b>{ticker}</b> — Tín hiệu tạo đáy\n"
        f"KL thấp 3 phiên liên tiếp (sau {elapsed} ngày phân phối)\n"
        f"⚡ Quan sát kỹ phiên tiếp theo\n"
        f"<a href='{app_link}'>Xem cycle →</a>"
    )
    await asyncio.gather(
        _send_email(
            subject,
            html,
            cycle_id=cycle_id,
            event_type="m3_cycle_bottom",
            preview_text=_preview_text(tg_text),
        ),
        _send_telegram(tg_text, cycle_id=cycle_id, event_type="m3_cycle_bottom"),
    )


async def send_m3_daily_digest(trade_date, summary: dict) -> None:
    """Telegram digest for M3 daily scheduler results."""
    breakouts = summary.get("breakouts", [])
    warnings = summary.get("ten_day_warnings", [])
    bottoms = summary.get("bottoming_candidates", [])
    invalidations = summary.get("invalidations", [])

    if not any([breakouts, warnings, bottoms, invalidations]):
        return

    date_str = trade_date.strftime("%d/%m/%Y") if hasattr(trade_date, "strftime") else str(trade_date)
    lines = [f"<b>📘 M3 DAILY {date_str}</b>"]

    if breakouts:
        lines.append(f"📈 Breakout mới: {len(breakouts)}")
        for item in breakouts[:5]:
            lines.append(
                f"  • {item['ticker']} {item['vol_ratio']:.1f}x, +{item['price_change_pct']:.1f}%"
            )
        if len(breakouts) > 5:
            lines.append(f"  … +{len(breakouts) - 5} mã khác")

    if warnings:
        lines.append(f"⏰ Sắp vào cửa sổ: {len(warnings)}")
        for item in warnings[:5]:
            lines.append(f"  • {item['ticker']} còn ~{item['days_remaining']} ngày GD")
        if len(warnings) > 5:
            lines.append(f"  … +{len(warnings) - 5} mã khác")

    if bottoms:
        lines.append(f"🟢 Tín hiệu tạo đáy: {len(bottoms)}")
        for item in bottoms[:5]:
            lines.append(f"  • {item['ticker']} sau {item['trading_days_elapsed']} ngày phân phối")
        if len(bottoms) > 5:
            lines.append(f"  … +{len(bottoms) - 5} mã khác")

    if invalidations:
        lines.append(f"🔴 Mất vùng breakout: {len(invalidations)}")
        for item in invalidations[:5]:
            lines.append(f"  • {item['ticker']}")
        if len(invalidations) > 5:
            lines.append(f"  … +{len(invalidations) - 5} mã khác")

    lines.append(f"<a href='{settings.FRONTEND_URL}/cycles'>Xem M3 →</a>")
    text = "\n".join(lines)
    await _send_telegram(text, event_type="m3_daily_digest")


# ── Replay digest notifications ────────────────────────────────────────────

async def send_m1_replay_digest(
    run_id: str,
    days: int,
    hits: list,
    created: int,
    mode: str,
) -> None:
    """Telegram summary digest for M1 historical replay — never per-item."""
    if not hits:
        return

    mode_map = {"bootstrap": "BOOTSTRAP", "recovery": "KHÔI PHỤC", "manual": "THỦ CÔNG"}
    label = mode_map.get(mode, mode.upper())

    tickers = sorted({h["ticker"] for h in hits})
    lines = []
    for t in tickers[:5]:
        n = sum(1 for h in hits if h["ticker"] == t)
        lines.append(f"  • {t}: {n} hit")
    if len(tickers) > 5:
        lines.append(f"  … +{len(tickers) - 5} mã khác")

    text = (
        f"<b>📊 M1 {label} {days} ngày</b>\n"
        f"Tạo mới: {created} alert | Tổng hits: {len(hits)}\n"
        + "\n".join(lines)
        + f"\n<i>Run: {run_id[:8]}</i>"
    )
    await _send_telegram(text, event_type="m1_replay_digest")


async def send_m3_replay_digest(
    run_id: str,
    days: int,
    candidates: list,
    created: int,
    mode: str,
) -> None:
    """Telegram summary digest for M3 historical replay — never per-cycle."""
    mode_map = {"bootstrap": "BOOTSTRAP", "recovery": "KHÔI PHỤC", "manual": "THỦ CÔNG"}
    label = mode_map.get(mode, mode.upper())
    repeat_counts = Counter(c["ticker"] for c in candidates)

    highlight = [c for c in candidates if c.get("created")] or candidates
    lines = []
    for c in highlight[:5]:
        lines.append(
            f"  • {c['ticker']} {c['breakout_date']} "
            f"({c['vol_ratio']}x, +{c['price_change_pct']}%)"
        )
    if len(highlight) > 5:
        lines.append(f"  … +{len(highlight) - 5} mã khác")

    repeat_lines = []
    repeated = [(ticker, count) for ticker, count in repeat_counts.items() if count > 1]
    repeated.sort(key=lambda x: (-x[1], x[0]))
    for ticker, count in repeated[:5]:
        repeat_lines.append(f"  • {ticker}: {count} breakout / {days} ngày")
    if len(repeated) > 5:
        repeat_lines.append(f"  … +{len(repeated) - 5} mã khác")

    repeat_section = ""
    if repeat_lines:
        repeat_section = "🔁 Lặp nhiều:\n" + "\n".join(repeat_lines) + "\n"

    text = (
        f"<b>📈 M3 {label} {days} ngày</b>\n"
        f"Tạo mới: {created} cycle | Tổng candidates: {len(candidates)}\n"
        + repeat_section
        + ("Nổi bật:\n" + "\n".join(lines) if lines else "  (không có candidate)")
        + f"\n<i>Run: {run_id[:8]}</i>"
    )
    await _send_telegram(text, event_type="m3_replay_digest")
