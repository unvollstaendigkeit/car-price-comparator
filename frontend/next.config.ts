import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  typescript: {
    ignoreBuildErrors: true,
  },
  eslint: {
    ignoreDuringBuilds: true,
  },
  async rewrites() {
    // Local dev only -- in production/Vercel, vercel.json's own
    // multi-service rewrite routes /api/* to the backend service instead.
    // `next dev` alone doesn't know about that config (only `vercel dev`
    // would), so this exists purely to make localhost:3000 work the same
    // way against a locally-running backend (see backend/main.py, run via
    // `uvicorn main:app --port 8000`).
    if (process.env.NODE_ENV === 'production') return []
    return [
      { source: '/api/:path*', destination: 'http://127.0.0.1:8000/api/:path*' },
    ]
  },
}

export default nextConfig
