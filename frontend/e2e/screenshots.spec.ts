import { test, type Page } from '@playwright/test'

/**
 * Not an assertion suite: this captures the screens used in the README and the
 * demo walkthrough, so the images never drift from the running application.
 * Run with: npx playwright test screenshots
 */

const OUT = '../docs/screenshots'

test.use({ viewport: { width: 1440, height: 1000 } })

async function shoot(page: Page, name: string) {
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true })
}

test('capture the walkthrough', async ({ page }) => {
  await page.goto('/')
  await shoot(page, '01-overview')

  await page.goto('/new')
  await shoot(page, '02-new-invoice')

  const card = page.locator('article', { hasText: 'Happy path' }).first()
  await card.getByRole('button', { name: /run this sample/i }).click()
  await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/)
  await page.getByText(/completed|failed/i).first().waitFor({ timeout: 60_000 })
  await shoot(page, '03-run-approved')

  await page.goto('/new')
  const review = page.locator('article', { hasText: 'Image-only scan' }).first()
  await review.getByRole('button', { name: /run this sample/i }).click()
  await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/)
  await page.getByText(/completed|failed/i).first().waitFor({ timeout: 60_000 })
  await shoot(page, '04-run-review')

  await page.getByRole('button', { name: /make a correction/i }).click()
  await shoot(page, '05-correction')

  await page.goto('/runs')
  await shoot(page, '06-history')

  await page.goto('/reference')
  await shoot(page, '07-reference-and-policy')
})

/**
 * The layouts are inspected at the three widths the review notes call out. A
 * console error or a table wider than its viewport is a failure, not a note:
 * both are invisible in a screenshot taken at desktop width.
 */
for (const [label, width] of [
  ['1440', 1440],
  ['1024', 1024],
  ['390', 390],
] as const) {
  test(`layouts hold at ${label}px with no console errors`, async ({ page }) => {
    const errors: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text())
    })
    page.on('pageerror', (error) => errors.push(error.message))
    await page.setViewportSize({ width, height: 900 })

    for (const path of ['/', '/new', '/runs', '/reference']) {
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      // Asking the page to scroll sideways and reading back how far it moved is
      // the honest test. Comparing scrollWidth misses the case where an absolute
      // descendant escapes a scroll container, and flags wide-but-scrollable
      // tables that are working as intended.
      const overflow = await page.evaluate(() => {
        window.scrollTo(9999, 0)
        const moved = window.scrollX
        window.scrollTo(0, 0)
        return moved
      })
      if (overflow > 1) throw new Error(`${path} scrolls sideways by ${overflow}px`)
      if (width === 390) await shoot(page, `mobile-${path === '/' ? 'overview' : path.slice(1)}`)
    }

    if (errors.length > 0) throw new Error(`console errors: ${errors.join(' | ')}`)
  })
}
