import { useQuery } from '@tanstack/react-query'
import { BookLock, Info } from 'lucide-react'
import { api, ApiError } from '../../lib/api'
import { Card, ErrorState, Spinner } from '../../components/ui'

export function ReferencePage() {
  const vendors = useQuery({ queryKey: ['vendors'], queryFn: api.vendors })
  const pos = useQuery({ queryKey: ['purchase-orders'], queryFn: api.purchaseOrders })
  const policy = useQuery({ queryKey: ['policy'], queryFn: api.policy })

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Reference data and policy</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Read-only. These vendors and purchase orders are the ground truth every invoice is
          checked against, and the rules below are the only thing that decides an outcome.
        </p>
      </header>

      <Card
        title="Purchase orders"
        description={pos.data?.balance_note}
        actions={
          pos.data && (
            <span className="text-[11px] text-muted">source: {pos.data.source}</span>
          )
        }
      >
        {pos.isLoading && <Spinner label="Loading purchase orders" />}
        {pos.isError && (
          <ErrorState message={(pos.error as ApiError).message} onRetry={() => pos.refetch()} />
        )}
        {pos.data && (
          <div className="relative -mx-4 -my-4 overflow-x-auto">
            <table className="w-full min-w-[54rem] text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th scope="col" className="px-4 py-2 font-medium">PO</th>
                  <th scope="col" className="px-4 py-2 font-medium">Vendor</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Approved</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Committed</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Remaining</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    With tolerance
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {pos.data.items.map((po) => (
                  <tr key={po.id} className="hover:bg-canvas">
                    <td className="tabular px-4 py-2 font-medium text-ink">
                      {po.po_number}
                      {po.description && (
                        <p className="text-[11px] font-normal text-muted">{po.description}</p>
                      )}
                    </td>
                    <td className="px-4 py-2 text-ink">
                      {po.vendor_name}
                      {po.vendor_status === 'blocked' && (
                        <span className="ml-1.5 rounded border border-blocked/25 bg-blocked-soft px-1 py-0.5 text-[10px] text-blocked">
                          blocked
                        </span>
                      )}
                    </td>
                    <td className="tabular px-4 py-2 text-right text-ink">{po.approved_total}</td>
                    <td className="tabular px-4 py-2 text-right text-ink">{po.committed}</td>
                    <td className="tabular px-4 py-2 text-right font-medium text-ink">
                      {po.available_nominal}
                    </td>
                    <td className="tabular px-4 py-2 text-right text-muted">
                      {po.available_including_tolerance}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted">{po.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Vendors"
        description="Matching is exact on the canonical name or a known alias. Anything else goes to a human."
      >
        {vendors.isLoading && <Spinner label="Loading vendors" />}
        {vendors.isError && (
          <ErrorState
            message={(vendors.error as ApiError).message}
            onRetry={() => vendors.refetch()}
          />
        )}
        {vendors.data && (
          <ul className="divide-y divide-line">
            {vendors.data.items.map((vendor) => (
              <li key={vendor.id} className="flex flex-wrap gap-x-4 gap-y-1 py-2">
                <div className="min-w-56 flex-1">
                  <p className="text-sm font-medium text-ink">
                    {vendor.canonical_name}
                    {vendor.status === 'blocked' && (
                      <span className="ml-2 rounded border border-blocked/25 bg-blocked-soft px-1.5 py-0.5 text-[10px] font-medium text-blocked">
                        blocked
                      </span>
                    )}
                  </p>
                  {vendor.aliases.length > 0 && (
                    <p className="mt-0.5 text-[11px] text-muted">
                      Also accepted as: {vendor.aliases.join(' · ')}
                    </p>
                  )}
                </div>
                <p className="tabular text-xs text-muted">
                  {vendor.vendor_code ?? '—'} · {vendor.supported_currency}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Decision policy"
        description={
          policy.data ? `Version ${policy.data.version}. Applied identically to every run.` : undefined
        }
      >
        {policy.isLoading && <Spinner label="Loading policy" />}
        {policy.data && (
          <>
            <dl className="space-y-3">
              {policy.data.plain_language.map((entry) => (
                <div key={entry.title}>
                  <dt className="text-sm font-medium text-ink">{entry.title}</dt>
                  <dd className="mt-0.5 text-xs leading-relaxed text-muted">{entry.detail}</dd>
                </div>
              ))}
            </dl>

            <div className="mt-5 border-t border-line pt-4">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-muted uppercase">
                <BookLock aria-hidden="true" className="size-3.5" />
                What this system deliberately does not do
              </h3>
              <ul className="mt-2 space-y-1">
                {policy.data.boundaries.map((line) => (
                  <li key={line} className="flex gap-2 text-xs text-muted">
                    <Info aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
                    {line}
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}
