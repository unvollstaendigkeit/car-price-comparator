import { en } from "./en"
import { sk } from "./sk"
import type { Locale } from "./locale-context"

export type Dictionary = typeof en

export const dictionaries: Record<Locale, Dictionary> = { sk, en }

/** Compile-time check that sk.ts and en.ts stay in sync (same shape). */
const _shapeCheck: Dictionary = sk
void _shapeCheck
