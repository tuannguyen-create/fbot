'use client'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { cyclesApi } from '@/lib/api'
import { CycleProgressBar } from '@/components/CycleProgressBar'
import { PhaseBadge } from '@/components/PhaseBadge'
import { formatDateICT } from '@/lib/formatters'
import Link from 'next/link'

export default function CyclesPage() {
  const [phase, setPhase] = useState<string>('distribution_in_progress,bottoming_candidate')

  const { data, isLoading } = useQuery({
    queryKey: ['cycles', 'list', phase],
    queryFn: () => cyclesApi.list({ phase: phase || undefined, limit: 50 }),
    refetchInterval: 60_000,
  })

  const cycles = data?.cycles ?? []

  return (
    <div className="space-y-4 max-w-3xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Cycle Tracker</h1>
        <select
          value={phase}
          onChange={(e) => setPhase(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm"
        >
          <option value="distribution_in_progress,bottoming_candidate">Đang active</option>
          <option value="distribution_in_progress">Đang phân phối</option>
          <option value="bottoming_candidate">Tạo đáy</option>
          <option value="invalidated">Vô hiệu</option>
          <option value="">Tất cả</option>
        </select>
      </div>

      {isLoading ? (
        <div className="text-center py-8 text-gray-400">Đang tải...</div>
      ) : cycles.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          <p className="text-3xl mb-2">📊</p>
          <p>Không có chu kỳ nào đang theo dõi</p>
        </div>
      ) : (
        <div className="space-y-3">
          {cycles.map((c) => (
            <Link
              key={c.id}
              href={`/cycles/${c.id}`}
              className="block bg-white rounded-lg border border-gray-200 p-4 hover:border-orange-300 transition-colors"
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="text-lg font-bold text-gray-900">{c.ticker}</span>
                  <PhaseBadge phase={c.phase} />
                  {c.game_type && (
                    <span className="text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">
                      {c.game_type}
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-400">
                  Breakout: {formatDateICT(c.breakout_date)}
                </div>
              </div>

              <CycleProgressBar
                elapsed={c.trading_days_elapsed ?? 0}
                total={c.estimated_dist_days ?? 20}
                phase={c.phase}
              />

              {c.rewatch_window_start ? (
                <div className="mt-2 flex items-center justify-between text-xs text-gray-500">
                  <span>
                    Cửa sổ quan sát: <b>{formatDateICT(c.rewatch_window_start)}</b>
                    {c.rewatch_window_end && <> → <b>{formatDateICT(c.rewatch_window_end)}</b></>}
                  </span>
                  {c.days_remaining != null && (
                    <span className={`font-medium ${c.days_remaining <= 10 ? 'text-orange-600' : ''}`}>
                      {c.days_remaining <= 10 ? '⏰ ' : ''}Còn {c.days_remaining} ngày
                    </span>
                  )}
                </div>
              ) : c.invalidation_reason ? (
                <p className="mt-1 text-xs text-red-400">{c.invalidation_reason}</p>
              ) : null}
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
