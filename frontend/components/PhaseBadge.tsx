import type { Phase } from '@/types/cycle'

interface Props {
  phase: Phase | string
}

const labels: Record<string, string> = {
  distribution_in_progress: 'Đang phân phối',
  bottoming_candidate: 'Tạo đáy',
  invalidated: 'Vô hiệu',
  done: 'Hoàn thành',
}

const styles: Record<string, string> = {
  distribution_in_progress: 'bg-orange-100 text-orange-700',
  bottoming_candidate: 'bg-yellow-100 text-yellow-700',
  invalidated: 'bg-red-100 text-red-500',
  done: 'bg-green-100 text-green-700',
}

export function PhaseBadge({ phase }: Props) {
  const label = labels[phase] ?? phase
  const style = styles[phase] ?? 'bg-gray-100 text-gray-600'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${style}`}>
      {label}
    </span>
  )
}
