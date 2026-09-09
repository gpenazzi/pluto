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
