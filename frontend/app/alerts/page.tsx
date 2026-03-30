'use client'
import { Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useRouter, useSearchParams } from 'next/navigation'
import { alertsApi, healthApi } from '@/lib/api'
import { AlertStatusBadge } from '@/components/AlertStatusBadge'
import { InfoTooltip } from '@/components/InfoTooltip'
import { QualityBadge } from '@/components/QualityBadge'
import { M1QualityLegend } from '@/components/M1QualityLegend'
import { OriginBadge } from '@/components/OriginBadge'
import { M1Guide } from '@/components/ScannerGuide'
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
  const status = params.get('status') ?? 'active'
  const magicOnly = params.get('magic_only') === 'true'
  const origin = (params.get('origin') ?? '') as 'live' | 'historical_replay' | 'recovery_replay' | ''
  const repeatDays = Number(params.get('repeat_days') ?? 5)
  const repeatMin = Number(params.get('repeat_min') ?? 2)
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

  const { data: health } = useQuery({
    queryKey: ['health', 'alerts-page'],
    queryFn: () => healthApi.check(),
    refetchInterval: 30_000,
  })
  const { data: repeatsData, isLoading: repeatsLoading } = useQuery({
    queryKey: ['alerts', 'repeats', { ticker, status, origin, repeatDays, repeatMin }],
    queryFn: () => alertsApi.repeats({
      ticker: ticker || undefined,
      status: status || undefined,
      origin: origin || undefined,
      days: repeatDays,
      min_count: repeatMin,
      limit: 20,
    }),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  const alerts = data?.alerts ?? []
  const repeated = repeatsData?.items ?? []
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

      <M1Guide
        activeTickers={health?.active_ticker_count}
        effectiveTickers={health?.effective_intraday_ticker_count ?? health?.effective_ticker_count}
      />

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
          <option value="active">Đáng xem</option>
          <option value="">Tất cả</option>
          <option value="fired">Chờ xác nhận</option>
          <option value="confirmed">Xác nhận</option>
          <option value="cancelled">Huỷ</option>
          <option value="expired">Hết phiên</option>
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
        <select
          value={String(repeatDays)}
          onChange={(e) => updateFilter('repeat_days', e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm"
        >
          <option value="1">Lặp trong 1 ngày</option>
          <option value="5">Lặp trong 5 ngày</option>
          <option value="10">Lặp trong 10 ngày</option>
          <option value="25">Lặp trong 25 ngày</option>
        </select>
        <select
          value={String(repeatMin)}
          onChange={(e) => updateFilter('repeat_min', e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm"
        >
          <option value="2">Lặp từ 2 lần</option>
          <option value="3">Lặp từ 3 lần</option>
          <option value="5">Lặp từ 5 lần</option>
          <option value="10">Lặp từ 10 lần</option>
        </select>
        <span className="text-xs text-gray-400 ml-auto">
          {total} kết quả
        </span>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-gray-800">Mã M1 lặp nhiều</h2>
            <p className="text-xs text-gray-400">
              Cửa sổ {repeatDays} ngày, tối thiểu {repeatMin} lần. Phần này dùng cùng bộ lọc nguồn, trạng thái và mã ở trên.
            </p>
          </div>
          <span className="text-xs text-gray-400">
            {repeatsLoading ? 'Đang tính…' : `${repeatsData?.total_tickers ?? 0} mã lặp`}
          </span>
        </div>

        {repeatsLoading ? (
          <div className="text-sm text-gray-400 mt-3">Đang tính tần suất M1...</div>
        ) : repeated.length === 0 ? (
          <div className="text-sm text-gray-400 mt-3">Không có mã nào lặp đủ ngưỡng trong cửa sổ này.</div>
        ) : (
          <div className="mt-3 space-y-3">
            <div className="flex flex-wrap gap-2">
              {repeated.slice(0, 8).map((item) => (
                <button
                  key={item.ticker}
                  onClick={() => updateFilter('ticker', item.ticker)}
                  className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs text-amber-800"
                >
                  {item.ticker}: {item.total_alerts} lần
                </button>
              ))}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {repeated.slice(0, 6).map((item) => (
                <Link
                  key={item.ticker}
                  href={`/alerts?ticker=${item.ticker}&status=${status || ''}&origin=${origin || ''}&repeat_days=${repeatDays}&repeat_min=${repeatMin}`}
                  className="rounded-lg border border-gray-200 px-4 py-3 hover:border-orange-300 transition-colors"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="font-semibold text-gray-900">{item.ticker}</div>
                      <div className="text-xs text-gray-400">
                        Gần nhất: {formatDateTimeICT(item.latest_bar_time)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-lg font-bold text-amber-700">{item.total_alerts} lần</div>
                      <div className="text-xs text-gray-400">
                        max {formatRatio(item.max_ratio_5d)}
                      </div>
                    </div>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs">
                    <span className="rounded bg-green-50 px-2 py-0.5 text-green-700">Xác nhận {item.confirmed_count}</span>
                    <span className="rounded bg-orange-50 px-2 py-0.5 text-orange-700">Chờ {item.fired_count}</span>
                    <span className="rounded bg-gray-100 px-2 py-0.5 text-gray-700">Huỷ {item.cancelled_count}</span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-700">Hết phiên {item.expired_count}</span>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-sm text-gray-600">
        <p><b>Đáng xem</b> là mặc định chỉ hiện các alert còn cần chú ý: <b>Chờ 15p</b> và <b>Xác nhận</b>. Các trạng thái <b>Hết phiên</b> hoặc <b>Không xác nhận</b> vẫn được lưu để xem lại, nhưng bị ẩn khỏi mặc định.</p>
        <p className="mt-1"><b>Giờ thị trường</b> là phút đang được quét. <b>Chờ 15p</b> nghĩa là tín hiệu mới phát hiện, còn đợi xác nhận sau 15 phút.</p>
        <p className="mt-1"><b>Hết phiên</b> nghĩa là alert xuất hiện quá muộn nên không còn đủ 15 phút để xác nhận trong cùng phiên. App giữ lại để xem lại, nhưng không coi là tín hiệu đã xác nhận.</p>
        <p className="mt-1"><b>Bên mua</b> = tỷ lệ khối lượng chủ động mua. <b>Chất lượng</b> = điểm A/B/C để ưu tiên xem trước, không phải lệnh mua tự động.</p>
        <p className="mt-1"><b>Mã M1 lặp nhiều</b> giúp nhìn ra mã bị kích hoạt volume abnormal lặp liên tục trong một cửa sổ ngắn. Đây là dấu hiệu để chú ý mã bị kéo mạnh hoặc bị bơm breakout nhiều nhịp.</p>
      </div>

      <M1QualityLegend />

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
                  <th className="text-left px-4 py-2 text-xs font-medium text-gray-500 uppercase">
                    <span className="inline-flex items-center gap-1">
                      Giờ thị trường
                      <InfoTooltip title="Giờ thị trường">
                        Đây là phút giao dịch thực tế đang bị quét. Alert có thể được ghi sớm vài giây trong cùng phút do app chụp snapshot mỗi khoảng 15 giây.
                      </InfoTooltip>
                    </span>
                  </th>
                  <th className="text-right px-4 py-2 text-xs font-medium text-gray-500 uppercase">Khối lượng</th>
                  <th className="text-right px-4 py-2 text-xs font-medium text-gray-500 uppercase">KL / cơ sở</th>
                  <th className="text-right px-4 py-2 text-xs font-medium text-gray-500 uppercase">
                    <span className="inline-flex items-center gap-1">
                      Bên mua
                      <InfoTooltip title="Bên mua">
                        Tỷ lệ khối lượng chủ động mua trên tổng khối lượng chủ động mua+bán. Gần 100% là mua chủ động mạnh, gần 0% là bán chủ động mạnh.
                      </InfoTooltip>
                    </span>
                  </th>
                  <th className="text-center px-4 py-2 text-xs font-medium text-gray-500 uppercase">
                    <span className="inline-flex items-center gap-1">
                      Điểm M1
                      <InfoTooltip title="Điểm M1">
                        Không phải chỉ là nến. Điểm M1 = Nến tối đa 30 + Nền 25 + MA 25 + MACD 20. A = 70-100, B = 40-69, C = 0-39.
                      </InfoTooltip>
                    </span>
                  </th>
                  <th className="text-center px-4 py-2 text-xs font-medium text-gray-500 uppercase">
                    <span className="inline-flex items-center gap-1">
                        Trạng thái
                      <InfoTooltip title="Trạng thái">
                        Chờ 15p = vừa phát hiện, còn đợi lực duy trì sau 15 phút. Xác nhận 15p = lực còn khỏe. Không xác nhận = spike hụt lực. Hết phiên = alert đến quá muộn nên không còn đủ 15 phút để xác nhận.
                      </InfoTooltip>
                    </span>
                  </th>
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
                    <td className="px-4 py-2 text-gray-500">
                      <div>{formatDateTimeICT(a.bar_time ?? a.fired_at)}</div>
                      <div className="text-xs text-gray-400">{slotToTimeStr(a.slot)} giờ VN</div>
                    </td>
                    <td className="px-4 py-2 text-right font-mono">{formatVolume(a.volume)}</td>
                    <td className="px-4 py-2 text-right font-semibold text-orange-600">{formatRatio(a.ratio_5d)}</td>
                    <td className="px-4 py-2 text-right text-gray-600">{formatPct(a.bu_pct)}</td>
                    <td className="px-4 py-2 text-center"><QualityBadge grade={a.quality_grade} score={a.quality_score} reason={a.quality_reason} /></td>
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
                  {a.bu_pct != null && <span>Mua: {formatPct(a.bu_pct)}</span>}
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
