'use client'
import type { ReactNode } from 'react'

function GuideCard({
  title,
  tone,
  children,
}: {
  title: string
  tone: 'orange' | 'blue'
  children: ReactNode
}) {
  const toneClasses = tone === 'orange'
    ? 'border-orange-200 bg-orange-50'
    : 'border-blue-200 bg-blue-50'

  return (
    <div className={`rounded-xl border p-4 ${toneClasses}`}>
      <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
      <div className="mt-2 space-y-2 text-sm text-gray-700">{children}</div>
    </div>
  )
}

export function M1Guide({
  activeTickers,
  effectiveTickers,
}: {
  activeTickers?: number
  effectiveTickers?: number
}) {
  return (
    <GuideCard title="M1 hoạt động thế nào" tone="orange">
      <p>
        <b>Đầu vào là tick realtime</b>. Hệ thống cộng dồn tick thành bar 1 phút, nhưng cứ khoảng <b>15 giây</b> lại
        chụp snapshot giữa phút để báo sớm nếu khối lượng đang phình mạnh.
      </p>
      <p>
        <b>Trạng thái</b>: <b>Chờ 15p</b> = vừa phát hiện, chưa qua xác nhận. <b>Xác nhận 15p</b> = lực duy trì tốt.
        <b> Không xác nhận</b> = spike hụt lực sau đó.
      </p>
      <p>
        <b>Bên mua</b> = tỷ lệ khối lượng chủ động mua. <b>Chất lượng</b> = điểm A/B/C của nến, nền tích lũy,
        MA10/MA20 và MACD.
      </p>
      {(activeTickers != null || effectiveTickers != null) && (
        <p className="text-xs text-gray-600">
          Hiện app đang quét intraday
          {activeTickers != null ? (
            <> <b>{effectiveTickers ?? '—'}</b> / <b>{activeTickers}</b> mã.</>
          ) : (
            <> <b>{effectiveTickers ?? '—'}</b> mã.</>
          )}
        </p>
      )}
    </GuideCard>
  )
}

export function M3Guide({
  activeTickers,
  coveredTickers,
}: {
  activeTickers?: number
  coveredTickers?: number
}) {
  return (
    <GuideCard title="M3 hoạt động thế nào" tone="blue">
      <p>
        <b>M3 là daily scanner</b>. Sau khi bar ngày được chốt, hệ thống quét breakout theo <b>khối lượng so với MA20</b>
        và <b>mức tăng giá so với hôm trước</b>.
      </p>
      <p>
        <b>Candidate</b> = mã vừa có tín hiệu breakout theo daily. <b>Cycle</b> = mã đã được đưa vào trạng thái
        theo dõi qua các pha phân phối, rewatch và tạo đáy.
      </p>
      {(activeTickers != null || coveredTickers != null) && (
        <p className="text-xs text-gray-600">
          Hiện app đang có dữ liệu daily cho
          {activeTickers != null ? (
            <> <b>{coveredTickers ?? activeTickers}</b> / <b>{activeTickers}</b> mã.</>
          ) : (
            <> <b>{coveredTickers ?? '—'}</b> mã.</>
          )}
        </p>
      )}
    </GuideCard>
  )
}
