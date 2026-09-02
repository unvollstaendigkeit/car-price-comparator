"use client"

import { cn } from "@/lib/format"
import { useLocale, type Locale } from "@/lib/i18n/locale-context"

const OPTIONS: Locale[] = ["sk", "en"]
const LABELS: Record<Locale, string> = { sk: "SK", en: "EN" }

export function LocaleSwitcher() {
  const { locale, setLocale } = useLocale()
  return (
    <div role="group" aria-label="Language" className="inline-flex w-fit items-center gap-1.5 text-[13px] font-medium">
      {OPTIONS.map((l, i) => (
        <span key={l} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-faint">|</span>}
          <button
            type="button"
            aria-pressed={locale === l}
            onClick={() => setLocale(l)}
            className={cn("transition-colors", locale === l ? "text-accent" : "text-faint hover:text-foreground")}
          >
            {LABELS[l]}
          </button>
        </span>
      ))}
    </div>
  )
}
