import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { FileUp, Inbox, Sparkles } from 'lucide-react'
import { api } from '../../lib/api'
import { Button, Card, DecisionBadge, EmptyState, ErrorState, ExecutionBadge, Spinner } from '../../components/ui'
import { formatDuration, formatMoney, formatRelative } from '../../lib/format'
import type { Metric, RunSummary } from '../../types/api'

const HEADLINE_METRICS = [
  ['cases_processed', 'Cases processed'],
  ['cases_approved', 'Approved'],
  ['cases_needing_review', 'Needs review'],
  ['cases_blocked', 'Blocked'],
  ['failed_attempts', 'Failed attempts'],
  ['median_processing_ms', 'Median run time'],
] as const

export function OverviewPage() {
  const navigate = useNavigate()
  const dashboard = useQuery({
    queryKey: ['dashboard'],
    queryFn: api.dashboard,
    refetchInterval: 8000,
  })

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Overview</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            Turn a vendor invoice PDF into an explainable decision against purchase-order
            reference data. Every figure below comes from stored runs.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="primary" onClick={() => navigate('/new')}>
            <FileUp aria-hidden="true" className="size-4" />
            Process invoice
          </Button>
          <Button onClick={() => navigate('/new#samples')}>
            <Sparkles aria-hidden="true" className="size-4" />
            Try a sample
          </Button>
        </div>
      </header>

      {dashboard.isLoading && <Spinner label="Loading metrics" />}
      {dashboard.isError && (
        <ErrorState
          message="The dashboard could not be loaded."
          onRetry={() => dashboard.refetch()}
        />
      )}

      {dashboard.data && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
            {HEADLINE_METRICS.map(([key, label]) => (
              <MetricTile
                key={key}
                label={label}
                metric={dashboard.data.metrics[key]}
                isDuration={key === 'median_processing_ms'}
              />
            ))}
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card
              title="Automation rate"
              description="Stated with its denominator so it cannot be read as a general accuracy claim."
              className="xl:col-span-1"
            >
              <AutomationRate metric={dashboard.data.metrics.auto_approval_rate} />
              <dl className="mt-4 space-y-2 border-t border-line pt-3 text-xs">
                <div className="flex justify-between gap-3">
                  <dt className="text-muted">Reserved commitments</dt>
                  <dd className="tabular font-medium">
                    {String(dashboard.data.metrics.reserved_commitments.value)}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted">Total run attempts</dt>
                  <dd className="tabular font-medium">
                    {String(dashboard.data.metrics.total_runs.value)}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted">Cases with a human correction</dt>
                  <dd className="tabular font-medium">
                    {dashboard.data.cases_with_human_correction}
                  </dd>
                </div>
              </dl>
              <p className="mt-3 text-[11px] text-muted">
                A reserved commitment holds budget against a purchase order. This prototype never
                initiates a payment.
              </p>
            </Card>

            <Card
              title="Recent runs"
              description="Newest first, including retries and corrections."
              className="xl:col-span-2"
              actions={
                <Link to="/runs" className="text-xs font-medium text-brand hover:underline">
                  View all history
                </Link>
              }
            >
              {dashboard.data.recent_runs.length === 0 ? (
                <EmptyState
                  icon={Inbox}
                  title="No invoices processed yet"
                  description="Upload a PDF or run one of the bundled samples to see a decision with its evidence."
                  action={
                    <Button variant="primary" size="sm" onClick={() => navigate('/new')}>
                      Process the first invoice
                    </Button>
                  }
                />
              ) : (
                <RecentRunsTable runs={dashboard.data.recent_runs} />
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  )
}

function MetricTile({
  label,
  metric,
  isDuration,
}: {
  label: string
  metric: Metric | undefined
  isDuration?: boolean
}) {
  const raw = metric?.value
  const display =
    raw === null || raw === undefined
      ? '—'
      : isDuration
        ? formatDuration(Number(raw))
        : String(raw)
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-3">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="tabular mt-1 text-2xl font-semibold text-ink">{display}</p>
      <p className="mt-1 text-[11px] leading-snug text-muted">{metric?.definition}</p>
    </div>
  )
}

function AutomationRate({ metric }: { metric: Metric | undefined }) {
  const value = metric?.value
  const percentage = typeof value === 'number' ? Math.round(value * 1000) / 10 : null
  return (
    <div>
      <p className="tabular text-3xl font-semibold text-ink">
        {percentage === null ? '—' : `${percentage}%`}
      </p>
      <p className="mt-1 text-xs text-muted">
        {metric?.numerator ?? 0} of {metric?.denominator ?? 0} decided cases were approved with no
        human correction.
      </p>
      {percentage === null && (
        <p className="mt-2 text-[11px] text-muted">
          No case has reached a decision yet, so there is nothing to divide by.
        </p>
      )}
    </div>
  )
}

/**
 * The scroll wrapper is `relative` deliberately: a statically positioned scroll
 * container does not clip absolutely positioned descendants, so the sr-only
 * label in the last column escapes it and makes the whole page scroll sideways
 * on a narrow screen.
 */
function RecentRunsTable({ runs }: { runs: RunSummary[] }) {
  return (
    <div className="relative -mx-4 overflow-x-auto px-4">
      <table className="w-full min-w-[720px] text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th scope="col" className="py-2 pr-3 font-medium">Invoice</th>
            <th scope="col" className="py-2 pr-3 font-medium">Vendor</th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">Amount</th>
            <th scope="col" className="py-2 pr-3 font-medium">Decision</th>
            <th scope="col" className="py-2 pr-3 font-medium">Execution</th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">Duration</th>
            <th scope="col" className="py-2 pr-3 font-medium">Created</th>
            <th scope="col" className="py-2 font-medium"><span className="sr-only">Open</span></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id} className="border-b border-line/70 last:border-0">
              <td className="py-2.5 pr-3 font-medium">
                {run.invoice_number ?? <span className="text-muted">Not extracted</span>}
              </td>
              <td className="py-2.5 pr-3 text-muted">{run.vendor_display_name ?? '—'}</td>
              <td className="tabular py-2.5 pr-3 text-right">{formatMoney(run.gross_total)}</td>
              <td className="py-2.5 pr-3"><DecisionBadge decision={run.decision} /></td>
              <td className="py-2.5 pr-3"><ExecutionBadge status={run.execution_status} /></td>
              <td className="tabular py-2.5 pr-3 text-right text-muted">
                {formatDuration(run.processing_duration_ms)}
              </td>
              <td className="py-2.5 pr-3 text-muted">{formatRelative(run.created_at)}</td>
              <td className="py-2.5 text-right">
                <Link
                  to={`/runs/${run.id}`}
                  className="text-xs font-medium text-brand hover:underline"
                >
                  Open
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
