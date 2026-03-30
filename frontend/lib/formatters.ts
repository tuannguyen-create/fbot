import { format, toZonedTime } from 'date-fns-tz'
import type { AlertSummary } from '@/types/alert'

const ICT = 'Asia/Ho_Chi_Minh'

export function toICT(utcIso: string): Date {
  return toZonedTime(new Date(utcIso), ICT)
}

export function formatAlertTime(utcIso: string): string {
  return format(toICT(utcIso), 'HH:mm', { timeZone: ICT })
}

export function formatDateTimeICT(utcIso: string): string {
  return format(toICT(utcIso), 'dd/MM/yyyy HH:mm', { timeZone: ICT })
}

export function formatDateICT(dateStr: string): string {
  return format(new Date(dateStr), 'dd/MM/yyyy')
}

export function formatVolume(v: number | null | undefined): string {
  if (v == null) return 'N/A'
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(0)}K`
  return v.toLocaleString()
}

export function formatRatio(r: number | null | undefined): string {
  if (r == null) return 'N/A'
  return `${r.toFixed(2)}x`
}

export function formatPct(p: number | null | undefined): string {
  if (p == null) return 'N/A'
  return `${p.toFixed(1)}%`
}

export function slotToTimeStr(slot: number): string {
  // slot 0 = 9:00, slot 149 = 11:29, slot 150 = 13:00, slot 239 = 14:29
  let totalMin: number
  if (slot < 150) {
    totalMin = 9 * 60 + slot
  } else {
    totalMin = 13 * 60 + (slot - 150)
  }
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`
}

export function getRatioColor(ratio: number | null): string {
  if (!ratio) return 'bg-gray-200 text-gray-700'
  if (ratio >= 3) return 'bg-red-500 text-white'
  if (ratio >= 2) return 'bg-orange-400 text-white'
  if (ratio >= 1.5) return 'bg-yellow-300 text-gray-800'
  return 'bg-gray-200 text-gray-700'
}

export function getStatusColor(status: AlertSummary['status']): string {
  switch (status) {
    case 'confirmed': return 'text-green-600 bg-green-100'
    case 'cancelled': return 'text-gray-500 bg-gray-100'
    case 'expired': return 'text-slate-600 bg-slate-100'
    default: return 'text-orange-600 bg-orange-100'
  }
}

export function getStatusLabel(status: AlertSummary['status']): string {
  switch (status) {
    case 'confirmed': return '✅ Xác nhận 15p'
    case 'cancelled': return '⚪ Không xác nhận'
    case 'expired': return '🕓 Hết phiên'
    default: return '⏳ Chờ 15p'
  }
}
