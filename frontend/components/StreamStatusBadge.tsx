'use client'
import { useAlertStore } from '@/stores/alertStore'

export function StreamStatusBadge() {
  const status = useAlertStore((s) => s.streamStatus)

  if (status === 'connected') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700">
        <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
        LIVE
      </span>
    )
  }
  if (status === 'connecting') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
        <span className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" />
        Đang kết nối...
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium bg-red-100 text-red-700">
      <span className="w-2 h-2 rounded-full bg-red-500" />
      Mất kết nối
    </span>
  )
}
