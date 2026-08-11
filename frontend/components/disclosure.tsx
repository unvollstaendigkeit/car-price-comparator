"use client"

import type { ReactNode } from "react"
import { cn } from "@/lib/format"

/**
 * Presentation-only progressive-disclosure wrapper built on native
 * <details>/<summary> (accessible, no JS state). Used to move technical
 * transparency (sample sizes, provenance, confidence reasons, retrieval
 * errors, comparables) out of the primary visual hierarchy without ever
 * removing it.
 */
export function Disclosure({
  summary,
  children,
  className,
  tone = "muted",
}: {
  summary: ReactNode
  children: ReactNode
  className?: string
  tone?: "muted" | "accent"
}) {
  return (
    <details className={cn("group", className)}>
      <summary
        className={cn(
          "flex cursor-pointer list-none items-center gap-1.5 text-[13px] font-medium transition-colors",
          "[&::-webkit-details-marker]:hidden",
          tone === "accent"
            ? "text-accent hover:text-accent"
            : "text-faint hover:text-foreground",
        )}
      >
        <svg
          viewBox="0 0 12 12"
          className="h-3 w-3 shrink-0 transition-transform duration-150 group-open:rotate-90"
          aria-hidden="true"
        >
          <path d="M4 2.5 8 6l-4 3.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {summary}
      </summary>
      <div className="mt-2.5">{children}</div>
    </details>
  )
}
