import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { Slice } from '../types'
import { money, pct } from '../format'
import { cssVar } from './slices'

echarts.use([PieChart, TooltipComponent, SVGRenderer])

export default function Donut({ slices, colors, currency }: { slices: Slice[]; colors: Map<string, string>; currency: string }) {
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
    const surface = cssVar('--surface')
    chart.current.setOption({
      tooltip: {
        trigger: 'item',
        backgroundColor: surface, borderColor: cssVar('--border'), textStyle: { color: cssVar('--ink') },
        formatter: (p: { name: string; value: number; percent: number }) =>
          `<b>${p.name}</b><br/>${money(p.value, currency)} · ${pct(p.percent)}`,
      },
      series: [{
        type: 'pie',
        radius: ['62%', '92%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: surface, borderWidth: 2, borderRadius: 3 },
        // No outside labels: the legend beside the chart carries every value and weight,
        // and labels in a 340px column collide and clip.
        label: { show: false },
        labelLine: { show: false },
        emphasis: { scale: false, itemStyle: { borderWidth: 3 } },
        data: slices.map((s) => ({ name: s.label, value: parseFloat(s.value), itemStyle: { color: colors.get(s.label) } })),
      }],
    }, true)
  }, [slices, colors, currency])

  return <div className="donut" ref={ref} role="img" aria-label="Allocation donut chart" />
}

