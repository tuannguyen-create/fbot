'use client'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { watchlistApi } from '@/lib/api'
import { PhaseBadge } from '@/components/PhaseBadge'
import Link from 'next/link'
import { use } from 'react'

const GAME_TYPE_STYLES: Record<string, string> = {
  speculative:      'bg-orange-100 text-orange-700',
  state_enterprise: 'bg-blue-100 text-blue-700',
  institutional:    'bg-gray-100 text-gray-600',
}

export default function TickerDetailPage({ params }: { params: Promise<{ ticker: string }> }) {
  const { ticker } = use(params)
  const queryClient = useQueryClient()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['watchlist-summary', ticker],
    queryFn: () => watchlistApi.summary(ticker),
  })

  const { data: wlData } = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => watchlistApi.list(),
  })

  const wlItem = wlData?.tickers.find((t) => t.ticker === ticker)

  const m3Mutation = useMutation({
    mutationFn: (eligible_for_m3: boolean) =>
      watchlistApi.updateM3(ticker, { eligible_for_m3 }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchlist'] })
      queryClient.invalidateQueries({ queryKey: ['watchlist-summary', ticker] })
    },
  })

  if (isLoading) return <div className="text-center py-12 text-gray-400">Đang tải...</div>
  if (isError || !data) return (
    <div className="text-center py-12 text-red-400">
      Không tìm thấy ticker <strong>{ticker}</strong>.{' '}
      <Link href="/watchlist" className="underline">Quay lại</Link>
    </div>
  )

  const cycle = data.active_cycle

  return (
    <div className="space-y-6 max-w-2xl mx-auto">
      <div className="flex items-center gap-3">
        <Link href="/watchlist" className="text-sm text-gray-400 hover:text-gray-600">← Watchlist</Link>
      </div>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{ticker}</h1>
          {data.company_name && (
            <p className="text-sm text-gray-500 mt-0.5">{data.company_name}</p>
          )}
        </div>
        {wlItem && (
          <div className="flex items-center gap-2">
            {wlItem.game_type && (
              <span className={`inline-flex px-2 py-0.5 rounded text-xs ${GAME_TYPE_STYLES[wlItem.game_type] ?? 'bg-gray-100 text-gray-500'}`}>
                {wlItem.game_type}
              </span>
            )}
            <button
              title={wlItem.eligible_for_m3 ? 'M3 đang bật — click để tắt' : 'M3 đang tắt — click để bật'}
              onClick={() => m3Mutation.mutate(!wlItem.eligible_for_m3)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium border transition-colors ${
                wlItem.eligible_for_m3
                  ? 'bg-orange-50 border-orange-300 text-orange-700'
                  : 'bg-gray-50 border-gray-300 text-gray-500'
              }`}
            >
              M3 {wlItem.eligible_for_m3 ? 'ON' : 'OFF'}
            </button>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-xs text-gray-500 uppercase mb-1">Alerts hôm nay</div>
          <div className="text-2xl font-bold text-gray-900">{data.today_alerts}</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-xs text-gray-500 uppercase mb-1">Chu kỳ M3</div>
          <div className="mt-0.5">
            {cycle ? <PhaseBadge phase={cycle.phase} /> : <span className="text-sm text-gray-400">Không có</span>}
          </div>
        </div>
      </div>

      {cycle && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
          <h2 className="text-sm font-semibold text-gray-700">Chu kỳ hiện tại</h2>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <div>
              <span className="text-gray-500">Breakout: </span>
              <span className="font-medium">{cycle.breakout_date ?? '—'}</span>
            </div>
            {cycle.breakout_zone_low != null && (
              <div>
                <span className="text-gray-500">Vùng breakout: </span>
                <span className="font-medium">{cycle.breakout_zone_low.toLocaleString()}{cycle.breakout_zone_high ? ` – ${cycle.breakout_zone_high.toLocaleString()}` : ''}</span>
              </div>
            )}
            {cycle.rewatch_window_start && (
              <div className="col-span-2">
                <span className="text-gray-500">Cửa sổ quan sát: </span>
                <span className="font-medium">{cycle.rewatch_window_start} → {cycle.rewatch_window_end ?? '?'}</span>
              </div>
            )}
            {cycle.phase_reason && (
              <div className="col-span-2 text-gray-500 italic text-xs">{cycle.phase_reason}</div>
            )}
          </div>
          <Link
            href={`/cycles/${cycle.id}`}
            className="inline-block text-xs text-orange-600 hover:underline"
          >
            Xem chi tiết chu kỳ →
          </Link>
        </div>
      )}
    </div>
  )
}
