import type { Metadata, Viewport } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import './globals.css'

const geistSans = Geist({ subsets: ['latin'], variable: '--font-geist-sans' })
const geistMono = Geist_Mono({ subsets: ['latin'], variable: '--font-geist-mono' })

export const metadata: Metadata = {
  title: 'Carval — Market Valuation',
  description:
    'Value a used car against two independent marketplaces (Autobazar.eu and Bazoš.sk), shown separately with full data transparency.',
}

export const viewport: Viewport = {
  themeColor: '#1a1d24',
  colorScheme: 'dark',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} bg-background`}>
      <body>{children}</body>
    </html>
  )
}
