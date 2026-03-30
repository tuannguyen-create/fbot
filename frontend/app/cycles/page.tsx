'use client'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { cyclesApi, healthApi } from '@/lib/api'
import { CycleProgressBar } from '@/components/CycleProgressBar'
import { PhaseBadge } from '@/components/PhaseBadge'
import { OriginBadge } from '@/components/OriginBadge'
import { M3Guide } from '@/components/ScannerGuide'
import { formatDateICT, formatRatio, formatVolume } from '@/lib/formatters'
import Link from 'next/link'

export default function CyclesPage() {
  const [phase, setPhase] = useState<string>('distribution_in_progress,bottoming_candidate')

  const { data, isLoading } = useQuery({
    queryKey: ['cycles', 'list', phase],
    queryFn: () => cyclesApi.list({ phase: phase || undefined, limit: 50 }),
    refetchInterval: 60_000,
  })

  const cycles = data?.cycles ?? []
  const { data: candidatesData, isLoading: candidatesLoading } = useQuery({
    queryKey: ['cycles', 'candidates'],
    queryFn: () => cyclesApi.candidates({ days: 25, limit: 50 }),
    refetchInterval: 5 * 60_000,
  })
  const { data: health } = useQuery({
    queryKey: ['health', 'cycles-page'],
    queryFn: () => healthApi.check(),
    refetchInterval: 60_000,
  })
  const candidates = candidatesData?.candidates ?? []

  return (
    <div className="space-y-4 max-w-3xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Theo dõi chu kỳ</h1>
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

      <M3Guide
        activeTickers={health?.active_ticker_count}
        coveredTickers={candidatesData?.tickers_with_data}
      />

      <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-sm text-gray-600">
        <p><b>Candidate</b> là breakout daily vừa quét ra. <b>Cycle</b> là mã đã được đưa vào luồng theo dõi nhiều ngày.</p>
        <p className="mt-1"><b>Đang phân phối</b> = sau breakout. <b>Tạo đáy</b> = có dấu hiệu cạn cung, chuẩn bị vào cửa sổ quan sát.</p>
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
                  <OriginBadge origin={c.origin} />
                  {c.game_type && (
                    <span className="text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">
                      {c.game_type}
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-400">
                  Đột phá: {formatDateICT(c.breakout_date)}
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

      <div className="pt-2">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h2 className="text-sm font-semibold text-gray-800">Breakout M3 gần đây</h2>
            <p className="text-xs text-gray-400">
              Scan daily 25 ngày, hiện có dữ liệu cho {candidatesData?.tickers_with_data ?? '—'} mã
            </p>
          </div>
          <span className="text-xs text-gray-400">{candidatesData?.total ?? 0} candidates</span>
        </div>

        {candidatesLoading ? (
          <div className="text-center py-8 text-gray-400">Đang tải breakout gần đây...</div>
        ) : candidates.length === 0 ? (
          <div className="text-center py-8 text-gray-400 border border-dashed border-gray-200 rounded-lg">
            Chưa có breakout M3 nào để hiển thị
          </div>
        ) : (
          <div className="space-y-3">
            {candidates.map((c) => (
              <div key={`${c.ticker}-${c.breakout_date}`} className="bg-white rounded-lg border border-gray-200 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-lg font-bold text-gray-900">{c.ticker}</span>
                    {c.game_type && (
                      <span className="text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">
                        {c.game_type}
                      </span>
                    )}
                    {c.cycle_id ? (
                      <Link href={`/cycles/${c.cycle_id}`} className="text-xs text-orange-600 hover:underline">
                        Đã tạo cycle →
                      </Link>
                    ) : (
                      <span className="text-xs text-blue-600 bg-blue-50 px-2 py-0.5 rounded">Candidate</span>
                    )}
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-semibold text-orange-600">{formatRatio(c.vol_ratio)}</div>
                    <div className="text-xs text-gray-400">{formatDateICT(c.breakout_date)}</div>
                  </div>
                </div>
                <div className="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-xs text-gray-500">
                  <div>KL: <b>{formatVolume(c.volume)}</b></div>
                  <div>Tăng giá: <b>{c.price_change_pct.toFixed(2)}%</b></div>
                  <div>MA20 vol: <b>{formatVolume(c.ma20_used)}</b></div>
                  <div>Giá đóng: <b>{c.close.toLocaleString()}</b></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
