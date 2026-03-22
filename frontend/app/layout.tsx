import type { Metadata } from 'next'
import './globals.css'
import { Providers } from './providers'
import { Layout } from '@/components/Layout'

export const metadata: Metadata = {
  title: 'fbot — VN Stock Alerts',
  description: 'Vietnam stock market alert system',
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
