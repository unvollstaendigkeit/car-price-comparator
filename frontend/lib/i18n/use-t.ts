"use client"

import { useLocale } from "./locale-context"
import { dictionaries } from "./dictionaries"

/** Current-locale dictionary, e.g. `useT().tabs.single`. */
export function useT() {
  const { locale } = useLocale()
  return dictionaries[locale]
}
