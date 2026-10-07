"""API keys: long random secrets that programs send to call the API.

Two kinds:
- secret keys, `rf_live_...`, for servers (never put one in a web page)
- public keys, `rf_pub_...`, for the chat widget (Phase 5), limited to some websites

We store only the SHA-256 hash of a key. Keys are long and random, so a fast hash is safe
(passwords need a slow hash because people choose weak ones), and with the hash we can find
the key in one indexed lookup.
"""

import hashlib
import secrets
import string
from dataclasses import dataclass
from urllib.parse import urlsplit

from shared.db.models import ApiKeyKind

LIVE_KEY_PREFIX = "rf_live_"
PUBLIC_KEY_PREFIX = "rf_pub_"
KEY_RANDOM_LENGTH = 40  # 40 letters and digits: about 238 random bits
DISPLAY_PREFIX_LENGTH = 12  # we keep the first 12 characters, so users can tell keys apart

_ALPHABET = string.ascii_letters + string.digits
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    key: str  # the full key: shown to the user once, never stored
    display_prefix: str
    key_hash: str


def generate_api_key(kind: ApiKeyKind) -> GeneratedKey:
    prefix = PUBLIC_KEY_PREFIX if kind is ApiKeyKind.PUBLIC else LIVE_KEY_PREFIX
    key = prefix + "".join(secrets.choice(_ALPHABET) for _ in range(KEY_RANDOM_LENGTH))
    return GeneratedKey(
        key=key, display_prefix=key[:DISPLAY_PREFIX_LENGTH], key_hash=hash_api_key(key)
    )


def hash_api_key(key: str) -> str:
    """The SHA-256 of the key, as 64 hex characters."""
    return hashlib.sha256(key.encode()).hexdigest()


def is_api_key(token: str) -> bool:
    """Login tokens and API keys use the same header. API keys start with our prefixes."""
    return token.startswith((LIVE_KEY_PREFIX, PUBLIC_KEY_PREFIX))


def normalize_origin(value: str) -> str:
    """Check a website origin and write it the way browsers send it.

    "https://Example.com:443/" -> "https://example.com". Only a scheme, a host and a port.
    """
    parts = urlsplit(value.strip())
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise ValueError("An origin starts with http:// or https:// and has a host name.")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        raise ValueError(
            "An origin has only a scheme, a host and a port, like https://example.com."
        )
    port = parts.port  # raises ValueError if the port is not a valid number
    port_text = "" if port in (None, _DEFAULT_PORTS[parts.scheme]) else f":{port}"
    return f"{parts.scheme}://{parts.hostname}{port_text}"
