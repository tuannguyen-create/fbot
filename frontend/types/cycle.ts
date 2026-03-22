export interface CycleSummary {
  id: number
  ticker: string
  breakout_date: string          // YYYY-MM-DD
  phase: 'distributing' | 'bottoming' | 'done'
  days_remaining: number | null
  predicted_bottom_date: string | null
  trading_days_elapsed: number | null
  estimated_dist_days: number | null
}

export interface CycleDetail extends CycleSummary {
  peak_volume: number | null
  breakout_price: number | null
  alert_sent_10d: boolean
  alert_sent_bottom: boolean
  created_at: string
  updated_at: string
}
