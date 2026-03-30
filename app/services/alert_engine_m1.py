"""Module 1: Volume Scanner Alert Engine."""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from collections import defaultdict

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


# ── Pure evaluation (no side effects) ─────────────────────────────────────

def _calc_bu_pct(bar: dict) -> Optional[float]:
    bu = bar.get("bu", 0) or 0
    sd = bar.get("sd", 0) or 0
    return (bu / (bu + sd) * 100) if (bu + sd) > 0 else None


def evaluate_bar(bar: dict, avg_5d: float) -> Optional[dict]:
    """Pure M1 evaluation — no I/O, no side effects.

    Returns an evaluation dict when this bar crosses the alert threshold,
    or None when it does not. Safe to call from historical replay paths.

    Result keys: ticker, bar_time, bar_time_ict, slot, volume, ratio,
                 in_magic, threshold, bu_pct.
    """
    if not avg_5d or avg_5d <= 0:
        return None
    try:
        bar_time_utc: datetime = bar["bar_time"]
        if isinstance(bar_time_utc, str):
            bar_time_utc = datetime.fromisoformat(bar_time_utc.replace("Z", "+00:00"))
        bar_time_ict = to_ict(bar_time_utc)

        slot = get_slot(bar_time_ict.time())
        if slot is None:
            return None

        volume = bar.get("volume") or 0
        in_magic = is_magic_window(bar_time_ict.time())
        threshold = settings.THRESHOLD_MAGIC if in_magic else settings.THRESHOLD_NORMAL

        elapsed_seconds = bar_time_ict.second
        if elapsed_seconds >= 10:
            ratio = int(volume * (60 / elapsed_seconds)) / avg_5d
        else:
            ratio = volume / avg_5d

        if ratio < threshold:
            return None

        return {
            "ticker": bar.get("ticker"),
            "bar_time": bar_time_utc,
            "bar_time_ict": bar_time_ict,
            "slot": slot,
            "volume": volume,
            "ratio": ratio,
            "in_magic": in_magic,
            "threshold": threshold,
            "bu_pct": _calc_bu_pct(bar),
        }
    except Exception as e:
        logger.debug(f"evaluate_bar error: {e}")
        return None


# ── Historical scan (read-only, no alerts emitted) ────────────────────────

