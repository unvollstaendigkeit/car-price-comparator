"use client"

/**
 * Locale is pure client state (localStorage), never part of the URL/routing.
 * SK is the site default: SSR and first paint always render SK, then flip to
 * a stored EN preference after mount - a deliberate, minor tradeoff for
 * keeping routing untouched.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

export type Locale = "sk" | "en"
export const DEFAULT_LOCALE: Locale = "sk"
const STORAGE_KEY = "carval:locale"

const LocaleContext = createContext<{ locale: Locale; setLocale: (l: Locale) => void }>({
  locale: DEFAULT_LOCALE,
  setLocale: () => {},
})

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE)

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY)
      if (stored === "sk" || stored === "en") setLocaleState(stored)
    } catch {
      // private mode / storage disabled - fall back to the default silently
    }
  }, [])

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  function setLocale(l: Locale) {
    setLocaleState(l)
    try {
      window.localStorage.setItem(STORAGE_KEY, l)
    } catch {
      // ignore - locale just won't persist across visits
    }
  }

  return <LocaleContext.Provider value={{ locale, setLocale }}>{children}</LocaleContext.Provider>
}

export function useLocale() {
  return useContext(LocaleContext)
}
