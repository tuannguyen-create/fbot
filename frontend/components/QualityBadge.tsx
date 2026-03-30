interface Props {
  grade: 'A' | 'B' | 'C' | null | undefined
  reason?: string | null
  score?: number | null
}

const STYLES: Record<string, string> = {
  A: 'bg-green-100 text-green-700',
  B: 'bg-yellow-100 text-yellow-700',
  C: 'bg-gray-100 text-gray-500',
}

export function QualityBadge({ grade, reason, score }: Props) {
  if (!grade) return null
  const style = STYLES[grade] ?? 'bg-gray-100 text-gray-500'
  const gradeHint = grade === 'A' ? '70-100' : grade === 'B' ? '40-69' : '0-39'
  const title = `${grade}: ${gradeHint} điểm${score != null ? ` • hiện tại ${score}/100` : ''}${reason ? ` • ${reason}` : ''}`
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold ${style}`}
      title={title}
    >
      {grade}
      {score != null && <span className="opacity-80">{score}</span>}
    </span>
  )
}
