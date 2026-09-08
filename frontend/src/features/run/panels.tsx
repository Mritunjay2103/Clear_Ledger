import { useState } from 'react'
import clsx from 'clsx'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleDashed,
  CircleSlash,
  Quote,
  X,
} from 'lucide-react'
import { Card, DecisionBadge, QualityChip } from '../../components/ui'
import { formatDuration, formatStamp } from '../../lib/format'
import type {
  DecisionPayload,
  Evidence,
  ExtractedInvoice,
  ExtractionMetadata,
  FieldQuality,
  RuleResult,
  StageView,
  WorkflowEventView,
} from '../../types/api'

export function StageTimeline({ stages, live }: { stages: StageView[]; live: boolean }) {
  return (
    <Card
      title="Processing stages"
      description={live ? 'Updating as the run progresses.' : 'Recorded during the run.'}
    >
      <ol className="space-y-1">
        {stages.map((stage) => (
          <li key={stage.stage_key} className="flex gap-3 rounded-md px-1.5 py-1.5">
            <StageIcon status={stage.status} />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <p
                  className={clsx(
                    'text-sm',
                    stage.status === 'pending' ? 'text-muted' : 'font-medium text-ink',
                  )}
                >
                  {stage.stage_label}
                </p>
                {stage.elapsed_ms !== null && (
                  <span className="tabular text-[11px] text-muted">
                    {formatDuration(stage.elapsed_ms)}
                  </span>
                )}
              </div>
              {stage.messages.map((message) => (
                <p key={message} className="mt-0.5 text-xs text-muted">
                  {message}
                </p>
              ))}
              {stage.warnings.map((warning) => (
                <p key={warning} className="mt-0.5 flex gap-1.5 text-xs text-review">
                  <AlertTriangle aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
                  {warning}
                </p>
              ))}
              {stage.errors.map((error) => (
                <p key={error} className="mt-0.5 text-xs text-blocked">
                  {error}
                </p>
              ))}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  )
}

function StageIcon({ status }: { status: StageView['status'] }) {
  const base = 'mt-0.5 grid size-4 shrink-0 place-items-center rounded-full'
  if (status === 'completed')
    return (
      <span className={clsx(base, 'bg-approved text-white')} aria-label="completed">
        <Check aria-hidden="true" className="size-3" strokeWidth={3} />
      </span>
    )
  if (status === 'failed')
    return (
      <span className={clsx(base, 'bg-blocked text-white')} aria-label="failed">
        <X aria-hidden="true" className="size-3" strokeWidth={3} />
      </span>
    )
  if (status === 'running')
    return (
      <span className={clsx(base, 'text-brand')} aria-label="running">
        <span className="size-2.5 animate-pulse rounded-full bg-brand" />
      </span>
    )
  if (status === 'skipped')
    return (
      <span className={clsx(base, 'text-muted')} aria-label="skipped">
        <CircleSlash aria-hidden="true" className="size-3.5" />
      </span>
    )
  return (
    <span className={clsx(base, 'text-line')} aria-label="pending">
      <CircleDashed aria-hidden="true" className="size-3.5" />
    </span>
  )
}

export function DecisionPanel({
  payload,
  ruleResults,
}: {
  payload: DecisionPayload
  ruleResults: RuleResult[]
}) {
  const outcome = payload.outcome
  if (!outcome) return null
  const driving = ruleResults.filter(
    (rule) => rule.status === 'fail' || rule.status === 'review',
  )
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <DecisionBadge decision={outcome.decision} size="lg" />
          <h2 className="mt-2 text-lg leading-snug font-semibold text-ink">{outcome.headline}</h2>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted">
            {outcome.explanation}
          </p>
        </div>
      </div>

      {outcome.primary_reasons.length > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">Why</h3>
          <ul className="mt-1.5 space-y-1">
            {outcome.primary_reasons.map((reason) => (
              <li key={reason} className="flex gap-2 text-sm text-ink">
                <span aria-hidden="true" className="mt-2 size-1 shrink-0 rounded-full bg-muted" />
                {reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {outcome.next_actions.length > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">
            What to do next
          </h3>
          <ul className="mt-1.5 space-y-1">
            {outcome.next_actions.map((action) => (
              <li key={action} className="flex gap-2 text-sm text-ink">
                <span aria-hidden="true" className="mt-2 size-1 shrink-0 rounded-full bg-muted" />
                {action}
              </li>
            ))}
          </ul>
        </div>
      )}

      {driving.length > 0 && (
        <p className="mt-4 border-t border-line pt-3 text-xs text-muted">
          Driven by {driving.length} rule {driving.length === 1 ? 'result' : 'results'}:{' '}
          <span className="tabular">{driving.map((rule) => rule.code).join(', ')}</span>
        </p>
      )}
    </Card>
  )
}

const FIELD_LABELS: Record<string, string> = {
  vendor_name: 'Vendor name',
  invoice_number: 'Invoice number',
  invoice_date: 'Invoice date',
  explicit_po_number: 'PO number',
  currency: 'Currency',
  subtotal: 'Subtotal',
  tax_total: 'Tax',
  gross_total: 'Gross total',
  document_kind: 'Document kind',
}

export function FieldsPanel({
  invoice,
  metadata,
  quality,
  overrides,
}: {
  invoice: ExtractedInvoice | undefined
  metadata: ExtractionMetadata | undefined
  quality: FieldQuality[]
  overrides: Record<string, unknown> | null
}) {
  if (!invoice) {
    return (
      <Card title="Extracted fields">
        <p className="text-sm text-muted">Nothing was extracted from this document.</p>
      </Card>
    )
  }
  const qualityByField = new Map(quality.map((entry) => [entry.field, entry]))
  const order = [
    'vendor_name',
    'invoice_number',
    'invoice_date',
    'explicit_po_number',
    'currency',
    'subtotal',
    'tax_total',
    'gross_total',
  ] as const

  return (
    <Card
      title="Extracted fields"
      description={
        metadata
          ? `${metadata.provider}${metadata.model ? ` · ${metadata.model}` : ''} · ${formatDuration(metadata.duration_ms)}`
          : undefined
      }
    >
      <dl className="divide-y divide-line">
        {order.map((field) => {
          const value = invoice[field] as string | null
          const entry = qualityByField.get(field)
          const overridden = Boolean(overrides && field in overrides)
          return (
            <div key={field} className="grid gap-1 py-2 sm:grid-cols-[140px_minmax(0,1fr)]">
              <dt className="text-xs text-muted">{FIELD_LABELS[field] ?? field}</dt>
              <dd className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={clsx(
                      'text-sm',
                      value ? 'font-medium text-ink' : 'text-muted italic',
                    )}
                  >
                    {value ?? 'not found'}
                  </span>
                  {entry && <QualityChip label={entry.label} />}
                  {overridden && (
                    <span className="rounded border border-brand/30 bg-brand-soft px-1.5 py-0.5 text-[10px] font-medium text-brand">
                      corrected by reviewer
                    </span>
                  )}
                </div>
                {entry?.evidence ? (
                  <EvidenceQuote evidence={entry.evidence} />
                ) : (
                  entry?.reason && <p className="mt-0.5 text-[11px] text-muted">{entry.reason}</p>
                )}
              </dd>
            </div>
          )
        })}
      </dl>

      {invoice.line_items.length > 0 && (
        <Disclosure label={`Line items (${invoice.line_items.length})`}>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted">
                <th className="py-1 font-medium">Description</th>
                <th className="py-1 text-right font-medium">Qty</th>
                <th className="py-1 text-right font-medium">Unit</th>
                <th className="py-1 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody className="tabular divide-y divide-line">
              {invoice.line_items.map((item, index) => (
                <tr key={`${item.description}-${index}`}>
                  <td className="max-w-0 truncate py-1 pr-2 text-ink">
                    {item.description ?? '—'}
                  </td>
                  <td className="py-1 text-right text-muted">{item.quantity ?? '—'}</td>
                  <td className="py-1 text-right text-muted">{item.unit_price ?? '—'}</td>
                  <td className="py-1 text-right font-medium text-ink">
                    {item.net_amount ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!invoice.line_items_complete && (
            <p className="mt-2 text-[11px] text-muted">
              The line item list is incomplete, so it was not used to check the printed total.
            </p>
          )}
        </Disclosure>
      )}

      {(invoice.extraction_warnings.length > 0 || invoice.ambiguities.length > 0) && (
        <ul className="mt-3 space-y-1 border-t border-line pt-3">
          {[...invoice.extraction_warnings, ...invoice.ambiguities].map((note) => (
            <li key={note} className="flex gap-1.5 text-xs text-review">
              <AlertTriangle aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
              {note}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export function EvidenceQuote({ evidence }: { evidence: Evidence }) {
  return (
    <p className="mt-1 flex gap-1.5 rounded border border-line bg-canvas px-2 py-1 text-[11px] leading-relaxed text-muted">
      <Quote aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
      <span className="min-w-0">
        <span className="text-ink">“{evidence.excerpt}”</span>
        <span className="ml-1 whitespace-nowrap">
          — page {evidence.page},{' '}
          {evidence.provenance === 'ocr'
            ? 'read by OCR'
            : evidence.provenance === 'human_correction'
              ? 'entered by a reviewer'
              : 'PDF text layer'}
        </span>
      </span>
    </p>
  )
}

export function ChecksPanel({ results }: { results: RuleResult[] }) {
  if (results.length === 0) return null
  const grouped: [string, RuleResult[]][] = [
    ['Needs attention', results.filter((r) => r.status === 'fail' || r.status === 'review')],
    ['Passed', results.filter((r) => r.status === 'pass')],
    ['Not applicable', results.filter((r) => r.status === 'skipped')],
  ]
  return (
    <Card title="Policy checks" description="Deterministic rules; no model is involved here.">
      <div className="space-y-4">
        {grouped.map(([label, rules]) =>
          rules.length === 0 ? null : (
            <section key={label}>
              <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">
                {label} ({rules.length})
              </h3>
              <ul className="mt-1.5 space-y-1.5">
                {rules.map((rule) => (
                  <li
                    key={rule.code}
                    className={clsx(
                      'rounded-md border px-2.5 py-2',
                      rule.status === 'fail'
                        ? 'border-blocked/25 bg-blocked-soft'
                        : rule.status === 'review'
                          ? 'border-review/25 bg-review-soft'
                          : 'border-line bg-canvas',
                    )}
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                      <p className="text-sm text-ink">{rule.description}</p>
                      <code className="tabular text-[10px] text-muted">{rule.code}</code>
                    </div>
                    {(rule.observed_value || rule.expected_value) && (
                      <p className="tabular mt-1 text-[11px] text-muted">
                        {rule.observed_value && <>observed: {rule.observed_value}</>}
                        {rule.observed_value && rule.expected_value && ' · '}
                        {rule.expected_value && <>expected: {rule.expected_value}</>}
                      </p>
                    )}
                    {rule.next_action && (
                      <p className="mt-1 text-[11px] text-muted">{rule.next_action}</p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ),
        )}
      </div>
    </Card>
  )
}

export function ReferencePanel({ payload }: { payload: DecisionPayload }) {
  const vendor = payload.vendor_resolution
  const po = payload.po_resolution
  const comparison = payload.po_comparison
  if (!vendor && !po) return null

  return (
    <Card title="Reference matching" description="How the invoice was tied to known records.">
      <div className="space-y-4">
        {vendor && (
          <section>
            <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">Vendor</h3>
            <p className="mt-1 text-sm text-ink">
              {vendor.vendor ? vendor.vendor.canonical_name : 'Not resolved'}
              {vendor.matched_on && (
                <span className="text-muted"> · matched on {vendor.matched_on}</span>
              )}
            </p>
            <p className="mt-0.5 text-xs text-muted">{vendor.reason}</p>
            {vendor.candidates.length > 0 && (
              <ul className="mt-1.5 space-y-0.5">
                {vendor.candidates.map((candidate) => (
                  <li key={candidate.vendor_id} className="tabular text-[11px] text-muted">
                    {candidate.canonical_name} — {Math.round(candidate.similarity * 100)}% similar (
                    {candidate.matched_on}, {candidate.status})
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {po && (
          <section className="border-t border-line pt-3">
            <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">
              Purchase order
            </h3>
            <p className="mt-1 text-sm text-ink">
              {po.purchase_order ? po.purchase_order.po_number : 'Not resolved'}
              <span className="text-muted"> · {po.source.replaceAll('_', ' ')}</span>
            </p>
            <p className="mt-0.5 text-xs text-muted">{po.reason}</p>
            {po.candidates.length > 0 && (
              <ul className="mt-1.5 space-y-0.5">
                {po.candidates.map((candidate) => (
                  <li key={candidate.po_id} className="tabular text-[11px] text-muted">
                    {candidate.po_number} — {candidate.rationale}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {comparison && comparison.po_number && (
          <section className="border-t border-line pt-3">
            <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">
              Budget arithmetic
            </h3>
            <dl className="tabular mt-1.5 space-y-1 text-xs">
              <Row label="Approved on the PO" value={comparison.approved_total_display} />
              <Row
                label="Already committed by other approvals"
                value={comparison.committed_before_display}
              />
              <Row label="This invoice" value={comparison.invoice_gross_display} />
              <Row label="Projected total" value={comparison.projected_total_display} strong />
              <Row
                label={`Allowed including tolerance (${comparison.tolerance_display ?? '—'})`}
                value={comparison.allowed_total_display}
              />
              <Row
                label="Remaining after this invoice"
                value={comparison.remaining_nominal_after_display}
              />
            </dl>
            <p className="mt-2 text-xs text-muted">
              {comparison.reserved
                ? 'This amount is now committed against the purchase order.'
                : 'No amount was committed against the purchase order for this run.'}
              {comparison.within_tolerance_overage &&
                ' The overage sits inside the tolerance band.'}
            </p>
          </section>
        )}
      </div>
    </Card>
  )
}

function Row({
  label,
  value,
  strong,
}: {
  label: string
  value: string | null | undefined
  strong?: boolean
}) {
  if (!value) return null
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className={clsx(strong ? 'font-semibold text-ink' : 'text-ink')}>{value}</dd>
    </div>
  )
}

export function EventLog({ events }: { events: WorkflowEventView[] }) {
  return (
    <Disclosure label={`Event log (${events.length} entries)`} card>
      <ol className="tabular space-y-1 text-[11px]">
        {events.map((event) => (
          <li key={event.sequence} className="flex gap-2">
            <span className="w-16 shrink-0 text-muted">{formatStamp(event.timestamp)}</span>
            <span className="w-10 shrink-0 text-muted">#{event.sequence}</span>
            <span
              className={clsx(
                'w-20 shrink-0',
                event.event_type === 'failed'
                  ? 'text-blocked'
                  : event.event_type === 'warning'
                    ? 'text-review'
                    : 'text-muted',
              )}
            >
              {event.event_type}
            </span>
            <span className="min-w-0 text-ink">
              <span className="text-muted">{event.stage_label}: </span>
              {event.short_message}
            </span>
          </li>
        ))}
      </ol>
    </Disclosure>
  )
}

export function Disclosure({
  label,
  children,
  card,
  defaultOpen,
}: {
  label: string
  children: React.ReactNode
  card?: boolean
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(Boolean(defaultOpen))
  const body = (
    <>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 text-left text-xs font-medium text-muted hover:text-ink"
      >
        <ChevronDown
          aria-hidden="true"
          className={clsx('size-3.5 transition-transform', open && 'rotate-180')}
        />
        {label}
      </button>
      {open && <div className="mt-3">{children}</div>}
    </>
  )
  if (card) return <Card>{body}</Card>
  return <div className="mt-4 border-t border-line pt-3">{body}</div>
}
