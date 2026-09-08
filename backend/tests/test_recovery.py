"""What a crash does.

A process can die between any two stages. The invariant is that no run is left
claiming to be executing when nothing is executing it, and that work which never
started is not lost. Neither may quietly turn into a business decision.
"""

from __future__ import annotations

from app.db.models import InvoiceCase, InvoiceDocument, WorkflowEvent, WorkflowRun
from app.services import queue as queue_module


def _run(session, status: str, *, sha: str) -> WorkflowRun:
    document = InvoiceDocument(
        sha256=sha,
        storage_key=f"{sha}.pdf",
        safe_original_filename="invoice.pdf",
        byte_count=1024,
        page_count=1,
    )
    session.add(document)
    session.flush()
    case = InvoiceCase(original_document_id=document.id)
    session.add(case)
    session.flush()
    run = WorkflowRun(
        case_id=case.id,
        document_id=document.id,
        trigger="upload",
        policy_version="v1",
        execution_status=status,
    )
    session.add(run)
    session.commit()
    return run


def test_a_run_left_executing_by_a_dead_process_is_marked_interrupted(session) -> None:
    # No other process can be executing it: this process is the only worker, and
    # it has just started. So RUNNING here means the previous process died.
    stale = _run(session, "RUNNING", sha="a" * 64)

    result = queue_module.recover_on_startup()
    session.expire_all()

    assert result["interrupted"] == 1
    assert stale.execution_status == "INTERRUPTED"
    assert stale.error_code == "run_interrupted"
    # Crucially, not a decision. Nothing was concluded about the invoice.
    assert stale.decision is None


def test_the_interruption_is_written_to_the_event_log_a_reviewer_reads(session) -> None:
    stale = _run(session, "RUNNING", sha="b" * 64)

    queue_module.recover_on_startup()
    session.expire_all()

    events = session.query(WorkflowEvent).filter(WorkflowEvent.run_id == stale.id).all()
    assert [event.event_type for event in events] == ["failed"]
    assert "restarted" in events[0].short_message
    # Retryable, because the failure was the server's, not the document's.
    assert events[0].structured_metadata["retryable"] is True


def test_work_that_never_started_is_requeued_rather_than_abandoned(session, monkeypatch) -> None:
    queued = [_run(session, "QUEUED", sha=f"{index:064d}") for index in range(3)]
    enqueued: list[str] = []
    monkeypatch.setattr(queue_module.run_queue, "enqueue", enqueued.append)

    result = queue_module.recover_on_startup()
    session.expire_all()

    assert result["requeued"] == 3
    # Oldest first: a queue that reorders itself on restart is a queue that
    # surprises whoever was waiting.
    assert enqueued == [run.id for run in queued]
    # Still QUEUED, not marked interrupted: they are about to run for real.
    assert all(run.execution_status == "QUEUED" for run in queued)


def test_a_completed_run_is_untouched_by_recovery(session, monkeypatch) -> None:
    done = _run(session, "COMPLETED", sha="c" * 64)
    done.decision = "APPROVED"
    session.commit()
    monkeypatch.setattr(queue_module.run_queue, "enqueue", lambda _run_id: None)

    result = queue_module.recover_on_startup()
    session.expire_all()

    assert result == {"interrupted": 0, "requeued": 0}
    assert done.execution_status == "COMPLETED"
    assert done.decision == "APPROVED"


def test_an_interrupted_run_can_be_retried_and_history_survives(client, samples_dir) -> None:
    """End to end: the decision and its events reload after the run is done."""
    with open(samples_dir.path("happy-path"), "rb") as handle:
        created = client.post(
            "/api/runs",
            files={"file": ("happy-saffron-inv-1001.pdf", handle, "application/pdf")},
        )
    assert created.status_code in (200, 202), created.text
    run_id = created.json()["run"]["id"]

    first = client.get(f"/api/runs/{run_id}").json()
    second = client.get(f"/api/runs/{run_id}").json()

    assert first["decision"] == "APPROVED"
    assert second["decision"] == first["decision"]
    assert second["last_event_sequence"] == first["last_event_sequence"]
    # All eight stages are on the record, not just the final answer.
    stages = {event["stage_key"] for event in second["events"]}
    assert stages == {
        "intake",
        "read_document",
        "extract_fields",
        "validate_facts",
        "match_references",
        "evaluate_policy",
        "commit_decision",
        "publish_output",
    }
