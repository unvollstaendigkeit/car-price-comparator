"use client"

import { cn } from "@/lib/format"
import { useLocale, type Locale } from "@/lib/i18n/locale-context"

const OPTIONS: Locale[] = ["sk", "en"]
const LABELS: Record<Locale, string> = { sk: "SK", en: "EN" }

export function LocaleSwitcher() {
  const { locale, setLocale } = useLocale()
  return (
    <div role="group" aria-label="Language" className="inline-flex w-fit rounded-md border border-border bg-surface p-0.5">
      {OPTIONS.map((l) => (
        <button
          key={l}
          type="button"
          aria-pressed={locale === l}
          onClick={() => setLocale(l)}
          className={cn(
            "rounded px-2 py-1 text-[13px] font-medium transition-colors",
            locale === l ? "bg-accent text-accent-foreground" : "text-muted hover:text-foreground",
          )}
        >
          {LABELS[l]}
        </button>
      ))}
    </div>
  )
}
