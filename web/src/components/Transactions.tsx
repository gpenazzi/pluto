import { useState } from 'react'
import { api } from '../api'
import type { Transaction } from '../types'
import { num } from '../format'

export default function Transactions({ transactions, onChanged }: { transactions: Transaction[]; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null)
  async function remove(t: Transaction) {
    if (!confirm(`Remove ${t.date} ${t.type} ${t.instrument ?? ''}? (You can revert later.)`)) return
    try { await api.removeTransaction(t.id); onChanged() } catch (e) { setError(String((e as Error).message)) }
  }
  const rows = [...transactions].reverse()
  return (
    <section className="card">
      <h2>Transactions <span className="right dim">{transactions.length} total</span></h2>
      {error && <div className="error">{error}</div>}
      <div style={{ overflowX: 'auto', maxHeight: 360, overflowY: 'auto' }}>
        <table>
          <thead><tr><th>Date</th><th>Type</th><th>Instrument</th><th className="n">Qty</th><th className="n">Price</th><th className="n">Amount</th><th>Ccy</th><th className="n">Fees</th><th>Source</th><th></th></tr></thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id}>
                <td>{t.date}</td><td>{t.type}</td><td>{t.instrument ?? ''}{t.note ? <div className="dim">{t.note}</div> : null}</td>
                <td className="n">{t.quantity ? num(t.quantity, 4) : ''}</td><td className="n">{t.price ? num(t.price) : ''}</td>
                <td className="n">{t.amount ? num(t.amount) : ''}</td><td>{t.currency}</td><td className="n">{num(t.fees)}</td>
                <td><span className="tag">{t.source}</span></td>
                <td><button onClick={() => remove(t)} title="Remove">✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
