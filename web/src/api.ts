import type { ChatMessage, Portfolio, Transaction, Valuation, Version } from './types'
import { SseParser, type SseEvent } from './sse'

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = (body as { error?: string; details?: unknown }).error ?? res.statusText
    throw new Error(typeof err === 'string' ? err : JSON.stringify(err))
  }
  return body as T
}

export const api = {
  portfolio: () => fetch('/api/portfolio').then(json<Portfolio>),
  valuation: (force = false) => fetch(`/api/valuation?force=${force}`).then(json<Valuation>),
  transactions: () => fetch('/api/transactions?last=200').then(json<{ transactions: Transaction[] }>),
  versions: () => fetch('/api/versions').then(json<{ head: number; versions: Version[] }>),
  addTransaction: (body: Record<string, string | undefined>) =>
    fetch('/api/transactions', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
      .then(json<{ version: number }>),
  removeTransaction: (id: string) => fetch(`/api/transactions/${id}`, { method: 'DELETE' }).then(json<{ version: number }>),
  addInstrument: (body: { isin?: string; symbol?: string }) =>
    fetch('/api/instruments', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
      .then(json<{ added: boolean; instrument?: { id: string; name: string }; candidates?: unknown[]; notes?: string[] }>),
  resolve: (query: string) => fetch(`/api/instruments/resolve?query=${encodeURIComponent(query)}`).then(json<ResolveResult>),
  revert: (v: number) => fetch(`/api/versions/${v}/revert`, { method: 'POST' }).then(json<{ version: number }>),
  undo: () => fetch('/api/undo', { method: 'POST' }).then(json<{ version: number }>),
  chatMessages: () => fetch('/api/chat/messages').then(json<{ messages: ChatMessage[]; busy: boolean; provider: string }>),
}

export interface ResolveCandidate {
  name: string
  isin: string | null
  type: string
  confidence: number
  preferred_symbol: string
  currency: string
  listings: { symbol: string; exchange: string; currency: string }[]
  notes: string[]
}

export interface ResolveResult {
  already_in_portfolio: { id: string; name: string } | null
  unique: ResolveCandidate | null
  candidates: ResolveCandidate[]
  notes: string[]
  hint: string | null
}

/** POST a chat message and stream server-sent events back. Resolves with the number of
 * events received so the caller can tell a silent stream from a normal one. */
export async function streamChat(message: string, onEvent: (ev: ChatMessage) => void): Promise<number> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify({ message }),
  })
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { error?: string }).error ?? res.statusText)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()
  let count = 0
  const handle = (ev: SseEvent) => {
    const rec = JSON.parse(ev.data) as ChatMessage
    rec.type = (rec.type ?? ev.event) as ChatMessage['type']
    count++
    onEvent(rec)
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    parser.push(decoder.decode(value, { stream: true }), handle)
  }
  parser.push(decoder.decode(), handle)
  return count
}
