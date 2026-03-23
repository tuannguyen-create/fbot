"""Notification service via Resend email."""
import logging
from datetime import datetime
from typing import Optional

from app.config import settings
from app.utils.timezone import format_ict, format_time_ict, to_ict
from app.utils.trading_hours import slot_to_time_str

logger = logging.getLogger(__name__)

_pool = None
_resend = None


def inject_deps(pool):
    global _pool, _resend
    _pool = pool
    try:
        import resend as resend_module
        resend_module.api_key = settings.RESEND_API_KEY
        _resend = resend_module
        logger.info("Resend initialized")
    except ImportError:
        logger.warning("resend package not installed — emails disabled")


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
    status_label = {"confirmed": "✅ Đã xác nhận (15 phút)", "cancelled": "❌ Không xác nhận", "fired": "⏳ Chờ xác nhận"}.get(alert["status"], alert["status"])

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
    predicted_bottom = cycle["predicted_bottom_date"]
    bottom_str = predicted_bottom.strftime("%d/%m/%Y") if hasattr(predicted_bottom, "strftime") else str(predicted_bottom)

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
      <td style="padding:8px; font-weight:bold;">Dự kiến phân phối</td>
      <td style="padding:8px;">{est_days} ngày giao dịch</td>
    </tr>
    <tr style="background:#f9f9f9;">
      <td style="padding:8px; font-weight:bold;">Dự kiến vùng đáy</td>
      <td style="padding:8px; color:#27ae60;"><b>{bottom_str}</b></td>
    </tr>
  </table>
  <p style="color:#666; margin-top:12px;">
    ⚠️ fbot sẽ nhắc lại 10 ngày trước vùng đáy dự kiến.
  </p>
  <p style="color:#888; font-size:0.85em;">fbot — Hệ thống cảnh báo chứng khoán tự động</p>
</body>
</html>
"""


def _render_cycle_10day_html(cycle: dict) -> str:
    ticker = cycle["ticker"]
    predicted_bottom = cycle["predicted_bottom_date"]
    bottom_str = predicted_bottom.strftime("%d/%m/%Y") if hasattr(predicted_bottom, "strftime") else str(predicted_bottom)
    days_rem = cycle["days_remaining"] or 10

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; color: #222; padding: 20px; max-width: 600px;">
  <h2 style="color:#3498db;">⏰ Nhắc nhở: Còn {days_rem} ngày đến vùng tích lũy — {ticker}</h2>
  <p>Vùng đáy dự kiến: <b style="font-size:1.2em;">{bottom_str}</b></p>
  <p style="color:#666;">
    Hãy chuẩn bị theo dõi {ticker} trong {days_rem} ngày tới.<br>
    fbot sẽ thông báo khi phát hiện dấu hiệu đáy thực sự.
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


async def _send_email(subject: str, html: str, alert_id: int = None, cycle_id: int = None):
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
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO notification_log (alert_id, cycle_id, channel, message_id, status)
                VALUES ($1, $2, 'email', $3, 'sent')
                """,
                alert_id,
                cycle_id,
                message_id,
            )
            if alert_id:
                await conn.execute(
                    "UPDATE volume_alerts SET email_sent=TRUE WHERE id=$1", alert_id
                )
        logger.info(f"Email sent: {subject} → {settings.RESEND_RECIPIENTS}")
        return message_id
    except Exception as e:
        logger.error(f"Resend failed: {e}")
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO notification_log (alert_id, cycle_id, channel, status)
                VALUES ($1, $2, 'email', 'failed')
                """,
                alert_id,
                cycle_id,
            )
        return None


async def send_volume_alert_email(alert_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM volume_alerts WHERE id=$1", alert_id)
    if not row:
        return
    alert = dict(row)
    ticker = alert["ticker"]
    slot = alert["slot"]
    time_str = slot_to_time_str(slot)
    ratio = f"{alert['ratio_5d']:.2f}" if alert["ratio_5d"] else "?"
    magic_flag = " ⚡" if alert["in_magic_window"] else ""
    subject = f"🔥 [{ticker}] KL bất thường — {time_str} ICT | {ratio}x baseline{magic_flag}"
    html = _render_volume_alert_html(alert)
    await _send_email(subject, html, alert_id=alert_id)


async def send_cycle_breakout_email(cycle_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM cycle_events WHERE id=$1", cycle_id)
    if not row:
        return
    cycle = dict(row)
    ticker = cycle["ticker"]
    subject = f"📈 [{ticker}] Breakout phát hiện — Phân phối {cycle['estimated_dist_days']} ngày"
    html = _render_cycle_breakout_html(cycle)
    await _send_email(subject, html, cycle_id=cycle_id)


async def send_cycle_10day_warning_email(cycle_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM cycle_events WHERE id=$1", cycle_id)
    if not row:
        return
    cycle = dict(row)
    ticker = cycle["ticker"]
    days_rem = cycle["days_remaining"] or 10
    subject = f"⏰ [{ticker}] Còn {days_rem} ngày đến vùng tích lũy dự kiến"
    html = _render_cycle_10day_html(cycle)
    await _send_email(subject, html, cycle_id=cycle_id)


async def send_cycle_bottom_email(cycle_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM cycle_events WHERE id=$1", cycle_id)
    if not row:
        return
    cycle = dict(row)
    ticker = cycle["ticker"]
    subject = f"🟢 [{ticker}] Vào vùng đáy — KL thấp 3 phiên liên tiếp"
    html = _render_cycle_bottom_html(cycle)
    await _send_email(subject, html, cycle_id=cycle_id)
