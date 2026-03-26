"""Module 1: Volume Scanner Alert Engine."""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from app.config import settings
from app.utils.timezone import to_ict
from app.utils.trading_hours import get_slot, is_magic_window, is_trading_hours
from app.services import baseline_service, notification

logger = logging.getLogger(__name__)

_pool = None
_redis = None
# SSE broadcast queue — injected from stream module
_alert_queue: Optional[asyncio.Queue] = None


def inject_deps(pool, redis, alert_queue: asyncio.Queue = None):
    global _pool, _redis, _alert_queue
    _pool = pool
    _redis = redis
    if alert_queue is not None:
        _alert_queue = alert_queue


# In-memory: pending 15-min confirmations
# key = ticker, value = {alert_id, slot, confirm_by_slot}
_pending_confirms: dict[str, dict] = {}


async def process(bar: dict, is_partial: bool = False):
    """
    Process a single 1-minute bar from FiinQuantX.
    bar = {ticker, bar_time (UTC ISO), open, high, low, close, volume, bu, sd, fb, fs, fn}

    is_partial=True: called for mid-minute tick snapshots (early spike detection).
    Skips _check_confirmations() to prevent the cumulative minute volume from
    being added to the 15-min confirm accumulator on every 15-s partial call,
    which would inflate the ratio 2-4x and cause false confirmations.
    """
    ticker = bar["ticker"]
    try:
        bar_time_utc: datetime = bar["bar_time"]
        if isinstance(bar_time_utc, str):
            bar_time_utc = datetime.fromisoformat(bar_time_utc.replace("Z", "+00:00"))
        bar_time_ict = to_ict(bar_time_utc)

        slot = get_slot(bar_time_ict.time())
        if slot is None:
            return

        baseline = await baseline_service.get_baseline(ticker, slot)
        if baseline is None:
            return
        avg_5d = baseline.get("avg_5d", 0)
        if not avg_5d or avg_5d == 0:
            return

        volume = bar["volume"]
        in_magic = is_magic_window(bar_time_ict.time())
        threshold = settings.THRESHOLD_MAGIC if in_magic else settings.THRESHOLD_NORMAL

        # Rate projection: if FiinQuantX sends mid-minute running updates
        # (bar_time.second > 0), project current volume to full minute.
        # Allows early detection at ~10-20s instead of waiting for bar close.
        elapsed_seconds = bar_time_ict.second
        if elapsed_seconds >= 10:
            projected_volume = int(volume * (60 / elapsed_seconds))
            ratio = projected_volume / avg_5d
        else:
            ratio = volume / avg_5d

        if ratio >= threshold:
            await _fire_alert(ticker, bar, slot, ratio, baseline, in_magic, bar_time_ict)

        # Check pending 15-min confirmations — skip for partial bars (Fix 2).
        # Only completed bars should accumulate confirm volume.
        if not is_partial:
            await _check_confirmations(ticker, bar, slot)

    except Exception as e:
        logger.error(f"M1 process error for {ticker}: {e}", exc_info=True)


# ── M1 Quality Layer ───────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> Optional[list[float]]:
    """EMA over values (oldest-first). Returns None if insufficient data."""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _calc_macd(
    closes_newest_first: list[float],
) -> tuple[Optional[float], Optional[bool]]:
    """MACD(12,26,9) histogram from close prices newest-first.
    Returns (hist_now rounded 4dp, hist_rising bool) or (None, None).
    Needs ≥34 bars for a reliable signal.
    """
    if len(closes_newest_first) < 34:
        return None, None
    closes = list(reversed(closes_newest_first))  # oldest-first for EMA
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    if ema12 is None or ema26 is None:
        return None, None
    # ema12[k] covers close at position 11+k; ema26[k] covers close at position 25+k.
    # They align when ema12[14+k] and ema26[k] both cover close[25+k].
    macd_line = [e12 - e26 for e12, e26 in zip(ema12[14:], ema26)]
    signal = _ema(macd_line, 9)
    if signal is None or len(signal) < 2:
        return None, None
    hist_now  = round(macd_line[-1] - signal[-1], 4)
    hist_prev = round(macd_line[-2] - signal[-2], 4)
    return hist_now, hist_now > hist_prev


