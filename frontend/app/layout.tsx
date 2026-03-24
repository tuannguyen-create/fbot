import type { Metadata } from 'next'
import './globals.css'
import { Providers } from './providers'
import { Layout } from '@/components/Layout'

export const metadata: Metadata = {
  title: 'fbot — Cảnh báo chứng khoán',
  description: 'Hệ thống cảnh báo thị trường chứng khoán Việt Nam',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <Providers>
          <Layout>{children}</Layout>
        </Providers>
      </body>
    </html>
  )
}
