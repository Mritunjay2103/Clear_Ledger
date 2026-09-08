"""Access control, and the checks that keep a demo from being abused."""

from __future__ import annotations

import base64

import pytest

from app.api.security import StartupConfigurationError, validate_startup_configuration
from app.config import settings


def _auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_a_mutation_without_the_request_header_is_refused(client, samples_dir) -> None:
    # A cross-site form can resend Basic credentials but cannot set this header
    # without triggering a preflight the server will not allow.
    path = samples_dir.path("happy-path")
    with path.open("rb") as handle:
        response = client.post(
            "/api/runs",
            files={"file": (path.name, handle, "application/pdf")},
            headers={"X-ClearLedger-Request": ""},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "missing_request_header"


def test_a_mutation_from_an_unknown_origin_is_refused(client, samples_dir) -> None:
    path = samples_dir.path("happy-path")
    with path.open("rb") as handle:
        response = client.post(
            "/api/runs",
            files={"file": (path.name, handle, "application/pdf")},
            headers={"Origin": "https://not-our-app.example"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


def test_reading_is_unaffected_by_the_mutation_checks(client) -> None:
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/capabilities").status_code == 200


def test_when_credentials_are_configured_reads_require_them(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_username", "reviewer")
    monkeypatch.setattr(settings, "demo_password", "a-long-enough-password")

    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs", headers=_auth("reviewer", "wrong")).status_code == 401
    ok = client.get("/api/runs", headers=_auth("reviewer", "a-long-enough-password"))
    assert ok.status_code == 200


def test_a_rejected_request_tells_the_browser_to_prompt(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_username", "reviewer")
    monkeypatch.setattr(settings, "demo_password", "a-long-enough-password")

    response = client.get("/api/runs")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


def test_health_stays_reachable_so_a_platform_can_probe_it(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_username", "reviewer")
    monkeypatch.setattr(settings, "demo_password", "a-long-enough-password")

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/readiness").status_code in (200, 503)


def test_production_refuses_to_start_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "demo_username", "")
    monkeypatch.setattr(settings, "demo_password", "")

    with pytest.raises(StartupConfigurationError):
        validate_startup_configuration()


def test_a_short_password_is_refused_rather_than_silently_accepted(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "demo_username", "reviewer")
    monkeypatch.setattr(settings, "demo_password", "short")

    with pytest.raises(StartupConfigurationError):
        validate_startup_configuration()


def test_repeated_mutations_are_rate_limited(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    codes = [client.post("/api/runs").status_code for _ in range(5)]

    assert codes[:3] == [422, 422, 422]  # rejected on content, but counted
    assert 429 in codes
    body = client.post("/api/runs").json()
    assert body["error"]["retryable"] is True


def test_an_oversized_upload_is_refused_by_streaming_not_after_buffering(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    oversized = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)

    response = client.post(
        "/api/runs",
        files={"file": ("big.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 413
    assert "1" in response.json()["error"]["message"]


def test_a_filename_cannot_escape_the_upload_directory(client) -> None:
    from app.services.documents import sanitize_filename

    for hostile in (
        "../../../../etc/passwd",
        "..\\..\\windows\\system32\\cmd.exe",
        "/absolute/path/invoice.pdf",
        "in\x00jected.pdf",
    ):
        cleaned = sanitize_filename(hostile)
        assert "/" not in cleaned
        assert "\\" not in cleaned
        assert ".." not in cleaned
        assert "\x00" not in cleaned


def test_a_sample_id_cannot_be_used_to_read_arbitrary_files(client) -> None:
    for hostile in ("../../../etc/passwd", "..%2f..%2fsecrets", "happy-path/../../"):
        response = client.get(f"/api/samples/{hostile}/file")
        assert response.status_code in (400, 404), hostile
