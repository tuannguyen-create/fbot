'use client'
import { useQuery } from '@tanstack/react-query'
import { alertsApi } from '@/lib/api'
import { AlertStatusBadge } from '@/components/AlertStatusBadge'
import { formatAlertTime, formatDateTimeICT, formatVolume, formatRatio, formatPct, slotToTimeStr } from '@/lib/formatters'
import Link from 'next/link'

interface Props {
  params: { id: string }
}

export default function AlertDetailPage({ params }: Props) {
  const id = Number(params.id)

  const { data, isLoading, error } = useQuery({
    queryKey: ['alerts', 'detail', id],
    queryFn: () => alertsApi.get(id),
    enabled: !isNaN(id),
  })

  const alert = data?.alert

  if (isLoading) return <div className="text-center py-8 text-gray-400">Đang tải...</div>
  if (error || !alert) return (
    <div className="text-center py-12 text-gray-400">
      <p className="text-2xl mb-2">❌</p>
      <p>Không tìm thấy cảnh báo #{id}</p>
      <Link href="/alerts" className="text-orange-600 text-sm hover:underline mt-2 block">← Quay lại</Link>
    </div>
  )

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <Link href="/alerts" className="text-sm text-gray-500 hover:text-gray-700">← Quay lại Alerts</Link>

      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-xl font-bold text-gray-900">
              Alert #{alert.id} — {alert.ticker}
            </h1>
            <p className="text-sm text-gray-500">
              Phiên GD: <span className="font-medium text-gray-700">
                {alert.bar_time ? formatAlertTime(alert.bar_time) : slotToTimeStr(alert.slot)} ICT
              </span>
              <span className="mx-2 text-gray-300">·</span>
              Ghi nhận: {formatDateTimeICT(alert.fired_at)}
            </p>
          </div>
          <AlertStatusBadge status={alert.status} />
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Phiên GD</p>
            <p className="font-semibold">{alert.bar_time ? formatAlertTime(alert.bar_time) : slotToTimeStr(alert.slot)} ICT</p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Magic Window</p>
            <p className="font-semibold">{alert.in_magic_window ? '⚡ Có' : 'Không'}</p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Khối lượng</p>
            <p className="font-semibold text-lg">{formatVolume(alert.volume)}</p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Baseline (5 ngày)</p>
            <p className="font-semibold">{formatVolume(alert.baseline_5d)}</p>
          </div>
          <div className="bg-orange-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Tỷ lệ KL/Baseline</p>
            <p className="font-bold text-xl text-orange-600">{formatRatio(alert.ratio_5d)}</p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">BU% (Bên mua)</p>
            <p className="font-semibold">{formatPct(alert.bu_pct)}</p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Foreign Net</p>
            <p className="font-semibold">{formatVolume(alert.foreign_net)}</p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">15-phút ratio</p>
            <p className="font-semibold">
              {alert.ratio_15m ? formatRatio(alert.ratio_15m) : 'Chờ...'}
              {alert.confirmed_at && (
                <span className="ml-1 text-xs text-gray-400">({formatDateTimeICT(alert.confirmed_at)})</span>
              )}
            </p>
          </div>
        </div>

        <div className="mt-4 pt-4 border-t border-gray-100 text-sm text-gray-500">
          <p>Email: {alert.email_sent ? '✅ Đã gửi' : '⏳ Chưa gửi'}</p>
          {alert.cycle_event_id && (
            <p className="mt-1">
              Chu kỳ liên quan:{' '}
              <Link href={`/cycles/${alert.cycle_event_id}`} className="text-orange-600 hover:underline">
                Cycle #{alert.cycle_event_id} →
              </Link>
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
