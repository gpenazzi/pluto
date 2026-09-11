import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SseParser, type SseEvent } from '../src/sse.ts'

function collect(chunks: string[]): SseEvent[] {
  const out: SseEvent[] = []
  const p = new SseParser()
  for (const c of chunks) p.push(c, (e) => out.push(e))
  return out
}

test('parses LF framing', () => {
  const evs = collect(['event: text\ndata: {"a":1}\n\nevent: done\ndata: {}\n\n'])
  assert.deepEqual(evs, [{ event: 'text', data: '{"a":1}' }, { event: 'done', data: '{}' }])
})

test('parses CRLF framing (sse-starlette default)', () => {
  const evs = collect(['event: text\r\ndata: {"a":1}\r\n\r\n'])
  assert.deepEqual(evs, [{ event: 'text', data: '{"a":1}' }])
})

test('handles events split across chunks and ignores pings', () => {
  const evs = collect([': ping\n\nevent: te', 'xt\ndata: {"a"', ':1}\n', '\nevent: done\ndata: {}', '\n\n'])
  assert.deepEqual(evs, [{ event: 'text', data: '{"a":1}' }, { event: 'done', data: '{}' }])
})

test('joins multi-line data', () => {
  const evs = collect(['data: a\ndata: b\n\n'])
  assert.deepEqual(evs, [{ event: 'message', data: 'a\nb' }])
})
