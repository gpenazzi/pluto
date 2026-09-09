// Screenshot + smoke check of a running Pluto server. Usage:
//   node scripts/screenshot.mjs [url] [outDir]
// Needs `npx playwright install chromium` once.
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const url = process.argv[2] ?? 'http://127.0.0.1:8321/'
const out = process.argv[3] ?? 'test-results'
mkdirSync(out, { recursive: true })

const browser = await chromium.launch()
let failures = 0
for (const scheme of ['light', 'dark']) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1100 }, colorScheme: scheme })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  await page.goto(url, { waitUntil: 'networkidle' })
  await page.waitForSelector('.hero', { timeout: 30000 })
  await page.waitForSelector('.donut svg path', { timeout: 30000 })
  const hero = await page.textContent('.hero')
  const legend = await page.$$eval('.legend li', (els) => els.length)
  const rows = await page.$$eval('table tbody tr', (els) => els.length)
  const stale = await page.$$eval('.tag.warn', (els) => els.length)
  const missing = await page.$$eval('.tag.bad', (els) => els.length)
  await page.screenshot({ path: `${out}/ui-${scheme}.png`, fullPage: true })
  console.log(`${scheme}: hero=${hero} legend=${legend} rows=${rows} stale=${stale} missing=${missing} errors=${errors.length}`)
  for (const e of errors) console.log('  console/page error:', e)
  if (errors.length || legend === 0 || !hero) failures++
  await page.close()
}
await browser.close()
process.exit(failures ? 1 : 0)
