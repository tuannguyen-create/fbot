import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        alert: {
          fired: '#f97316',
          confirmed: '#22c55e',
          cancelled: '#9ca3af',
        },
        ratio: {
          normal: '#e5e7eb',
          elevated: '#fde047',
          high: '#fb923c',
          extreme: '#ef4444',
        },
        phase: {
          distributing: '#f97316',
          bottoming: '#eab308',
          done: '#22c55e',
        },
      },
    },
  },
  plugins: [],
}
export default config
