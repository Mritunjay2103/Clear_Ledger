"""An application-managed queue with a single worker.

One worker is deliberate: the prototype runs on SQLite in one process, and a
single serialised worker keeps the reservation ledger easy to reason about. The
worker task is owned and awaited on shutdown rather than fired and forgotten,
and blocking PDF/OCR work runs in a thread so the event loop keeps serving
requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import settings
from app.db.models import WorkflowEvent, WorkflowRun
from app.db.session import session_scope
from app.services.workflow import execute_run

logger = logging.getLogger("clearledger.queue")


class QueueFull(Exception):
    pass


class RunQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] | None = None
        self._worker: asyncio.Task | None = None
        self._stopping = False
        self._active_run_id: str | None = None

    @property
    def depth(self) -> int:
        return self._queue.qsize() if self._queue else 0

    @property
    def capacity(self) -> int:
        return settings.max_queue_depth

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    async def start(self) -> None:
        self._stopping = False
        self._queue = asyncio.Queue(maxsize=settings.max_queue_depth)
        self._worker = asyncio.create_task(self._run_worker(), name="clearledger-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    def enqueue(self, run_id: str) -> None:
        if self._queue is None:
            raise QueueFull("The processing queue is not running.")
        try:
            self._queue.put_nowait(run_id)
        except asyncio.QueueFull as exc:
            raise QueueFull(
                f"The processing queue is full ({settings.max_queue_depth} waiting). "
                "Try again shortly."
            ) from exc

    async def _run_worker(self) -> None:
        assert self._queue is not None
        while not self._stopping:
            run_id = await self._queue.get()
            self._active_run_id = run_id
            try:
                await asyncio.to_thread(execute_run, run_id)
            except asyncio.CancelledError:
                _mark_interrupted(run_id, "The server shut down while this run was executing.")
                raise
            except Exception:
                logger.exception("worker failed on run %s", run_id)
            finally:
                self._active_run_id = None
                self._queue.task_done()


run_queue = RunQueue()


def _mark_interrupted(run_id: str, reason: str) -> None:
    with session_scope() as session:
        run = session.get(WorkflowRun, run_id)
        if run is None or run.execution_status not in {"QUEUED", "RUNNING"}:
            return
        run.execution_status = "INTERRUPTED"
        run.error_code = "run_interrupted"
        run.error_message = reason
        run.completed_at = datetime.now(UTC)
        run.version += 1
        _append_event(session, run_id, "publish_output", "failed", reason)


def _append_event(session, run_id: str, stage_key: str, event_type: str, message: str) -> None:
    from sqlalchemy import func

    sequence = (
        session.scalar(
            select(func.max(WorkflowEvent.sequence)).where(WorkflowEvent.run_id == run_id)
        )
        or 0
    ) + 1
    session.add(
        WorkflowEvent(
            run_id=run_id,
            sequence=sequence,
            stage_key=stage_key,
            event_type=event_type,
            timestamp=datetime.now(UTC),
            short_message=message,
            structured_metadata={"error_code": "run_interrupted", "retryable": True},
        )
    )


def recover_on_startup() -> dict[str, int]:
    """Mark stale RUNNING work interrupted and re-queue work that never started."""
    requeued: list[str] = []
    interrupted = 0
    with session_scope() as session:
        stale = list(
            session.scalars(select(WorkflowRun).where(WorkflowRun.execution_status == "RUNNING"))
        )
        for run in stale:
            run.execution_status = "INTERRUPTED"
            run.error_code = "run_interrupted"
            run.error_message = (
                "The server restarted while this run was executing. Retry to create a linked run."
            )
            run.completed_at = datetime.now(UTC)
            run.version += 1
            _append_event(
                session,
                run.id,
                "publish_output",
                "failed",
                "The server restarted while this run was executing.",
            )
            interrupted += 1

        queued = list(
            session.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.execution_status == "QUEUED")
                .order_by(WorkflowRun.created_at)
            )
        )
        requeued = [run.id for run in queued]

    for run_id in requeued:
        try:
            run_queue.enqueue(run_id)
        except QueueFull:
            logger.warning("could not requeue run %s: queue full", run_id)
            break
    return {"interrupted": interrupted, "requeued": len(requeued)}
