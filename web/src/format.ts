export function money(v: string | number | null | undefined, currency: string, digits = 2): string {
  if (v === null || v === undefined || v === '') return '–'
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '–'
  return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n)
}

export function num(v: string | number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || v === '') return '–'
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '–'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n)
}

export function pct(v: string | number | null | undefined, digits = 1, sign = false): string {
  if (v === null || v === undefined || v === '') return '–'
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '–'
  const s = new Intl.NumberFormat(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n)
  return (sign && n > 0 ? '+' : '') + s + '%'
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  const mins = Math.round((Date.now() - t) / 60000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m ago`
  const h = Math.round(mins / 60)
  if (h < 36) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

export function today(): string {
  return new Date().toISOString().slice(0, 10)
}
