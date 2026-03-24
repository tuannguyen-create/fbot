'use client'
import { useQuery } from '@tanstack/react-query'
import { cyclesApi } from '@/lib/api'
import { CycleProgressBar } from '@/components/CycleProgressBar'
import { PhaseBadge } from '@/components/PhaseBadge'
import { formatDateICT, formatVolume } from '@/lib/formatters'
import Link from 'next/link'

interface Props {
  params: { id: string }
}

export default function CycleDetailPage({ params }: Props) {
  const id = Number(params.id)

  const { data, isLoading, error } = useQuery({
    queryKey: ['cycles', 'detail', id],
    queryFn: () => cyclesApi.get(id),
    enabled: !isNaN(id),
  })

  const cycle = data?.cycle

  if (isLoading) return <div className="text-center py-8 text-gray-400">Đang tải...</div>
  if (error || !cycle) return (
    <div className="text-center py-12 text-gray-400">
      <p className="text-2xl mb-2">❌</p>
      <p>Không tìm thấy chu kỳ #{id}</p>
      <Link href="/cycles" className="text-orange-600 text-sm hover:underline mt-2 block">← Quay lại</Link>
    </div>
  )

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <Link href="/cycles" className="text-sm text-gray-500 hover:text-gray-700">← Quay lại Cycles</Link>

      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Cycle #{cycle.id} — {cycle.ticker}</h1>
            <p className="text-sm text-gray-500">Breakout: {formatDateICT(cycle.breakout_date)}</p>
          </div>
          <div className="flex items-center gap-2">
            <PhaseBadge phase={cycle.phase} />
            {cycle.game_type && (
              <span className="text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">
                {cycle.game_type}
              </span>
            )}
          </div>
        </div>

        {cycle.phase_reason && (
          <p className="text-sm text-gray-600 bg-gray-50 rounded p-2 mb-4">
            {cycle.phase_reason}
          </p>
        )}

        <div className="mb-4">
          <CycleProgressBar
            elapsed={cycle.trading_days_elapsed ?? 0}
            total={cycle.estimated_dist_days ?? 20}
            phase={cycle.phase}
          />
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Giá breakout</p>
            <p className="font-semibold">
              {cycle.breakout_price ? `${cycle.breakout_price.toLocaleString()}đ` : 'N/A'}
            </p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Volume đỉnh</p>
            <p className="font-semibold">{formatVolume(cycle.peak_volume)}</p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Phân phối dự kiến</p>
            <p className="font-semibold">{cycle.estimated_dist_days ?? 20} ngày GD</p>
          </div>
          <div className="bg-orange-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Cửa sổ quan sát</p>
            <p className="font-semibold text-orange-700 text-xs">
              {cycle.rewatch_window_start
                ? `${formatDateICT(cycle.rewatch_window_start)} → ${formatDateICT(cycle.rewatch_window_end ?? cycle.rewatch_window_start)}`
                : 'N/A'}
            </p>
          </div>
          {cycle.breakout_zone_low && (
            <div className="bg-red-50 rounded p-3">
              <p className="text-xs text-gray-400 uppercase mb-1">Ngưỡng vô hiệu</p>
              <p className="font-semibold text-red-600">
                &lt; {cycle.breakout_zone_low.toLocaleString()}đ
              </p>
            </div>
          )}
          {cycle.invalidation_reason && (
            <div className="bg-red-50 rounded p-3 col-span-2">
              <p className="text-xs text-gray-400 uppercase mb-1">Lý do vô hiệu hóa</p>
              <p className="font-semibold text-red-600">{cycle.phase_reason}</p>
            </div>
          )}
        </div>

        <div className="mt-4 pt-4 border-t border-gray-100 space-y-1 text-sm text-gray-500">
          <p>Breakout email: {cycle.breakout_email_sent ? '✅ Đã gửi' : '⏳ Chờ'}</p>
          <p>Cảnh báo 10 ngày: {cycle.alert_sent_10d ? '✅ Đã gửi' : '⏳ Chờ'}</p>
          <p>Tín hiệu tạo đáy: {cycle.alert_sent_bottom ? '✅ Đã gửi' : '⏳ Chờ'}</p>
        </div>
      </div>
    </div>
  )
}
