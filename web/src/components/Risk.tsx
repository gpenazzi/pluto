import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { HeatmapChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { api } from '../api'
import type { RiskData } from '../types'
import { pct } from '../format'
import { cssVar } from './slices'
import Unavailable from './Unavailable'

echarts.use([HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, SVGRenderer])
const PERIODS = ['3m', '6m', '1y', '3y', '5y']

export default function Risk({ refreshKey }: { refreshKey: number }) {
  const [period, setPeriod] = useState('1y')
  const [data, setData] = useState<RiskData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api.risk(period).then((d) => { if (live) { setData(d); setError(null) } }).catch((e) => { if (live) setError(String((e as Error).message)) })
    return () => { live = false }
  }, [period, refreshKey])

  return (
    <section className="card">
      <h2>
        Risk
        <span className="right seg">{PERIODS.map((p) => <button key={p} className={p === period ? 'on' : ''} onClick={() => setPeriod(p)}>{p.toUpperCase()}</button>)}</span>
      </h2>
      {error && <Unavailable what="Risk analysis" reason={error} />}
      {data && !data.available && <Unavailable what="Risk analysis" reason={data.reason} />}
      {data?.available && (
        <>
          <div className="tiles">
            <div className="tile"><div className="dim">Portfolio volatility (ann.)</div><div className="tile-v">{pct(data.portfolio_volatility_pct)}</div><div className="dim">{data.start} → {data.end}</div></div>
            <div className="tile"><div className="dim">Diversification ratio</div><div className="tile-v">{data.diversification_ratio}</div><div className="dim">weighted avg vol ÷ portfolio vol</div></div>
            {data.benchmark && <div className="tile"><div className="dim">Benchmark {data.benchmark.symbol}</div><div className="tile-v">{pct(data.benchmark.volatility_pct)}</div><div className="dim">correlation {data.benchmark.correlation ?? '–'}</div></div>}
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead><tr><th>Instrument</th><th className="n">Weight</th><th className="n">Volatility</th><th className="n">Risk contribution</th><th className="n">Beta</th></tr></thead>
              <tbody>
                {data.holdings.map((h) => (
                  <tr key={h.instrument_id}>
                    <td>{h.name}</td><td className="n">{pct(h.weight_pct)}</td><td className="n">{pct(h.volatility_pct)}</td>
                    <td className="n">{pct(h.contribution_pct)}</td><td className="n">{h.beta ?? '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.warnings.map((w, i) => <div key={i} className="notice">{w}{data.excluded.length ? `: ${data.excluded.map((e) => e.name).join(', ')}` : ''}</div>)}
          <details style={{ marginTop: 8 }}>
            <summary className="dim">Correlation matrix</summary>
            <Heatmap names={data.correlation.names} matrix={data.correlation.matrix} />
          </details>
        </>
      )}
      {!data && !error && <div className="dim">Loading history…</div>}
    </section>
  )
}

function Heatmap({ names, matrix }: { names: string[]; matrix: number[][] }) {
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
    const short = names.map((n) => (n.length > 18 ? n.slice(0, 16) + '…' : n))
    const data: [number, number, number][] = []
    matrix.forEach((row, i) => row.forEach((v, j) => data.push([j, i, v])))
    chart.current.setOption({
      grid: { left: 8, right: 8, top: 8, bottom: 8, containLabel: true },
      tooltip: { formatter: (p: { data: [number, number, number] }) => `${names[p.data[1]]} × ${names[p.data[0]]}: ${p.data[2].toFixed(2)}`,
        backgroundColor: cssVar('--surface'), borderColor: cssVar('--border'), textStyle: { color: cssVar('--ink') } },
      xAxis: { type: 'category', data: short, axisLabel: { rotate: 45, color: cssVar('--muted'), fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false } },
      yAxis: { type: 'category', data: short, axisLabel: { color: cssVar('--muted'), fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false } },
      // diverging: red (-1) · neutral gray (0) · blue (+1), per the palette's diverging pair
      visualMap: { min: -1, max: 1, show: false, inRange: { color: [cssVar('--s8'), cssVar('--grid'), cssVar('--s1')] } },
      series: [{ type: 'heatmap', data, itemStyle: { borderColor: cssVar('--surface'), borderWidth: 2 }, label: { show: names.length <= 12, color: cssVar('--ink'), fontSize: 10, formatter: (p: { data: [number, number, number] }) => p.data[2].toFixed(2) } }],
    }, true)
  }, [names, matrix])
  return <div ref={ref} style={{ width: '100%', height: Math.max(260, 28 * names.length + 80) }} role="img" aria-label="Correlation matrix" />
}
