'use client'
import { Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useRouter, useSearchParams } from 'next/navigation'
import { alertsApi } from '@/lib/api'
import { AlertStatusBadge } from '@/components/AlertStatusBadge'
import { QualityBadge } from '@/components/QualityBadge'
import { OriginBadge } from '@/components/OriginBadge'
import { formatDateTimeICT, formatVolume, formatRatio, formatPct, slotToTimeStr } from '@/lib/formatters'
import Link from 'next/link'

const LIMIT = 50

export default function AlertsPage() {
  return (
    <Suspense fallback={<div className="text-center py-8 text-gray-400">Đang tải...</div>}>
      <AlertsContent />
    </Suspense>
  )
}

function AlertsContent() {
  const params = useSearchParams()
  const router = useRouter()

  const ticker = params.get('ticker') ?? ''
  const status = params.get('status') ?? ''
  const magicOnly = params.get('magic_only') === 'true'
  const origin = (params.get('origin') ?? '') as 'live' | 'historical_replay' | 'recovery_replay' | ''
  const offset = Number(params.get('offset') ?? 0)

  const { data, isLoading, error } = useQuery({
    queryKey: ['alerts', 'list', { ticker, status, magicOnly, origin, offset }],
    queryFn: () => alertsApi.list({
      ticker: ticker || undefined,
      status: status || undefined,
      magic_only: magicOnly,
      origin: origin || undefined,
      limit: LIMIT,
      offset,
    }),
  })

  const alerts = data?.alerts ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / LIMIT)
  const currentPage = Math.floor(offset / LIMIT) + 1

  function updateFilter(key: string, value: string) {
    const p = new URLSearchParams(params.toString())
    if (value) p.set(key, value)
    else p.delete(key)
    if (key !== 'offset') p.delete('offset')
    router.push(`/alerts?${p.toString()}`)
  }

  return (
    <div className="space-y-4 max-w-5xl mx-auto">
      <h1 className="text-xl font-bold text-gray-900">Cảnh báo khối lượng</h1>

      {/* Filters */}
      <div className="bg-white rounded-lg border border-gray-200 p-3 flex flex-wrap gap-2 items-center">
        <input
          type="text"
          placeholder="Mã (HPG, VCB...)"
          value={ticker}
          onChange={(e) => updateFilter('ticker', e.target.value.toUpperCase())}
          className="border border-gray-300 rounded px-2 py-1 text-sm w-32"
        />
        <select
          value={status}
          onChange={(e) => updateFilter('status', e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm"
        >
          <option value="">Tất cả</option>
          <option value="fired">Chờ xác nhận</option>
          <option value="confirmed">Xác nhận</option>
          <option value="cancelled">Huỷ</option>
        </select>
        <label className="flex items-center gap-1.5 text-sm text-gray-600">
          <input
            type="checkbox"
            checked={magicOnly}
            onChange={(e) => updateFilter('magic_only', e.target.checked ? 'true' : '')}
          />
          Chỉ cửa sổ vàng
        </label>
        <select
          value={origin}
          onChange={(e) => updateFilter('origin', e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm"
        >
          <option value="">Tất cả nguồn</option>
          <option value="live">Live</option>
          <option value="historical_replay">Lịch sử</option>
          <option value="recovery_replay">Phục hồi</option>
        </select>
        <span className="text-xs text-gray-400 ml-auto">
          {total} kết quả
        </span>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="text-center py-8 text-gray-400">Đang tải...</div>
      ) : error ? (
        <div className="text-center py-8 text-red-500">Lỗi kết nối server</div>
      ) : alerts.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          <p className="text-2xl mb-2">🔇</p>
          <p>Không có cảnh báo nào</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-2 text-xs font-medium text-gray-500 uppercase">Mã</th>
                  <th className="text-left px-4 py-2 text-xs font-medium text-gray-500 uppercase">Thời gian</th>
                  <th className="text-right px-4 py-2 text-xs font-medium text-gray-500 uppercase">Khối lượng</th>
                  <th className="text-right px-4 py-2 text-xs font-medium text-gray-500 uppercase">Tỷ lệ</th>
                  <th className="text-right px-4 py-2 text-xs font-medium text-gray-500 uppercase">BU%</th>
                  <th className="text-center px-4 py-2 text-xs font-medium text-gray-500 uppercase">CL</th>
                  <th className="text-center px-4 py-2 text-xs font-medium text-gray-500 uppercase">Trạng thái</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {alerts.map((a) => (
                  <tr key={a.id} className="hover:bg-gray-50 cursor-pointer">
                    <td className="px-4 py-2">
                      <Link href={`/alerts/${a.id}`} className="font-semibold text-gray-900 hover:text-orange-600">
                        {a.ticker}
                        {a.in_magic_window && <span className="ml-1 text-yellow-500" title="Cửa sổ vàng">⚡</span>}
                      </Link>
                      <OriginBadge origin={a.origin} className="ml-1.5" />
                    </td>
                    <td className="px-4 py-2 text-gray-500">{formatDateTimeICT(a.bar_time ?? a.fired_at)}</td>
                    <td className="px-4 py-2 text-right font-mono">{formatVolume(a.volume)}</td>
                    <td className="px-4 py-2 text-right font-semibold text-orange-600">{formatRatio(a.ratio_5d)}</td>
                    <td className="px-4 py-2 text-right text-gray-600">{formatPct(a.bu_pct)}</td>
                    <td className="px-4 py-2 text-center"><QualityBadge grade={a.quality_grade} /></td>
                    <td className="px-4 py-2 text-center"><AlertStatusBadge status={a.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden divide-y divide-gray-100">
            {alerts.map((a) => (
              <Link key={a.id} href={`/alerts/${a.id}`} className="block px-4 py-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold">{a.ticker} {a.in_magic_window && '⚡'}</span>
                  <AlertStatusBadge status={a.status} />
                </div>
                <div className="text-xs text-gray-500 flex gap-3">
                  <span>{slotToTimeStr(a.slot)}</span>
                  <span>KL: {formatVolume(a.volume)}</span>
                  <span className="text-orange-600 font-semibold">{formatRatio(a.ratio_5d)}</span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <button
            disabled={offset === 0}
            onClick={() => updateFilter('offset', String(Math.max(0, offset - LIMIT)))}
            className="px-3 py-1 border border-gray-300 rounded disabled:opacity-40"
          >
            ← Trước
          </button>
          <span className="text-gray-500">Trang {currentPage} / {totalPages}</span>
          <button
            disabled={offset + LIMIT >= total}
            onClick={() => updateFilter('offset', String(offset + LIMIT))}
            className="px-3 py-1 border border-gray-300 rounded disabled:opacity-40"
          >
            Tiếp →
          </button>
        </div>
      )}
    </div>
  )
}
