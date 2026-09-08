import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Download,
  ExternalLink,
  FileText,
  GitBranch,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react'
import { api, ApiError } from '../../lib/api'
import {
  Button,
  Card,
  DecisionBadge,
  ErrorState,
  ExecutionBadge,
  LinkButton,
  Spinner,
} from '../../components/ui'
import { formatBytes, formatDateTime, formatDuration, formatMoney } from '../../lib/format'
import { useRunLive } from './useRunLive'
import {
  ChecksPanel,
  DecisionPanel,
  EventLog,
  FieldsPanel,
  ReferencePanel,
  StageTimeline,
} from './panels'
import { CorrectionForm } from './CorrectionForm'
import type { RunDetail } from '../../types/api'

export function RunPage() {
  const { runId = '' } = useParams()
  const { runQuery, detail, events, stages, isLive, polling } = useRunLive(runId)

  if (runQuery.isLoading) return <Spinner label="Loading the run" />
  if (runQuery.isError) {
    const error = runQuery.error as ApiError
    return (
      <ErrorState
        title="This run could not be loaded"
        message={error.message}
        detail={error.detail}
        onRetry={() => runQuery.refetch()}
      />
    )
  }
  if (!detail) return null

  const payload = detail.decision_payload ?? {}
  const quality = payload.field_quality ?? []

  return (
    <div className="space-y-5">
      <RunHeader run={detail} live={isLive} polling={polling} />

      {detail.execution_status === 'FAILED' && (
        <ErrorState
          title="The run stopped before reaching a decision"
          message={detail.error_message ?? 'An unexpected error ended the run.'}
          detail={
            detail.error_code
              ? `This is a technical failure (${detail.error_code}), not a decision about the invoice. Retrying processes the same stored file again.`
              : undefined
          }
        />
      )}

      {detail.execution_status === 'INTERRUPTED' && (
        <ErrorState
          title="This run was interrupted"
          message={
            detail.error_message ??
            'The server restarted while this run was in flight, so it never reached a decision.'
          }
          detail="Nothing was committed. Retry to process the same stored file again."
        />
      )}

      {(detail.decision || detail.execution_status === 'COMPLETED') && (
        <DecisionPanel payload={payload} ruleResults={detail.rule_results} />
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <FieldsPanel
            invoice={detail.extraction.invoice}
            metadata={detail.extraction.metadata}
            quality={quality}
            overrides={(detail.overrides?.fields as Record<string, unknown>) ?? null}
          />
          <ChecksPanel results={detail.rule_results} />
          <ReferencePanel payload={payload} />
          <CorrectionForm run={detail} />
          <CaseLineage run={detail} />
          <EventLog events={events} />
        </div>

        <div className="space-y-5">
          <StageTimeline stages={stages} live={isLive} />
          <DocumentPanel run={detail} />
          <ProvenancePanel run={detail} />
        </div>
      </div>
    </div>
  )
}

function RunHeader({
  run,
  live,
  polling,
}: {
  run: RunDetail
  live: boolean
  polling: boolean
}) {
  const queryClient = useQueryClient()
  const retry = useMutation({
    mutationFn: () => api.retry(run.id),
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ['run', data.run.id] })
      window.location.assign(`/runs/${data.run.id}`)
    },
  })

  return (
    <header className="space-y-3">
      <Link
        to="/runs"
        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted hover:text-ink"
      >
        <ArrowLeft aria-hidden="true" className="size-3.5" />
        All runs
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold">
            {run.invoice_number ?? run.document?.filename ?? 'Invoice run'}
          </h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <ExecutionBadge status={run.execution_status} />
            {polling && <span className="text-brand">live</span>}
            <span>{run.vendor_display_name ?? 'vendor not resolved'}</span>
            <span className="tabular">{formatMoney(run.gross_total)}</span>
            {run.matched_po_number && <span className="tabular">{run.matched_po_number}</span>}
            <span>{formatDateTime(run.created_at)}</span>
            {run.processing_duration_ms !== null && (
              <span>took {formatDuration(run.processing_duration_ms)}</span>
            )}
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {!live && <DecisionBadge decision={run.decision} />}
          {run.is_terminal && (
            <Button
              size="sm"
              onClick={() => retry.mutate()}
              disabled={retry.isPending}
              title="Process the same stored file again"
            >
              <RefreshCw aria-hidden="true" className="size-3.5" />
              {retry.isPending ? 'Starting…' : 'Retry'}
            </Button>
          )}
          <LinkButton href={api.exportJsonUrl(run.id)} download>
            <Download aria-hidden="true" className="size-3.5" />
            JSON
          </LinkButton>
          <LinkButton href={api.exportCsvUrl(run.id)} download>
            <Download aria-hidden="true" className="size-3.5" />
            CSV
          </LinkButton>
        </div>
      </div>

      {retry.isError && (
        <ErrorState
          message={(retry.error as ApiError).message}
          detail={(retry.error as ApiError).detail}
        />
      )}
    </header>
  )
}

