# fbot Frontend Specification — Complete
**Version**: 1.0 | **Date**: 2026-03-22 | **Author**: PM (Claude)

---

## Overview

fbot frontend là React/Next.js dashboard cho agent/investor theo dõi cảnh báo chứng khoán real-time. Kết nối với FastAPI backend qua REST + SSE.

**Target user**: 1-2 người (Tuấn và Chú). Internal tool, không public.

---

## 1. Tech Stack

| Component | Technology |
|-----------|-----------|
| Framework | Next.js 14 (App Router) |
| Language | TypeScript strict |
| Styling | TailwindCSS + Shadcn UI |
| State | Zustand 5.x |
| Data Fetching | TanStack Query v5 (react-query) |
| Real-time | SSE via EventSource (native browser API) |
| Charts | Recharts |
| Date Formatting | date-fns (ICT timezone) |
| HTTP Client | Fetch API (native) |

---

## 2. Pages & Routes

```
/                    → redirect to /dashboard
/dashboard           → Main Dashboard (today's overview)
/alerts              → Alert Feed (full history + filters)
/alerts/[id]         → Alert Detail
/cycles              → Cycle Tracker (M3 active cycles)
/cycles/[id]         → Cycle Detail
/watchlist           → Watchlist overview (33 stocks summary)
/settings            → App Settings (thresholds, recipients)
```

---

## 3. Page Specifications

### 3.1 Dashboard (`/dashboard`)

