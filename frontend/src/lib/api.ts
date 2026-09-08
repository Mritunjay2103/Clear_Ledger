import type {
  Capabilities,
  DashboardResponse,
  EventsResponse,
  PolicyResponse,
  PurchaseOrderRow,
  RunDetail,
  RunListResponse,
  RunSummary,
  SampleCatalog,
  VendorRow,
} from '../types/api'

/** Every mutation carries this header; a cross-site form cannot set it. */
const REQUEST_HEADER = 'X-ClearLedger-Request'

export class ApiError extends Error {
  code: string
  detail: string | null
  status: number
  retryable: boolean

  constructor(status: number, code: string, message: string, detail?: string | null, retryable = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail ?? null
    this.retryable = retryable
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = 'request_failed'
  let message = `Request failed with status ${response.status}.`
  let detail: string | null = null
  let retryable = false
  try {
    const body = await response.json()
    if (body?.error) {
      code = body.error.code ?? code
      message = body.error.message ?? message
      detail = body.error.detail ?? null
      retryable = Boolean(body.error.retryable)
    }
  } catch {
    // A non-JSON body means the server or a proxy failed before our handlers.
  }
  return new ApiError(response.status, code, message, detail, retryable)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (method !== 'GET' && method !== 'HEAD') {
    headers.set(REQUEST_HEADER, '1')
    if (!headers.has('Idempotency-Key')) {
      headers.set('Idempotency-Key', crypto.randomUUID())
    }
  }
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  capabilities: () => request<Capabilities>('/api/capabilities'),
  policy: () => request<PolicyResponse>('/api/policy'),
  dashboard: () => request<DashboardResponse>('/api/dashboard'),
  samples: () => request<SampleCatalog>('/api/samples'),
  sampleFileUrl: (id: string) => `/api/samples/${encodeURIComponent(id)}/file`,

  vendors: () => request<{ items: VendorRow[]; note: string; source: string }>('/api/vendors'),
  purchaseOrders: () =>
    request<{ items: PurchaseOrderRow[]; balance_note: string; source: string }>(
      '/api/purchase-orders',
    ),

  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  runEvents: (id: string, after: number) =>
    request<EventsResponse>(`/api/runs/${id}/events?after=${after}`),
  documentUrl: (id: string) => `/api/runs/${id}/document`,
  exportJsonUrl: (id: string) => `/api/runs/${id}/export.json`,
  exportCsvUrl: (id: string) => `/api/runs/${id}/export.csv`,

  listRuns: (params: Record<string, string | number | boolean | undefined>) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '' && value !== false) query.set(key, String(value))
    })
    return request<RunListResponse>(`/api/runs?${query.toString()}`)
  },

  uploadFile: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<{ run: RunSummary; idempotent_replay: boolean }>('/api/runs', {
      method: 'POST',
      body,
    })
  },

  /** Samples are submitted as real bytes through the same upload endpoint. */
  runSample: async (sampleId: string, filename: string) => {
    const response = await fetch(api.sampleFileUrl(sampleId), { credentials: 'same-origin' })
    if (!response.ok) throw await toApiError(response)
    const blob = await response.blob()
    const file = new File([blob], filename, { type: 'application/pdf' })
    return api.uploadFile(file)
  },

  retry: (id: string) =>
    request<{ run: RunSummary }>(`/api/runs/${id}/retry`, { method: 'POST' }),

  correct: (
    id: string,
    payload: {
      actor_label: string
      reason: string
      expected_version: number
      fields?: Record<string, string>
      selected_po_id?: string | null
      selected_vendor_id?: string | null
    },
  ) =>
    request<{ run: RunSummary; review_action_id: string }>(`/api/runs/${id}/corrections`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
}
