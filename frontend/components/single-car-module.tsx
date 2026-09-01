"use client"

import type { CarInput } from "@/lib/types"
import { carInputToSearchParams } from "@/lib/car-input-url"
import { CarForm } from "@/components/car-form"

/**
 * Single-car valuation entry point. Submitting the form no longer runs the
 * comparison inline — it hands off to /result in a new tab (its own URL,
 * built from the car's inputs) so the search+result flow lives there. This
 * tab's form is left as-is, ready for another car, rather than duplicating
 * the live scrape here too.
 */
export function SingleCarModule() {
  function handleSubmit(input: CarInput) {
    const params = carInputToSearchParams(input)
    window.open(`/result?${params.toString()}`, "_blank", "noopener,noreferrer")
  }

  return (
    <div className="flex flex-col gap-7">
      <CarForm onSubmit={handleSubmit} busy={false} />
    </div>
  )
}
