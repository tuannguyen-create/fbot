'use client'

export function M1QualityLegend() {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
      <h3 className="font-semibold text-gray-900">A / B / C được chấm như thế nào</h3>
      <p className="mt-2">
        Điểm chất lượng M1 là tổng của <b>4 phần</b>, tối đa <b>100 điểm</b>.
      </p>
      <div className="mt-3 space-y-1 text-sm">
        <p><b>A</b> = <b>70-100</b> điểm</p>
        <p><b>B</b> = <b>40-69</b> điểm</p>
        <p><b>C</b> = <b>0-39</b> điểm</p>
      </div>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <div className="rounded-lg bg-white p-3 border border-gray-200">
          <p className="font-medium text-gray-900">1. Nến tối đa 30 điểm</p>
          <p className="mt-1 text-xs text-gray-600">+30 nếu nến xanh mạnh: thân ≥ 50%, đóng ở 1/3 trên.</p>
          <p className="text-xs text-gray-600">+15 nếu nến xanh vừa: thân ≥ 40%.</p>
        </div>
        <div className="rounded-lg bg-white p-3 border border-gray-200">
          <p className="font-medium text-gray-900">2. Nền tối đa 25 điểm</p>
          <p className="mt-1 text-xs text-gray-600">+25 nếu 20 nến gần nhất đi ngang hẹp &lt; 3% và vol 20 nến ≤ 80% vol 50 nến.</p>
        </div>
        <div className="rounded-lg bg-white p-3 border border-gray-200">
          <p className="font-medium text-gray-900">3. MA tối đa 25 điểm</p>
          <p className="mt-1 text-xs text-gray-600">+25 nếu giá &gt; MA10 &gt; MA20.</p>
          <p className="text-xs text-gray-600">+10 nếu chỉ cần giá &gt; MA10.</p>
        </div>
        <div className="rounded-lg bg-white p-3 border border-gray-200">
          <p className="font-medium text-gray-900">4. MACD tối đa 20 điểm</p>
          <p className="mt-1 text-xs text-gray-600">+20 nếu histogram dương và đang tăng.</p>
          <p className="text-xs text-gray-600">+10 nếu histogram dương nhưng chưa tăng.</p>
        </div>
      </div>
      <p className="mt-3 text-xs text-gray-500">
        Đây là thang điểm ưu tiên quan sát, không phải tín hiệu mua tự động.
      </p>
    </div>
  )
}