def compute_m1_features(bar: dict, recent_bars: list[dict]) -> dict:
    """Compute M1 quality features from trigger bar + up to 50 preceding 1m bars.

    Parameters
    ----------
    bar : dict
        The trigger bar (open, high, low, close, volume, bu, sd).
    recent_bars : list[dict]
        Recent bars ordered newest-first (index 0 = bar immediately before trigger).
        Expected up to 50 bars fetched from intraday_1m.

    Returns
    -------
    dict with keys: body_pct, upper_shadow_pct, lower_shadow_pct, close_pos,
    strong_bull_candle, avg_vol_20, avg_vol_50, range_pct, is_sideways_base,
    ma10, ma20, price_above_ma10, ma_stack_up, macd_hist, macd_hist_rising,
    quality_score (0-100), quality_reason (str).
    """
    o = float(bar.get("open") or 0)
    h = float(bar.get("high") or 0)
    lo = float(bar.get("low") or 0)
    c = float(bar.get("close") or 0)

    candle_range = h - lo
    body = abs(c - o)
    upper_shadow = h - max(c, o)

    eps = 1e-9
    body_pct         = round(body / (candle_range + eps) * 100, 1)
    upper_shadow_pct = round(upper_shadow / (candle_range + eps) * 100, 1)
    lower_shadow_pct = round(100 - body_pct - upper_shadow_pct, 1)
    close_pos        = round((c - lo) / (candle_range + eps) * 100, 1)

    # Strong bull: body ≥50%, close in upper third, green candle
    strong_bull_candle = bool(body_pct >= 50.0 and close_pos >= 67.0 and c > o)

    # Volume regime using recent bars (newest-first slices)
    vols_20 = [float(b.get("volume") or 0) for b in recent_bars[:20]]
    vols_50 = [float(b.get("volume") or 0) for b in recent_bars[:50]]
    avg_vol_20 = sum(vols_20) / len(vols_20) if vols_20 else 0.0
    avg_vol_50 = sum(vols_50) / len(vols_50) if vols_50 else 0.0

    # Sideways base: last 20 bars' close range < 3% AND avg vol declining
    closes_20 = [float(b.get("close") or 0) for b in recent_bars[:20] if b.get("close")]
    if closes_20:
        ph = max(closes_20)
        pl = min(closes_20)
        mid = (ph + pl) / 2
        range_pct = round((ph - pl) / (mid + eps) * 100, 2)
    else:
        range_pct = 0.0

    is_sideways_base = bool(
        range_pct > 0
        and range_pct < 3.0
        and avg_vol_20 > 0
        and avg_vol_20 <= avg_vol_50 * 0.8
    )

    # MA alignment: build closes list newest-first with trigger bar prepended
    closes_for_ma = [c] + [float(b.get("close") or 0) for b in recent_bars[:49]]
    ma10 = round(sum(closes_for_ma[:10]) / 10, 2) if len(closes_for_ma) >= 10 else None
    ma20 = round(sum(closes_for_ma[:20]) / 20, 2) if len(closes_for_ma) >= 20 else None
    price_above_ma10 = bool(c > ma10) if ma10 is not None else None
    ma_stack_up = bool(
        price_above_ma10 is True
        and ma10 is not None
        and ma20 is not None
        and ma10 > ma20
    )

    # MACD
    macd_hist, macd_hist_rising = _calc_macd(closes_for_ma)

    # Composite score
    score = 0
    reasons: list[str] = []

    if strong_bull_candle:
        score += 30
        reasons.append("nến tăng mạnh")
    elif body_pct >= 40 and c > o:
        score += 15
        reasons.append("nến tăng vừa")

    if is_sideways_base:
        score += 25
        reasons.append("nền tích lũy")

    if ma_stack_up:
        score += 25
        reasons.append("MA xếp chồng tăng")
    elif price_above_ma10:
        score += 10
        reasons.append("trên MA10")

    if macd_hist is not None and macd_hist > 0 and macd_hist_rising:
        score += 20
        reasons.append("MACD tăng")
    elif macd_hist is not None and macd_hist > 0:
        score += 10
        reasons.append("MACD dương")

    return {
        "body_pct": body_pct,
        "upper_shadow_pct": upper_shadow_pct,
        "lower_shadow_pct": lower_shadow_pct,
        "close_pos": close_pos,
        "strong_bull_candle": strong_bull_candle,
        "avg_vol_20": int(avg_vol_20),
        "avg_vol_50": int(avg_vol_50),
        "range_pct": range_pct,
        "is_sideways_base": is_sideways_base,
        "ma10": ma10,
        "ma20": ma20,
        "price_above_ma10": price_above_ma10,
        "ma_stack_up": ma_stack_up,
        "macd_hist": macd_hist,
        "macd_hist_rising": macd_hist_rising,
        "quality_score": min(score, 100),
        "quality_reason": ", ".join(reasons) if reasons else "không đủ tín hiệu",
    }


