import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { PenLine } from 'lucide-react'
import { api, ApiError } from '../../lib/api'
import { Button, Card, ErrorState, Field, inputClass } from '../../components/ui'
import type { RunDetail } from '../../types/api'

/** Fields the workflow accepts as reviewer overrides. Mirrors CORRECTABLE_FIELDS. */
const CORRECTABLE: [string, string, string][] = [
  ['vendor_name', 'Vendor name', 'as printed on the invoice'],
  ['invoice_number', 'Invoice number', ''],
  ['invoice_date', 'Invoice date', 'YYYY-MM-DD'],
  ['currency', 'Currency', 'three-letter code'],
  ['subtotal', 'Subtotal', 'digits only, e.g. 84000.00'],
  ['tax_total', 'Tax', 'digits only'],
  ['gross_total', 'Invoice total', 'digits only'],
]

export function CorrectionForm({ run }: { run: RunDetail }) {
  const navigate = useNavigate()
  const invoice = run.extraction.invoice
  const [open, setOpen] = useState(false)
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  const [selectedPoId, setSelectedPoId] = useState('')
  const [selectedVendorId, setSelectedVendorId] = useState('')

  const vendors = useQuery({ queryKey: ['vendors'], queryFn: api.vendors, enabled: open })
  const pos = useQuery({ queryKey: ['purchase-orders'], queryFn: api.purchaseOrders, enabled: open })

  const submit = useMutation({
    mutationFn: () =>
      api.correct(run.id, {
        actor_label: actor.trim(),
        reason: reason.trim(),
        expected_version: run.version,
        fields: Object.fromEntries(
          Object.entries(fields).filter(([, value]) => value.trim() !== ''),
        ),
        selected_po_id: selectedPoId || null,
        selected_vendor_id: selectedVendorId || null,
      }),
    onSuccess: (data) => navigate(`/runs/${data.run.id}`),
  })

  const error = submit.error as ApiError | null
  const hasChange =
    Object.values(fields).some((value) => value.trim() !== '') ||
    Boolean(selectedPoId) ||
    Boolean(selectedVendorId)
  const ready = actor.trim().length >= 2 && reason.trim().length >= 8 && hasChange

  if (run.reservation?.active) {
    return (
      <Card title="Correct and reprocess">
        <p className="text-sm text-muted">
          This run holds an active commitment against{' '}
          <span className="font-medium text-ink">{run.matched_po_number}</span>. Corrections are
          disabled for approved runs so a commitment cannot be silently replaced or double counted.
          Revoking an approval is a documented limitation of this build.
        </p>
      </Card>
    )
  }

  return (
    <Card
      title="Correct and reprocess"
      description="Your values are recorded as human supplied and the invoice is processed again as a new run. The original run stays exactly as it was."
      actions={
        <Button size="sm" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          <PenLine aria-hidden="true" className="size-3.5" />
          {open ? 'Cancel' : 'Make a correction'}
        </Button>
      }
    >
      {!open ? (
        <p className="text-sm text-muted">
          Use this when the document was read wrongly, or when you can confirm which vendor or
          purchase order the invoice belongs to.
        </p>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (ready) submit.mutate()
          }}
        >
          {error && (
            <ErrorState
              title="The correction was not accepted"
              message={error.message}
              detail={error.detail}
            />
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Your name or initials">
              <input
                className={inputClass}
                value={actor}
                onChange={(event) => setActor(event.target.value)}
                placeholder="A. Reviewer"
                required
                minLength={2}
                maxLength={120}
              />
            </Field>
            <Field label="Why are you changing this?" hint="At least 8 characters. Stored on the case.">
              <input
                className={inputClass}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Total was misread from the scan"
                required
                minLength={8}
                maxLength={1000}
              />
            </Field>
          </div>

          <fieldset className="grid gap-3 sm:grid-cols-2">
            <legend className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">
              Field values
            </legend>
            {CORRECTABLE.map(([name, label, hint]) => {
              const current = (invoice?.[name as keyof typeof invoice] as string | null) ?? null
              return (
                <Field
                  key={name}
                  label={label}
                  hint={hint ? `${hint}${current ? ` · read as “${current}”` : ''}` : current ? `read as “${current}”` : 'not found'}
                >
                  <input
                    className={inputClass}
                    value={fields[name] ?? ''}
                    placeholder={current ?? 'leave blank to keep'}
                    onChange={(event) =>
                      setFields((previous) => ({ ...previous, [name]: event.target.value }))
                    }
                  />
                </Field>
              )
            })}
          </fieldset>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Confirm the vendor" hint="Optional. Overrides name matching.">
              <select
                className={inputClass}
                value={selectedVendorId}
                onChange={(event) => setSelectedVendorId(event.target.value)}
              >
                <option value="">Leave as matched</option>
                {vendors.data?.items.map((vendor) => (
                  <option key={vendor.id} value={vendor.id}>
                    {vendor.canonical_name}
                    {vendor.status === 'blocked' ? ' (blocked)' : ''}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Confirm the purchase order" hint="Optional. Overrides PO matching.">
              <select
                className={inputClass}
                value={selectedPoId}
                onChange={(event) => setSelectedPoId(event.target.value)}
              >
                <option value="">Leave as matched</option>
                {pos.data?.items.map((po) => (
                  <option key={po.id} value={po.id}>
                    {po.po_number} — {po.vendor_name} · {po.available_nominal} left
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="flex items-center gap-3">
            <Button type="submit" variant="primary" disabled={!ready || submit.isPending}>
              {submit.isPending ? 'Reprocessing…' : 'Save and reprocess'}
            </Button>
            <p className="text-[11px] text-muted">
              Reprocessing starts a new run linked to this one, at policy version{' '}
              {run.policy_version}.
            </p>
          </div>
        </form>
      )}
    </Card>
  )
}
