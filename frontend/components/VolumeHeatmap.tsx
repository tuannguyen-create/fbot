'use client'
import { useQuery } from '@tanstack/react-query'
import { alertsApi } from '@/lib/api'
import { getRatioColor } from '@/lib/formatters'
import Link from 'next/link'

export function VolumeHeatmap() {
  const { data } = useQuery({
    queryKey: ['alerts', 'today'],
    queryFn: () => alertsApi.summaryToday(),
    refetchInterval: 60_000,
  })

  const byTicker = data?.by_ticker ?? {}

  const tickers = [
    'ACB','BCM','BID','CTG','FPT','GAS','GVR','HDB','HPG','LPB',
    'MBB','MSN','MWG','PLX','SAB','SHB','SSB','SSI','STB','TCB',
    'VCB','VHM','VIB','VIC','VJC','VNM','VPB','VPL','VRE','VPG',
    'NVL','PDR','KBC',
  ]

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">
        Khối lượng hôm nay — 33 mã
      </h3>
      <div className="flex flex-wrap gap-1.5">
        {tickers.map((ticker) => {
          const alerts = byTicker[ticker] ?? 0
          const colorClass =
            alerts >= 3 ? 'bg-red-500 text-white' :
            alerts >= 2 ? 'bg-orange-400 text-white' :
            alerts >= 1 ? 'bg-yellow-300 text-gray-800' :
                          'bg-gray-100 text-gray-600'
          return (
            <Link
              key={ticker}
              href={`/watchlist`}
              className={`px-2 py-1 rounded text-xs font-medium ${colorClass} hover:opacity-80 transition-opacity`}
              title={`${ticker}: ${alerts} cảnh báo hôm nay`}
            >
              {ticker}
              {alerts > 0 && <span className="ml-1 font-bold">{alerts}</span>}
            </Link>
          )
        })}
      </div>
      <p className="text-xs text-gray-400 mt-2">
        Màu: <span className="text-yellow-600">■</span> 1 cảnh báo &nbsp;
        <span className="text-orange-500">■</span> 2 cảnh báo &nbsp;
        <span className="text-red-500">■</span> 3+ cảnh báo
      </p>
    </div>
  )
}
