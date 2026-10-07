"""Tests for API key generation, hashing and allowed origins."""

import hashlib
import re

import pytest

from api.auth.keys import generate_api_key, hash_api_key, is_api_key, normalize_origin
from shared.db.models import ApiKeyKind


def test_a_secret_key_has_the_live_prefix_and_40_random_characters() -> None:
    generated = generate_api_key(ApiKeyKind.SECRET)

    assert re.fullmatch(r"rf_live_[A-Za-z0-9]{40}", generated.key)
    assert generated.display_prefix == generated.key[:12]


def test_a_public_key_has_its_own_prefix() -> None:
    generated = generate_api_key(ApiKeyKind.PUBLIC)

    assert re.fullmatch(r"rf_pub_[A-Za-z0-9]{40}", generated.key)


def test_only_the_sha256_hash_is_kept() -> None:
    generated = generate_api_key(ApiKeyKind.SECRET)

    assert generated.key_hash == hashlib.sha256(generated.key.encode()).hexdigest()
    assert generated.key_hash == hash_api_key(generated.key)


def test_keys_are_random() -> None:
    keys = {generate_api_key(ApiKeyKind.SECRET).key for _ in range(200)}

    assert len(keys) == 200


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("rf_live_abc", True),
        ("rf_pub_abc", True),
        ("eyJhbGciOiJIUzI1NiJ9.e30.sig", False),  # a JWT
        ("sk_live_abc", False),
    ],
)
def test_api_keys_are_told_apart_from_login_tokens(token: str, expected: bool) -> None:
    assert is_api_key(token) is expected


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://example.com", "https://example.com"),
        ("https://Example.COM/", "https://example.com"),
        ("https://example.com:443", "https://example.com"),
        ("http://example.com:80", "http://example.com"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("  https://shop.example.com  ", "https://shop.example.com"),
    ],
)
def test_origins_are_written_the_way_browsers_send_them(origin: str, expected: str) -> None:
    assert normalize_origin(origin) == expected


@pytest.mark.parametrize(
    "origin",
    [
        "example.com",  # no scheme
        "ftp://example.com",  # not http(s)
        "https://example.com/page",  # a path
        "https://example.com/?a=1",  # a query
        "https://user:pass@example.com",  # a login
        "https://example.com:99999",  # not a port
        "https://",  # no host
    ],
)
def test_bad_origins_are_rejected(origin: str) -> None:
    with pytest.raises(ValueError, match=r"."):
        normalize_origin(origin)
