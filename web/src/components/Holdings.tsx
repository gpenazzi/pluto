import type { Valuation } from '../types'
import { money, num, pct, timeAgo } from '../format'

export default function Holdings({ valuation }: { valuation: Valuation }) {
  const base = valuation.base_currency
  return (
    <section className="card">
      <h2>Holdings <span className="right dim">prices as of {new Date(valuation.as_of).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span></h2>
      {valuation.warnings.map((w, i) => <div key={i} className="notice">{w}</div>)}
      {valuation.missing_prices.length > 0 && (
        <div className="notice">No price for: {valuation.missing_prices.join(', ')}. These positions are excluded from the total.</div>
      )}
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>Instrument</th><th className="n">Qty</th><th className="n">Price</th><th className="n">Day</th>
              <th className="n">Value ({base})</th><th className="n">Weight</th><th className="n">P&amp;L</th><th>Quote</th>
            </tr>
          </thead>
          <tbody>
            {valuation.positions.map((p) => {
              const pnl = p.unrealized_pnl !== null ? parseFloat(p.unrealized_pnl) : null
              const day = p.day_change_pct !== null ? parseFloat(p.day_change_pct) : null
              return (
                <tr key={p.instrument_id}>
                  <td>{p.name}<div className="dim">{p.instrument_id}</div></td>
                  <td className="n">{num(p.quantity, 2)}</td>
                  <td className="n">{p.price ? `${num(p.price)} ${p.price_currency}` : '–'}</td>
                  <td className={'n ' + (day === null ? '' : day >= 0 ? 'up' : 'down')}>{pct(day, 2, true)}</td>
                  <td className="n">{money(p.market_value, base)}</td>
                  <td className="n">{pct(p.weight_pct)}</td>
                  <td className={'n ' + (pnl === null ? '' : pnl >= 0 ? 'up' : 'down')}>
                    {money(pnl, base)}<div className="dim">{pct(p.unrealized_pnl_pct, 1, true)}</div>
                  </td>
                  <td>
                    {p.quote_source ? (
                      <>
                        {p.stale && <span className="tag warn">stale</span>} <span className="dim">{p.quote_source} · {timeAgo(p.quote_time)}</span>
                      </>
                    ) : <span className="tag bad">missing</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="dim" style={{ marginTop: 8 }}>
        {valuation.cash_tracked ? `Cash ${money(valuation.cash_value, base)}` : 'Cash not tracked (record a deposit to start)'} ·{' '}
        {valuation.providers.map((pr) => (
          <span key={pr.name} title={pr.last_error ?? ''}>
            {pr.name} {pr.failures ? `${pr.failures}/${pr.calls} failed` : 'ok'}{pr.in_cooldown ? ' (cooldown)' : ''}{' '}
          </span>
        ))}
      </div>
    </section>
  )
}
