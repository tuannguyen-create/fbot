'use client'
import { useQuery } from '@tanstack/react-query'
import { alertsApi, cyclesApi, healthApi } from '@/lib/api'
import { StatCard } from '@/components/StatCard'
import { LiveAlertFeed } from '@/components/LiveAlertFeed'
import { VolumeHeatmap } from '@/components/VolumeHeatmap'
import { CycleProgressBar } from '@/components/CycleProgressBar'
import { PhaseBadge } from '@/components/PhaseBadge'
import { M1Guide, M3Guide } from '@/components/ScannerGuide'
import { formatDateICT, formatRatio } from '@/lib/formatters'
import Link from 'next/link'

export default function DashboardPage() {
  const { data: todaySummary } = useQuery({
    queryKey: ['alerts', 'today'],
    queryFn: () => alertsApi.summaryToday(),
    refetchInterval: 30_000,
  })

  const { data: cyclesData } = useQuery({
    queryKey: ['cycles', 'active'],
    queryFn: () => cyclesApi.list({ phase: 'distribution_in_progress,bottoming_candidate', limit: 5 }),
    refetchInterval: 60_000,
  })

  const { data: candidatesData, isError: candidatesError } = useQuery({
    queryKey: ['cycles', 'candidates', 'dashboard'],
    queryFn: () => cyclesApi.candidates({ days: 25, limit: 5 }),
    retry: 1,
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
  })

  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => healthApi.check(),
    refetchInterval: 30_000,
  })

  const today = new Date().toLocaleDateString('vi-VN', { weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Asia/Ho_Chi_Minh' })
  const cycles = cyclesData?.cycles ?? []
  const candidates = candidatesData?.candidates ?? []

  return (
    <div className="space-y-4 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Tổng quan</h1>
          <p className="text-sm text-gray-500">{today}</p>
        </div>
        {health && (
          <div className="flex gap-2 text-xs">
            <span className={health.db === 'ok' ? 'text-green-600' : 'text-red-600'}>
              CSDL {health.db === 'ok' ? '✅' : '❌'}
            </span>
            <span className={
              health.redis === 'ok' ? 'text-green-600' :
              health.redis === 'disabled' ? 'text-gray-400' : 'text-red-600'
            }>
              Redis {health.redis === 'ok' ? '✅' : health.redis === 'disabled' ? '—' : '❌'}
            </span>
          </div>
        )}
      </div>

      {/* === Phiên hôm nay === */}
      <div className="flex items-center gap-2">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Phiên hôm nay</h2>
        <div className="flex-1 h-px bg-gray-100" />
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          title="Alerts hôm nay"
          value={todaySummary?.total ?? '—'}
          subtitle="Tổng cảnh báo"
          color={todaySummary?.total ? 'warning' : 'default'}
        />
        <StatCard
          title="Xác nhận"
          value={todaySummary ? `${todaySummary.confirmed}/${todaySummary.total}` : '—'}
          subtitle="Xác nhận 15 phút"
          color="success"
        />
        <StatCard
          title="Chu kỳ hoạt động"
          value={cyclesData?.total ?? '—'}
          subtitle="Đang theo dõi"
          color="default"
        />
        <StatCard
          title="Kết nối"
          value={
            !health ? '—' :
            health.stream === 'connected' ? 'Kết nối' :
            health.stream_reason === 'outside_hours' ? 'Ngoài giờ' :
            health.stream_reason === 'connecting' ? 'Đang kết nối...' :
            health.stream_reason === 'reconnecting' ? 'Kết nối lại...' :
            health.stream_reason === 'error' ? 'Lỗi kết nối' :
            'Mất kết nối'
          }
          color={
            !health ? 'default' :
            health.stream === 'connected' ? 'success' :
            health.stream_reason === 'outside_hours' ? 'default' :
            health.stream_reason === 'connecting' ? 'warning' :
            health.stream_reason === 'reconnecting' ? 'warning' :
            'danger'
          }
        />
      </div>

      <div className="grid md:grid-cols-2 gap-3">
        <M1Guide
          activeTickers={health?.active_ticker_count}
          effectiveTickers={health?.effective_intraday_ticker_count ?? health?.effective_ticker_count}
        />
        <M3Guide
          activeTickers={health?.active_ticker_count}
          coveredTickers={candidatesData?.tickers_with_data ?? health?.effective_daily_ticker_count}
        />
      </div>

      {/* Live feed */}
      <div>
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Cảnh báo M1 trực tiếp</h2>
        <LiveAlertFeed />
      </div>

      {/* Heatmap */}
      <VolumeHeatmap />

      {/* === Đang theo dõi === */}
      {cycles.length > 0 && (
      <div className="flex items-center gap-2">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Đang theo dõi</h2>
        <div className="flex-1 h-px bg-gray-100" />
      </div>
      )}

      {/* Active cycles */}
      {cycles.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-gray-700">Chu kỳ đang theo dõi</h3>
            <Link href="/cycles" className="text-xs text-orange-600 hover:underline">Xem tất cả →</Link>
          </div>
          <div className="space-y-3">
            {cycles.map((c) => (
              <div key={c.id} className="hover:bg-gray-50 -mx-2 px-2 py-1 rounded">
                <Link href={`/cycles/${c.id}`} className="block">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-sm">{c.ticker}</span>
                      <span className="text-xs text-gray-400">đột phá {c.breakout_date}</span>
                    </div>
                    <PhaseBadge phase={c.phase} />
                  </div>
                  <CycleProgressBar
                    elapsed={c.trading_days_elapsed ?? 0}
                    total={c.estimated_dist_days ?? 20}
                    phase={c.phase}
                  />
                  {c.rewatch_window_start && (
                    <p className="text-xs text-gray-400 mt-1">
                      Cửa sổ quan sát: {formatDateICT(c.rewatch_window_start)}
                      {c.rewatch_window_end && ` → ${formatDateICT(c.rewatch_window_end)}`}
                      {c.days_remaining != null && ` (còn ${c.days_remaining} ngày)`}
                    </p>
                  )}
                </Link>
                {c.source_alert_id && (
                  <Link
                    href={`/alerts/${c.source_alert_id}`}
                    className="text-xs text-orange-500 hover:underline"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {c.source_alert_inferred ? 'Alert liên quan' : 'Alert nguồn'} #{c.source_alert_id} →
                  </Link>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {candidatesError && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-700">
          Không tải được danh sách breakout M3 gần đây. Backend scan đang chậm hoặc vừa timeout.
        </div>
      )}

      {candidates.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-700">Breakout M3 gần đây</h3>
              <p className="text-xs text-gray-400">Daily scan 25 ngày, không cần đợi cycle materialize</p>
            </div>
            <Link href="/cycles" className="text-xs text-orange-600 hover:underline">Xem danh sách →</Link>
          </div>
          <div className="space-y-2">
            {candidates.map((c) => (
              <div key={`${c.ticker}-${c.breakout_date}`} className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold">{c.ticker}</span>
                  <span className="text-gray-400">đột phá {formatDateICT(c.breakout_date)}</span>
                  {c.cycle_id ? (
                    <Link href={`/cycles/${c.cycle_id}`} className="text-xs text-orange-600 hover:underline">
                      cycle →
                    </Link>
                  ) : (
                    <span className="text-xs text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded">candidate</span>
                  )}
                </div>
                <div className="text-right">
                  <div className="font-semibold text-orange-600">{formatRatio(c.vol_ratio)}</div>
                  <div className="text-xs text-gray-400">+{c.price_change_pct.toFixed(2)}%</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
