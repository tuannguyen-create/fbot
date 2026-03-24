'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { StreamStatusBadge } from './StreamStatusBadge'

const navItems = [
  { href: '/dashboard', label: 'Tổng quan', icon: '📊' },
  { href: '/alerts', label: 'Cảnh báo', icon: '🔔' },
  { href: '/cycles', label: 'Chu kỳ', icon: '📈' },
  { href: '/watchlist', label: 'Theo dõi', icon: '📋' },
  { href: '/settings', label: 'Cài đặt', icon: '⚙️' },
]

export function Layout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()

  return (
    <div className="min-h-screen bg-gray-50 flex">
      {/* Sidebar — desktop */}
      <aside className="hidden md:flex flex-col w-56 bg-white border-r border-gray-200 fixed inset-y-0 left-0 z-30">
        <div className="p-4 border-b border-gray-200">
          <h1 className="text-xl font-bold text-gray-900">fbot</h1>
          <p className="text-xs text-gray-400">Cảnh báo chứng khoán</p>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {navItems.map((item) => {
            const active = pathname.startsWith(item.href)
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                  active
                    ? 'bg-orange-50 text-orange-700 font-medium'
                    : 'text-gray-600 hover:bg-gray-100'
                }`}
              >
                <span>{item.icon}</span>
                {item.label}
              </Link>
            )
          })}
        </nav>
        <div className="p-3 border-t border-gray-200">
          <StreamStatusBadge />
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 md:ml-56 flex flex-col">
        {/* Top bar — mobile header */}
        <header className="md:hidden bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
          <h1 className="text-lg font-bold text-gray-900">fbot</h1>
          <StreamStatusBadge />
        </header>

        <main className="flex-1 p-4 pb-20 md:pb-4">
          {children}
        </main>
      </div>

      {/* Bottom nav — mobile */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 bg-white border-t border-gray-200 z-30">
        <div className="flex">
          {navItems.map((item) => {
            const active = pathname.startsWith(item.href)
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex-1 flex flex-col items-center py-2 text-xs transition-colors ${
                  active ? 'text-orange-600' : 'text-gray-500'
                }`}
              >
                <span className="text-lg">{item.icon}</span>
                <span className="mt-0.5">{item.label}</span>
              </Link>
            )
          })}
        </div>
      </nav>
    </div>
  )
}
