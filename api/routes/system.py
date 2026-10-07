"""System endpoints: liveness, readiness and Prometheus metrics."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from api.dependencies import get_app_settings, get_ready_checks
from api.readiness import CheckStatus, DependencyCheck, run_checks
from shared.config import Settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, CheckStatus]


@router.get("/health")
async def health() -> HealthResponse:
    """Liveness: the process is running. It does not call any other service."""
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyResponse}},
)
async def ready(
    response: Response,
    checks: Annotated[list[DependencyCheck], Depends(get_ready_checks)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ReadyResponse:
    """Readiness: every service the API needs is reachable. Returns 503 if one is not."""
    results = await run_checks(checks, settings.ready_check_timeout_seconds)
    all_ok = all(result == "ok" for result in results.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ok" if all_ok else "unavailable", checks=results)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Metrics in Prometheus text format. Phase 6 adds request and pipeline metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
