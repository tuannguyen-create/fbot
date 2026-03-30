'use client'
import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { settingsApi, healthApi, notificationsApi } from '@/lib/api'
import { formatDateTimeICT } from '@/lib/formatters'

export default function SettingsPage() {
  const qc = useQueryClient()

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: () => settingsApi.get(),
  })

  const { data: health, refetch: refetchHealth } = useQuery({
    queryKey: ['health'],
    queryFn: () => healthApi.check(),
    refetchInterval: 10_000,
  })

  const [thresholds, setThresholds] = useState({
    threshold_normal: 2.0,
    threshold_magic: 1.5,
    threshold_confirm_15m: 1.3,
  })
  const [reviewWindow, setReviewWindow] = useState<'today' | '7d' | '30d'>('today')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (settings) {
      setThresholds({
        threshold_normal: settings.threshold_normal,
        threshold_magic: settings.threshold_magic,
        threshold_confirm_15m: settings.threshold_confirm_15m,
      })
    }
  }, [settings])

  const mutation = useMutation({
    mutationFn: () => settingsApi.updateThresholds(thresholds),
    onSuccess: () => {
      setSaved(true)
      qc.invalidateQueries({ queryKey: ['settings'] })
      setTimeout(() => setSaved(false), 3000)
    },
  })

  const { data: reviewData, isLoading: reviewLoading } = useQuery({
    queryKey: ['notifications', 'review', reviewWindow],
    queryFn: () => notificationsApi.review({ window: reviewWindow, channel: 'telegram', limit: 30 }),
    refetchInterval: 30_000,
  })

  if (isLoading) return <div className="text-center py-8 text-gray-400">Đang tải...</div>

  return (
    <div className="max-w-xl mx-auto space-y-4">
      <h1 className="text-xl font-bold text-gray-900">Cài đặt</h1>

      {/* Thresholds */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="font-semibold text-gray-700 mb-4">Ngưỡng cảnh báo</h2>
        <div className="space-y-3">
          {[
            { key: 'threshold_normal' as const, label: 'Ngưỡng thông thường', desc: 'KL / Baseline 5 ngày' },
            { key: 'threshold_magic' as const, label: 'Ngưỡng cửa sổ vàng', desc: '9:00-9:30, 11:00-11:30, 13:00-13:30' },
            { key: 'threshold_confirm_15m' as const, label: 'Ngưỡng xác nhận 15 phút', desc: 'Tỷ lệ tích lũy 15 phút' },
          ].map(({ key, label, desc }) => (
            <div key={key}>
              <label className="text-sm font-medium text-gray-700">{label}</label>
              <p className="text-xs text-gray-400 mb-1">{desc}</p>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  step="0.1"
                  min="0.5"
                  max="10"
                  value={thresholds[key]}
                  onChange={(e) => setThresholds((prev) => ({ ...prev, [key]: parseFloat(e.target.value) }))}
                  className="border border-gray-300 rounded px-2 py-1 text-sm w-20"
                />
                <span className="text-sm text-gray-500">x cơ sở</span>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="px-4 py-2 bg-orange-500 text-white rounded text-sm font-medium hover:bg-orange-600 disabled:opacity-50"
          >
            {mutation.isPending ? 'Đang lưu...' : 'Lưu thay đổi'}
          </button>
          {saved && <span className="text-sm text-green-600">✅ Đã lưu</span>}
          {mutation.isError && <span className="text-sm text-red-600">❌ Lỗi lưu</span>}
        </div>
      </div>

      {/* System Status */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-gray-700">Trạng thái hệ thống</h2>
          <button onClick={() => refetchHealth()} className="text-xs text-orange-600 hover:underline">
            Làm mới
          </button>
        </div>
        {health ? (
          <div className="space-y-2 text-sm">
            <StatusRow label="Cơ sở dữ liệu (PostgreSQL)" status={health.db === 'ok'} />
            <StatusRow label="Bộ nhớ đệm Redis" status={health.redis === 'ok' ? true : health.redis === 'disabled' ? 'neutral' : false} neutralLabel="— Tắt" />
            <StatusRow
              label="Luồng FiinQuantX"
              status={health.stream === 'connected' ? true : health.stream_reason === 'outside_hours' ? 'neutral' : false}
              statusLabel={
                health.stream === 'connected' ? '✅ Kết nối' :
                health.stream_reason === 'outside_hours' ? '— Ngoài giờ' :
                health.stream_reason === 'connecting' ? '⏳ Đang kết nối...' :
                health.stream_reason === 'reconnecting' ? '⏳ Kết nối lại...' :
                '❌ Lỗi kết nối'
              }
            />
            <StatusRow
              label="Telegram"
              status={settings?.telegram_configured ? true : false}
              statusLabel={settings?.telegram_configured ? '✅ Đã cấu hình' : '❌ Chưa cấu hình'}
            />
            <StatusRow
              label="Email"
              status={settings?.email_configured ? true : false}
              statusLabel={settings?.email_configured ? '✅ Đã cấu hình' : '❌ Chưa cấu hình'}
            />
          </div>
        ) : (
          <p className="text-sm text-gray-400">Đang kiểm tra...</p>
        )}
      </div>

      {/* Info */}
      <div className="bg-gray-50 rounded-lg border border-gray-200 p-4 text-sm text-gray-500">
        <p>Theo dõi: <b>{settings?.active_ticker_count ?? 33}</b> mã active</p>
        <p className="mt-1 text-orange-500">
          M1/stream thực tế: {settings?.effective_stream_ticker_count ?? settings?.effective_ticker_count}/
          {settings?.fiinquant_stream_ticker_limit ?? settings?.fiinquant_ticker_limit} mã
        </p>
        <p className="mt-1 text-blue-500">
          M3/daily thực tế: {settings?.effective_daily_ticker_count ?? settings?.active_ticker_count} mã
        </p>
        {!settings?.telegram_configured && (
          <p className="mt-2 text-red-500">
            Telegram chưa cấu hình, nên dù app có alert/cycle thì bot sẽ không bắn tin nhắn.
          </p>
        )}
        <p className="mt-1">Luồng: <b>{settings?.stream_status === 'connected' ? 'Kết nối' : 'Mất kết nối'}</b></p>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-5 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold text-gray-700">Review thông báo</h2>
            <p className="text-xs text-gray-400">
              Tự động cập nhật các tin Telegram đã gửi hoặc lẽ ra sẽ gửi để bạn review trong ngày và xem lại lịch sử.
            </p>
          </div>
          <div className="flex gap-1 rounded-lg bg-gray-100 p-1 text-xs">
            {([
              ['today', 'Hôm nay'],
              ['7d', '7 ngày'],
              ['30d', '30 ngày'],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setReviewWindow(key)}
                className={`px-2 py-1 rounded ${reviewWindow === key ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {reviewData && (
          <div className="grid grid-cols-3 gap-3 text-sm">
            <MiniStat label="Đã gửi" value={reviewData.sent_count} color="text-green-600" />
            <MiniStat label="Lỗi gửi" value={reviewData.failed_count} color="text-red-600" />
            <MiniStat label="Tin dự kiến" value={reviewData.draft_count} color="text-amber-600" />
          </div>
        )}

        <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600 space-y-1">
          <p><b>Đã gửi</b>: tin Telegram đã được bot gửi thật.</p>
          <p><b>Lỗi gửi</b>: bot đã cố gửi nhưng Telegram trả lỗi.</p>
          <p><b>Tin dự kiến</b>: tin lẽ ra sẽ gửi trong ngày, dùng để review khi Telegram chưa cấu hình hoặc bị lỗi.</p>
        </div>

        {!reviewData?.telegram_configured && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
            Telegram chưa cấu hình hoặc chưa đầy đủ. Danh sách dưới đây sẽ đóng vai trò <b>review feed</b> để thấy các tin lẽ ra sẽ đi trong ngày.
          </div>
        )}

        {reviewLoading ? (
          <div className="text-sm text-gray-400">Đang tải review thông báo...</div>
        ) : !reviewData || reviewData.items.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-200 px-4 py-8 text-center text-sm text-gray-400">
            Chưa có activity nào trong cửa sổ này.
          </div>
        ) : (
          <div className="space-y-3">
            {reviewData.items.map((item) => (
              <div key={item.id} className="rounded-lg border border-gray-200 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-gray-900">{item.title}</span>
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        item.source === 'draft' ? 'bg-blue-50 text-blue-600' : 'bg-gray-100 text-gray-600'
                      }`}>
                        {item.source === 'draft' ? 'Dự kiến' : 'Log gửi'}
                      </span>
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        item.status === 'sent' ? 'bg-green-50 text-green-600' :
                        item.status === 'failed' ? 'bg-red-50 text-red-600' :
                        'bg-amber-50 text-amber-600'
                      }`}>
                        {item.status === 'sent' ? 'Đã gửi' : item.status === 'failed' ? 'Lỗi gửi' : 'Chưa gửi'}
                      </span>
                    </div>
                    <p className="text-sm text-gray-600 whitespace-pre-line">{item.preview_text ?? '—'}</p>
                    <div className="text-xs text-gray-400 flex items-center gap-2 flex-wrap">
                      {item.sent_at && <span>{formatDateTimeICT(item.sent_at)}</span>}
                      {item.event_type && <span>{eventTypeLabel(item.event_type)}</span>}
                      {item.ticker && <span>{item.ticker}</span>}
                    </div>
                  </div>
                  {item.link && (
                    <Link href={item.link} className="text-xs text-orange-600 hover:underline shrink-0">
                      Xem →
                    </Link>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function StatusRow({ label, status, statusLabel, neutralLabel }: { label: string; status: boolean | 'neutral'; statusLabel?: string; neutralLabel?: string }) {
  const defaultLabel = status === true ? '✅ Tốt' : status === 'neutral' ? (neutralLabel ?? '— Tắt') : '❌ Lỗi'
  return (
    <div className="flex items-center justify-between">
      <span className="text-gray-600">{label}</span>
      <span className={status === true ? 'text-green-600' : status === 'neutral' ? 'text-gray-400' : 'text-red-600'}>
        {statusLabel ?? defaultLabel}
      </span>
    </div>
  )
}

function MiniStat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="rounded-lg border border-gray-200 px-3 py-2">
      <div className="text-xs text-gray-400 uppercase tracking-wide">{label}</div>
      <div className={`text-xl font-bold ${color}`}>{value}</div>
    </div>
  )
}

function eventTypeLabel(eventType: string) {
  const labels: Record<string, string> = {
    m1_alert_fired: 'M1 phát hiện sớm',
    m1_alert_confirmation: 'M1 kết quả 15 phút',
    m3_cycle_breakout: 'M3 breakout',
    m3_cycle_10d: 'M3 còn 10 ngày',
    m3_cycle_bottom: 'M3 vào vùng đáy',
    m3_daily_digest: 'Tổng hợp M3 cuối ngày',
    m1_replay_digest: 'Tổng hợp M1 lịch sử',
    m3_replay_digest: 'Tổng hợp M3 lịch sử',
  }
  return labels[eventType] ?? eventType
}
