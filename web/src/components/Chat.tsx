import { useEffect, useRef, useState } from 'react'
import { api, streamChat } from '../api'
import type { ChatMessage } from '../types'

const WRITE_TOOLS = new Set(['add_transaction', 'add_instrument', 'remove_transaction', 'revert_to_version', 'undo'])

export default function Chat({ onChanged }: { onChanged: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [provider, setProvider] = useState('')
  const [showTools, setShowTools] = useState(true)
  const log = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.chatMessages().then((r) => { setMessages(r.messages); setBusy(r.busy); setProvider(r.provider) }).catch(() => {})
  }, [])
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }) }, [messages, busy])

  async function send(e?: React.FormEvent) {
    e?.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)
    setMessages((m) => [...m, { role: 'user', text }])
    let changed = false
    try {
      const n = await streamChat(text, (ev) => {
        if (ev.type === 'done') return
        if (ev.type === 'tool_call' && WRITE_TOOLS.has(ev.name ?? '')) changed = true
        setMessages((m) => [...m, ev])
      })
      if (n === 0) {
        setMessages((m) => [...m, { role: 'assistant', type: 'error', text: 'No response from the server. Check the terminal running `pluto serve`.' }])
      }
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', type: 'error', text: String(err) }])
    } finally {
      setBusy(false)
      if (changed) onChanged()
    }
  }

  return (
    <section className="card chat">
      <h2>Chat <span className="right dim">{provider}</span>
        <label className="dim" style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
          <input type="checkbox" checked={showTools} onChange={(e) => setShowTools(e.target.checked)} /> tools
        </label>
      </h2>
      <div className="log" ref={log}>
        {messages.length === 0 && (
          <div className="dim">Tell Pluto what you did, e.g. "Bought 20 units of Vanguard All World, ISIN IE00BK5BQT80, at 166.10". It records the transaction when everything is unambiguous and asks otherwise.</div>
        )}
        {messages.map((m, i) => {
          if (m.role === 'user') return <div key={i} className="msg user">{m.text}</div>
          if (m.type === 'text') return <div key={i} className="msg assistant">{m.text}</div>
          if (m.type === 'error') return <div key={i} className="msg error">{m.text}</div>
          if (!showTools) return null
          if (m.type === 'tool_call') return <div key={i} className="msg tool">→ {m.name}({fmtArgs(m.args)})</div>
          if (m.type === 'tool_result') return <div key={i} className={'msg tool' + (m.ok ? '' : ' err')}>{m.ok ? '✓' : '✗'} {(m.text ?? '').slice(0, 140)}{(m.text ?? '').length > 140 ? '…' : ''}</div>
          return null
        })}
        {busy && <div className="thinking">Pluto is thinking…</div>}
      </div>
      <form onSubmit={send}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="Message Pluto… (Enter to send, Shift+Enter for a new line)"
          disabled={busy}
        />
        <button className="primary" type="submit" disabled={busy || !input.trim()}>Send</button>
      </form>
      <div className="hint">Runs through your Claude subscription on this machine. Say "undo" to revert the last change.</div>
    </section>
  )
}

function fmtArgs(args?: Record<string, unknown>): string {
  if (!args) return ''
  return Object.entries(args).filter(([, v]) => v !== null && v !== undefined && v !== '').map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ')
}
