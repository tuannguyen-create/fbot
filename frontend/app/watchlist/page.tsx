'use client'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { watchlistApi } from '@/lib/api'
import Link from 'next/link'

const GAME_TYPE_STYLES: Record<string, string> = {
  speculative:      'bg-orange-100 text-orange-700',
  state_enterprise: 'bg-blue-100 text-blue-700',
  institutional:    'bg-gray-100 text-gray-600',
}

export default function WatchlistPage() {
  const [search, setSearch] = useState('')
  const [vn30Only, setVn30Only] = useState(false)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => watchlistApi.list(),
  })

  const m3Mutation = useMutation({
    mutationFn: ({ ticker, eligible_for_m3 }: { ticker: string; eligible_for_m3: boolean }) =>
      watchlistApi.updateM3(ticker, { eligible_for_m3 }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const tickers = (data?.tickers ?? []).filter((t) => {
    if (vn30Only && !t.in_vn30) return false
    if (search && !t.ticker.includes(search.toUpperCase())) return false
    return true
  })

  return (
    <div className="space-y-4 max-w-4xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Watchlist</h1>
        <span className="text-sm text-gray-500">{data?.tickers.length ?? 0} mã</span>
      </div>

      <div className="flex gap-2 items-center">
        <input
          type="text"
          placeholder="Tìm ticker..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm w-32"
        />
        <label className="flex items-center gap-1.5 text-sm text-gray-600">
          <input type="checkbox" checked={vn30Only} onChange={(e) => setVn30Only(e.target.checked)} />
          VN30 only
        </label>
      </div>

      {isLoading ? (
        <div className="text-center py-8 text-gray-400">Đang tải...</div>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="hidden md:grid grid-cols-5 gap-3 px-4 py-2 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase">
            <span>Ticker</span>
            <span>Công ty</span>
            <span className="text-center">Loại</span>
            <span className="text-center">Game</span>
            <span className="text-center">M3</span>
          </div>
          <div className="divide-y divide-gray-100">
            {tickers.map((t) => (
              <div key={t.ticker} className="grid grid-cols-2 md:grid-cols-5 gap-2 px-4 py-2.5 items-center hover:bg-gray-50">
                <Link href={`/watchlist/${t.ticker}`} className="font-semibold text-sm hover:text-orange-600">
                  {t.ticker}
                </Link>
                <span className="text-sm text-gray-600 truncate">{t.company_name ?? '—'}</span>
                <span className="text-center">
                  {t.in_vn30 ? (
                    <span className="inline-flex px-2 py-0.5 rounded text-xs bg-blue-100 text-blue-700">VN30</span>
                  ) : (
                    <span className="inline-flex px-2 py-0.5 rounded text-xs bg-gray-100 text-gray-500">Khác</span>
                  )}
                </span>
                <span className="text-center">
                  {t.game_type ? (
                    <span className={`inline-flex px-2 py-0.5 rounded text-xs ${GAME_TYPE_STYLES[t.game_type] ?? 'bg-gray-100 text-gray-500'}`}>
                      {t.game_type}
                    </span>
                  ) : '—'}
                </span>
                <span className="text-center">
                  <button
                    title={t.eligible_for_m3 ? 'M3 đang bật — click để tắt' : 'M3 đang tắt — click để bật'}
                    onClick={() => m3Mutation.mutate({ ticker: t.ticker, eligible_for_m3: !t.eligible_for_m3 })}
                    className={`w-8 h-4 rounded-full transition-colors relative ${
                      t.eligible_for_m3 ? 'bg-orange-400' : 'bg-gray-300'
                    }`}
                  >
                    <span className={`absolute top-0.5 w-3 h-3 bg-white rounded-full shadow transition-transform ${
                      t.eligible_for_m3 ? 'translate-x-4' : 'translate-x-0.5'
                    }`} />
                  </button>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
