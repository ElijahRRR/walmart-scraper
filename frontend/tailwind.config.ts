// tailwind.config.ts — 扩展沃尔玛蓝主色
import type { Config } from 'tailwindcss'

export default {
  content: [
    './components/**/*.{vue,js,ts}',
    './layouts/**/*.vue',
    './pages/**/*.vue',
    './composables/**/*.{js,ts}',
    './app.vue',
  ],
  theme: {
    extend: {
      colors: {
        // 沃尔玛蓝主色系
        walmart: {
          DEFAULT: '#0071ce',
          50:  '#e6f2fb',
          100: '#cce5f6',
          200: '#99ccee',
          300: '#66b2e5',
          400: '#3399dc',
          500: '#0071ce', // 主色
          600: '#005fa3',
          700: '#004d82',
          800: '#003a61',
          900: '#002740',
        },
      },
      borderRadius: {
        DEFAULT: '0.5rem',
      },
      boxShadow: {
        card: '0 1px 6px rgba(0,0,0,0.08)',
        'card-hover': '0 4px 16px rgba(0,113,206,0.12)',
      },
    },
  },
  plugins: [],
} satisfies Config