async def scan_m1_history(days: int = 25) -> list[dict]:
    """Scan intraday_1m for bars that would trigger M1 alerts using rolling
    historical baselines — no DB writes, no SSE, no email.

    APPROXIMATION NOTICE: this evaluates completed 1m bars only. Live M1
    uses partial mid-minute tick detection and rate projection, so this
    will miss spikes detected intra-minute and may compute slightly different
    ratios. Treat results as a bar-close approximation, not an exact replay.

    Historical baseline (avg_5d) is computed per ticker+slot from the same
    slot's volume over the 5 preceding calendar days in intraday_1m — not
    from the live baseline_service (which reflects current data, not the
    correct historical value for each bar).

    Requires intraday_1m to be populated (see historical_intraday_service).
    """
    scan_cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # 10 extra calendar days to build a stable 5-day rolling baseline
    lookback_cutoff = scan_cutoff - timedelta(days=10)

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ticker, bar_time, open, high, low, close, volume, bu, sd, fn
            FROM intraday_1m
            WHERE bar_time >= $1 AND volume > 0
            ORDER BY ticker, bar_time
            """,
            lookback_cutoff,
        )

    # Group bars by (ticker, slot) — each list is chronologically sorted
    by_ticker_slot: dict[tuple, list] = defaultdict(list)
    for row in rows:
        bar = dict(row)
        bar_time_ict = to_ict(bar["bar_time"])
        slot = get_slot(bar_time_ict.time())
        if slot is None:
            continue
        bar["_slot"] = slot
        bar["_ict_date"] = bar_time_ict.date()
        by_ticker_slot[(bar["ticker"], slot)].append(bar)

    results: list[dict] = []

    for (ticker, slot), bars in by_ticker_slot.items():
        for i, bar in enumerate(bars):
            # Only report bars inside the requested scan window
            if bar["bar_time"] < scan_cutoff:
                continue

            # Rolling avg_5d: volumes from the same ticker+slot over the
            # 5 previous calendar days (days_diff in [1..7] handles weekends)
            bar_date = bar["_ict_date"]
            prev_vols = [
                b["volume"] for b in bars[:i]
                if 1 <= (bar_date - b["_ict_date"]).days <= 7 and b["volume"] > 0
            ]
            if len(prev_vols) < 3:
                continue

            avg_5d = sum(prev_vols[-5:]) / min(len(prev_vols), 5)
            result = evaluate_bar(bar, avg_5d)
            if result:
                # Compute quality features from preceding bars (newest-first)
                recent = list(reversed(bars[:i]))[:50]
                features = compute_m1_features(bar, recent)
                q_score = features["quality_score"]
                q_grade = "A" if q_score >= 70 else "B" if q_score >= 40 else "C"
                results.append({
                    "ticker": ticker,
                    "bar_time": bar["bar_time"].isoformat(),
                    "slot": slot,
                    "volume": result["volume"],
                    "ratio": round(result["ratio"], 3),
                    "avg_5d_hist": int(avg_5d),
                    "in_magic": result["in_magic"],
                    "threshold": result["threshold"],
                    "bu_pct": round(result["bu_pct"], 1) if result["bu_pct"] is not None else None,
                    "foreign_net": bar.get("fn"),
                    "quality_score": q_score,
                    "quality_grade": q_grade,
                    "quality_reason": features["quality_reason"],
                    "strong_bull_candle": features["strong_bull_candle"],
                    "is_sideways_base": features["is_sideways_base"],
                    "features": features,
                })

    results.sort(key=lambda x: x["bar_time"])
    logger.info(
        f"M1 scan_history: {len(results)} hits over last {days} days "
        f"(bar-close approx, rolling baseline)"
    )
    return results


async def _settle_historical_alert(
    conn,
    alert_id: int,
    hit: dict,
    bar_time: datetime,
) -> None:
    """Compute confirmed/cancelled for a historical alert from intraday_1m data.

    Mirrors _check_confirmations() but reads from intraday_1m instead of
    accumulating live bars.  Uses the rolling historical avg_5d from the
    hit dict (avg_5d_hist), not the current live baseline.
    """
    window_end = bar_time + timedelta(minutes=15)
    bars = await conn.fetch(
        """
        SELECT volume FROM intraday_1m
        WHERE ticker=$1 AND bar_time >= $2 AND bar_time < $3
          AND volume > 0
        ORDER BY bar_time
        """,
        hit["ticker"], bar_time, window_end,
    )
    if not bars:
        return
    cumulative = sum(r["volume"] for r in bars)
    elapsed_slots = len(bars)
    avg_5d = hit.get("avg_5d_hist", 0)
    expected_15m = avg_5d * elapsed_slots if avg_5d else 1
    ratio_15m = cumulative / expected_15m if expected_15m > 0 else 0
    status = "confirmed" if ratio_15m >= settings.THRESHOLD_CONFIRM_15M else "cancelled"
    # confirmed_at = end of the historical 15-min window (bar_time + 15min),
    # NOT NOW() — so the UI shows the actual market time of confirmation,
    # not the time the replay job ran.
    await conn.execute(
        """
        UPDATE volume_alerts
        SET status=$1, confirmed_at=$2, ratio_15m=$3
        WHERE id=$4
        """,
        status, window_end, round(ratio_15m, 4), alert_id,
    )


async def replay_m1_history(
    days: int = 25,
    apply: bool = False,
    mode: str = "bootstrap",
    notify_mode: str = "none",
) -> dict:
    """Persist historical M1 hits with origin='historical_replay'.

    Calls scan_m1_history() for rolling-baseline detection, then optionally
    inserts each hit into volume_alerts with:
      - origin = 'historical_replay' (or 'recovery_replay' if mode='recovery')
      - is_actionable = FALSE
      - No SSE push, no per-item Telegram/email

    Idempotent: existing alert at same ticker+slot+ICT-date is skipped.

    APPROXIMATION NOTICE: bar-close volume only, not mid-minute tick detection.
    Use as research/audit/recovery layer — not as a source of live alerts.
    """
    from datetime import date, timedelta

    run_origin = "recovery_replay" if mode == "recovery" else "historical_replay"
    run_id = uuid.uuid4()
    date_from = date.today() - timedelta(days=days + 10)
    date_to   = date.today() - timedelta(days=1)

    if apply:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO replay_runs
                    (id, module, mode, date_from, date_to, apply,
                     notify_mode, status, started_at)
                VALUES ($1, 'm1', $2, $3, $4, TRUE, $5, 'running', NOW())
                """,
                run_id, mode, date_from, date_to, notify_mode,
            )

    hits = await scan_m1_history(days=days)
    created_count = 0
    skipped_count = 0

    if apply:
        if hits:
            async with _pool.acquire() as conn:
                for hit in hits:
                    bar_time = datetime.fromisoformat(hit["bar_time"])
                    inserted_id = await conn.fetchval(
                        """
                        INSERT INTO volume_alerts
                            (ticker, slot, bar_time, volume, baseline_5d, ratio_5d, bu_pct,
                             foreign_net, in_magic_window, status,
                             features, quality_score, quality_grade, quality_reason,
                             strong_bull_candle, is_sideways_base,
                             origin, replay_run_id, replayed_at, is_actionable)
                        VALUES ($1, $2, $3, $4, $5, $6, $7,
                                $8, $9, 'fired',
                                $10::jsonb, $11, $12, $13,
                                $14, $15,
                                $16, $17, NOW(), FALSE)
                        ON CONFLICT (ticker, slot,
                            (DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh')))
                        DO NOTHING
                        RETURNING id
                        """,
                        hit["ticker"], hit["slot"], bar_time,
                        hit["volume"], hit.get("avg_5d_hist"), hit["ratio"], hit.get("bu_pct"),
                        hit.get("foreign_net"),
                        hit.get("in_magic", False),
                        json.dumps(hit["features"]) if hit.get("features") else None,
                        hit.get("quality_score"), hit.get("quality_grade"), hit.get("quality_reason"),
                        hit.get("strong_bull_candle"), hit.get("is_sideways_base"),
                        run_origin, run_id,
                    )
                    if inserted_id is not None:
                        created_count += 1
                        await _settle_historical_alert(conn, inserted_id, hit, bar_time)
                    else:
                        skipped_count += 1

        async with _pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE replay_runs
                SET status='done', finished_at=NOW(),
                    created_count=$2, skipped_count=$3
                WHERE id=$1
                """,
                run_id, created_count, skipped_count,
            )

    result = {
        "run_id": str(run_id),
        "hits_found": len(hits),
        "created_count": created_count,
        "skipped_count": skipped_count,
        "applied": apply,
        "mode": mode,
        "notify_mode": notify_mode,
        "approximation_notice": (
            "Bar-close approximation only — not an exact replay of live M1 "
            "tick detection. Use for research/audit/recovery, not live alerts."
        ),
    }

    if apply and notify_mode == "digest" and hits:
        await notification.send_m1_replay_digest(
            run_id=str(run_id), days=days, hits=hits,
            created=created_count, mode=mode,
        )

    logger.info(
        f"M1 replay_history: {len(hits)} hits, {created_count} created, "
        f"{skipped_count} skipped (apply={apply}, mode={mode})"
    )
    return result


# ── Live processing ────────────────────────────────────────────────────────

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
    is_green_candle = bool(c > o)
    strong_bull_candle = bool(body_pct >= 50.0 and close_pos >= 67.0 and is_green_candle)

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
    candle_score = 0
    base_score = 0
    ma_score = 0
    macd_score = 0
    reasons: list[str] = []

    if strong_bull_candle:
        candle_score = 30
        reasons.append("nến +30")
    elif body_pct >= 40 and is_green_candle:
        candle_score = 15
        reasons.append("nến +15")

    if is_sideways_base:
        base_score = 25
        reasons.append("nền +25")

    if ma_stack_up:
        ma_score = 25
        reasons.append("MA +25")
    elif price_above_ma10:
        ma_score = 10
        reasons.append("MA +10")

    if macd_hist is not None and macd_hist > 0 and macd_hist_rising:
        macd_score = 20
        reasons.append("MACD +20")
    elif macd_hist is not None and macd_hist > 0:
        macd_score = 10
        reasons.append("MACD +10")

    score = candle_score + base_score + ma_score + macd_score
    score_detail = (
        f"Nến {candle_score}/30 • Nền {base_score}/25 • "
        f"MA {ma_score}/25 • MACD {macd_score}/20"
    )
    quality_reason = (
        f"{score_detail}. Tổng {min(score, 100)}/100."
        if score > 0 else
        "Nến 0/30 • Nền 0/25 • MA 0/25 • MACD 0/20. Tổng 0/100."
    )

    return {
        "body_pct": body_pct,
        "upper_shadow_pct": upper_shadow_pct,
        "lower_shadow_pct": lower_shadow_pct,
        "close_pos": close_pos,
        "is_green_candle": is_green_candle,
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
        "candle_score": candle_score,
        "base_score": base_score,
        "ma_score": ma_score,
        "macd_score": macd_score,
        "quality_score": min(score, 100),
        "quality_reason": quality_reason,
        "quality_tags": reasons,
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

        asyncio.create_task(notification.send_volume_alert_confirmation(alert_id))
    except Exception as e:
        logger.error(f"M1 confirm error: {e}")

    # Remove from pending
    del _pending_confirms[ticker]
