"""System health/version endpoints (Task 13.2, Requirement 18.5). Public --
no authorizer, matching the Task 3.7 feasibility gate's proven pattern of
a public route coexisting with protected ones without leaking anything.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from xc_platform.api.context import AppContext
from xc_platform.api.deps import get_context

router = APIRouter(prefix="/api/v1/system", tags=["system"])

SCHEMA_VERSION = 3  # migrations 0001-0003


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/version")
def version(
    request: Request, ctx: AppContext = Depends(get_context)
) -> dict[str, object]:
    active = ctx.snapshot_reader.current()
    return {
        "request_id": request.state.request_id,
        "schema_version": SCHEMA_VERSION,
        "publication_id": active.manifest.publication_id,
        "published_at": active.manifest.created_at,
    }
