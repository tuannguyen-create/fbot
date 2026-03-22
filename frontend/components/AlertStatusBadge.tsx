import type { AlertSummary } from '@/types/alert'
import { getStatusColor, getStatusLabel } from '@/lib/formatters'

interface Props {
  status: AlertSummary['status']
}

export function AlertStatusBadge({ status }: Props) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${getStatusColor(status)}`}>
      {getStatusLabel(status)}
    </span>
  )
}
