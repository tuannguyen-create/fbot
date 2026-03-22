interface Props {
  elapsed: number
  total: number
  phase: 'distributing' | 'bottoming' | 'done'
}

const phaseColor = {
  distributing: 'bg-orange-400',
  bottoming: 'bg-yellow-400',
  done: 'bg-green-500',
}

export function CycleProgressBar({ elapsed, total, phase }: Props) {
  const pct = total > 0 ? Math.min(100, Math.round((elapsed / total) * 100)) : 0

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-gray-200 rounded-full h-2 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${phaseColor[phase]}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-gray-500 w-12 text-right">
        Ngày {elapsed}/{total}
      </span>
    </div>
  )
}
