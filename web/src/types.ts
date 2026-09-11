export interface Instrument {
  id: string
  name: string
  isin: string | null
  type: 'etf' | 'stock'
  asset_class: string
  currency: string
  symbols: string[]
}

export interface Position {
  instrument_id: string
  name: string
  quantity: string
  avg_cost: string
  cost_basis: string
  currency: string
  realized_pnl: string
}

export interface Portfolio {
  name: string
  base_currency: string
  version: number
  instruments: Instrument[]
  positions: Position[]
  cash: Record<string, string>
  transactions_count: number
}

export interface ValuedPosition {
  instrument_id: string
  name: string
  quantity: string
  price: string | null
  price_currency: string | null
  quote_source: string | null
  quote_time: string | null
  stale: boolean | null
  day_change_pct: string | null
  market_value: string | null
  weight_pct: string
  unrealized_pnl: string | null
  unrealized_pnl_pct: string | null
}

export interface Slice {
  label: string
  value: string
  weight_pct: string
}

export type BreakdownKey = 'instrument' | 'asset_type' | 'asset_class' | 'currency'

export interface Valuation {
  as_of: string
  base_currency: string
  total_value: string
  cash_value: string
  positions: ValuedPosition[]
  breakdown: Record<'asset_type' | 'asset_class' | 'currency', Slice[]>
  missing_prices: string[]
  stale_prices: string[]
  cash_tracked: boolean
  warnings: string[]
  providers: { name: string; failures: number; calls: number; last_error: string | null; in_cooldown: boolean }[]
}

export interface Transaction {
  id: string
  date: string
  type: string
  instrument_id: string | null
  instrument: string | null
  quantity: string | null
  price: string | null
  amount: string | null
  currency: string
  fees: string
  note: string | null
  source: string
}

export interface Version {
  version: number
  created_at: string
  message: string
  transactions: number
  reverted_from: number | null
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  type?: 'text' | 'tool_call' | 'tool_result' | 'done' | 'error'
  text: string
  name?: string
  args?: Record<string, unknown>
  ok?: boolean
}

export interface PortfolioSummary {
  name: string
  base_currency: string
  version: number
  instruments: number
  transactions: number
}

export interface PerfMetrics {
  start: string
  end: string
  start_value: string
  end_value: string
  net_flows: string
  gain: string
  twr_pct: string | null
  twr_annualized_pct: string | null
  mwr_annualized_pct: string | null
  volatility_pct: string | null
  max_drawdown_pct: string | null
  drawdown_from: string | null
  drawdown_to: string | null
  series: [string, number, number | null][]
  note?: string
  days?: number
  contributions?: { instrument_id: string; name: string; gain: string; weight_pct: string }[]
  excluded?: { instrument_id: string; name: string; history_from: string | null; weight_pct: string | null }[]
  missing?: string[]
}

export interface Performance {
  period: string
  base_currency: string
  actual: PerfMetrics
  composition: PerfMetrics
  benchmark: { symbol: string; currency: string; twr: string | null } | null
  missing_history: string[]
}

export interface ExposureRow { label: string; weight_pct: number }
export interface ExposureData {
  available: boolean
  reason?: string
  covered_pct: number
  by_region: ExposureRow[]
  by_country: ExposureRow[]
  by_sector: ExposureRow[]
  unknown: { instrument_id: string; name: string; weight_pct: number; reason: string }[]
  notes: string[]
  instruments: { instrument_id: string; name: string; weight_pct: number; source: string | null; as_of: string | null; countries: Record<string, number>; sectors: Record<string, number>; note: string | null }[]
}

export interface RiskData {
  available: boolean
  reason?: string
  start: string
  end: string
  days: number
  portfolio_volatility_pct: string
  diversification_ratio: string
  benchmark: { symbol: string; volatility_pct: string | null; correlation: string | null } | null
  holdings: { instrument_id: string; name: string; weight_pct: string; volatility_pct: string; contribution_pct: string; beta: string | null }[]
  correlation: { ids: string[]; names: string[]; matrix: number[][] }
  excluded: { instrument_id: string; name: string }[]
  warnings: string[]
}
