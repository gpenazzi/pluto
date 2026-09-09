import { useState } from 'react'
import { api } from '../api'
import type { Version } from '../types'

export default function Versions({ head, versions, onChanged }: { head: number; versions: Version[]; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null)
  async function revert(v: number) {
    try { await api.revert(v); onChanged() } catch (e) { setError(String((e as Error).message)) }
  }
  const rows = [...versions].reverse()
  return (
    <section className="card">
      <h2>Versions <span className="right dim">HEAD = {head} · reverting never deletes</span></h2>
      {error && <div className="error">{error}</div>}
      <div style={{ maxHeight: 300, overflowY: 'auto' }}>
        <table>
          <thead><tr><th>#</th><th>When</th><th>Message</th><th className="n">Tx</th><th></th></tr></thead>
          <tbody>
            {rows.map((v) => (
              <tr key={v.version} style={v.version === head ? { fontWeight: 600 } : undefined}>
                <td>{v.version}</td>
                <td className="dim">{new Date(v.created_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}</td>
                <td>{v.message}{v.reverted_from ? <span className="dim"> (from {v.reverted_from})</span> : null}</td>
                <td className="n">{v.transactions}</td>
                <td>{v.version !== head && <button onClick={() => revert(v.version)}>Revert to this</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
