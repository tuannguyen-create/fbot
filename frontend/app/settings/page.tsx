'use client'
import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { settingsApi, healthApi } from '@/lib/api'

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
          </div>
        ) : (
          <p className="text-sm text-gray-400">Đang kiểm tra...</p>
        )}
      </div>

      {/* Info */}
      <div className="bg-gray-50 rounded-lg border border-gray-200 p-4 text-sm text-gray-500">
        <p>Theo dõi: <b>{settings?.active_ticker_count ?? 33}</b> mã active
          {settings && settings.active_ticker_count > settings.fiinquant_ticker_limit && (
            <span className="text-orange-500 ml-1">(scan thực tế: {settings.effective_ticker_count}/{settings.fiinquant_ticker_limit} mã do giới hạn FiinQuantX)</span>
          )}
        </p>
        <p className="mt-1">Luồng: <b>{settings?.stream_status === 'connected' ? 'Kết nối' : 'Mất kết nối'}</b></p>
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
