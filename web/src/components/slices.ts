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

/**
 * Give every label in `labels` a colour, keeping the ones already in `assigned`.
 * A new label takes the first palette colour not used by a label on screen right now, so a
 * label keeps its colour while it is visible, and a colour freed by a label that left (another
 * portfolio, a removed holding) is reused instead of falling back to grey.
 */
export function assignColors(
  assigned: Map<string, string>, labels: string[], palette: string[], other: string,
): Map<string, string> {
  const onScreen = new Set(labels)
  const used = new Set<string>()
  for (const [label, color] of assigned) if (onScreen.has(label) && color !== other) used.add(color)
  for (const label of labels) {
    const current = assigned.get(label)
    if (current !== undefined && current !== other) continue
    if (label.startsWith('Other')) { assigned.set(label, other); continue }
    const free = palette.find((c) => !used.has(c)) ?? other
    assigned.set(label, free)
    if (free !== other) used.add(free)
  }
  return assigned
}

/** Stable colour per entity within one breakdown key: first-seen label takes the next free slot. */
export function useSliceColors(slices: Slice[], key: string): Map<string, string> {
  const [assigned] = useState(() => new Map<string, Map<string, string>>())
  return useMemo(() => {
    if (!assigned.has(key)) assigned.set(key, new Map())
    const m = assignColors(assigned.get(key)!, slices.map((s) => s.label), SLOTS.map(cssVar), cssVar('--other'))
    return new Map(m)
  }, [slices, key, assigned])
}
