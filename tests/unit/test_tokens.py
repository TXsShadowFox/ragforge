"""Tests for login tokens (JWT): signature, expiry and common attacks."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from api.auth.tokens import create_access_token, read_access_token

SECRET = SecretStr("unit-test-secret-that-is-at-least-32-characters")
OTHER_SECRET = SecretStr("another-secret-that-is-also-32-characters-long")


def test_a_token_gives_back_its_user_id() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, SECRET, timedelta(minutes=5))

    assert read_access_token(token, SECRET) == user_id


def test_an_expired_token_is_rejected() -> None:
    token = create_access_token(uuid.uuid4(), SECRET, timedelta(seconds=-1))

    with pytest.raises(jwt.ExpiredSignatureError):
        read_access_token(token, SECRET)


def test_a_token_signed_with_another_secret_is_rejected() -> None:
    token = create_access_token(uuid.uuid4(), OTHER_SECRET, timedelta(minutes=5))

    with pytest.raises(jwt.InvalidSignatureError):
        read_access_token(token, SECRET)


def test_an_unsigned_token_is_rejected() -> None:
    # The classic JWT attack: "alg": "none" and no signature at all.
    now = datetime.now(UTC)
    payload = {"sub": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=5)}
    token = jwt.encode(payload, key=None, algorithm="none")

    with pytest.raises(jwt.InvalidTokenError):
        read_access_token(token, SECRET)


def test_a_token_without_an_expiry_time_is_rejected() -> None:
    payload = {"sub": str(uuid.uuid4()), "iat": datetime.now(UTC)}
    token = jwt.encode(payload, SECRET.get_secret_value(), algorithm="HS256")

    with pytest.raises(jwt.MissingRequiredClaimError):
        read_access_token(token, SECRET)


def test_the_subject_must_be_a_user_id() -> None:
    now = datetime.now(UTC)
    payload = {"sub": "admin", "iat": now, "exp": now + timedelta(minutes=5)}
    token = jwt.encode(payload, SECRET.get_secret_value(), algorithm="HS256")

    with pytest.raises(jwt.InvalidTokenError):
        read_access_token(token, SECRET)
