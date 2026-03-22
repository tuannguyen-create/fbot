type Phase = 'distributing' | 'bottoming' | 'done'

interface Props {
  phase: Phase
}

const labels: Record<Phase, string> = {
  distributing: 'Phân phối',
  bottoming: 'Tạo đáy',
  done: 'Hoàn thành',
}

const styles: Record<Phase, string> = {
  distributing: 'bg-orange-100 text-orange-700',
  bottoming: 'bg-yellow-100 text-yellow-700',
  done: 'bg-green-100 text-green-700',
}

export function PhaseBadge({ phase }: Props) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${styles[phase]}`}>
      {labels[phase]}
    </span>
  )
}
