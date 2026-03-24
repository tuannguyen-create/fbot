# fbot — Tài liệu Q&A toàn diện

> Phiên bản: 2026-03 | Thuật toán: meeting-goc v1.5

---

## Mục lục

1. [Tổng quan hệ thống](#1-tổng-quan-hệ-thống)
2. [Watchlist & Tickers](#2-watchlist--tickers)
3. [M1 — Quét khối lượng bất thường](#3-m1--quét-khối-lượng-bất-thường)
4. [M3 — Phân tích chu kỳ (meeting-goc v1.5)](#4-m3--phân-tích-chu-kỳ-meeting-goc-v15)
5. [Stream FiinQuantX](#5-stream-fiinquantx)
6. [Baseline Service](#6-baseline-service)
7. [APScheduler — Lịch chạy tự động](#7-apscheduler--lịch-chạy-tự-động)
8. [Notifications — Email & Telegram](#8-notifications--email--telegram)
9. [Các trường dữ liệu quan trọng](#9-các-trường-dữ-liệu-quan-trọng)
10. [Giao diện UI — Giải thích từng trang](#10-giao-diện-ui--giải-thích-từng-trang)
11. [Thứ gì có trong DB nhưng chưa hiển thị trên UI](#11-thứ-gì-có-trong-db-nhưng-chưa-hiển-thị-trên-ui)
12. [Các ngưỡng & tham số](#12-các-ngưỡng--tham-số)
13. [Trạng thái & phân loại](#13-trạng-thái--phân-loại)
14. [FAQ — Câu hỏi thường gặp](#14-faq--câu-hỏi-thường-gặp)

---

## 1. Tổng quan hệ thống

### fbot là gì?

fbot là hệ thống giám sát chứng khoán tự động cho thị trường Việt Nam (HOSE). Hệ thống:
- Kết nối trực tiếp với FiinQuantX để nhận dữ liệu real-time từng phút
- Phát hiện khối lượng bất thường theo thuật toán M1
- Phân tích chu kỳ phân phối/tích lũy theo thuật toán M3 (meeting-goc v1.5)
- Gửi cảnh báo qua email (Resend) và Telegram ngay khi phát hiện tín hiệu
- Hiển thị dashboard real-time qua giao diện web Next.js

### Kiến trúc tổng thể

```
FiinQuantX WebSocket
        │
        ▼
stream_ingester.py  ──► intraday_1m (DB)
        │
        ▼
alert_engine_m1.py  ──► volume_alerts (DB) ──► Email/Telegram
        │
        ▼
alert_engine_m3.py  ──► cycle_events (DB) ──► Email/Telegram
        │
APScheduler (15:10 ICT)
        │
        ▼
alert_engine_m3.run_daily()

baseline_service.py ──► volume_baselines (DB + Memory cache + Redis)

FastAPI Backend ──► Next.js Frontend (SSE real-time)
```

### Stack công nghệ

- **Backend**: Python 3.11+, FastAPI, asyncpg, asyncio
- **Frontend**: Next.js 14, React Query, Zustand, Tailwind CSS
- **Database**: PostgreSQL
- **Cache**: Redis (tùy chọn, có thể bỏ qua)
- **Stream**: FiinQuantX Python SDK (WebSocket/SignalR)
- **Email**: Resend API
- **Notifications**: Telegram Bot API
- **Scheduler**: APScheduler

---

## 2. Watchlist & Tickers

### Danh sách theo dõi gồm những gì?

33 tickers chia làm 2 nhóm:

**VN30 (Q1/2026 basket) — 30 tickers:**
ACB, BCM, BID, CTG, FPT, GAS, GVR, HDB, HPG, LPB, MBB, MSN, MWG, PLX, SAB, SHB, SSB, SSI, STB, TCB, VCB, VHM, VIB, VIC, VJC, VNM, VPB, VPL, VRE, VPG

**3 "game stocks" thêm vào:**
NVL, PDR, KBC

Tổng cộng 33 vì đây là giới hạn của FiinQuantX gói miễn phí.

### eligible_for_m3 là gì?

Cờ bật/tắt cho từng ticker, kiểm soát xem ticker đó có được phân tích chu kỳ M3 không.

- Mặc định: **TRUE** (tất cả ticker đều được phân tích)
- Khi tắt (`FALSE`): M1 vẫn phát alert bình thường, nhưng không tạo cycle_events
- Toggle trực tiếp trên UI: trang `/watchlist/[ticker]` — nút **M3 ON/OFF**

### game_type là gì?

Phân loại chiến lược giao dịch của ticker:

| game_type | Màu UI | Ý nghĩa |
|---|---|---|
| `speculative` | Cam | Cổ phiếu đầu cơ, biến động mạnh |
| `state_enterprise` | Xanh dương | Doanh nghiệp nhà nước, ổn định |
| `institutional` | Xám | Tổ chức, chỉ số cơ bản (mặc định) |

---

## 3. M1 — Quét khối lượng bất thường

### M1 hoạt động thế nào?

Mỗi phút, khi nhận bar dữ liệu từ FiinQuantX:

1. Xác định **slot** (số thứ tự phút trong phiên giao dịch, 0–239)
2. Lấy **baseline** (khối lượng trung bình 5 ngày tại slot đó)
3. Tính **ratio** = volume hiện tại / baseline_5d
4. Nếu ratio ≥ ngưỡng → bắn alert

### Ngưỡng kích hoạt alert

| Loại phiên | Ngưỡng |
|---|---|
| **Giờ bình thường** | ≥ 2.0x baseline_5d |
| **Magic Window** | ≥ 1.5x baseline_5d |

### Magic Window là gì?

Các khung giờ có xác suất tín hiệu cao hơn do thanh khoản tập trung:

- **9:00 – 9:30** (mở cửa buổi sáng)
- **11:00 – 11:30** (cuối buổi sáng)
- **13:00 – 13:30** (mở cửa buổi chiều)

Alert trong magic window được đánh dấu `in_magic_window=TRUE` và hiển thị ⚡ trên UI.

### Rate projection là gì?

Nếu FiinQuantX gửi bar mid-minute (second > 0), tức bar chưa đóng hết 1 phút:

```python
if elapsed_seconds >= 10:
    projected_volume = int(volume * (60 / elapsed_seconds))
    ratio = projected_volume / avg_5d
```

Cơ chế này cho phép phát hiện sớm (~10-20 giây) thay vì phải chờ hết phút.

### Dedup (tránh trùng lặp alert) hoạt động thế nào?

**2 lớp dedup:**

1. **Redis throttle**: Sau khi bắn alert, key `alert_throttle:{ticker}:{slot}` được đặt với TTL 1800s (30 phút). Nếu key tồn tại → bỏ qua.

2. **DB unique index**: `ON CONFLICT (ticker, slot, DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh'))` → Mỗi (ticker, slot, ngày ICT) chỉ có 1 alert. Redis là hàng rào đầu, DB là hàng rào cuối.

### Xác nhận 15 phút (confirmation) là gì?

Sau khi alert bắn, hệ thống theo dõi 15 phút tiếp theo:

- Tích lũy volume trong 15 phút
- Tính `ratio_15m = cumulative_volume_15m / (avg_5d × 15_slots)`
- Nếu `ratio_15m ≥ 1.3` → status = **confirmed** ✅
- Nếu không → status = **cancelled** ❌

Status ban đầu là **fired** (⏳ đang chờ).

---

## 4. M3 — Phân tích chu kỳ (meeting-goc v1.5)

### M3 là gì?

Thuật toán phát hiện và theo dõi chu kỳ **phân phối → tích lũy** dựa trên lý thuyết Wyckoff:

1. **Phân phối**: Sau breakout khối lượng lớn, "cá mập" phân phối cổ phiếu dần (khoảng 20 ngày giao dịch)
2. **Bottoming**: Volume giảm về mức thấp, tín hiệu phân phối kết thúc
3. **Cửa sổ quan sát (Rewatch Window)**: Thời điểm tích lũy có thể bắt đầu, cần theo dõi

### Điều kiện nhận diện Breakout

Cả 2 điều kiện phải thỏa mãn:

| Điều kiện | Ngưỡng |
|---|---|
| Volume ngày hôm nay / MA20 daily | ≥ **3.0x** |
| Biến động giá so với ngày hôm qua | ≥ **+3%** |

### Khi nào M3 chạy?

**Hai con đường kích hoạt:**

1. **Intraday (real-time)**: Khi M1 bắn alert → M3 kiểm tra ngay volume tích lũy intraday (`intraday_1m`). Nếu đủ ngưỡng → tạo cycle ngay trong phiên.

2. **End-of-day (15:10 ICT)**: APScheduler chạy sau khi `daily_ohlcv` được cập nhật (15:05). Dùng dữ liệu ngày cuối cùng.

### Các pha của chu kỳ

| Phase | Màu badge | Ý nghĩa |
|---|---|---|
| `distribution_in_progress` | Cam | Đang phân phối, khoảng 20 ngày |
| `bottoming_candidate` | Vàng/Nâu | Tín hiệu tạo đáy (3 phiên KL thấp) |
| `invalidated` | Đỏ | Bị vô hiệu hóa (giá dưới vùng breakout) |
| `done` | Xám | Hoàn tất (dùng thủ công) |

### Điều kiện chuyển pha

**→ `bottoming_candidate`**: Cả 3 điều kiện phải đúng:
- 3 phiên liên tiếp volume < 50% MA20
- `days_remaining ≤ 0` (đã qua thời gian phân phối ước tính)
- Chưa gửi `alert_sent_bottom`

**→ `invalidated`**: Giá đóng cửa hôm nay < `breakout_zone_low` (−3% so với giá breakout)

### Rewatch Window là gì?

**Cửa sổ quan sát** là khoảng thời gian dự kiến cá mập bắt đầu tích lũy trở lại.

- `rewatch_window_start` = breakout_date + 20 ngày giao dịch
- `rewatch_window_end` = rewatch_window_start + 10 ngày giao dịch

Khi chuyển sang `bottoming_candidate`, cửa sổ được cập nhật lại:
- `rewatch_window_start` = ngày hôm nay (khi phát hiện đáy)
- `rewatch_window_end` = ngày hôm nay + 10 ngày giao dịch

### Breakout Zone là gì?

Vùng giá tham chiếu từ giá đóng cửa ngày breakout:

| | Tính toán | Ý nghĩa |
|---|---|---|
| `breakout_zone_low` | giá_breakout × 0.97 (−3%) | Ngưỡng vô hiệu hóa |
| `breakout_zone_high` | giá_breakout × 1.05 (+5%) | Vùng kháng cự tham chiếu |

### source_alert_id và source_alert_inferred là gì?

Liên kết giữa cycle_event và volume_alert nguồn gốc:

- `source_alert_id`: ID của alert M1 kích hoạt cycle này
- `source_alert_inferred`:
  - **FALSE** = canonical — M1 trực tiếp gọi M3 intraday, liên kết chính xác
  - **TRUE** = heuristic backfill — từ migration, lấy alert có ratio_5d cao nhất trong ngày (có thể không phải alert thực sự gây breakout)

Trên UI: "Alert nguồn" (FALSE) vs "Alert liên quan" (TRUE)

---

## 5. Stream FiinQuantX

### Stream kết nối thế nào?

FiinQuantX dùng SignalR (33 connections). Mỗi connection là 1 "kênh" dữ liệu. `_stream_blocking()` chạy trong thread executor riêng để không block event loop asyncio.

### Trạng thái stream trên UI

| Trạng thái | Màu | Điều kiện |
|---|---|---|
| `Kết nối` (connected) | Xanh lá | `_stream_connected = True` |
| `Ngoài giờ` (outside_hours) | Xám | Ngoài 9:00–14:30 ICT hoặc không phải ngày GD |
| `Đang kết nối...` (connecting) | Vàng | Trong giờ GD, chưa nhận bar nào, < 120s kể từ startup |
| `Kết nối lại...` (reconnecting) | Vàng | Mất kết nối nhưng đã có bar gần đây (< 5 phút) |
| `Lỗi kết nối` (error) | Đỏ | Mất kết nối, bar cuối > 5 phút, không thuộc trường hợp trên |

### Overnight edge case là gì?

Khi process chạy qua đêm và sáng hôm sau kết nối lại:
- `_last_bar_time` = timestamp từ phiên hôm qua (~14:30 ICT)
- Lúc 9:00 ICT sáng hôm sau, bar_time đó cũ > 5 phút → có thể nhảy thẳng lên "error"

**Fix**: Hệ thống kiểm tra nếu `_last_bar_time < session_open_utc` (trước giờ mở cửa hôm nay), áp dụng grace period 120s tính từ `session_open_utc` thay vì từ `_startup_at`.

### Proactive restart là gì?

JWT của FiinQuantX hết hạn sau 1 giờ. Để tránh tình huống 33 connection bị ngắt đồng loạt (gây spike kết nối lại), hệ thống chủ động restart sau 55 phút:

1. Sau 55 phút → `_close_event()` → stream kết thúc gracefully
2. Chờ 90 giây (server dọn stale sessions)
3. Reconnect với JWT mới

### Watchdog là gì?

Task chạy mỗi 60 giây, trong giờ GD (9:00–15:10 ICT):
- Nếu connected nhưng không có bar mới trong > 10 phút → force reconnect

---

## 6. Baseline Service

### Baseline được tính thế nào?

Mỗi tối (18:00 ICT), `rebuild_all()` chạy cho tất cả 33 tickers:

1. Lấy 25 ngày dữ liệu `intraday_1m` gần nhất
2. Nhóm theo slot (0–239)
3. Tính cho mỗi (ticker, slot):
   - `avg_5d`: Trung bình volume 5 phiên gần nhất tại slot đó
   - `avg_20d`: Trung bình 20 phiên (nếu đủ dữ liệu)
   - `std_dev`: Độ lệch chuẩn 20 phiên

### Baseline được cache thế nào?

**3 tầng cache:**

```
In-Memory (_mem_cache) ← tốc độ 0ms, ~0.8MB, ~7,920 entries
        ↓ (fallback)
Redis (optional)       ← distributed, TTL 86400s
        ↓ (fallback)
PostgreSQL             ← persistent
```

Khi startup, `warm_cache()` load toàn bộ từ DB vào memory. Trong giờ GD, mọi lookup đều từ memory → không tốn DB query.

---

## 7. APScheduler — Lịch chạy tự động

| Giờ (ICT) | Job | Mô tả |
|---|---|---|
| 18:00 | `baseline_service.rebuild_all()` | Tính lại baseline từ intraday data |
| 15:05 | `aggregate_daily()` | Gộp intraday_1m → daily_ohlcv |
| 15:10 | `alert_engine_m3.run_daily()` | Chạy M3 phân tích sau khi có daily data |
| 19:00 | `cleanup_old_intraday()` | Xóa intraday_1m cũ hơn 25 ngày |

Tất cả jobs chỉ chạy trên ngày giao dịch (kiểm tra `is_trading_day()`).

---

## 8. Notifications — Email & Telegram

### Có bao nhiêu loại thông báo?

4 loại notification:

| Loại | Khi nào | Subject |
|---|---|---|
| **Volume Alert** | M1 bắn alert | `🔥 [TICKER] KL bất thường — HH:MM ICT | Xx baseline` |
| **Cycle Breakout** | M3 tạo cycle mới | `📈 [TICKER] Breakout phát hiện — Phân phối 20 ngày` |
| **10-day Warning** | Còn ≤ 10 ngày đến rewatch window | `⏰ [TICKER] Còn N ngày đến cửa sổ quan sát` |
| **Bottoming Signal** | 3 phiên KL thấp, phân phối kết thúc | `🟢 [TICKER] Tín hiệu tạo đáy — KL thấp 3 phiên liên tiếp` |

### Email và Telegram gửi đồng thời không?

Có. Cả hai được gửi song song qua `asyncio.gather()`.

### Nội dung email Volume Alert gồm gì?

- Thời điểm phiên GD (slot → HH:MM ICT)
- Khối lượng thực tế
- Baseline 5 ngày
- Tỷ lệ KL/Baseline (ratio_5d)
- BU% (tỷ lệ bên mua)
- Foreign Net (dòng tiền ngoại ròng)
- Magic Window: Có/Không
- Trạng thái: Đã xác nhận / Không xác nhận / Đang chờ

### Thông báo có được lưu lại không?

Có, bảng `notification_log` lưu tất cả:
- `alert_id` hoặc `cycle_id` (một trong hai)
- `channel`: 'email'
- `message_id`: ID từ Resend
- `status`: 'sent' / 'failed'

---

## 9. Các trường dữ liệu quan trọng

### volume_alerts — Bảng alerts

| Trường | Kiểu | Mô tả |
|---|---|---|
| `id` | int | Primary key |
| `ticker` | text | Mã cổ phiếu (uppercase) |
| `slot` | int | Số thứ tự phút trong phiên (0–239) |
| `bar_time` | timestamptz | Thời điểm bar thực tế (UTC, NOT NULL) |
| `fired_at` | timestamptz | Thời điểm DB insert (do DB tự set) |
| `volume` | int | Khối lượng thực tế trong phút đó |
| `baseline_5d` | float | Baseline trung bình 5 ngày tại slot đó |
| `ratio_5d` | float | volume / baseline_5d (hoặc projected) |
| `bu_pct` | float | % bên mua = bu/(bu+sd) × 100 |
| `foreign_net` | int | Dòng tiền ngoại ròng (fn field) |
| `in_magic_window` | bool | Có trong magic window không |
| `status` | text | 'fired' / 'confirmed' / 'cancelled' |
| `confirmed_at` | timestamptz | Khi nào xác nhận/hủy |
| `ratio_15m` | float | Ratio xác nhận 15 phút |
| `email_sent` | bool | Đã gửi email chưa |
| `cycle_event_id` | int | FK → cycle_events (nếu có) |

**Quan trọng**: `bar_time` là "sự thật" cho mọi logic nghiệp vụ (dedup, lọc ngày, đếm hôm nay). `fired_at` chỉ dùng để biết khi nào hệ thống xử lý.

### cycle_events — Bảng chu kỳ

| Trường | Kiểu | Mô tả |
|---|---|---|
| `id` | int | Primary key |
| `ticker` | text | Mã cổ phiếu |
| `breakout_date` | date | Ngày breakout (YYYY-MM-DD) |
| `breakout_price` | float | Giá đóng cửa ngày breakout |
| `peak_volume` | int | Volume ngày breakout |
| `phase` | text | Pha hiện tại |
| `game_type` | text | Loại game (từ watchlist) |
| `estimated_dist_days` | int | Số ngày phân phối dự kiến (mặc định 20) |
| `trading_days_elapsed` | int | Số ngày GD đã trải qua |
| `days_remaining` | int | Số ngày GD còn lại |
| `breakout_zone_low` | float | Ngưỡng vô hiệu hóa (−3%) |
| `breakout_zone_high` | float | Vùng kháng cự tham chiếu (+5%) |
| `rewatch_window_start` | date | Ngày bắt đầu cửa sổ quan sát |
| `rewatch_window_end` | date | Ngày kết thúc cửa sổ quan sát |
| `phase_reason` | text | Mô tả lý do pha hiện tại |
| `invalidation_reason` | text | Lý do vô hiệu hóa |
| `source_alert_id` | int | FK → volume_alerts (alert nguồn) |
| `source_alert_inferred` | bool | FALSE=canonical, TRUE=heuristic backfill |
| `predicted_bottom_date` | date | Deprecated, dùng rewatch_window_start |
| `alert_sent_10d` | bool | Đã gửi cảnh báo 10-day chưa |
| `alert_sent_bottom` | bool | Đã gửi cảnh báo bottoming chưa |
| `breakout_email_sent` | bool | Đã gửi email breakout chưa |

### volume_baselines — Bảng baseline

| Trường | Mô tả |
|---|---|
| `ticker` + `slot` | Primary key composite |
| `avg_5d` | Trung bình volume 5 ngày gần nhất tại slot |
| `avg_20d` | Trung bình 20 ngày (nếu đủ dữ liệu) |
| `std_dev` | Độ lệch chuẩn 20 ngày |
| `sample_count` | Số mẫu dữ liệu |
| `updated_date` | Ngày cập nhật gần nhất |

### intraday_1m — Bảng dữ liệu phút

Lưu toàn bộ OHLCV + flow data từng phút, giữ 25 ngày gần nhất.

| Trường | Mô tả |
|---|---|
| `ticker`, `bar_time` | PK |
| `open`, `high`, `low`, `close` | Giá |
| `volume` | Khối lượng |
| `bu`, `sd` | Buy up / Sell down (số lượng lệnh) |
| `fb`, `fs`, `fn` | Foreign buy / Foreign sell / Foreign net |

### Giờ giao dịch & Slots

```
09:00 – 11:30 ICT → slots 0–149   (150 phút, buổi sáng)
11:30 – 13:00 ICT → nghỉ trưa (không có slot)
13:00 – 14:30 ICT → slots 150–239 (90 phút, buổi chiều)

slot 0 = 09:00
slot 149 = 11:29
slot 150 = 13:00
slot 239 = 14:29
```

---

## 10. Giao diện UI — Giải thích từng trang

### /dashboard

**Phiên hôm nay:**
- **Alerts hôm nay**: Tổng số alert có bar_time = hôm nay (ICT)
- **Xác nhận**: `confirmed/total` — bao nhiêu alert được confirm trong 15 phút
- **Chu kỳ active**: Tổng cycle_events đang ở pha active (distribution/bottoming)
- **Stream**: Trạng thái kết nối FiinQuantX real-time (5 trạng thái, xem mục 5)

**DB/Redis health** (góc trên phải): DB ✅/❌ và Redis ✅/—/❌

**Cảnh báo real-time** (LiveAlertFeed): Feed SSE hiển thị alert mới nhất, flash animation 2 giây khi có alert mới.

**Volume Heatmap**: Lưới 33 tickers, màu sắc theo số alert hôm nay:
- Trắng/Xám: 0 alerts
- Vàng: 1 alert
- Cam: 2 alerts
- Đỏ: ≥ 3 alerts

**Chu kỳ đang theo dõi**: Tối đa 5 cycles active nhất, kèm progress bar và link đến cycle detail.

### /alerts

Danh sách alerts với filter: ticker, ngày (date_from/date_to), status, magic_only.

| Cột | Giải thích |
|---|---|
| Thời điểm | `bar_time` → HH:MM ICT (slot → giờ) |
| Ticker | Mã cổ phiếu |
| Tỷ lệ | `ratio_5d` × baseline |
| BU% | Tỷ lệ bên mua |
| ⚡ | Magic window |
| Badge | Status: fired/confirmed/cancelled |

### /alerts/[id]

Chi tiết 1 alert:

| Nhãn UI | Trường DB | Giải thích |
|---|---|---|
| Phiên GD | `bar_time` | Thời điểm bar thực tế (ICT) |
| Ghi nhận | `fired_at` | Khi nào hệ thống insert DB |
| Khối lượng | `volume` | Volume phút đó |
| Baseline 5d | `baseline_5d` | TB 5 ngày tại slot đó |
| Ratio 5d | `ratio_5d` | volume/baseline (có thể là projected) |
| BU% | `bu_pct` | % bên mua |
| Foreign Net | `foreign_net` | Dòng ngoại ròng |
| Magic Window | `in_magic_window` | ⚡ nếu TRUE |
| Xác nhận 15' | `status` + `ratio_15m` | confirmed/cancelled/fired |

Link đến cycle liên quan nếu `cycle_event_id` có giá trị.

### /watchlist

Danh sách tất cả 33 tickers với:
- `in_vn30` badge
- `game_type` badge (nếu có)
- `eligible_for_m3` — M3 ON/OFF
- Alert count hôm nay
- Phase badge của active cycle

### /watchlist/[ticker]

Chi tiết 1 ticker:
- Tên công ty
- game_type badge + nút toggle M3 ON/OFF
- **Alerts hôm nay**: Số alert có bar_time = hôm nay
- **Chu kỳ M3**: Phase badge của active cycle
- **Chu kỳ hiện tại** (nếu có):
  - Ngày breakout, vùng breakout (low–high)
  - Cửa sổ quan sát (rewatch_window_start → end)
  - Lý do pha (phase_reason)
  - Link "Xem chi tiết chu kỳ" + "Alert nguồn/Alert liên quan"
- **Lịch sử cảnh báo**: Tổng/xác nhận 30 ngày + 5 alert gần nhất

### /cycles

Danh sách tất cả cycles với filter phase. Mỗi dòng:
- Ticker + breakout_date
- Phase badge
- Progress bar (trading_days_elapsed / estimated_dist_days)
- Rewatch window dates

### /cycles/[id]

Chi tiết 1 cycle:
- Breakout date + price
- Phase badge + phase_reason
- Progress bar
- Rewatch window
- Breakout zone (low/high)
- Invalidation threshold
- Email notification flags (alert_sent_10d, alert_sent_bottom, breakout_email_sent)
- Link đến source alert (nếu có)

---

## 11. Thứ gì có trong DB nhưng chưa hiển thị trên UI

### volume_alerts

| Trường | Có trong DB | Hiển thị UI | Ghi chú |
|---|---|---|---|
| `confirmed_at` | ✅ | ❌ | Timestamp khi confirm/cancel, chưa hiển thị |
| `ratio_15m` | ✅ | ✅ Trang detail | Chỉ hiện ở `/alerts/[id]`, không có ở list |
| `email_sent` | ✅ | ❌ | Flag nội bộ |
| `baseline_5d` | ✅ | ✅ Trang detail | Chỉ ở `/alerts/[id]` |
| `foreign_net` | ✅ | ✅ Trang detail | Chỉ ở `/alerts/[id]` |
| `bu_pct` | ✅ | ✅ (list + detail) | Hiển thị ở cả list và detail |

### cycle_events

| Trường | Có trong DB | Hiển thị UI | Ghi chú |
|---|---|---|---|
| `peak_volume` | ✅ | ✅ Trang detail `/cycles/[id]` | Chỉ ở detail |
| `breakout_price` | ✅ | ✅ Trang detail | Chỉ ở detail |
| `distributed_so_far` | ✅ | ❌ | Trùng với trading_days_elapsed, chưa dùng |
| `invalidation_reason` | ✅ | ✅ Trang detail | Hiện khi phase=invalidated |
| `breakout_email_sent` | ✅ | ✅ Trang detail | Chỉ ở `/cycles/[id]` |
| `alert_sent_10d` | ✅ | ✅ Trang detail | Chỉ ở `/cycles/[id]` |
| `alert_sent_bottom` | ✅ | ✅ Trang detail | Chỉ ở `/cycles/[id]` |
| `avg_vol_last3` | ✅ (nếu có) | ❌ | Không được query về |

### intraday_1m

Toàn bộ bảng này **không hiển thị trực tiếp trên UI**. Chỉ dùng để:
- Tính baseline
- M3 đếm volume tích lũy intraday
- M1 persistence (lưu bar nhưng không query lại cho UI)

Trang có thể thêm: chart intraday volume của ngày giao dịch cho từng ticker.

### volume_baselines

Không hiển thị trực tiếp. Có thể thêm: "Baseline hôm nay tại slot X = Y" vào trang alert detail.

### notification_log

Không hiển thị trên UI. Có thể thêm: lịch sử email đã gửi trong trang cycle detail.

---

## 12. Các ngưỡng & tham số

### Alert Engine M1

| Tham số | Giá trị | Mô tả |
|---|---|---|
| `THRESHOLD_NORMAL` | **2.0** | Ngưỡng trigger giờ bình thường |
| `THRESHOLD_MAGIC` | **1.5** | Ngưỡng trigger magic window |
| `THRESHOLD_CONFIRM_15M` | **1.3** | Ngưỡng xác nhận 15 phút |
| Redis throttle TTL | **1800s** (30 phút) | Thời gian chặn alert trùng slot |

### Alert Engine M3

| Tham số | Giá trị | Mô tả |
|---|---|---|
| `BREAKOUT_VOL_MULT` | **3.0** | Daily volume / MA20 để nhận diện breakout |
| `BREAKOUT_PRICE_PCT` | **0.03** (3%) | Biến động giá tối thiểu ngày breakout |
| `_BREAKOUT_ZONE_DOWN` | **0.97** (−3%) | Ngưỡng vô hiệu hóa cycle |
| `_BREAKOUT_ZONE_UP` | **1.05** (+5%) | Vùng kháng cự tham chiếu |
| `est_dist_days` | **20** | Số ngày phân phối dự kiến (hardcode) |
| `_REWATCH_WINDOW_DAYS` | **10** | Số ngày trong cửa sổ quan sát |
| `ALERT_DAYS_BEFORE_CYCLE` | **10** | Gửi cảnh báo khi còn ≤ N ngày |
| Bottoming threshold | **< 50% MA20** × 3 ngày liên tiếp | Điều kiện bottoming candidate |

### Stream

| Tham số | Giá trị | Mô tả |
|---|---|---|
| `BACKOFF_BASE` | **60s** | Thời gian chờ tối thiểu khi crash |
| `BACKOFF_MAX` | **300s** (5 phút) | Thời gian chờ tối đa |
| `_STALE_MINUTES` | **10** | Không có data N phút → watchdog restart |
| `_PROACTIVE_RESTART_SECS` | **3300s** (55 phút) | Restart trước khi JWT hết hạn |
| `_PROACTIVE_RESTART_WAIT` | **90s** | Chờ sau proactive restart |
| `_CONNECT_TIMEOUT_SECS` | **120s** | Grace period trước khi báo "error" |

---

## 13. Trạng thái & phân loại

### Alert status

| Status | Icon UI | Ý nghĩa |
|---|---|---|
| `fired` | ⏳ Chờ | Mới bắn, chờ 15 phút xác nhận |
| `confirmed` | ✅ Xác nhận | Volume tiếp tục cao trong 15 phút |
| `cancelled` | ❌ Không xác nhận | Volume không duy trì |

### Cycle phase

| Phase | Badge | Ý nghĩa |
|---|---|---|
| `distribution_in_progress` | 🟠 Phân phối | Đang trong giai đoạn phân phối (~20 ngày) |
| `bottoming_candidate` | 🟡 Tạo đáy | Tín hiệu kết thúc phân phối |
| `invalidated` | 🔴 Vô hiệu | Giá phá vùng breakout |
| `done` | ⬜ Hoàn tất | Manual close |

### Stream reason

| Reason | Màu | Thông điệp UI |
|---|---|---|
| `null` | Xanh lá | "Kết nối" (stream connected) |
| `outside_hours` | Xám | "Ngoài giờ" |
| `connecting` | Vàng | "Đang kết nối..." |
| `reconnecting` | Vàng | "Kết nối lại..." |
| `error` | Đỏ | "Lỗi kết nối" |

---

## 14. FAQ — Câu hỏi thường gặp

### Tại sao có 2 cột thời gian: bar_time và fired_at?

- **`bar_time`** = Thời điểm thực tế của bar dữ liệu trên thị trường (ICT → UTC)
- **`fired_at`** = Thời điểm hệ thống ghi vào DB (`DEFAULT NOW()`)

Hai cột này có thể chênh nhau vài giây (do xử lý) hoặc vài phút (nếu có backlog). Mọi logic nghiệp vụ (đếm alert hôm nay, lọc theo ngày, dedup) đều dùng `bar_time`. `fired_at` chỉ để biết hệ thống xử lý lúc nào.

### Tại sao alert list/heatmap đôi khi không khớp với số trên email?

Email gửi ngay khi alert bắn (chưa xác nhận). Số trên UI đếm theo `bar_time` = hôm nay, không phân biệt status. Nếu filter theo `status=confirmed` sẽ ra số ít hơn.

### Tại sao một ticker có thể có nhiều alerts trong cùng 1 phút?

Không thể. Dedup theo `(ticker, slot, DATE(bar_time))` — mỗi slot/ngày chỉ có 1 alert tối đa.

### Tại sao stream hiện "Đang kết nối..." lâu mà không có alert?

Có thể vì:
1. Chưa có dữ liệu baseline → `get_baseline()` trả về None → bỏ qua bar
2. Volume chưa đủ ngưỡng
3. FiinQuantX đang gửi data nhưng không có ticker nào vượt ngưỡng

Kiểm tra log backend: `M1 process error` hoặc baseline warnings.

### Tại sao cycle bị "Vô hiệu" ngay sau khi tạo?

Giá đóng cửa hôm nay đã dưới `breakout_zone_low` (−3% giá breakout). Có thể:
1. Dữ liệu giá không chính xác
2. Breakout "giả" — volume lớn nhưng giá không giữ được

### Tại sao có "Alert liên quan" thay vì "Alert nguồn"?

Chu kỳ này được tạo từ migration backfill (`source_alert_inferred=TRUE`). Alert được chọn theo heuristic (ratio_5d cao nhất trong ngày), không phải alert trực tiếp trigger cycle. Với cycle mới tạo từ M1→M3 intraday, luôn là "Alert nguồn" (`source_alert_inferred=FALSE`).

### Nếu Redis không cấu hình, hệ thống có hoạt động không?

Có. Redis là optional:
- Không có Redis → bỏ qua Redis throttle (chỉ còn DB dedup)
- Baseline cache chỉ dùng 2 tầng (memory + DB)
- Stream status hiển thị "Redis —" (disabled)

### BU% có nghĩa gì? Cao hay thấp là tốt?

`BU% = bu / (bu + sd) × 100`

- `bu` = Buy Up: số lệnh khớp ở giá trần/giá cao
- `sd` = Sell Down: số lệnh khớp ở giá sàn/giá thấp

**BU% cao** (> 60%) = áp lực mua chiếm ưu thế — tín hiệu tích cực khi có volume lớn.
**BU% thấp** (< 40%) = áp lực bán chiếm ưu thế — cẩn thận nếu đi kèm volume lớn.

### Foreign Net âm có nghĩa gì?

`foreign_net = fb - fs` (foreign buy - foreign sell)

- **Dương**: Khối ngoại mua ròng
- **Âm**: Khối ngoại bán ròng

Khi volume bất thường + foreign_net âm lớn → có thể tổ chức nước ngoài đang xả hàng.

### Confirmation rate là gì?

`confirmed_30d / total_30d × 100%`

Tỷ lệ alert được "xác nhận" (volume duy trì cao trong 15 phút sau) trong 30 ngày qua cho 1 ticker. Ticker có confirm rate cao → tín hiệu volume của nó thường đáng tin cậy hơn.

### Cửa sổ quan sát (Rewatch Window) có thay đổi không?

Có 2 trường hợp:
1. **Tạo cycle**: Cửa sổ = breakout_date + 20 ngày GD → +10 ngày GD
2. **Chuyển sang Bottoming**: Cửa sổ được cập nhật lại = ngày phát hiện bottoming → +10 ngày GD

Vì vậy cửa sổ quan sát cuối cùng thường sát hơn với thực tế.

### Tại sao estimated_dist_days luôn là 20?

Đây là hardcode dựa trên quan sát thực nghiệm. Có thể điều chỉnh trong tương lai dựa trên `game_type` (ví dụ: speculative có thể ngắn hơn 10–15 ngày).

---

*Tài liệu được tạo tự động từ source code. Cập nhật lần cuối: 2026-03.*
