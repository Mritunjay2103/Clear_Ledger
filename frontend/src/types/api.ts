export type Decision = 'APPROVED' | 'REVIEW' | 'BLOCKED'
export type ExecutionStatus = 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'INTERRUPTED'
export type RuleStatus = 'pass' | 'fail' | 'review' | 'skipped'
export type RuleSeverity = 'info' | 'warning' | 'review' | 'block'
export type QualityLabel = 'verified' | 'uncertain' | 'missing' | 'human_supplied'
export type Provenance = 'pdf_text' | 'ocr' | 'human_correction'

export interface Money {
  minor: number
  amount: string
  currency: string
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    detail?: string | null
    retryable?: boolean
  }
}

export interface RunSummary {
  id: string
  case_id: string
  parent_run_id: string | null
  trigger: 'upload' | 'sample' | 'retry' | 'correction'
  execution_status: ExecutionStatus
  decision: Decision | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  processing_duration_ms: number | null
  invoice_number: string | null
  vendor_display_name: string | null
  gross_total: Money | null
  currency: string | null
  matched_po_number: string | null
  has_human_correction: boolean
  extraction_provider: string | null
  actual_model: string | null
  error_code: string | null
  error_message: string | null
  headline: string | null
}

export interface StageView {
  stage_key: string
  stage_label: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  started_at: string | null
  completed_at: string | null
  elapsed_ms: number | null
  messages: string[]
  warnings: string[]
  errors: string[]
}

export interface WorkflowEventView {
  sequence: number
  stage_key: string
  stage_label: string
  event_type: 'started' | 'completed' | 'failed' | 'warning' | 'skipped'
  timestamp: string
  short_message: string
  structured_metadata: Record<string, unknown> | null
}

export interface Evidence {
  page: number
  excerpt: string
  provenance: Provenance
}

export interface LineItem {
  description: string | null
  quantity: string | null
  unit_price: string | null
  net_amount: string | null
  tax_amount: string | null
}

export interface ExtractedInvoice {
  vendor_name: string | null
  vendor_reference: string | null
  invoice_number: string | null
  invoice_date: string | null
  explicit_po_number: string | null
  currency: string | null
  subtotal: string | null
  tax_total: string | null
  gross_total: string | null
  line_items: LineItem[]
  line_items_complete: boolean
  line_amounts_tax_inclusive: boolean | null
  document_kind: 'invoice' | 'credit_note' | 'unknown'
  evidence: Record<string, Evidence>
  missing_fields: string[]
  ambiguities: string[]
  extraction_warnings: string[]
}

export interface ExtractionMetadata {
  provider: string
  model: string | null
  parser_version: string | null
  prompt_hash: string | null
  duration_ms: number
  repair_attempted: boolean
  fallback_from: string | null
  fallback_reason: string | null
  pages_total: number
  pages_ocr: number[]
  ocr_engine: string | null
  notes: string[]
}

export interface RuleResult {
  code: string
  status: RuleStatus
  severity: RuleSeverity
  description: string
  observed_value: string | null
  expected_value: string | null
  evidence_refs: string[]
  next_action: string | null
  mandatory: boolean
}

export interface FieldQuality {
  field: string
  label: QualityLabel
  reason: string | null
  evidence: Evidence | null
}

export interface PoComparison {
  po_number: string | null
  currency: string | null
  verdict: string
  within_tolerance_overage: boolean
  reserved: boolean
  invoice_gross_display: string | null
  approved_total_display: string | null
  committed_before_display: string | null
  tolerance_display: string | null
  allowed_total_display: string | null
  projected_total_display: string | null
  remaining_nominal_before_display: string | null
  remaining_allowed_before_display: string | null
  remaining_nominal_after_display: string | null
  projected_remaining_allowed_display: string | null
}

export interface PoCandidate {
  po_id: string
  po_number: string
  currency: string
  approved_total_minor: number
  committed_minor: number
  status: string
  rationale: string
}

export interface VendorCandidate {
  vendor_id: string
  canonical_name: string
  status: string
  similarity: number
  matched_on: string
}

