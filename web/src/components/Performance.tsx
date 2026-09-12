import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { api } from '../api'
import type { PerfMetrics, Performance as Perf } from '../types'
import { money, pct } from '../format'
import { cssVar } from './slices'

echarts.use([LineChart, GridComponent, TooltipComponent, SVGRenderer])

const PERIODS = ['1m', '3m', '6m', 'ytd', '1y', '3y', '5y', 'all']
type View = 'composition' | 'actual'

export default function Performance({ currency, refreshKey }: { currency: string; refreshKey: number }) {
  const [period, setPeriod] = useState('1y')
  const [view, setView] = useState<View>('composition')
  const [data, setData] = useState<Perf | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let live = true
    // fetch-on-change: state is set when the request resolves
    // oxlint-disable-next-line react/set-state-in-effect
    setLoading(true)
    api.performance(period).then((d) => { if (live) { setData(d); setError(null) } })
      .catch((e) => { if (live) setError(String((e as Error).message)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [period, refreshKey])

  const m: PerfMetrics | null = data ? data[view] : null
  return (
    <section className="card">
      <h2>
        Performance
        <span className="seg" style={{ marginLeft: 8 }}>
          <button className={view === 'composition' ? 'on' : ''} onClick={() => setView('composition')} title="Today's holdings, backtested on total-return prices">Composition</button>
          <button className={view === 'actual' ? 'on' : ''} onClick={() => setView('actual')} title="What the portfolio was worth given its transactions">Actual</button>
        </span>
        <span className="right seg">
          {PERIODS.map((p) => <button key={p} className={p === period ? 'on' : ''} onClick={() => setPeriod(p)}>{p.toUpperCase()}</button>)}
        </span>
      </h2>
      {error && <div className="error">{error}</div>}
      {m && (
        <>
          <div className="tiles">
            <Tile label={view === 'actual' ? 'Time-weighted return' : 'Total return'} value={pct(m.twr_pct, 2, true)} tone={m.twr_pct} />
            <Tile label="Annualized" value={pct(m.twr_annualized_pct, 2, true)} tone={m.twr_annualized_pct} />
            {view === 'actual' && <Tile label="Money-weighted (ann.)" value={pct(m.mwr_annualized_pct, 2, true)} tone={m.mwr_annualized_pct} />}
            <Tile label="Volatility (ann.)" value={pct(m.volatility_pct)} />
            <Tile label="Max drawdown" value={pct(m.max_drawdown_pct)} sub={m.drawdown_from ? `${m.drawdown_from} → ${m.drawdown_to}` : undefined} tone={m.max_drawdown_pct} />
            <Tile label="Gain" value={money(m.gain, currency)} sub={`${money(m.start_value, currency)} → ${money(m.end_value, currency)}`} tone={m.gain} />
            {data?.benchmark && <Tile label={`Benchmark ${data.benchmark.symbol}`} value={pct(data.benchmark.twr, 2, true)} tone={data.benchmark.twr} />}
          </div>
          {m.note && (
            <div className="notice">
              {m.note}
              {m.excluded && m.excluded.length > 0 && (
                <span className="dim"> ({m.excluded.map((e) => `${e.name}${e.history_from ? ` since ${e.history_from}` : ''}`).join('; ')})</span>
              )}
            </div>
          )}
          <Line series={m.series} currency={currency} benchmark={data?.benchmark?.symbol ?? null} label={view === 'composition' ? "Today's holdings" : 'Portfolio'} />
          {view === 'composition' && m.contributions && m.contributions.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary className="dim">Contribution by instrument (total return over the window)</summary>
              <table>
                <thead><tr><th>Instrument</th><th className="n">Gain</th><th className="n">Weight</th></tr></thead>
                <tbody>
                  {m.contributions.map((c) => (
                    <tr key={c.instrument_id}>
                      <td>{c.name}</td>
                      <td className={'n ' + (parseFloat(c.gain) >= 0 ? 'up' : 'down')}>{money(c.gain, currency)}</td>
                      <td className="n">{pct(c.weight_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          {data && data.missing_history.length > 0 && <div className="dim" style={{ marginTop: 6 }}>No price history for: {data.missing_history.join(', ')}</div>}
        </>
      )}
      {loading && !m && (
        <div className="notice">
          Loading price history… The first view of a portfolio downloads daily prices for every holding
          and the benchmark; later views use the cache.
        </div>
      )}
    </section>
  )
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string | null }) {
  const n = tone !== undefined && tone !== null ? parseFloat(tone) : NaN
  const cls = Number.isNaN(n) ? '' : n >= 0 ? 'up' : 'down'
  return (
    <div className="tile">
      <div className="dim">{label}</div>
      <div className={'tile-v ' + cls}>{value}</div>
      {sub && <div className="dim">{sub}</div>}
    </div>
  )
}

function Line({ series, currency, benchmark, label }: { series: [string, number, number | null][]; currency: string; benchmark: string | null; label: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  useEffect(() => {
    if (!ref.current) return
    chart.current = echarts.init(ref.current, undefined, { renderer: 'svg' })
    const ro = new ResizeObserver(() => chart.current?.resize())
    ro.observe(ref.current)
    return () => { ro.disconnect(); chart.current?.dispose(); chart.current = null }
  }, [])
  useEffect(() => {
    if (!chart.current) return
    const hasBench = benchmark !== null && series.some((r) => r[2] !== null)
    chart.current.setOption({
      grid: { left: 8, right: 8, top: 8, bottom: 24, containLabel: true },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: cssVar('--ink-2') } },
        backgroundColor: cssVar('--surface'), borderColor: cssVar('--border'), textStyle: { color: cssVar('--ink') },
        valueFormatter: (v: number) => money(v, currency),
      },
      xAxis: { type: 'category', data: series.map((r) => r[0]), axisLine: { lineStyle: { color: cssVar('--grid') } }, axisLabel: { color: cssVar('--muted') } },
      yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: cssVar('--grid') } }, axisLabel: { color: cssVar('--muted'), formatter: (v: number) => new Intl.NumberFormat(undefined, { notation: 'compact' }).format(v) } },
      series: [
        { name: label, type: 'line', data: series.map((r) => r[1]), showSymbol: false, lineStyle: { width: 2, color: cssVar('--s1') }, itemStyle: { color: cssVar('--s1') } },
        ...(hasBench ? [{ name: benchmark, type: 'line', data: series.map((r) => r[2]), showSymbol: false, lineStyle: { width: 2, color: cssVar('--muted'), type: 'dashed' }, itemStyle: { color: cssVar('--muted') } }] : []),
      ],
    }, true)
  }, [series, currency, benchmark, label])
  return (
    <>
      <div ref={ref} style={{ width: '100%', height: 260 }} role="img" aria-label="Portfolio value over time" />
      <div className="dim" style={{ display: 'flex', gap: 14 }}>
        <span><span className="sw" style={{ display: 'inline-block', width: 12, height: 3, background: cssVar('--s1'), verticalAlign: 'middle', marginRight: 6 }} />{label}</span>
        {benchmark && <span><span style={{ display: 'inline-block', width: 12, height: 0, borderTop: `2px dashed ${cssVar('--muted')}`, verticalAlign: 'middle', marginRight: 6 }} />{benchmark} (same money, total return)</span>}
      </div>
    </>
  )
}
