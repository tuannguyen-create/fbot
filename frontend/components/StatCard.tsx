interface Props {
  title: string
  value: string | number
  subtitle?: string
  color?: 'default' | 'warning' | 'success' | 'danger'
}

const colorMap = {
  default: 'border-gray-200',
  warning: 'border-orange-300',
  success: 'border-green-300',
  danger: 'border-red-300',
}

const valueColorMap = {
  default: 'text-gray-900',
  warning: 'text-orange-600',
  success: 'text-green-600',
  danger: 'text-red-600',
}

export function StatCard({ title, value, subtitle, color = 'default' }: Props) {
  return (
    <div className={`bg-white rounded-lg border-2 ${colorMap[color]} p-4 flex flex-col gap-1`}>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{title}</p>
      <p className={`text-2xl font-bold ${valueColorMap[color]}`}>{value}</p>
      {subtitle && <p className="text-xs text-gray-400">{subtitle}</p>}
    </div>
  )
}
