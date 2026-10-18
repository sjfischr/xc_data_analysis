"""System health/version endpoints (Task 13.2, Requirement 18.5). Public --
no authorizer, matching the Task 3.7 feasibility gate's proven pattern of
a public route coexisting with protected ones without leaking anything.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from xc_platform.api.context import AppContext
from xc_platform.api.deps import get_context
from xc_platform.db.migrator import latest_schema_version

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/version")
def version(
    request: Request, ctx: AppContext = Depends(get_context)
) -> dict[str, object]:
    """``schema_version`` is the live data's; ``supported_schema_version``
    is the newest this code can read. A publish must not exceed the latter
    (models.py refuses it). Until 2026-10-18 the first was a hardcoded 3
    that stayed 3 after migration 0004 shipped."""
    active = ctx.snapshot_reader.current()
    return {
        "request_id": request.state.request_id,
        "schema_version": active.manifest.schema_version,
        "supported_schema_version": latest_schema_version(),
        "publication_id": active.manifest.publication_id,
        "published_at": active.manifest.created_at,
    }
