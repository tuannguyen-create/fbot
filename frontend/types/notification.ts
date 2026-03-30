export interface NotificationReviewItem {
  id: string
  source: 'log' | 'draft'
  channel: 'telegram' | 'email'
  status: 'sent' | 'failed' | 'not_sent'
  sent_at: string | null
  event_type: string | null
  title: string
  preview_text: string | null
  alert_id: number | null
  cycle_id: number | null
  ticker: string | null
  link: string | null
  message_id?: string | null
}

export interface NotificationReviewData {
  window: 'today' | '7d' | '30d'
  channel: 'telegram' | 'email'
  telegram_configured: boolean
  sent_count: number
  failed_count: number
  draft_count: number
  items: NotificationReviewItem[]
}
