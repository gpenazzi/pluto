import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ExposureData } from '../types'
import { pct } from '../format'
import Unavailable from './Unavailable'

type Key = 'region' | 'country' | 'sector'
const KEYS: { key: Key; label: string }[] = [
  { key: 'region', label: 'Region' }, { key: 'country', label: 'Country' }, { key: 'sector', label: 'Sector' },
]
const MAX_ROWS = 12

export default function Exposure({ refreshKey }: { refreshKey: number }) {
  const [key, setKey] = useState<Key>('region')
  const [data, setData] = useState<ExposureData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = (refresh = false) => {
    setBusy(true)
    api.exposure(refresh).then((d) => { setData(d); setError(null) })
      .catch((e) => setError(String((e as Error).message)))
      .finally(() => setBusy(false))
  }
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { load() }, [refreshKey])

  const rows = data?.available ? (data[`by_${key}`] ?? []) : []
  const shown = rows.slice(0, MAX_ROWS)
  const rest = rows.slice(MAX_ROWS).reduce((a, r) => a + r.weight_pct, 0)
  const max = Math.max(1, ...shown.map((r) => r.weight_pct))
  return (
    <section className="card">
      <h2>
        Look-through
        <span className="seg" style={{ marginLeft: 8 }}>
          {KEYS.map((k) => <button key={k.key} className={k.key === key ? 'on' : ''} onClick={() => setKey(k.key)}>{k.label}</button>)}
        </span>
        <span className="right dim">
          {data?.available && `${data.covered_pct}% of value covered · `}
          <button onClick={() => load(true)} disabled={busy}>{busy ? 'Fetching…' : 'Refresh data'}</button>
        </span>
      </h2>
      {error && <Unavailable what="Look-through" reason={error} />}
      {data && !data.available && <Unavailable what="Look-through" reason={data.reason} />}
      {data?.available && (
        <>
          <div className="bars">
            {shown.map((r) => (
              <div className="bar-row" key={r.label}>
                <span className="bar-label" title={r.label}>{r.label}</span>
                <span className="bar-track"><span className={'bar-fill' + (r.label === 'Unknown' ? ' unknown' : '')} style={{ width: `${(r.weight_pct / max) * 100}%` }} /></span>
                <span className="bar-v">{pct(r.weight_pct)}</span>
              </div>
            ))}
            {rest > 0 && (
              <div className="bar-row"><span className="bar-label">Other ({rows.length - MAX_ROWS})</span><span className="bar-track"><span className="bar-fill" style={{ width: `${(rest / max) * 100}%` }} /></span><span className="bar-v">{pct(rest)}</span></div>
            )}
          </div>
          {data.unknown.length > 0 && (
            <div className="notice">
              No holdings data for {data.unknown.map((u) => `${u.name} (${u.weight_pct}%)`).join(', ')}: shown as "Unknown".
              <div className="dim">{data.unknown.map((u) => `${u.name}: ${u.reason}`).join(' · ')}</div>
            </div>
          )}
          {data.notes.map((n, i) => <div key={i} className="dim">{n}</div>)}
          <details style={{ marginTop: 8 }}>
            <summary className="dim">Data per instrument (source and date)</summary>
            <table>
              <thead><tr><th>Instrument</th><th className="n">Weight</th><th>Source</th><th>As of</th><th>Top countries</th><th>Note</th></tr></thead>
              <tbody>
                {data.instruments.map((r) => (
                  <tr key={r.instrument_id}>
                    <td>{r.name}</td><td className="n">{pct(r.weight_pct)}</td><td>{r.source ?? '–'}</td><td className="dim">{r.as_of ?? '–'}</td>
                    <td className="dim">{Object.entries(r.countries).slice(0, 3).map(([c, v]) => `${c} ${Math.round(v)}%`).join(', ')}</td>
                    <td className="dim">{r.note ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
      {!data && !error && <div className="dim">Loading holdings data…</div>}
    </section>
  )
}