async def _fetch_recent_bars(ticker: str, bar_time: datetime, n: int = 50) -> list[dict]:
    """Fetch up to n completed 1m bars for ticker with bar_time < trigger bar_time.
    Returns newest-first. Returns [] if pool unavailable or on error.
    """
    if _pool is None:
        return []
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT open, high, low, close, volume, bu, sd
                FROM intraday_1m
                WHERE ticker = $1 AND bar_time < $2
                ORDER BY bar_time DESC
                LIMIT $3
                """,
                ticker,
                bar_time,
                n,
            )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"_fetch_recent_bars failed for {ticker}: {e}")
        return []


async def _fire_alert(ticker: str, bar: dict, slot: int, ratio: float, baseline: dict, in_magic: bool, bar_time: datetime = None):
    """Insert alert (with dedup) and trigger SSE + email."""
    bu = bar.get("bu", 0) or 0
    sd = bar.get("sd", 0) or 0
    bu_pct: Optional[float] = (bu / (bu + sd) * 100) if (bu + sd) > 0 else None
    foreign_net = bar.get("fn")
    avg_5d = baseline.get("avg_5d")

    # Redis throttle check (30 min) — skip if Redis not configured
    throttle_key = f"alert_throttle:{ticker}:{slot}"
    if _redis is not None and await _redis.exists(throttle_key):
        return

    # Compute M1 quality features (non-blocking, best-effort)
    recent_bars = await _fetch_recent_bars(ticker, bar_time or bar["bar_time"])
    features    = compute_m1_features(bar, recent_bars)
    quality_score = features["quality_score"]
    quality_grade = (
        "A" if quality_score >= 70 else
        "B" if quality_score >= 40 else
        "C"
    )
    quality_reason = features["quality_reason"]

    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO volume_alerts
                    (ticker, slot, bar_time, volume, baseline_5d, ratio_5d, bu_pct, foreign_net,
                     in_magic_window, status,
                     features, quality_score, quality_grade, quality_reason,
                     strong_bull_candle, is_sideways_base)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'fired',
                        $10::jsonb, $11, $12, $13, $14, $15)
                ON CONFLICT (ticker, slot, (DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh')))
                DO NOTHING
                RETURNING id, fired_at
                """,
                ticker,
                slot,
                bar_time,
                bar["volume"],
                avg_5d,
                round(ratio, 4),
                round(bu_pct, 4) if bu_pct is not None else None,
                foreign_net,
                in_magic,
                json.dumps(features),
                quality_score,
                quality_grade,
                quality_reason,
                features["strong_bull_candle"],
                features["is_sideways_base"],
            )

        if row is None:
            # Duplicate
            return

        alert_id = row["id"]
        fired_at = row["fired_at"]

        # Set Redis throttle (skip if Redis not configured)
        if _redis is not None:
            await _redis.setex(throttle_key, 1800, "1")

        # Queue for 15-min confirmation
        _pending_confirms[ticker] = {
            "alert_id": alert_id,
            "slot": slot,
            "confirm_by_slot": slot + 15,
            "cumulative_volume": bar["volume"],
        }

        logger.info(f"M1 Alert fired: {ticker} slot={slot} ratio={ratio:.2f}x magic={in_magic}")

        # SSE push
        if _alert_queue is not None:
            await _alert_queue.put({
                "type": "volume_alert",
                "data": {
                    "id": alert_id,
                    "ticker": ticker,
                    "slot": slot,
                    "volume": bar["volume"],
                    "ratio_5d": round(ratio, 2),
                    "bu_pct": round(bu_pct, 1) if bu_pct is not None else None,
                    "in_magic_window": in_magic,
                    "status": "fired",
                    "fired_at": fired_at.isoformat() if fired_at else None,
                    "quality_score": quality_score,
                    "quality_grade": quality_grade,
                    "quality_reason": quality_reason,
                },
            })

        # Email notification (async, non-blocking)
        asyncio.create_task(notification.send_volume_alert_email(alert_id))

        # Trigger M3 intraday breakout check (volume spike may = breakout day)
        from app.services import alert_engine_m3
        asyncio.create_task(alert_engine_m3.check_intraday_breakout(ticker, bar, alert_id))

    except Exception as e:
        logger.error(f"M1 fire_alert error for {ticker}: {e}", exc_info=True)


