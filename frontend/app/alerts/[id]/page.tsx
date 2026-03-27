'use client'
import { useQuery } from '@tanstack/react-query'
import { alertsApi } from '@/lib/api'
import { AlertStatusBadge } from '@/components/AlertStatusBadge'
import { QualityBadge } from '@/components/QualityBadge'
import { OriginBadge } from '@/components/OriginBadge'
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
      <Link href="/alerts" className="text-sm text-gray-500 hover:text-gray-700">← Quay lại Cảnh báo</Link>

      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-xl font-bold text-gray-900">
              Cảnh báo #{alert.id} — {alert.ticker}
            </h1>
            <p className="text-sm text-gray-500">
              Phiên GD: <span className="font-medium text-gray-700">
                {alert.bar_time ? formatAlertTime(alert.bar_time) : slotToTimeStr(alert.slot)} giờ VN
              </span>
              <span className="mx-2 text-gray-300">·</span>
              Ghi nhận: {formatDateTimeICT(alert.fired_at)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <OriginBadge origin={alert.origin} />
            <AlertStatusBadge status={alert.status} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Phiên GD</p>
            <p className="font-semibold">{alert.bar_time ? formatAlertTime(alert.bar_time) : slotToTimeStr(alert.slot)} giờ VN</p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Cửa sổ vàng</p>
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
            <p className="text-xs text-gray-400 uppercase mb-1">Ngoại ròng</p>
            <p className="font-semibold">{formatVolume(alert.foreign_net)}</p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-400 uppercase mb-1">Tỷ lệ 15 phút</p>
            <p className="font-semibold">
              {alert.ratio_15m ? formatRatio(alert.ratio_15m) : 'Chờ...'}
              {alert.confirmed_at && (
                <span className="ml-1 text-xs text-gray-400">
                  ({alert.origin === 'live' ? '' : 'phiên GD '}
                  {formatDateTimeICT(alert.confirmed_at)})
                </span>
              )}
            </p>
          </div>
        </div>

        {/* M1 Quality section */}
        {alert.quality_score != null && (
          <div className="mt-4 bg-gray-50 rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-gray-700">Chất lượng M1</p>
              <QualityBadge grade={alert.quality_grade} reason={alert.quality_reason} />
            </div>
            <p className="text-xs text-gray-500">{alert.quality_reason}</p>
            <div className="w-full bg-gray-200 rounded-full h-1.5">
              <div
                className={`h-1.5 rounded-full transition-all ${
                  (alert.quality_score ?? 0) >= 70 ? 'bg-green-500' :
                  (alert.quality_score ?? 0) >= 40 ? 'bg-yellow-400' : 'bg-gray-400'
                }`}
                style={{ width: `${alert.quality_score}%` }}
              />
            </div>
            <p className="text-xs text-right text-gray-400">{alert.quality_score}/100</p>

            {alert.features && (
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs text-gray-600 pt-1 border-t border-gray-200">
                <div>Thân nến: <span className="font-medium">{alert.features.body_pct}%</span></div>
                <div>Bóng trên: <span className="font-medium">{alert.features.upper_shadow_pct}%</span></div>
                <div>Vị trí đóng cửa: <span className="font-medium">{alert.features.close_pos}%</span></div>
                <div>Nến tăng mạnh: <span className="font-medium">{alert.features.strong_bull_candle ? 'Có' : 'Không'}</span></div>
                <div>Biên độ 20 nến: <span className="font-medium">{alert.features.range_pct}%</span></div>
                <div>Nền tích lũy: <span className="font-medium">{alert.features.is_sideways_base ? 'Có' : 'Không'}</span></div>
                {alert.features.ma10 != null && (
                  <div>MA10: <span className="font-medium">{alert.features.ma10.toLocaleString()}</span></div>
                )}
                {alert.features.ma20 != null && (
                  <div>MA20: <span className="font-medium">{alert.features.ma20.toLocaleString()}</span></div>
                )}
                <div>MA stack tăng: <span className="font-medium">{alert.features.ma_stack_up ? 'Có' : 'Không'}</span></div>
                {alert.features.macd_hist != null && (
                  <div>
                    MACD hist:{' '}
                    <span className="font-medium">
                      {alert.features.macd_hist.toFixed(4)}
                      {alert.features.macd_hist_rising != null && (
                        <span className="ml-1 text-gray-400">{alert.features.macd_hist_rising ? '↑' : '↓'}</span>
                      )}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="mt-4 pt-4 border-t border-gray-100 text-sm text-gray-500">
          <p>Email: {alert.email_sent ? 'Đã gửi' : 'Chưa gửi'}</p>
          {alert.cycle_event_id && (
            <p className="mt-1">
              Chu kỳ liên quan:{' '}
              <Link href={`/cycles/${alert.cycle_event_id}`} className="text-orange-600 hover:underline">
                Chu kỳ #{alert.cycle_event_id} →
              </Link>
            </p>
          )}
        </div>

        {alert.origin !== 'live' && (
          <div className="mt-3 pt-3 border-t border-gray-100 space-y-1">
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <span>Nguồn:</span>
              <OriginBadge origin={alert.origin} />
              {!alert.is_actionable && (
                <span className="text-xs text-gray-400">(chỉ lưu trữ, không action)</span>
              )}
            </div>
            {alert.replayed_at && (
              <p className="text-xs text-gray-400">
                Thêm lại lúc: {formatDateTimeICT(alert.replayed_at)}
              </p>
            )}
            {alert.replay_run_id && (
              <p className="text-xs text-gray-400 font-mono truncate">
                Run: {alert.replay_run_id}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
