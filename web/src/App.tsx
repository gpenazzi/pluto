import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Portfolio, Transaction, Valuation, Version } from './types'
import { money, timeAgo } from './format'
import Allocation from './components/Allocation'
import Holdings from './components/Holdings'
import Chat from './components/Chat'
import TransactionForm from './components/TransactionForm'
import Transactions from './components/Transactions'
import Versions from './components/Versions'

const REFRESH_MS = 60_000

export default function App() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [valuation, setValuation] = useState<Valuation | null>(null)
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [versions, setVersions] = useState<{ head: number; versions: Version[] }>({ head: 0, versions: [] })
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async (force = false) => {
    try {
      const [p, t, v] = await Promise.all([api.portfolio(), api.transactions(), api.versions()])
      setPortfolio(p); setTransactions(t.transactions); setVersions(v)
      setValuation(await api.valuation(force))
      setError(null)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setLoading(false)
    }
  }, [])

  // Initial load: state is set after the fetch resolves, not synchronously.
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    const id = setInterval(() => { if (document.visibilityState === 'visible') refresh() }, REFRESH_MS)
    return () => clearInterval(id)
  }, [refresh])

  async function undo() {
    try { await api.undo(); refresh() } catch (e) { setError(String((e as Error).message)) }
  }

  const base = portfolio?.base_currency ?? 'EUR'
  return (
    <div className="app">
      <header className="top">
        <h1>Pluto</h1>
        <span className="sub">{portfolio?.name} · version {portfolio?.version}</span>
        {valuation && <span className="hero">{money(valuation.total_value, base)}</span>}
        {valuation && <span className="sub">as of {timeAgo(valuation.as_of)}{valuation.stale_prices.length ? ` · ${valuation.stale_prices.length} stale` : ''}</span>}
        <span className="spacer" />
        <button onClick={() => { setLoading(true); refresh(true) }} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh prices'}</button>
        <button onClick={undo} disabled={!portfolio || portfolio.version <= 1}>Undo</button>
      </header>
      <main className="main">
        {error && <div className="error">{error}</div>}
        {valuation && <Allocation valuation={valuation} />}
        {valuation && <Holdings valuation={valuation} />}
        {portfolio && <TransactionForm portfolio={portfolio} onChanged={() => refresh()} />}
        <Transactions transactions={transactions} onChanged={() => refresh()} />
        <Versions head={versions.head} versions={versions.versions} onChanged={() => refresh()} />
      </main>
      <Chat onChanged={() => refresh()} />
    </div>
  )
}