async def _check_confirmations(ticker: str, bar: dict, current_slot: int):
    """Check if any pending 15-min confirm is due."""
    pending = _pending_confirms.get(ticker)
    if not pending:
        return
    if current_slot < pending["confirm_by_slot"]:
        if current_slot == pending["slot"]:
            # This is the completed bar for the same minute that triggered the alert.
            # The alert fired from a partial snapshot; replace partial volume with the
            # full minute's volume so the confirm accumulator starts from the right base.
            _pending_confirms[ticker]["cumulative_volume"] = bar["volume"]
        else:
            _pending_confirms[ticker]["cumulative_volume"] += bar["volume"]
        return

    # 15 min elapsed — evaluate
    alert_id = pending["alert_id"]
    orig_slot = pending["slot"]
    cumulative = pending["cumulative_volume"]
    elapsed_slots = current_slot - orig_slot

    baseline = await baseline_service.get_baseline(ticker, orig_slot)
    avg_5d = baseline.get("avg_5d", 0) if baseline else 0
    expected_15m = avg_5d * elapsed_slots if avg_5d else 1
    ratio_15m = cumulative / expected_15m if expected_15m > 0 else 0

    status = "confirmed" if ratio_15m >= settings.THRESHOLD_CONFIRM_15M else "cancelled"

    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE volume_alerts
                SET status=$1, confirmed_at=NOW(), ratio_15m=$2
                WHERE id=$3
                """,
                status,
                round(ratio_15m, 4),
                alert_id,
            )
        logger.info(f"M1 Confirm: {ticker} alert_id={alert_id} status={status} ratio_15m={ratio_15m:.2f}")

        # SSE push — update clients with new status
        if _alert_queue is not None:
            await _alert_queue.put({
                "type": "alert_status_update",
                "data": {
                    "id": alert_id,
                    "status": status,
                    "ratio_15m": round(ratio_15m, 2),
                },
            })
    except Exception as e:
        logger.error(f"M1 confirm error: {e}")

    # Remove from pending
    del _pending_confirms[ticker]
