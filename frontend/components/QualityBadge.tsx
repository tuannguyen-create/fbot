interface Props {
  grade: 'A' | 'B' | 'C' | null | undefined
  reason?: string | null
}

const STYLES: Record<string, string> = {
  A: 'bg-green-100 text-green-700',
  B: 'bg-yellow-100 text-yellow-700',
  C: 'bg-gray-100 text-gray-500',
}

export function QualityBadge({ grade, reason }: Props) {
  if (!grade) return null
  const style = STYLES[grade] ?? 'bg-gray-100 text-gray-500'
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-semibold ${style}`}
      title={reason ?? undefined}
    >
      {grade}
    </span>
  )
}
