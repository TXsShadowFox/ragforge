"""FastAPI dependencies: shared objects that routes receive as parameters.

Tests replace them with `app.dependency_overrides`.
"""

from fastapi import Request

from api.readiness import DependencyCheck
from shared.config import Settings


def get_app_settings(request: Request) -> Settings:
    """The settings the app was created with."""
    settings: Settings = request.app.state.settings
    return settings


def get_ready_checks(request: Request) -> list[DependencyCheck]:
    """The readiness probes, built at startup (see `api.main.lifespan`)."""
    checks: list[DependencyCheck] = request.app.state.ready_checks
    return checks
