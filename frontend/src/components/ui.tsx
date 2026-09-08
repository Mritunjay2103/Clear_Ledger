import type { ReactNode } from 'react'
import clsx from 'clsx'
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  Clock,
  Loader2,
  MinusCircle,
  OctagonAlert,
  RefreshCw,
} from 'lucide-react'
import type { Decision, ExecutionStatus, QualityLabel, RuleResult } from '../types/api'
import {
  DECISION_LABEL,
  EXECUTION_LABEL,
  QUALITY_DEFINITION,
  QUALITY_LABEL,
} from '../lib/format'

/**
 * `min-w-0` is load-bearing: as a grid or flex child, a card is otherwise sized
 * by its widest content, so a scrollable table inside it widens the whole page
 * on a narrow screen instead of scrolling within the card.
 */
export function Card({
  title,
  description,
  actions,
  children,
  className,
  id,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  id?: string
}) {
  return (
    <section
      id={id}
      className={clsx('min-w-0 rounded-lg border border-line bg-surface shadow-xs', className)}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="px-4 py-4">{children}</div>
    </section>
  )
}

const DECISION_STYLE: Record<Decision, string> = {
  APPROVED: 'bg-approved-soft text-approved border-approved/25',
  REVIEW: 'bg-review-soft text-review border-review/25',
  BLOCKED: 'bg-blocked-soft text-blocked border-blocked/25',
}

const DECISION_ICON: Record<Decision, typeof CheckCircle2> = {
  APPROVED: CheckCircle2,
  REVIEW: AlertTriangle,
  BLOCKED: Ban,
}

export function DecisionBadge({
  decision,
  size = 'sm',
}: {
  decision: Decision | null
  size?: 'sm' | 'lg'
}) {
  if (!decision) {
    return (
      <span
        data-testid="decision-label"
        className="inline-flex items-center gap-1.5 rounded-md border border-line bg-canvas px-2 py-0.5 text-xs font-medium text-muted"
      >
        <CircleDashed aria-hidden="true" className="size-3.5" />
        No decision yet
      </span>
    )
  }
  const Icon = DECISION_ICON[decision]
  return (
    <span
      data-testid="decision-label"
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-md border font-semibold',
        DECISION_STYLE[decision],
        size === 'lg' ? 'px-3 py-1 text-sm' : 'px-2 py-0.5 text-xs',
      )}
    >
      <Icon aria-hidden="true" className={size === 'lg' ? 'size-4' : 'size-3.5'} />
      {DECISION_LABEL[decision]}
    </span>
  )
}

const EXECUTION_STYLE: Record<ExecutionStatus, string> = {
  QUEUED: 'text-muted',
  RUNNING: 'text-brand',
  COMPLETED: 'text-ink',
  FAILED: 'text-failed',
  INTERRUPTED: 'text-failed',
}

export function ExecutionBadge({ status }: { status: ExecutionStatus }) {
  const Icon =
    status === 'RUNNING'
      ? Loader2
      : status === 'QUEUED'
        ? Clock
        : status === 'COMPLETED'
          ? CheckCircle2
          : OctagonAlert
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 text-xs font-medium',
        EXECUTION_STYLE[status],
      )}
    >
      <Icon
        aria-hidden="true"
        className={clsx('size-3.5', status === 'RUNNING' && 'animate-spin')}
      />
      {EXECUTION_LABEL[status]}
    </span>
  )
}

export function RuleIcon({ result }: { result: RuleResult }) {
  if (result.status === 'pass' && result.severity === 'warning')
    return <AlertTriangle aria-hidden="true" className="size-4 shrink-0 text-review" />
  if (result.status === 'pass')
    return <CheckCircle2 aria-hidden="true" className="size-4 shrink-0 text-approved" />
  if (result.status === 'skipped')
    return <MinusCircle aria-hidden="true" className="size-4 shrink-0 text-muted" />
  if (result.severity === 'block')
    return <Ban aria-hidden="true" className="size-4 shrink-0 text-blocked" />
  return <AlertTriangle aria-hidden="true" className="size-4 shrink-0 text-review" />
}

const QUALITY_STYLE: Record<QualityLabel, string> = {
  verified: 'border-approved/25 bg-approved-soft text-approved',
  uncertain: 'border-review/25 bg-review-soft text-review',
  missing: 'border-line bg-canvas text-muted',
  human_supplied: 'border-brand/30 bg-brand-soft text-brand',
}

/** Says how much the extracted value can be relied on, in the reviewer's words. */
export function QualityChip({ label }: { label: QualityLabel }) {
  return (
    <span
      className={clsx(
        'rounded border px-1.5 py-0.5 text-[10px] font-medium',
        QUALITY_STYLE[label],
      )}
      title={QUALITY_DEFINITION[label]}
    >
      {QUALITY_LABEL[label]}
    </span>
  )
}

export function Button({
  children,
  variant = 'secondary',
  size = 'md',
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
}) {
  return (
    <button
      {...props}
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-md border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55',
        size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3.5 py-2 text-sm',
        variant === 'primary' &&
          'border-brand bg-brand text-white hover:bg-brand-strong hover:border-brand-strong',
        variant === 'secondary' && 'border-line bg-surface text-ink hover:bg-canvas',
        variant === 'ghost' && 'border-transparent bg-transparent text-brand hover:bg-brand-soft',
        variant === 'danger' && 'border-blocked/30 bg-blocked-soft text-blocked hover:bg-rose-100',
        className,
      )}
    >
      {children}
    </button>
  )
}

export function LinkButton({
  href,
  children,
  download,
  className,
}: {
  href: string
  children: ReactNode
  download?: boolean
  className?: string
}) {
  return (
    <a
      href={href}
      download={download}
      target={download ? undefined : '_blank'}
      rel="noreferrer"
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium text-ink hover:bg-canvas',
        className,
      )}
    >
      {children}
    </a>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-6 text-sm text-muted">
      <Loader2 aria-hidden="true" className="size-4 animate-spin" />
      <span>{label}…</span>
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
  icon: Icon = CircleSlash,
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: typeof CircleSlash
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-line bg-canvas/60 px-6 py-10 text-center">
      <Icon aria-hidden="true" className="size-6 text-muted" />
      <p className="text-sm font-medium text-ink">{title}</p>
      {description && <p className="max-w-md text-xs text-muted">{description}</p>}
      {action}
    </div>
  )
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  detail,
  onRetry,
}: {
  title?: string
  message: string
  detail?: string | null
  onRetry?: () => void
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-2 rounded-md border border-blocked/25 bg-blocked-soft px-4 py-3"
    >
      <p className="flex items-center gap-2 text-sm font-semibold text-blocked">
        <OctagonAlert aria-hidden="true" className="size-4" />
        {title}
      </p>
      <p className="text-sm text-ink">{message}</p>
      {detail && <p className="text-xs text-muted">{detail}</p>}
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          <RefreshCw aria-hidden="true" className="size-3.5" />
          Try again
        </Button>
      )}
    </div>
  )
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: ReactNode
  hint?: string
}) {
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span className="font-medium text-ink">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-muted">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted focus:border-brand focus:outline-none'

export function DefinitionTooltip({ text }: { text: string }) {
  return (
    <span className="text-[11px] leading-snug text-muted" title={text}>
      {text}
    </span>
  )
}
