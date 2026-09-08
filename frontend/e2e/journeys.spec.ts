import { expect, test, type Page } from '@playwright/test'

/**
 * These cover the journeys a reviewer actually performs. They assume a server
 * with the synthetic samples mounted; they do not assume an empty database, so
 * they read the decision the run produced rather than asserting one outcome.
 */

async function runSample(page: Page, sampleTitle: string) {
  await page.goto('/new')
  const card = page.locator('article', { hasText: sampleTitle }).first()
  await card.getByRole('button', { name: /run this sample/i }).click()
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/)
  await expect(page.getByText(/completed|failed|interrupted/i).first()).toBeVisible({
    timeout: 60_000,
  })
}

test('the overview explains the system before any run exists', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByText(/synthetic/i).first()).toBeVisible()
})

const HAPPY_PATH = 'Happy path'
const OVER_TOLERANCE = 'breaks the tolerance'
const SCANNED = 'Image-only scan'

test('a sample runs end to end and every stage is recorded', async ({ page }) => {
  await runSample(page, HAPPY_PATH)

  const stages = page.getByRole('heading', { name: 'Processing stages' })
  await expect(stages).toBeVisible()
  for (const label of [
    'Intake',
    'Read document',
    'Extract fields',
    'Validate facts',
    'Match references',
    'Evaluate policy',
    'Commit decision',
    'Publish output',
  ]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible()
  }

  await expect(page.getByRole('heading', { name: 'Extracted fields' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Policy checks' })).toBeVisible()
})

test('every extracted value shows where it came from', async ({ page }) => {
  await runSample(page, HAPPY_PATH)
  await expect(page.getByText(/page \d+, (PDF text layer|read by OCR)/).first()).toBeVisible()
})

test('a decision states its reasons and the next action', async ({ page }) => {
  await runSample(page, OVER_TOLERANCE)
  await expect(page.getByRole('heading', { name: 'Why' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'What to do next' })).toBeVisible()
})

test('exports are downloadable from the run page', async ({ page }) => {
  await runSample(page, HAPPY_PATH)
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: 'JSON' }).click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/\.json$/)
})

test('a reload shows the same decision, because it was persisted', async ({ page }) => {
  await runSample(page, HAPPY_PATH)
  const url = page.url()
  const decision = await page.getByTestId('decision-label').first().innerText()

  await page.reload()

  await expect(page).toHaveURL(url)
  await expect(page.getByTestId('decision-label').first()).toHaveText(decision)
})

test('a correction creates a linked run and leaves the original alone', async ({ page }) => {
  await runSample(page, SCANNED)
  const originalUrl = page.url()
  const originalDecision = await page.getByTestId('decision-label').first().innerText()

  await page.getByRole('button', { name: /make a correction/i }).click()
  await page.getByLabel('Your name or initials').fill('A. Reviewer')
  await page.getByLabel(/why are you changing this/i).fill('Confirmed the purchase order with procurement')
  await page.getByLabel(/confirm the purchase order/i).selectOption({ index: 1 })
  await page.getByRole('button', { name: /save and reprocess/i }).click()

  // The correction navigates to a new run rather than editing this one.
  await expect(page).not.toHaveURL(originalUrl)
  await expect(page.getByText(/completed|failed/i).first()).toBeVisible({ timeout: 60_000 })

  // Both attempts are listed, and the reviewer's reason is on the record.
  await expect(page.getByRole('heading', { name: 'Case history' })).toBeVisible()
  const originalId = originalUrl.split('/').pop() as string
  await expect(page.locator(`a[href="/runs/${originalId}"]`)).toBeVisible()
  await expect(page.getByText('Confirmed the purchase order with procurement')).toBeVisible()

  await page.goto(originalUrl)
  await expect(page.getByTestId('decision-label').first()).toHaveText(originalDecision)
})

test('run history filters and links back to a run', async ({ page }) => {
  await page.goto('/runs')
  await expect(page.getByRole('heading', { name: 'Run history' })).toBeVisible()
  await page.getByLabel('Decision').selectOption('REVIEW')
  const first = page.locator('tbody tr').first()
  if (await first.isVisible()) {
    await first.getByRole('link').click()
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/)
  }
})

test('reference data and the policy are readable without running anything', async ({ page }) => {
  await page.goto('/reference')
  await expect(page.getByRole('heading', { name: 'Purchase orders' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Vendors' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Decision policy' })).toBeVisible()
  await expect(
    page.getByRole('heading', { name: /deliberately does not do/i }),
  ).toBeVisible()
})

test('a non-PDF upload is refused with a readable message', async ({ page }) => {
  await page.goto('/new')
  await page.setInputFiles('#invoice-file', {
    name: 'not-an-invoice.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('this is not a pdf'),
  })
  await page.getByRole('button', { name: /process invoice/i }).click()
  await expect(page.getByRole('alert')).toContainText(/pdf/i)
})
