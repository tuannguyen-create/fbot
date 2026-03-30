'use client'

import type { ReactNode } from 'react'

export function InfoTooltip({
  label = 'i',
  title,
  children,
}: {
  label?: string
  title?: string
  children: ReactNode
}) {
  return (
    <span className="relative inline-flex items-center group align-middle">
      <span
        className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-gray-300 bg-white text-[10px] font-semibold text-gray-500 cursor-help"
        aria-label={title ?? 'Thông tin'}
      >
        {label}
      </span>
      <span className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 hidden w-72 -translate-x-1/2 rounded-lg border border-gray-200 bg-white p-3 text-left text-xs font-normal text-gray-700 shadow-lg group-hover:block group-focus-within:block">
        {title && <span className="mb-1 block font-semibold text-gray-900">{title}</span>}
        {children}
      </span>
    </span>
  )
}
