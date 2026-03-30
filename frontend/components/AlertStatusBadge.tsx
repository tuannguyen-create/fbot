import type { AlertSummary } from '@/types/alert'
import { getStatusColor } from '@/lib/formatters'

interface Props {
  status: AlertSummary['status']
  confirmWindowMinutes?: number | null
  confirmWindowTargetMinutes?: number | null
  confirmWindowAvailableMinutes?: number | null
  confirmWindowComplete?: boolean | null
}

function getStatusLabel({
  status,
  confirmWindowMinutes,
  confirmWindowTargetMinutes,
  confirmWindowAvailableMinutes,
}: Props): string {
  const target = confirmWindowTargetMinutes ?? 15
  const settledWindow = confirmWindowMinutes != null && confirmWindowMinutes > 0
  const pendingPartial = !settledWindow && (confirmWindowAvailableMinutes ?? target) < target

  switch (status) {
    case 'confirmed':
      return settledWindow && confirmWindowMinutes! < target
        ? `✅ Xác nhận ${confirmWindowMinutes}/${target}p`
        : '✅ Xác nhận 15p'
    case 'cancelled':
      return settledWindow && confirmWindowMinutes! < target
        ? `⚪ Không xác nhận ${confirmWindowMinutes}/${target}p`
        : '⚪ Không xác nhận'
    case 'expired':
      if (settledWindow) return `🕓 Hết phiên ${confirmWindowMinutes}/${target}p`
      if (pendingPartial) return `🕓 Hết phiên ${(confirmWindowAvailableMinutes ?? 0)}/${target}p`
      return '🕓 Hết phiên'
    default:
      return pendingPartial
        ? `⏳ Chờ cuối phiên ${(confirmWindowAvailableMinutes ?? 0)}/${target}p`
        : '⏳ Chờ 15p'
  }
}

export function AlertStatusBadge(props: Props) {
  const { status } = props
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium whitespace-nowrap ${getStatusColor(status)}`}>
      {getStatusLabel(props)}
    </span>
  )
}
