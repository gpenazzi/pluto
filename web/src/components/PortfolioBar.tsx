import { useState } from 'react'
import { api } from '../api'
import type { PortfolioSummary } from '../types'

interface Props {
  current: string
  portfolios: PortfolioSummary[]
  onChanged: () => void
  onError: (msg: string | null) => void
}

export default function PortfolioBar({ current, portfolios, onChanged, onError }: Props) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [currency, setCurrency] = useState('EUR')
  const [busy, setBusy] = useState(false)

  async function run(action: () => Promise<unknown>) {
    setBusy(true); onError(null)
    try { await action(); onChanged() } catch (e) { onError(String((e as Error).message)) } finally { setBusy(false) }
  }

  async function create(e: React.FormEvent) {
    e.preventDefault()
    const n = name.trim()
    if (!n) return
    await run(() => api.createPortfolio(n, currency.trim().toUpperCase() || 'EUR'))
    setCreating(false); setName('')
  }

  async function remove() {
    const row = portfolios.find((p) => p.name === current)
    const what = row ? `"${current}" (${row.transactions} transactions, ${row.version} versions)` : `"${current}"`
    if (!confirm(`Delete portfolio ${what}?\n\nIt is moved to ~/.pluto/trash, not erased.`)) return
    await run(() => api.deletePortfolio(current))
  }

  return (
    <span className="pbar">
      <select value={current} disabled={busy} onChange={(e) => run(() => api.selectPortfolio(e.target.value))} aria-label="Portfolio">
        {portfolios.map((p) => <option key={p.name} value={p.name}>{p.name} · {p.base_currency}</option>)}
      </select>
      {creating ? (
        <form className="row" onSubmit={create}>
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="name" style={{ width: 140 }} />
          <input value={currency} onChange={(e) => setCurrency(e.target.value)} placeholder="EUR" style={{ width: 64 }} maxLength={3} />
          <button className="primary" type="submit" disabled={busy || !name.trim()}>Create</button>
          <button type="button" onClick={() => setCreating(false)}>Cancel</button>
        </form>
      ) : (
        <>
          <button onClick={() => setCreating(true)} disabled={busy}>New</button>
          <button onClick={remove} disabled={busy || portfolios.length <= 1} title={portfolios.length <= 1 ? 'Create another portfolio first' : 'Move this portfolio to the trash'}>Delete</button>
        </>
      )}
    </span>
  )
}
