"""ClearLedger maintenance commands.

    python scripts/manage.py validate-reference
    python scripts/manage.py seed
    python scripts/manage.py migrate
    python scripts/manage.py capabilities
    python scripts/manage.py reset-demo --confirm RESET

Cross-platform on purpose: the same commands work in PowerShell and in a POSIX
shell, so nothing essential is hidden in a Makefile.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.config import settings
from app.db.session import create_all, session_scope
from app.services.seed import seed_reference_data, validate_reference_data


def cmd_validate_reference(_args) -> int:
    report = validate_reference_data()
    if report.ok:
        print(
            f"Reference data is valid: {report.vendor_count} vendor(s), "
            f"{report.purchase_order_count} purchase order(s)."
        )
        return 0
    print("Reference data is invalid:")
    for error in report.errors:
        print(f"  - {error}")
    return 1


def cmd_migrate(_args) -> int:
    settings.ensure_directories()
    create_all()
    print(f"Schema is up to date at {settings.resolved_data_dir / 'clearledger.db'}")
    return 0


def cmd_seed(_args) -> int:
    settings.ensure_directories()
    create_all()
    with session_scope() as session:
        summary = seed_reference_data(session)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_capabilities(_args) -> int:
    from app.services import ocr as ocr_service
    from app.services.extraction import provider_availability

    ocr_status = ocr_service.probe()
    payload = {
        "environment": settings.app_env,
        "data_dir": str(settings.resolved_data_dir),
        "extraction_provider": settings.extraction_provider,
        "allow_rules_fallback": settings.allow_rules_fallback,
        "ocr": {
            "available": ocr_status.available,
            "engine": ocr_status.version,
            "languages": ocr_status.languages,
            "detail": ocr_status.detail,
        },
        "providers": {
            name: {
                "available": availability.available,
                "detail": availability.detail,
                "model": availability.model,
                "setup_command": availability.setup_command,
            }
            for name, availability in (
                (candidate, provider_availability(candidate))
                for candidate in ("rules", "ollama", "openai_compatible")
            )
        },
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_reset_demo(args) -> int:
    """Owner-only destructive reset, limited to the configured data directory."""
    if args.confirm != "RESET":
        print(
            "Refusing to reset. Re-run with --confirm RESET to erase the demo database, "
            f"uploads and temporary files under {settings.resolved_data_dir}."
        )
        return 1
    target = settings.resolved_data_dir
    if not target.exists():
        print(f"Nothing to reset: {target} does not exist.")
        return 0
    try:
        for child in target.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    except PermissionError as exc:
        print(
            f"Cannot reset while a process is holding {exc.filename}. Stop the running "
            "ClearLedger server (or container) and run this command again."
        )
        return 1
    settings.ensure_directories()
    create_all()
    with session_scope() as session:
        seed_reference_data(session)
    print(f"Demo data under {target} was erased and reference data reseeded.")
    return 0


COMMANDS = {
    "validate-reference": cmd_validate_reference,
    "migrate": cmd_migrate,
    "seed": cmd_seed,
    "capabilities": cmd_capabilities,
    "reset-demo": cmd_reset_demo,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="ClearLedger maintenance commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        sub = subparsers.add_parser(name)
        if name == "reset-demo":
            sub.add_argument("--confirm", default="")
    args = parser.parse_args()
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
