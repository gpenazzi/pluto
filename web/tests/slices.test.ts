import assert from 'node:assert/strict'
import { test } from 'node:test'
import { assignColors, foldSlices } from '../src/components/slices.ts'

const PALETTE = ['c1', 'c2', 'c3']
const OTHER = 'grey'

test('labels get palette colours in first-seen order and keep them', () => {
  const m = assignColors(new Map(), ['a', 'b'], PALETTE, OTHER)
  assert.deepEqual([...m], [['a', 'c1'], ['b', 'c2']])
  assignColors(m, ['b', 'a', 'c'], PALETTE, OTHER)
  assert.equal(m.get('a'), 'c1')
  assert.equal(m.get('b'), 'c2')
  assert.equal(m.get('c'), 'c3')
})

test('colours of labels that left the screen are reused, not grey', () => {
  // a first portfolio with more labels than the palette has colours
  const m = assignColors(new Map(), ['p', 'q', 'r', 's'], PALETTE, OTHER)
  assert.equal(m.get('s'), OTHER)
  // switching to a portfolio that shares one label: the rest take the freed colours
  assignColors(m, ['q', 'x', 'y'], PALETTE, OTHER)
  assert.equal(m.get('q'), 'c2')
  assert.equal(m.get('x'), 'c1')
  assert.equal(m.get('y'), 'c3')
  // a label that was grey only because the palette was full gets a colour once one is free
  assignColors(m, ['s', 'q'], PALETTE, OTHER)
  assert.notEqual(m.get('s'), OTHER)
  assert.equal(m.get('q'), 'c2')
})

test('an "Other" slice is always grey and never takes a palette colour', () => {
  const m = assignColors(new Map(), ['Other (3)', 'a'], PALETTE, OTHER)
  assert.equal(m.get('Other (3)'), OTHER)
  assert.equal(m.get('a'), 'c1')
})

test('foldSlices keeps up to eight slices and folds the rest into Other', () => {
  const s = (i: number) => ({ label: `l${i}`, value: '10', weight_pct: '10' })
  assert.equal(foldSlices([1, 2, 3, 4, 5, 6, 7, 8].map(s)).length, 8)
  const folded = foldSlices([1, 2, 3, 4, 5, 6, 7, 8, 9].map(s))
  assert.equal(folded.length, 8)
  assert.equal(folded[7].label, 'Other (2)')
  assert.equal(folded[7].value, '20.00')
})
