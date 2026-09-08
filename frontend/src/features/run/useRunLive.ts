import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { ExecutionStatus, RunDetail, StageView, WorkflowEventView } from '../../types/api'

const POLL_INTERVAL_MS = 850
const TERMINAL: ExecutionStatus[] = ['COMPLETED', 'FAILED', 'INTERRUPTED']

export const STAGES: [string, string][] = [
  ['intake', 'Intake'],
  ['read_document', 'Read document'],
  ['extract_fields', 'Extract fields'],
  ['validate_facts', 'Validate facts'],
  ['match_references', 'Match references'],
  ['evaluate_policy', 'Evaluate policy'],
  ['commit_decision', 'Commit decision'],
  ['publish_output', 'Publish output'],
]

/** Fold the event log into one row per stage, mirroring the server's view. */
export function foldStages(events: WorkflowEventView[]): StageView[] {
  const byStage = new Map<string, StageView>(
    STAGES.map(([key, label]) => [
      key,
      {
        stage_key: key,
        stage_label: label,
        status: 'pending',
        started_at: null,
        completed_at: null,
        elapsed_ms: null,
        messages: [],
        warnings: [],
        errors: [],
      },
    ]),
  )
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    const stage = byStage.get(event.stage_key)
    if (!stage) continue
    if (event.event_type === 'started') {
      stage.status = 'running'
      stage.started_at = event.timestamp
    } else if (event.event_type === 'completed') {
      stage.status = 'completed'
      stage.completed_at = event.timestamp
      stage.messages.push(event.short_message)
      const elapsed = event.structured_metadata?.elapsed_ms
      if (typeof elapsed === 'number') stage.elapsed_ms = elapsed
    } else if (event.event_type === 'failed') {
      stage.status = 'failed'
      stage.completed_at = event.timestamp
      stage.errors.push(event.short_message)
    } else if (event.event_type === 'warning') {
      stage.warnings.push(event.short_message)
    } else if (event.event_type === 'skipped') {
      stage.status = 'skipped'
      stage.messages.push(event.short_message)
    }
  }
  return STAGES.map(([key]) => byStage.get(key)!)
}

/**
 * Poll incremental events while a run is active, then fetch one final snapshot.
 *
 * Polling resumes from the last sequence we hold, so a browser refresh or a
 * dropped connection reconstructs the same run rather than losing progress. A
 * fast run that finishes between two polls is not missed: the terminal snapshot
 * is always fetched before polling stops.
 */
export function useRunLive(runId: string) {
  const queryClient = useQueryClient()
  const runQuery = useQuery<RunDetail>({
    queryKey: ['run', runId],
    queryFn: () => api.run(runId),
  })

  const [liveEvents, setLiveEvents] = useState<WorkflowEventView[]>([])
  const [liveStatus, setLiveStatus] = useState<ExecutionStatus | null>(null)
  const [trackedRunId, setTrackedRunId] = useState(runId)
  const afterRef = useRef(0)

  // Navigating from one run to another reuses this hook, so the previous run's
  // events have to be dropped before they are rendered against the new id.
  // Adjusting during render avoids a frame showing the wrong run's progress.
  if (trackedRunId !== runId) {
    setTrackedRunId(runId)
    setLiveEvents([])
    setLiveStatus(null)
  }

  // The cursor is reset in its own effect, declared before the polling effect so
  // it runs first: carrying a higher sequence over from the previous run would
  // silently skip the new run's early events.
  useEffect(() => {
    afterRef.current = 0
  }, [runId])

  const detail = runQuery.data
  const serverTerminal = detail ? TERMINAL.includes(detail.execution_status) : false
  const liveTerminal = liveStatus ? TERMINAL.includes(liveStatus) : false
  const active = Boolean(detail) && !serverTerminal && !liveTerminal

  useEffect(() => {
    if (!detail || !active) return
    afterRef.current = Math.max(afterRef.current, detail.last_event_sequence)
    let cancelled = false

    const tick = async () => {
      if (cancelled) return
      try {
        const payload = await api.runEvents(runId, afterRef.current)
        if (cancelled) return
        if (payload.events.length > 0) {
          afterRef.current = payload.last_sequence
          setLiveEvents((previous) => {
            const merged = new Map(previous.map((event) => [event.sequence, event]))
            payload.events.forEach((event) => merged.set(event.sequence, event))
            return [...merged.values()].sort((a, b) => a.sequence - b.sequence)
          })
        }
        if (payload.is_terminal) {
          setLiveStatus(payload.execution_status)
          await queryClient.invalidateQueries({ queryKey: ['run', runId] })
          await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
          return
        }
      } catch {
        // A transient network failure must not end the run view; the next tick
        // resumes from the same sequence.
      }
      if (!cancelled) timer = window.setTimeout(tick, POLL_INTERVAL_MS)
    }

    let timer = window.setTimeout(tick, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [runId, detail, active, queryClient])

  const events = useMemo(() => {
    const merged = new Map<number, WorkflowEventView>()
    detail?.events.forEach((event) => merged.set(event.sequence, event))
    liveEvents.forEach((event) => merged.set(event.sequence, event))
    return [...merged.values()].sort((a, b) => a.sequence - b.sequence)
  }, [detail, liveEvents])

  const stages = useMemo(() => foldStages(events), [events])

  return {
    runQuery,
    detail,
    events,
    stages,
    isLive: active,
    polling: active,
  }
}
