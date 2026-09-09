import { useMemo, useState } from 'react'
import type { Slice } from '../types'

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

const MAX_SLICES = 7
const SLOTS = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8']

/** Fold slices past MAX_SLICES into "Other"; colours follow the entity, never its rank. */
export function foldSlices(slices: Slice[]): Slice[] {
  if (slices.length <= MAX_SLICES + 1) return slices
  const head = slices.slice(0, MAX_SLICES)
  const rest = slices.slice(MAX_SLICES)
  const value = rest.reduce((a, s) => a + parseFloat(s.value), 0)
  const weight = rest.reduce((a, s) => a + parseFloat(s.weight_pct), 0)
  return [...head, { label: `Other (${rest.length})`, value: value.toFixed(2), weight_pct: weight.toFixed(2) }]
}

/** Stable colour per entity within one breakdown key: first-seen label takes the next slot. */
export function useSliceColors(slices: Slice[], key: string): Map<string, string> {
  const [assigned] = useState(() => new Map<string, Map<string, string>>())
  return useMemo(() => {
    if (!assigned.has(key)) assigned.set(key, new Map())
    const m = assigned.get(key)!
    for (const s of slices) {
      if (m.has(s.label)) continue
      if (s.label.startsWith('Other')) { m.set(s.label, cssVar('--other')); continue }
      const used = new Set(m.values())
      const slot = SLOTS.map(cssVar).find((c) => !used.has(c)) ?? cssVar('--other')
      m.set(s.label, slot)
    }
    return new Map(m)
  }, [slices, key, assigned])
}

