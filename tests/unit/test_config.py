"""Tests for the settings and for `.env.example`, the template of `.env`."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

SETTING_KEYS = {name.upper() for name in Settings.model_fields}

# Keys in .env.example that only Docker Compose uses, to set up the containers.
COMPOSE_ONLY_KEYS = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "RABBITMQ_USER",
    "RABBITMQ_PASSWORD",
    "GRAFANA_ADMIN_USER",
    "GRAFANA_ADMIN_PASSWORD",
}


def _example_keys() -> set[str]:
    lines = (line.strip() for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines())
    return {line.split("=", 1)[0] for line in lines if line and not line.startswith("#")}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove these keys from the real environment, so only the test decides them."""
    for key in SETTING_KEYS | COMPOSE_ONLY_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_env_example_lists_every_setting() -> None:
    assert SETTING_KEYS - _example_keys() == set()


def test_env_example_has_no_unknown_keys() -> None:
    assert _example_keys() - SETTING_KEYS - COMPOSE_ONLY_KEYS == set()


@pytest.mark.usefixtures("clean_env")
def test_env_example_is_valid_and_fills_in_passwords() -> None:
    settings = Settings(_env_file=ENV_EXAMPLE)

    # ${POSTGRES_USER} and friends are filled in from the same file.
    assert str(settings.database_url) == (
        "postgresql+asyncpg://ragforge:change-me-postgres@localhost:5432/ragforge"
    )
    assert str(settings.rabbitmq_url) == "amqp://ragforge:change-me-rabbitmq@localhost:5672/"
    assert settings.qdrant_api_key is None  # an empty value means "not set"


def test_secrets_only_come_from_the_environment() -> None:
    # A default value would be a password in the code (and in git). Secrets are required,
    # or empty ("not set") when they are optional, like a local Ollama's missing API key.
    secrets = {
        name: field
        for name, field in Settings.model_fields.items()
        if "SecretStr" in str(field.annotation)
    }
    assert {"jwt_secret", "s3_secret_key", "llm_api_key", "qdrant_api_key"} <= set(secrets)
    for name, field in secrets.items():
        assert field.is_required() or field.default is None, name
    for url in ("database_url", "redis_url", "rabbitmq_url"):  # they hold passwords too
        assert Settings.model_fields[url].is_required(), url


@pytest.mark.usefixtures("clean_env")
def test_missing_secret_stops_the_app() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


@pytest.mark.usefixtures("clean_env")
def test_environment_variables_win_over_the_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    assert Settings(_env_file=ENV_EXAMPLE).log_level == "DEBUG"


@pytest.mark.usefixtures("clean_env")
def test_a_short_jwt_secret_stops_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    # HS256 needs a key of at least 32 bytes; a short one is easy to guess.
    monkeypatch.setenv("JWT_SECRET", "too-short")

    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(_env_file=ENV_EXAMPLE)
