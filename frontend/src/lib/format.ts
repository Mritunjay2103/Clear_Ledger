import type { Decision, ExecutionStatus, Money, QualityLabel } from '../types/api'

/** Amounts arrive as exact decimal strings; only grouping is added here. */
export function formatMoney(money: Money | null | undefined): string {
  if (!money) return '—'
  return `${money.currency} ${groupDecimalString(money.amount)}`
}

export function formatAmountString(amount: string | null | undefined, currency = 'INR'): string {
  if (!amount) return '—'
  return `${currency} ${groupDecimalString(amount)}`
}

export function groupDecimalString(value: string): string {
  const negative = value.startsWith('-')
  const bare = negative ? value.slice(1) : value
  const [whole, fraction = '00'] = bare.split('.')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '-' : ''}${grouped}.${fraction}`
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return `${Math.floor(seconds / 86400)} d ago`
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes && bytes !== 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

export const DECISION_LABEL: Record<Decision, string> = {
  APPROVED: 'Approved',
  REVIEW: 'Needs review',
  BLOCKED: 'Blocked',
}

export const EXECUTION_LABEL: Record<ExecutionStatus, string> = {
  QUEUED: 'Queued',
  RUNNING: 'Running',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
  INTERRUPTED: 'Interrupted',
}

export const FIELD_LABEL: Record<string, string> = {
  vendor_name: 'Vendor name',
  vendor_reference: 'Vendor GSTIN',
  invoice_number: 'Invoice number',
  invoice_date: 'Invoice date',
  explicit_po_number: 'Purchase order printed on invoice',
  currency: 'Currency',
  subtotal: 'Subtotal',
  tax_total: 'Tax',
  gross_total: 'Invoice total',
  document_kind: 'Document type',
}

export const PROVENANCE_LABEL: Record<string, string> = {
  pdf_text: 'PDF text layer',
  ocr: 'OCR',
  human_correction: 'Supplied by a reviewer',
}

export const QUALITY_LABEL: Record<QualityLabel, string> = {
  verified: 'verified',
  uncertain: 'uncertain',
  missing: 'not found',
  human_supplied: 'reviewer supplied',
}

export const QUALITY_DEFINITION: Record<QualityLabel, string> = {
  verified:
    'The quoted excerpt was found in the page text, so the value came from the document itself.',
  uncertain:
    'A value was produced but its quoted excerpt could not be confirmed in the page text. Check it before relying on it.',
  missing: 'No value was found in the document for this field.',
  human_supplied: 'A reviewer entered this value; it did not come from the document.',
}

/** Short clock stamp for the dense event log. */
export function formatStamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString(undefined, {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
