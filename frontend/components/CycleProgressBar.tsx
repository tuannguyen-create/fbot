import type { Phase } from '@/types/cycle'

interface Props {
  elapsed: number
  total: number
  phase: Phase | string
}

const phaseColor: Record<string, string> = {
  distribution_in_progress: 'bg-orange-400',
  bottoming_candidate: 'bg-yellow-400',
  invalidated: 'bg-red-400',
  done: 'bg-green-500',
}

export function CycleProgressBar({ elapsed, total, phase }: Props) {
  const pct = total > 0 ? Math.min(100, Math.round((elapsed / total) * 100)) : 0
  const color = phaseColor[phase] ?? 'bg-gray-400'

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-gray-200 rounded-full h-2 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-gray-500 w-12 text-right">
        Ngày {elapsed}/{total}
      </span>
    </div>
  )
}
