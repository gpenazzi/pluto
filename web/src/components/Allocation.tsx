import { useMemo, useState } from 'react'
import type { BreakdownKey, Slice, Valuation } from '../types'
import Donut from './Donut'
import { foldSlices, useSliceColors } from './slices'
import { money, pct } from '../format'

const KEYS: { key: BreakdownKey; label: string }[] = [
  { key: 'instrument', label: 'Instrument' },
  { key: 'asset_class', label: 'Asset class' },
  { key: 'asset_type', label: 'Type' },
  { key: 'currency', label: 'Currency' },
]

function instrumentSlices(v: Valuation): Slice[] {
  const rows: Slice[] = v.positions
    .filter((p) => p.market_value !== null)
    .map((p) => ({ label: p.name, value: p.market_value!, weight_pct: p.weight_pct }))
  const cash = parseFloat(v.cash_value)
  if (cash > 0) {
    const total = parseFloat(v.total_value) || 1
    rows.push({ label: 'Cash', value: v.cash_value, weight_pct: ((cash / total) * 100).toFixed(2) })
  }
  return rows.sort((a, b) => parseFloat(b.value) - parseFloat(a.value))
}

function pretty(s: Slice): Slice {
  const label = s.label.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()).replace(/^Etf$/, 'ETF')
  return { ...s, label }
}

export default function Allocation({ valuation }: { valuation: Valuation }) {
  const [key, setKey] = useState<BreakdownKey>('instrument')
  const slices = useMemo(
    () => foldSlices(key === 'instrument' ? instrumentSlices(valuation) : valuation.breakdown[key].map(pretty)),
    [valuation, key],
  )
  const colors = useSliceColors(slices, key)
  return (
    <section className="card">
      <h2>
        Allocation
        <span className="right seg">
          {KEYS.map((k) => (
            <button key={k.key} className={k.key === key ? 'on' : ''} onClick={() => setKey(k.key)}>{k.label}</button>
          ))}
        </span>
      </h2>
      <div className="alloc">
        <Donut slices={slices} colors={colors} currency={valuation.base_currency} />
        <ul className="legend" aria-label="Allocation legend">
          {slices.map((s) => (
            <li key={s.label}>
              <span className="sw" style={{ background: colors.get(s.label) }} />
              <span>{s.label}</span>
              <span className="v">{money(s.value, valuation.base_currency)}</span>
              <span className="w">{pct(s.weight_pct)}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