function DocumentPanel({ run }: { run: RunDetail }) {
  if (!run.document) return null
  const url = api.documentUrl(run.id)
  return (
    <Card
      title="Source document"
      actions={
        <LinkButton href={url}>
          <ExternalLink aria-hidden="true" className="size-3.5" />
          Open
        </LinkButton>
      }
    >
      {/* <object> falls back to its children wherever the browser has no
          inline PDF viewer, so the panel is never a blank grey rectangle. */}
      <object
        data={`${url}#view=FitH&toolbar=0`}
        type="application/pdf"
        aria-label={`Preview of ${run.document.filename}`}
        className="h-96 w-full rounded border border-line bg-canvas"
      >
        <div className="grid h-full place-items-center gap-2 p-4 text-center">
          <p className="text-xs text-muted">
            This browser cannot show the PDF inline.
          </p>
          <LinkButton href={url}>
            <ExternalLink aria-hidden="true" className="size-3.5" />
            Open the document
          </LinkButton>
        </div>
      </object>
      <dl className="mt-3 space-y-1 text-[11px] text-muted">
        <div className="flex justify-between gap-2">
          <dt>File</dt>
          <dd className="truncate font-medium text-ink">{run.document.filename}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Size</dt>
          <dd className="tabular text-ink">{formatBytes(run.document.byte_count)}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Pages</dt>
          <dd className="tabular text-ink">{run.document.page_count}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0">SHA-256</dt>
          <dd className="tabular min-w-0 break-all text-ink">{run.document.sha256}</dd>
        </div>
      </dl>
      <p className="mt-2 flex gap-1.5 text-[11px] text-muted">
        <FileText aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
        The hash is what duplicate detection compares; re-uploading the same bytes is recognised
        immediately.
      </p>
    </Card>
  )
}

function ProvenancePanel({ run }: { run: RunDetail }) {
  const metadata = run.extraction.metadata
  if (!metadata) return null
  return (
    <Card title="How this was read" description="Everything that shaped the extraction.">
      <dl className="space-y-1.5 text-xs">
        <Line label="Extraction provider" value={metadata.provider} />
        <Line label="Model" value={metadata.model ?? 'none — deterministic parser'} />
        <Line label="Parser version" value={metadata.parser_version} />
        <Line label="Prompt hash" value={metadata.prompt_hash} mono />
        <Line label="Pages read" value={String(metadata.pages_total)} />
        <Line
          label="Pages needing OCR"
          value={metadata.pages_ocr.length > 0 ? metadata.pages_ocr.join(', ') : 'none'}
        />
        <Line label="OCR engine" value={metadata.ocr_engine ?? 'not used'} />
        <Line label="Extraction time" value={formatDuration(metadata.duration_ms)} />
        <Line label="Policy version" value={run.policy_version} />
      </dl>

      {metadata.fallback_from && (
        <p className="mt-3 flex gap-1.5 rounded border border-review/25 bg-review-soft px-2 py-1.5 text-[11px] text-review">
          <ShieldAlert aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
          <span>
            Fell back from {metadata.fallback_from} to {metadata.provider}.{' '}
            {metadata.fallback_reason}
          </span>
        </p>
      )}
      {metadata.repair_attempted && (
        <p className="mt-2 text-[11px] text-muted">
          The model's first response was not valid against the schema, so one repair attempt was
          made.
        </p>
      )}
      {metadata.notes.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {metadata.notes.map((note) => (
            <li key={note} className="text-[11px] text-muted">
              {note}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function Line({
  label,
  value,
  mono,
}: {
  label: string
  value: string | null
  mono?: boolean
}) {
  if (!value) return null
  return (
    <div className="flex justify-between gap-3">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className={mono ? 'tabular min-w-0 truncate text-ink' : 'min-w-0 truncate text-ink'}>
        {value}
      </dd>
    </div>
  )
}

function CaseLineage({ run }: { run: RunDetail }) {
  if (run.case_runs.length <= 1 && run.review_actions.length === 0) return null
  return (
    <Card
      title="Case history"
      description="Every attempt on this invoice, oldest first. Nothing is overwritten."
    >
      <ol className="space-y-2">
        {run.case_runs.map((entry) => (
          <li key={entry.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <GitBranch aria-hidden="true" className="size-3.5 shrink-0 text-muted" />
            {entry.id === run.id ? (
              <span className="font-semibold text-ink">this run</span>
            ) : (
              <Link to={`/runs/${entry.id}`} className="font-medium text-brand hover:underline">
                {entry.trigger} run
              </Link>
            )}
            <span className="text-muted">{entry.trigger}</span>
            <DecisionBadge decision={entry.decision} />
            <span className="text-muted">{formatDateTime(entry.created_at)}</span>
          </li>
        ))}
      </ol>

      {run.review_actions.length > 0 && (
        <div className="mt-4 space-y-2 border-t border-line pt-3">
          <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">
            Reviewer actions
          </h3>
          {run.review_actions.map((action) => (
            <div key={action.id} className="rounded border border-line bg-canvas px-2.5 py-2">
              <p className="text-xs text-ink">
                <span className="font-medium">{action.actor_label}</span> — {action.reason}
              </p>
              <p className="mt-1 text-[11px] text-muted">{formatDateTime(action.timestamp)}</p>
              <ul className="tabular mt-1 space-y-0.5 text-[11px] text-muted">
                {Object.entries(action.proposed_values).map(([key, value]) => (
                  <li key={key}>
                    {key}: {String(action.old_values[key] ?? 'not found')} → {String(value)}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
