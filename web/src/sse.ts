/** Incremental server-sent-events parser. Tolerates "\n" and "\r\n" line endings and
 * events split across chunks. Feed it text; it calls back once per complete event. */
export interface SseEvent {
  event: string
  data: string
}

export class SseParser {
  private buffer = ''

  push(chunk: string, onEvent: (ev: SseEvent) => void): void {
    this.buffer += chunk
    for (;;) {
      const m = /\r?\n\r?\n/.exec(this.buffer)
      if (!m) return
      const block = this.buffer.slice(0, m.index)
      this.buffer = this.buffer.slice(m.index + m[0].length)
      const ev = parseBlock(block)
      if (ev) onEvent(ev)
    }
  }
}

function parseBlock(block: string): SseEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const raw of block.split(/\r?\n/)) {
    if (!raw || raw.startsWith(':')) continue // comment / keep-alive ping
    const i = raw.indexOf(':')
    const field = i < 0 ? raw : raw.slice(0, i)
    let value = i < 0 ? '' : raw.slice(i + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }
  if (data.length === 0) return null
  return { event, data: data.join('\n') }
}
