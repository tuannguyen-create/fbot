'use client'

interface Props {
  origin: string
  className?: string
}

const STYLES: Record<string, string> = {
  historical_replay: 'bg-blue-50 text-blue-600 border border-blue-200',
  recovery_replay:   'bg-purple-50 text-purple-600 border border-purple-200',
}

const LABELS: Record<string, string> = {
  historical_replay: 'LỊCH SỬ',
  recovery_replay:   'PHỤC HỒI',
}

/**
 * Shows a badge only for non-live origins.
 * Live alerts show nothing — that is the default expected state.
 */
export function OriginBadge({ origin, className = '' }: Props) {
  const style = STYLES[origin]
  const label = LABELS[origin]
  if (!style) return null   // 'live' or unknown — render nothing
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${style} ${className}`}
    >
      {label}
    </span>
  )
}