**Purpose**: Tổng quan ngay khi mở app. Real-time alerts, heatmap, quick stats.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│  fbot  [●LIVE]                          [Settings]  │
├─────────────────────────────────────────────────────┤
│  Today: 22/03/2026 (Trading Day)                    │
│  Stream: ● Connected                                │
├────────────┬────────────┬────────────┬──────────────┤
│  Alerts    │  Confirmed │  Cycles    │  Emails Sent │
│  Today: 7  │   5/7      │  Active: 3 │     12       │
│  [stat]    │  [stat]    │  [stat]    │  [stat]      │
├─────────────────────────────────────────────────────┤
│  REAL-TIME ALERT FEED (SSE)                         │
│  ┌──────────────────────────────────────────────┐  │
│  │ 🔥 HPG  14:05  KL: 2.1x baseline  BU: 68%   │  │
│  │ ⚡ NVL  13:02  KL: 3.2x  [Magic Window]      │  │
│  │ ✅ ACB  11:15  KL: 1.8x  [Confirmed]         │  │
│  └──────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│  VOLUME HEATMAP (today's 33 stocks)                 │
│  [HPG][ACB][VCB][MBB]...(color = ratio intensity)  │
├─────────────────────────────────────────────────────┤
│  ACTIVE CYCLES                                      │
│  [NVL] Dist. D8/20  [PDR] Dist. D15/20  ...        │
└─────────────────────────────────────────────────────┘
```

**Components:**
- `<StreamStatusBadge>` — ● Connected / ○ Disconnected, auto-reconnect indicator
- `<StatCard>` — reusable stat tile
- `<LiveAlertFeed>` — SSE-powered real-time list, max 20 items, newest on top
- `<VolumeHeatmap>` — 33 ticker grid, color from gray→yellow→orange→red by ratio
- `<ActiveCyclesBanner>` — horizontal scroll cards for active cycles
- `<TradingDayBanner>` — shows if today is trading day, holidays highlighted

**Real-time behavior:**
- SSE connects on page mount via `useAlertStream()` hook
- New alert → prepend to list with flash animation (ring-2 ring-orange-400 for 2s)
- Heartbeat every 30s — no UI update (keepalive only)
- On SSE disconnect → show "Reconnecting..." badge, auto-retry every 5s

---

### 3.2 Alert Feed (`/alerts`)

**Purpose**: Full history of all alerts. Filter, sort, search.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│  Alert Feed                              [Export]   │
├──────────────────────────────────────────────────── │
│  [Ticker ▼] [Date Range] [Status ▼] [Magic Only □]  │
├─────────────────────────────────────────────────────┤
│  TICKER  TIME    VOLUME    RATIO   BU%   STATUS     │
│  HPG     14:05   1,234,500  2.1x   68%   Confirmed  │
│  NVL     13:02   3,456,000  3.2x   72%   ⚡Magic    │
│  ACB     11:15     890,000  1.8x   55%   Fired      │
│  ...                                                │
├─────────────────────────────────────────────────────┤
│  [← Prev]  Page 1 of 5  [Next →]                   │
└─────────────────────────────────────────────────────┘
```

**Components:**
- `<AlertFilters>` — ticker multi-select, date picker, status filter, magic window toggle
- `<AlertTable>` — sortable columns: time, volume, ratio, bu_pct, status
- `<AlertStatusBadge>` — fired/confirmed/cancelled with color coding
- `<Pagination>` — server-side pagination (limit=50, offset)

**Filter state**: persisted in URL params (`?ticker=HPG&date=2026-03-22&status=confirmed`)

---

### 3.3 Alert Detail (`/alerts/[id]`)

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│  ← Back to Alerts                                   │
│  Alert #1234 — HPG — 22/03/2026 14:05              │
├─────────────────────────────────────────────────────┤
│  Status: ✅ Confirmed (14:20)  |  Magic Window: No  │
├───────────────┬─────────────────────────────────────┤
│  Volume       │ 1,234,500                           │
│  Baseline 5d  │ 587,380                             │
│  Ratio        │ 2.1x                                │
│  BU %         │ 68.3%                               │
│  Foreign Net  │ +234,000                            │
│  15m Ratio    │ 1.45x (Confirmed)                   │
├─────────────────────────────────────────────────────┤
│  Related Cycle: [NVL D8/20 →]  (if linked)         │
│  Email: ✅ Sent at 14:05:12                         │
└─────────────────────────────────────────────────────┘
```

---

### 3.4 Cycle Tracker (`/cycles`)

**Purpose**: Theo dõi tất cả chu kỳ M3 đang active.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│  Cycle Tracker                                      │
│  [Phase ▼: All/Distributing/Bottoming/Done]         │
├─────────────────────────────────────────────────────┤
│  ACTIVE CYCLES                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │ NVL  Breakout: 10/03  Phase: Distributing     │  │
│  │ ████████░░░░░░░░  Day 8 of 20                 │  │
│  │ Predicted bottom: 07/04  ⏰ 10-day warning    │  │
│  └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐  │
│  │ PDR  Breakout: 05/03  Phase: Bottoming        │  │
│  │ ████████████████  Day 18 of 20               │  │
│  │ Predicted bottom: 01/04  🟢 Near bottom       │  │
│  └──────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│  COMPLETED CYCLES (last 30 days)                    │
│  [KBC  Breakout: 20/02  Done: 15/03]               │
└─────────────────────────────────────────────────────┘
```

**Components:**
- `<CycleCard>` — progress bar, phase badge, days remaining, predicted bottom
- `<CycleProgressBar>` — filled by `trading_days_elapsed / estimated_dist_days`
- `<PhaseBadge>` — distributing (orange) | bottoming (yellow) | done (green)

---

### 3.5 Cycle Detail (`/cycles/[id]`)

```
┌─────────────────────────────────────────────────────┐
│  ← Back to Cycles                                   │
│  Cycle #45 — NVL                                   │
├─────────────────────────────────────────────────────┤
│  Breakout Date:    10/03/2026                       │
│  Breakout Price:   15,600đ                         │
│  Peak Volume:      8,234,000                        │
│  Phase:            Distributing (Day 8/20)          │
│  Predicted Bottom: 07/04/2026                       │
├─────────────────────────────────────────────────────┤
│  Progress: ████████░░░░░░░░░░  40%                  │
├─────────────────────────────────────────────────────┤
│  Notifications:                                     │
│  ✅ Breakout email sent 10/03 08:30                 │
│  ⏳ 10-day warning: pending (Day 10)               │
│  ⏳ Bottom alert: pending                          │
└─────────────────────────────────────────────────────┘
```

---

### 3.6 Watchlist (`/watchlist`)

**Purpose**: Summary view of all 33 tickers.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│  Watchlist (33 stocks)                              │
│  [Search ticker]  [VN30 only □]                     │
├──────┬─────────────────┬──────────┬─────────────────┤
│Ticker│Company          │Today KL  │Active Cycle     │
├──────┼─────────────────┼──────────┼─────────────────┤
│ HPG  │Hoa Phat Group   │2 alerts  │—                │
│ NVL  │No Va Land       │1 alert   │D8/20 Dist.      │
│ ACB  │Asia Commercial  │—         │—                │
│ ...  │...              │...       │...              │
└──────┴─────────────────┴──────────┴─────────────────┘
```

---

### 3.7 Settings (`/settings`)

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│  Settings                                           │
├─────────────────────────────────────────────────────┤
│  ALERT THRESHOLDS                                   │
│  Normal Window Threshold:  [2.0] x baseline        │
│  Magic Window Threshold:   [1.5] x baseline        │
│  15-min Confirm Threshold: [1.3] x baseline        │
│                              [Save Thresholds]      │
├─────────────────────────────────────────────────────┤
│  STREAM STATUS                                      │
│  ● Connected to FiinQuantX                         │
│  Last data: 5s ago                                  │
│  Uptime: 4h 23m                                     │
├─────────────────────────────────────────────────────┤
│  SYSTEM HEALTH                                      │
│  Database: ✅  Redis: ✅  Stream: ✅  Email: ✅     │
└─────────────────────────────────────────────────────┘
```

---

## 4. TypeScript Types

```typescript
// types/alert.ts
export interface AlertSummary {
  id: number;
  ticker: string;
  fired_at: string;          // ISO UTC string
  slot: number;              // 0-329
  volume: number;
  ratio_5d: number | null;
  bu_pct: number | null;     // 0-100
  in_magic_window: boolean;
  status: 'fired' | 'confirmed' | 'cancelled';
}

export interface AlertDetail extends AlertSummary {
  baseline_5d: number | null;
  foreign_net: number | null;
  confirmed_at: string | null;
  ratio_15m: number | null;
}

// types/cycle.ts
export interface CycleSummary {
  id: number;
  ticker: string;
  breakout_date: string;     // YYYY-MM-DD
  phase: 'distributing' | 'bottoming' | 'done';
  days_remaining: number | null;
  predicted_bottom_date: string | null;
  trading_days_elapsed: number | null;
  estimated_dist_days: number | null;
}

export interface CycleDetail extends CycleSummary {
  peak_volume: number | null;
  breakout_price: number | null;
  alert_sent_10d: boolean;
  alert_sent_bottom: boolean;
}

// types/api.ts
export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// types/stream.ts
export type SSEEventType = 'volume_alert' | 'cycle_alert' | 'heartbeat';

export interface SSEEvent {
  type: SSEEventType;
  data: AlertSummary | CycleSummary | null;
}
```

---

## 5. API Client

```typescript
// lib/api.ts
const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  const json = await res.json();
  // MUST unwrap data.data per API contract
  if (!json.success) throw new Error(json.error ?? 'API Error');
  return json.data as T;
}

// Alerts
export const alertsApi = {
  list: (params: AlertListParams) =>
    apiFetch<{ alerts: AlertSummary[]; total: number }>(`/api/v1/alerts?${buildQuery(params)}`),
  get: (id: number) =>
    apiFetch<{ alert: AlertDetail }>(`/api/v1/alerts/${id}`),
  summaryToday: () =>
    apiFetch<{ total: number; confirmed: number }>(`/api/v1/alerts/summary/today`),
};

// Cycles
export const cyclesApi = {
  list: (params?: CycleListParams) =>
    apiFetch<{ cycles: CycleSummary[]; total: number }>(`/api/v1/cycles?${buildQuery(params)}`),
  get: (id: number) =>
    apiFetch<{ cycle: CycleDetail }>(`/api/v1/cycles/${id}`),
};

// Watchlist
export const watchlistApi = {
  list: () =>
    apiFetch<{ tickers: WatchlistItem[] }>(`/api/v1/watchlist`),
  summary: (ticker: string) =>
    apiFetch<WatchlistSummary>(`/api/v1/watchlist/${ticker}/summary`),
};

// Settings
export const settingsApi = {
  get: () =>
    apiFetch<AppSettings>(`/api/v1/settings`),
  updateThresholds: (body: ThresholdUpdate) =>
    apiFetch<{ updated: boolean }>(`/api/v1/settings/thresholds`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
};

// Health
export const healthApi = {
  check: () =>
    apiFetch<HealthStatus>(`/api/v1/health`),
};
```

---

## 6. SSE Hook

```typescript
// hooks/useAlertStream.ts
import { useEffect, useCallback } from 'react';
import { useAlertStore } from '@/stores/alertStore';

const SSE_URL = `${process.env.NEXT_PUBLIC_API_URL}/api/v1/stream/alerts`;
const RECONNECT_DELAY = 5000;

export function useAlertStream() {
  const addAlert = useAlertStore((s) => s.addAlert);
  const setStreamStatus = useAlertStore((s) => s.setStreamStatus);

  const connect = useCallback(() => {
    setStreamStatus('connecting');
    const es = new EventSource(SSE_URL);

    es.addEventListener('volume_alert', (e) => {
      const alert = JSON.parse(e.data) as AlertSummary;
      addAlert(alert);
    });

    es.addEventListener('cycle_alert', (e) => {
      // handled by cycle store
    });

    es.addEventListener('heartbeat', () => {
      // keepalive — no action
    });

    es.onerror = () => {
      setStreamStatus('disconnected');
      es.close();
      setTimeout(connect, RECONNECT_DELAY);
    };

    es.onopen = () => setStreamStatus('connected');

    return es;
  }, [addAlert, setStreamStatus]);

  useEffect(() => {
    const es = connect();
    return () => es.close();
  }, [connect]);
}
```

---

## 7. Zustand Stores

```typescript
// stores/alertStore.ts
interface AlertState {
  liveAlerts: AlertSummary[];       // real-time SSE list, max 50
  streamStatus: 'connected' | 'disconnected' | 'connecting';
  addAlert: (alert: AlertSummary) => void;
  setStreamStatus: (status: AlertState['streamStatus']) => void;
}

export const useAlertStore = create<AlertState>()((set) => ({
  liveAlerts: [],
  streamStatus: 'connecting',
  addAlert: (alert) =>
    set((s) => ({
      liveAlerts: [alert, ...s.liveAlerts].slice(0, 50),
    })),
  setStreamStatus: (status) => set({ streamStatus: status }),
}));

// stores/settingsStore.ts
interface SettingsState {
  thresholds: {
    normal: number;
    magic: number;
    confirm: number;
  };
  setThresholds: (t: Partial<SettingsState['thresholds']>) => void;
}
```

---

## 8. TanStack Query Setup

```typescript
// app/providers.tsx
'use client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,   // 30s (alerts are semi-realtime via SSE)
      retry: 2,
    },
  },
});

export function Providers({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
```

**Query keys:**
```typescript
export const queryKeys = {
  alerts: {
    list: (params: AlertListParams) => ['alerts', 'list', params] as const,
    detail: (id: number) => ['alerts', 'detail', id] as const,
    today: () => ['alerts', 'today'] as const,
  },
  cycles: {
    list: (params?: CycleListParams) => ['cycles', 'list', params] as const,
    detail: (id: number) => ['cycles', 'detail', id] as const,
  },
  health: () => ['health'] as const,
};
```

---

## 9. Utilities

```typescript
// lib/formatters.ts
import { format, toZonedTime } from 'date-fns-tz';
const ICT = 'Asia/Ho_Chi_Minh';

export function formatAlertTime(utcIso: string): string {
  const ict = toZonedTime(new Date(utcIso), ICT);
  return format(ict, 'HH:mm', { timeZone: ICT });
}

export function formatDateICT(utcIso: string): string {
  const ict = toZonedTime(new Date(utcIso), ICT);
  return format(ict, 'dd/MM/yyyy HH:mm', { timeZone: ICT });
}

export function formatVolume(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return v.toLocaleString();
}

export function formatRatio(r: number | null): string {
  if (r == null) return '—';
  return `${r.toFixed(2)}x`;
}

export function slotToTime(slot: number): string {
  // slot 0 = 09:00, slot 150 = 13:00
  if (slot < 150) {
    const totalMin = 9 * 60 + slot;
    return `${Math.floor(totalMin / 60).toString().padStart(2, '0')}:${(totalMin % 60).toString().padStart(2, '0')}`;
  } else {
    const totalMin = 13 * 60 + (slot - 150);
    return `${Math.floor(totalMin / 60).toString().padStart(2, '0')}:${(totalMin % 60).toString().padStart(2, '0')}`;
  }
}
```

---

## 10. Component Library

### 10.1 Shared Components

```typescript
// components/StreamStatusBadge.tsx
// Props: status: 'connected' | 'disconnected' | 'connecting'
// Renders: ● Connected (green) | ○ Disconnected (red) | ◌ Connecting (gray, pulse)

// components/AlertStatusBadge.tsx
// Props: status: 'fired' | 'confirmed' | 'cancelled'
// fired = orange, confirmed = green, cancelled = gray

// components/StatCard.tsx
// Props: title: string, value: string | number, subtitle?: string, color?: 'default' | 'warning' | 'success'

// components/PhaseBadge.tsx
// Props: phase: 'distributing' | 'bottoming' | 'done'
// distributing = orange, bottoming = yellow, done = green

// components/CycleProgressBar.tsx
// Props: elapsed: number, total: number
// Renders filled progress bar with % label
```

### 10.2 Layout

```typescript
// components/Layout.tsx
// Sidebar nav: Dashboard | Alerts | Cycles | Watchlist | Settings
// Top bar: fbot logo, stream status badge, today's date/trading status
// Mobile: bottom nav bar (5 tabs)
```

---

## 11. Volume Heatmap Component

```typescript
// components/VolumeHeatmap.tsx
// Data: GET /api/v1/alerts/summary/today returns by_ticker: { ticker: ratio }
// Renders: 33 ticker chips colored by ratio intensity
//   ratio < 1.5:  bg-gray-200   (normal)
//   ratio 1.5-2:  bg-yellow-300 (elevated)
//   ratio 2-3:    bg-orange-400 (high)
//   ratio >= 3:   bg-red-500    (extreme)
// Click on ticker → /watchlist/{ticker}
// Refresh: every 60s via React Query refetch
```

---

## 12. Mobile Responsiveness

- **Dashboard**: StatCards in 2×2 grid on mobile, 4×1 on desktop
- **Alert Feed**: Full-width table becomes stacked cards on mobile (`< 640px`)
- **Cycle Tracker**: Cards stack vertically on all screen sizes
- **Bottom nav** on mobile (`< 768px`): Dashboard | Alerts | Cycles | Watchlist | Settings
- **Sidebar nav** on desktop (`>= 768px`)
- All timestamps shown in ICT (UTC+7), format `dd/MM HH:mm`

---

## 13. Next.js Project Structure

```
frontend/
├── app/
│   ├── layout.tsx           # Root layout + Providers
│   ├── page.tsx             # redirect to /dashboard
│   ├── dashboard/
│   │   └── page.tsx
│   ├── alerts/
│   │   ├── page.tsx         # Alert Feed
│   │   └── [id]/
│   │       └── page.tsx     # Alert Detail
│   ├── cycles/
│   │   ├── page.tsx
│   │   └── [id]/
│   │       └── page.tsx
│   ├── watchlist/
│   │   └── page.tsx
│   └── settings/
│       └── page.tsx
├── components/
│   ├── ui/                  # Shadcn UI base components
│   ├── StreamStatusBadge.tsx
│   ├── AlertStatusBadge.tsx
│   ├── StatCard.tsx
│   ├── CycleProgressBar.tsx
│   ├── PhaseBadge.tsx
│   ├── VolumeHeatmap.tsx
│   ├── LiveAlertFeed.tsx
│   └── Layout.tsx
├── hooks/
│   ├── useAlertStream.ts
│   └── useHealth.ts
├── lib/
│   ├── api.ts
│   └── formatters.ts
├── stores/
│   ├── alertStore.ts
│   └── settingsStore.ts
├── types/
│   ├── alert.ts
│   ├── cycle.ts
│   └── api.ts
├── .env.local.example
├── next.config.ts
└── package.json
```

**next.config.ts:**
```typescript
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL}/api/:path*`,
      },
    ];
  },
};
export default nextConfig;
```

---

## 14. Environment Variables (Frontend)

```bash
# .env.local
NEXT_PUBLIC_API_URL=http://localhost:8000