export interface DecisionPayload {
  outcome?: {
    decision: Decision
    headline: string
    explanation: string
    primary_reasons: string[]
    next_actions: string[]
  }
  facts?: Record<string, unknown>
  po_comparison?: PoComparison
  vendor_resolution?: {
    confident: boolean
    matched_on: string | null
    reason: string
    vendor: { id: string; canonical_name: string; status: string } | null
    candidates: VendorCandidate[]
  }
  po_resolution?: {
    source: string
    reason: string
    reference_found: boolean
    reference_raw: string | null
    purchase_order: { id: string; po_number: string } | null
    candidates: PoCandidate[]
  }
  field_quality?: FieldQuality[]
  reservation_id?: string | null
  duplicate_file?: { case_id: string; run_id: string; detail: string } | null
  duplicate_identity?: { case_id: string; run_id: string; detail: string } | null
}

export interface ReviewActionView {
  id: string
  actor_label: string
  reason: string
  old_values: Record<string, unknown>
  proposed_values: Record<string, unknown>
  source_run_id: string
  derived_run_id: string | null
  timestamp: string
}

export interface RunDetail extends RunSummary {
  version: number
  policy_version: string
  overrides: Record<string, unknown> | null
  extraction_warnings: string[]
  document: {
    id: string
    filename: string
    sha256: string
    byte_count: number
    page_count: number
  } | null
  extraction: { invoice?: ExtractedInvoice; metadata?: ExtractionMetadata }
  page_count: number
  page_provenance: Provenance[]
  events: WorkflowEventView[]
  stages: StageView[]
  rule_results: RuleResult[]
  decision_payload: DecisionPayload
  policy_snapshot: Record<string, unknown> | null
  reference_snapshot: Record<string, unknown> | null
  reservation: {
    id: string
    amount: Money
    purchase_order_id: string
    active: boolean
    created_at: string
  } | null
  case_runs: RunSummary[]
  review_actions: ReviewActionView[]
  last_event_sequence: number
  is_terminal: boolean
}

export interface EventsResponse {
  run_id: string
  execution_status: ExecutionStatus
  decision: Decision | null
  is_terminal: boolean
  events: WorkflowEventView[]
  last_sequence: number
}

export interface SampleEntry {
  id: string
  filename: string
  scenario: string
  title: string
  summary: string
  order: number
  layout: string
  scanned: boolean
  preconditions: string[]
  vendor_printed_name: string
  invoice_number: string | null
  printed_po_number: string | null
  printed_total: string
  note: string
}

export interface SampleCatalog {
  disclaimer: string
  generated_from_reference_date: string
  scenarios: { scenario: string; samples: SampleEntry[] }[]
  samples: SampleEntry[]
}

export interface Capabilities {
  app_version: string
  environment: string
  demo_mode: boolean
  demo_notice: string
  extraction: {
    configured_provider: string
    configured_provider_label: string
    allow_rules_fallback: boolean
    providers: Record<
      string,
      {
        available: boolean
        detail: string
        model: string | null
        setup_command: string | null
        active: boolean
      }
    >
  }
  ocr: {
    available: boolean
    engine: string | null
    languages: string[]
    detail: string
    render_dpi: number
    timeout_seconds: number
  }
  limits: {
    max_upload_mb: number
    max_pdf_pages: number
    max_queue_depth: number
    queue_depth: number
  }
  auth: { reviewer_login_required: boolean }
}

export interface Metric {
  value: number | string | null
  definition: string
  numerator?: number
  denominator?: number
}

export interface DashboardResponse {
  metrics: Record<string, Metric>
  decision_breakdown: Record<Decision, number>
  cases_with_human_correction: number
  recent_runs: RunSummary[]
}

export interface VendorRow {
  id: string
  vendor_code: string | null
  canonical_name: string
  normalized_name: string
  aliases: string[]
  status: 'approved' | 'blocked'
  supported_currency: string
}

export interface PurchaseOrderRow {
  id: string
  po_number: string
  vendor_id: string
  vendor_name: string
  vendor_status: string
  currency: string
  status: string
  description: string | null
  approved_total: string
  approved_total_minor: number
  committed: string
  committed_minor: number
  available_nominal: string
  tolerance: string
  available_including_tolerance: string
  allowed_total_including_tolerance: string
}

export interface PolicyResponse {
  version: string
  values: Record<string, unknown>
  plain_language: { title: string; detail: string }[]
  boundaries: string[]
}

export interface RunListResponse {
  items: RunSummary[]
  page: number
  page_size: number
  total: number
  total_pages: number
}
