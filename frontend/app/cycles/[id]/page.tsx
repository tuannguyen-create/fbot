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
          <PhaseBadge phase={cycle.phase} />
        </div>

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
            <p className="text-xs text-gray-400 uppercase mb-1">Đáy dự kiến</p>
            <p className="font-semibold text-orange-700">
              {cycle.predicted_bottom_date ? formatDateICT(cycle.predicted_bottom_date) : 'N/A'}
            </p>
          </div>
        </div>

        <div className="mt-4 pt-4 border-t border-gray-100 space-y-1 text-sm text-gray-500">
          <p>Breakout email: ✅ Đã gửi</p>
          <p>10-ngày warning: {cycle.alert_sent_10d ? '✅ Đã gửi' : '⏳ Chờ'}</p>
          <p>Bottom alert: {cycle.alert_sent_bottom ? '✅ Đã gửi' : '⏳ Chờ'}</p>
        </div>
      </div>
    </div>
  )
}