# .env.production
NEXT_PUBLIC_API_URL=https://api.fbot.internal
```

---

## 15. S1 — Error States & Validation

| Scenario | Handling |
|----------|----------|
| API unreachable | TanStack Query retry 2x → show toast "Không kết nối được server" |
| SSE disconnected | StreamStatusBadge shows red "Disconnected" → auto-retry every 5s |
| Alert list empty | Show empty state: "Chưa có cảnh báo hôm nay" |
| Cycle list empty | Show empty state: "Không có chu kỳ đang theo dõi" |
| API returns `success: false` | apiFetch throws Error → caught by React Query error boundary |
| Invalid route `/alerts/999` | 404 page: "Không tìm thấy cảnh báo #999" |
| Network timeout | Fetch signal with 10s timeout → show "Timeout. Thử lại?" |
| Settings save fails | Inline error message under form, no toast |

---

## 16. S2 — Post-Completion Flow

| Action | Immediate | Next |
|--------|-----------|------|
| SSE volume_alert received | Alert prepended to LiveAlertFeed + flash animation | Auto-clears flash after 2s |
| SSE cycle_alert received | Cycle card badge updates | Cycles page re-fetches if open |
| Save thresholds | Success toast "Đã lưu" | Settings page reloads values |
| Click ticker in heatmap | Navigate to /watchlist/{ticker} | |
| Click alert row | Navigate to /alerts/{id} | |

---

## 17. S3 — Cross-Feature Integration

- **Dashboard ↔ SSE**: `useAlertStream()` hook runs on Dashboard mount. SSE pushes to Zustand store → LiveAlertFeed re-renders
- **Dashboard ↔ Cycles**: ActiveCyclesBanner fetches `/api/v1/cycles?phase=distributing,bottoming`
- **Alert Detail ↔ Cycle**: If `cycle_event_id` not null → show "Related Cycle" link
- **Settings → Alert Engine**: Threshold changes via PUT saved in DB → backend immediately uses new thresholds (no restart needed)
- **Watchlist ↔ Alerts**: Watchlist table shows `today_alerts` count from `/api/v1/watchlist/{ticker}/summary`

---

## 18. S5 — State & Persistence Matrix

| State | Storage | Notes |
|-------|---------|-------|
| Live alert stream | Zustand (memory) | Max 50 items, no persistence |
| Stream status | Zustand (memory) | Reset on page reload |
| Alert list filters | URL params | Persisted in URL for bookmarking |
| Alert list data | React Query cache | 30s stale time, refetch on focus |
| Settings form | React component state | Saved to backend on submit |
| Theme (dark/light) | localStorage | (future, not MVP) |

---

## 19. S6 — Manual QA Scenarios

1. **Dashboard loads**: Open `/dashboard` → stat cards show today's counts → heatmap renders 33 tickers
2. **SSE connects**: Stream badge shows "● Connected" within 3s → no console errors
3. **Live alert**: When backend fires alert → it appears at top of LiveAlertFeed within 1s with orange flash
4. **SSE disconnect**: Kill backend → badge turns red "Disconnected" → restart backend → auto-reconnects, badge turns green
5. **Alert list**: Open `/alerts` → table shows paginated alerts → filter by ticker "HPG" → only HPG rows
6. **Alert detail**: Click alert row → `/alerts/{id}` → all fields populated, timestamps in ICT
7. **Cycle tracker**: `/cycles` → active cycles show progress bars → phase badges correct color
8. **Save thresholds**: Change normal threshold to 2.5 → Save → refresh → shows 2.5
9. **Mobile**: Open on 375px width → bottom nav visible, tables become cards
10. **Empty state**: Query with filter that returns no results → shows Vietnamese empty state message (not blank/null)

---

## 20. Design Tokens (Shadcn/TailwindCSS)

```typescript
// tailwind.config.ts additions
colors: {
  alert: {
    fired: '#f97316',      // orange-500
    confirmed: '#22c55e',  // green-500
    cancelled: '#9ca3af',  // gray-400
  },
  ratio: {
    normal: '#e5e7eb',     // gray-200
    elevated: '#fde047',   // yellow-300
    high: '#fb923c',       // orange-400
    extreme: '#ef4444',    // red-500
  },
  phase: {
    distributing: '#f97316',  // orange-500
    bottoming: '#eab308',     // yellow-500
    done: '#22c55e',          // green-500
  },
}
```

---

## 21. Performance Considerations

- LiveAlertFeed: `max 50 items` in Zustand to prevent memory leak
- Heatmap: React Query `refetchInterval: 60_000` — NOT every second
- Alert list: server-side pagination, no client-side filtering (all filtering via API params)
- SSE: single EventSource per app session (not per component)
- No `useEffect` polling — all updates via SSE or React Query auto-refetch
