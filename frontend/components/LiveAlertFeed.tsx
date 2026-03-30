'use client'
import { useEffect, useState } from 'react'
import { useAlertStore } from '@/stores/alertStore'
import { useAlertStream } from '@/hooks/useAlertStream'
import { AlertStatusBadge } from './AlertStatusBadge'
import { QualityBadge } from './QualityBadge'
import { formatAlertTime, formatRatio, formatVolume, formatPct, slotToTimeStr } from '@/lib/formatters'
import Link from 'next/link'

export function LiveAlertFeed() {
  useAlertStream()
  const alerts = useAlertStore((s) => s.liveAlerts)
  const [flashIds, setFlashIds] = useState<Set<number>>(new Set())

  // Flash animation for new alerts
  useEffect(() => {
    if (alerts[0]) {
      const id = alerts[0].id
      setFlashIds((prev) => new Set([...prev, id]))
      const timer = setTimeout(() => {
        setFlashIds((prev) => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      }, 2000)
      return () => clearTimeout(timer)
    }
  }, [alerts[0]?.id])

  if (alerts.length === 0) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6 text-center text-gray-400">
        <p className="text-sm">Chưa có cảnh báo trong phiên này</p>
        <p className="text-xs mt-1">Cảnh báo sẽ hiện tại đây khi phát hiện KL bất thường</p>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      <div className="px-4 py-2 border-b border-gray-100 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">M1 đang báo trong phiên</h3>
        <span className="text-xs text-gray-400">{alerts.length} cảnh báo</span>
      </div>
      <div className="divide-y divide-gray-50 max-h-80 overflow-y-auto">
        {alerts.map((alert) => (
          <Link
            key={alert.id}
            href={`/alerts/${alert.id}`}
            className={`flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 transition-colors cursor-pointer ${
              flashIds.has(alert.id) ? 'ring-2 ring-orange-400 ring-inset' : ''
            }`}
          >
            <div className="flex-shrink-0">
              <span className="font-bold text-sm text-gray-900">{alert.ticker}</span>
              {alert.in_magic_window && (
                <span className="ml-1 text-yellow-500 text-xs" title="Cửa sổ vàng">⚡</span>
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 text-xs text-gray-500">
                <span>{slotToTimeStr(alert.slot)}</span>
                <span>KL: {formatVolume(alert.volume)}</span>
                <span className="font-semibold text-orange-600">{formatRatio(alert.ratio_5d)}</span>
                {alert.bu_pct != null && <span>Mua: {formatPct(alert.bu_pct)}</span>}
              </div>
            </div>
            <div className="flex items-center gap-1">
              <QualityBadge grade={alert.quality_grade} />
              <AlertStatusBadge
                status={alert.status}
                confirmWindowMinutes={alert.confirm_window_minutes}
                confirmWindowTargetMinutes={alert.confirm_window_target_minutes}
                confirmWindowAvailableMinutes={alert.confirm_window_available_minutes}
                confirmWindowComplete={alert.confirm_window_complete}
              />
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
