import { useState } from 'react'
import { api, type ResolveCandidate } from '../api'
import type { Portfolio } from '../types'
import { today } from '../format'

const TYPES = ['buy', 'sell', 'dividend', 'fee', 'deposit', 'withdrawal'] as const

export default function TransactionForm({ portfolio, onChanged }: { portfolio: Portfolio; onChanged: () => void }) {
  const [type, setType] = useState<(typeof TYPES)[number]>('buy')
  const [instrument, setInstrument] = useState(portfolio.instruments[0]?.id ?? '')
  const [quantity, setQuantity] = useState('')
  const [price, setPrice] = useState('')
  const [amount, setAmount] = useState('')
  const [fees, setFees] = useState('')
  const [date, setDate] = useState(today())
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const needsInstrument = type === 'buy' || type === 'sell' || type === 'dividend'
  const isTrade = type === 'buy' || type === 'sell'

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setError(null); setOk(null)
    try {
      const r = await api.addTransaction({
        type, instrument: needsInstrument ? instrument : undefined,
        quantity: isTrade ? quantity : undefined, price: isTrade ? price : undefined,
        amount: !isTrade ? amount : undefined, fees: fees || undefined, date, note: note || undefined,
      })
      setOk(`Recorded (version ${r.version})`)
      setQuantity(''); setPrice(''); setAmount(''); setFees(''); setNote('')
      onChanged()
    } catch (err) {
      setError(String((err as Error).message))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <h2>Add transaction <span className="right dim">GUI fallback: the chat does the same</span></h2>
      <form className="tx" onSubmit={submit}>
        <label>Type
          <select value={type} onChange={(e) => setType(e.target.value as typeof type)}>
            {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        {needsInstrument && (
          <label className="wide">Instrument
            <select value={instrument} onChange={(e) => setInstrument(e.target.value)}>
              {portfolio.instruments.map((i) => <option key={i.id} value={i.id}>{i.name} ({i.currency})</option>)}
            </select>
          </label>
        )}
        {isTrade ? (
          <>
            <label>Quantity<input value={quantity} onChange={(e) => setQuantity(e.target.value)} inputMode="decimal" required /></label>
            <label>Price per unit<input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" required /></label>
          </>
        ) : (
          <label>Amount<input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required /></label>
        )}
        <label>Fees<input value={fees} onChange={(e) => setFees(e.target.value)} inputMode="decimal" placeholder="0" /></label>
        <label>Date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
        <label className="wide">Note<input value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <button className="primary" type="submit" disabled={busy}>Add</button>
      </form>
      {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
      {ok && <div className="dim" style={{ marginTop: 8 }}>{ok}</div>}
      <AddInstrument onChanged={onChanged} />
    </section>
  )
}

function AddInstrument({ onChanged }: { onChanged: () => void }) {
  const [query, setQuery] = useState('')
  const [cands, setCands] = useState<ResolveCandidate[] | null>(null)
  const [notes, setNotes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  async function search(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setMsg(null)
    try {
      const r = await api.resolve(query.trim())
      setCands(r.unique ? [r.unique] : r.candidates)
      setNotes([...(r.already_in_portfolio ? [`Already in portfolio: ${r.already_in_portfolio.name}`] : []), ...r.notes])
    } catch (err) { setMsg(String((err as Error).message)) } finally { setBusy(false) }
  }
  async function add(c: ResolveCandidate, symbol: string) {
    setBusy(true); setMsg(null)
    try {
      const r = await api.addInstrument(c.isin ? { isin: c.isin } : { symbol })
      setMsg(r.added ? `Added ${r.instrument?.name}` : `Not added: ${r.instrument ? 'already present' : (r.notes ?? []).join('; ')}`)
      if (r.added) { setCands(null); setQuery(''); onChanged() }
    } catch (err) { setMsg(String((err as Error).message)) } finally { setBusy(false) }
  }

  return (
    <div className="resolve">
      <form className="row" onSubmit={search}>
        <span className="dim">New instrument:</span>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Name, ticker or ISIN" style={{ flex: 1, minWidth: 200 }} />
        <button type="submit" disabled={busy || !query.trim()}>Search</button>
      </form>
      {notes.map((n, i) => <div key={i} className="dim">{n}</div>)}
      {cands && cands.length === 0 && <div className="dim">No match.</div>}
      {cands && cands.length > 0 && (
        <ul>
          {cands.map((c) => (
            <li key={c.name + c.preferred_symbol}>
              <b>{c.name}</b> <span className="dim">{c.isin ?? 'no ISIN'} · {c.type}</span>
              {c.listings.map((l) => (
                <button key={l.symbol} onClick={() => add(c, l.symbol)} disabled={busy} title={`Add with ${l.symbol} as preferred listing`}>
                  {l.symbol} · {l.currency}
                </button>
              ))}
            </li>
          ))}
        </ul>
      )}
      {msg && <div className="dim">{msg}</div>}
    </div>
  )
}
