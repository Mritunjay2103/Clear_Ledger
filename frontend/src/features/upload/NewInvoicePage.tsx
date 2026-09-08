import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { Download, FileUp, Info, Play, ScanLine } from 'lucide-react'
import { api, ApiError } from '../../lib/api'
import { Button, Card, ErrorState, LinkButton, Spinner } from '../../components/ui'
import type { SampleEntry } from '../../types/api'

export function NewInvoicePage() {
  const navigate = useNavigate()
  const [dragging, setDragging] = useState(false)
  const [selected, setSelected] = useState<File | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const capabilities = useQuery({ queryKey: ['capabilities'], queryFn: api.capabilities })
  const samples = useQuery({ queryKey: ['samples'], queryFn: api.samples })

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadFile(file),
    onSuccess: (data) => navigate(`/runs/${data.run.id}`),
    onSettled: () => setPending(null),
  })

  const runSample = useMutation({
    mutationFn: ({ id, filename }: { id: string; filename: string }) =>
      api.runSample(id, filename),
    onSuccess: (data) => navigate(`/runs/${data.run.id}`),
    onSettled: () => setPending(null),
  })

  const busy = upload.isPending || runSample.isPending
  const error = (upload.error ?? runSample.error) as ApiError | null
  const limits = capabilities.data?.limits

  function submit(file: File) {
    setPending('upload')
    upload.mutate(file)
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">New invoice</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Upload a PDF, or run a bundled synthetic sample. A sample is submitted as real bytes
          through the same upload path, so nothing about it is special-cased.
        </p>
      </header>

      {error && (
        <ErrorState
          title="The document was not accepted"
          message={error.message}
          detail={error.detail}
        />
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card title="Upload a PDF" description="The file is read on the server; nothing is parsed in your browser.">
          <div
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault()
              setDragging(false)
              const file = event.dataTransfer.files?.[0]
              if (file) setSelected(file)
            }}
            className={clsx(
              'flex flex-col items-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center',
              dragging ? 'border-brand bg-brand-soft' : 'border-line bg-canvas/50',
            )}
          >
            <FileUp aria-hidden="true" className="size-7 text-muted" />
            <div>
              <p className="text-sm font-medium">Drop an invoice PDF here</p>
              <p className="mt-1 text-xs text-muted">
                PDF only · up to {limits?.max_upload_mb ?? 10} MiB · up to{' '}
                {limits?.max_pdf_pages ?? 10} pages
              </p>
            </div>
            <input
              ref={inputRef}
              id="invoice-file"
              type="file"
              accept="application/pdf,.pdf"
              className="sr-only"
              onChange={(event) => setSelected(event.target.files?.[0] ?? null)}
            />
            <Button onClick={() => inputRef.current?.click()} disabled={busy}>
              Choose a file
            </Button>
            {selected && (
              <p className="text-xs text-ink">
                Selected: <span className="font-medium">{selected.name}</span>{' '}
                <span className="text-muted">({Math.round(selected.size / 1024)} KB)</span>
              </p>
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted">
              Extraction mode for this run:{' '}
              <span className="font-medium text-ink">
                {capabilities.data?.extraction.configured_provider_label ?? 'loading…'}
              </span>
              {capabilities.data && !capabilities.data.ocr.available && (
                <span className="ml-1 text-review">· OCR unavailable, scans will not be read</span>
              )}
            </p>
            <Button
              variant="primary"
              disabled={!selected || busy}
              onClick={() => selected && submit(selected)}
            >
              {pending === 'upload' && busy ? 'Submitting…' : 'Process invoice'}
            </Button>
          </div>
        </Card>

        <Card title="What happens next" description="Eight recorded stages, then a decision.">
          <ol className="space-y-2 text-xs text-muted">
            {[
              'Intake: the file is validated and hashed.',
              'Read document: text layer first, OCR only for pages that need it.',
              'Extract fields: typed invoice facts with a quoted source excerpt each.',
              'Validate facts: excerpts are checked against the real page text.',
              'Match references: vendor and purchase order are resolved.',
              'Evaluate policy: deterministic rules, no model involved.',
              'Commit decision: the decision and any commitment are written atomically.',
              'Publish output: the report and exports become available.',
            ].map((step, index) => (
              <li key={step} className="flex gap-2">
                <span className="tabular grid size-4 shrink-0 place-items-center rounded-full bg-canvas text-[10px] font-semibold text-ink">
                  {index + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </Card>
      </div>

      <Card
        id="samples"
        title="Synthetic samples"
        description={samples.data?.disclaimer ?? 'Fictional vendors, invoices and amounts.'}
      >
        {samples.isLoading && <Spinner label="Loading samples" />}
        {samples.isError && (
          <ErrorState message="The sample catalog could not be loaded." onRetry={() => samples.refetch()} />
        )}
        {samples.data && (
          <div className="space-y-6">
            {samples.data.scenarios.map((group) => (
              <section key={group.scenario}>
                <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">
                  {group.scenario}
                </h3>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {group.samples.map((sample) => (
                    <SampleCard
                      key={sample.id}
                      sample={sample}
                      busy={busy}
                      pending={pending === sample.id}
                      onRun={() => {
                        setPending(sample.id)
                        runSample.mutate({ id: sample.id, filename: sample.filename })
                      }}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

function SampleCard({
  sample,
  busy,
  pending,
  onRun,
}: {
  sample: SampleEntry
  busy: boolean
  pending: boolean
  onRun: () => void
}) {
  return (
    <article className="flex flex-col justify-between gap-3 rounded-lg border border-line bg-surface p-3">
      <div>
        <div className="flex items-start justify-between gap-2">
          <h4 className="text-sm font-semibold text-ink">{sample.title}</h4>
          {sample.scanned && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded border border-line bg-canvas px-1.5 py-0.5 text-[10px] font-medium text-muted">
              <ScanLine aria-hidden="true" className="size-3" />
              Scan
            </span>
          )}
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-muted">{sample.summary}</p>
        <dl className="tabular mt-2 space-y-0.5 text-[11px] text-muted">
          <div className="flex justify-between gap-2">
            <dt>Printed total</dt>
            <dd className="font-medium text-ink">{sample.printed_total}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt>Printed PO</dt>
            <dd className="font-medium text-ink">{sample.printed_po_number ?? 'none'}</dd>
          </div>
        </dl>
        {sample.preconditions.length > 0 && (
          <p className="mt-2 flex items-start gap-1.5 rounded border border-review/25 bg-review-soft px-2 py-1.5 text-[11px] text-review">
            <Info aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
            <span>{sample.preconditions.join(' ')}</span>
          </p>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button variant="primary" size="sm" onClick={onRun} disabled={busy}>
          <Play aria-hidden="true" className="size-3.5" />
          {pending ? 'Starting…' : 'Run this sample'}
        </Button>
        <LinkButton href={api.sampleFileUrl(sample.id)} download>
          <Download aria-hidden="true" className="size-3.5" />
          PDF
        </LinkButton>
      </div>
    </article>
  )
}
