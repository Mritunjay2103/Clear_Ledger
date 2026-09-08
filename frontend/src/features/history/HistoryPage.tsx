import { useState } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Inbox } from 'lucide-react'
import { api, ApiError } from '../../lib/api'
import {
  Button,
  Card,
  DecisionBadge,
  EmptyState,
  ErrorState,
  ExecutionBadge,
  Spinner,
  inputClass,
} from '../../components/ui'
import { formatDateTime, formatDuration, formatMoney } from '../../lib/format'
import type { Decision } from '../../types/api'

const PAGE_SIZE = 20

export function HistoryPage() {
  const [page, setPage] = useState(1)
  const [decision, setDecision] = useState<Decision | ''>('')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [corrected, setCorrected] = useState(false)

  const runs = useQuery({
    queryKey: ['runs', page, decision, status, search, corrected],
    queryFn: () =>
      api.listRuns({
        page,
        page_size: PAGE_SIZE,
        decision: decision || undefined,
        execution_status: status || undefined,
        search: search.trim() || undefined,
        has_correction: corrected || undefined,
      }),
    placeholderData: keepPreviousData,
  })

  function reset<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value)
      setPage(1)
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Run history</h1>
        <p className="mt-1 text-sm text-muted">
          Every attempt ever made, including retries and corrections. Runs are never edited in
          place.
        </p>
      </header>

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex min-w-56 flex-1 flex-col gap-1 text-xs">
            <span className="font-medium text-ink">Search</span>
            <input
              className={inputClass}
              value={search}
              placeholder="Invoice number, vendor or PO"
              onChange={(event) => reset(setSearch)(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium text-ink">Decision</span>
            <select
              className={inputClass}
              value={decision}
              onChange={(event) => reset(setDecision)(event.target.value as Decision | '')}
            >
              <option value="">Any</option>
              <option value="APPROVED">Approved</option>
              <option value="REVIEW">Needs review</option>
              <option value="BLOCKED">Blocked</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium text-ink">Execution</span>
            <select
              className={inputClass}
              value={status}
              onChange={(event) => reset(setStatus)(event.target.value)}
            >
              <option value="">Any</option>
              <option value="QUEUED">Queued</option>
              <option value="RUNNING">Running</option>
              <option value="COMPLETED">Completed</option>
              <option value="FAILED">Failed</option>
              <option value="INTERRUPTED">Interrupted</option>
            </select>
          </label>
          <label className="flex items-center gap-2 pb-2 text-xs text-ink">
            <input
              type="checkbox"
              checked={corrected}
              onChange={(event) => reset(setCorrected)(event.target.checked)}
              className="size-3.5"
            />
            Only corrected runs
          </label>
        </div>
      </Card>

      {runs.isLoading && <Spinner label="Loading runs" />}
      {runs.isError && (
        <ErrorState
          message={(runs.error as ApiError).message}
          onRetry={() => runs.refetch()}
        />
      )}

      {runs.data && runs.data.items.length === 0 && (
        <EmptyState
          icon={Inbox}
          title="No runs match these filters"
          description="Clear the filters, or process an invoice to get started."
          action={
            <Link to="/new">
              <Button size="sm" variant="primary">
                Process an invoice
              </Button>
            </Link>
          }
        />
      )}

      {runs.data && runs.data.items.length > 0 && (
        <Card className="overflow-hidden" >
          <div className="relative -mx-4 -my-4 overflow-x-auto">
            <table className="w-full min-w-[52rem] text-sm">
              <caption className="sr-only">Processing runs, newest first</caption>
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th scope="col" className="px-4 py-2 font-medium">Invoice</th>
                  <th scope="col" className="px-4 py-2 font-medium">Vendor</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Total</th>
                  <th scope="col" className="px-4 py-2 font-medium">PO</th>
                  <th scope="col" className="px-4 py-2 font-medium">Decision</th>
                  <th scope="col" className="px-4 py-2 font-medium">Execution</th>
                  <th scope="col" className="px-4 py-2 font-medium">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {runs.data.items.map((run) => (
                  <tr key={run.id} className="hover:bg-canvas">
                    <td className="px-4 py-2">
                      <Link
                        to={`/runs/${run.id}`}
                        className="font-medium text-brand hover:underline"
                      >
                        {run.invoice_number ?? 'no invoice number'}
                      </Link>
                      <p className="text-[11px] text-muted">
                        {run.trigger}
                        {run.has_human_correction && ' · corrected'}
                      </p>
                    </td>
                    <td className="max-w-48 truncate px-4 py-2 text-ink">
                      {run.vendor_display_name ?? '—'}
                    </td>
                    <td className="tabular px-4 py-2 text-right text-ink">
                      {formatMoney(run.gross_total)}
                    </td>
                    <td className="tabular px-4 py-2 text-muted">
                      {run.matched_po_number ?? '—'}
                    </td>
                    <td className="px-4 py-2">
                      <DecisionBadge decision={run.decision} />
                    </td>
                    <td className="px-4 py-2">
                      <ExecutionBadge status={run.execution_status} />
                      {run.processing_duration_ms !== null && (
                        <p className="tabular text-[11px] text-muted">
                          {formatDuration(run.processing_duration_ms)}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-2 text-xs whitespace-nowrap text-muted">
                      {formatDateTime(run.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {runs.data && runs.data.total_pages > 1 && (
        <nav className="flex items-center justify-between" aria-label="Pagination">
          <p className="text-xs text-muted">
            Page {runs.data.page} of {runs.data.total_pages} · {runs.data.total} runs
          </p>
          <div className="flex gap-2">
            <Button size="sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
              Previous
            </Button>
            <Button
              size="sm"
              disabled={page >= runs.data.total_pages}
              onClick={() => setPage((value) => value + 1)}
            >
              Next
            </Button>
          </div>
        </nav>
      )}
    </div>
  )
}
